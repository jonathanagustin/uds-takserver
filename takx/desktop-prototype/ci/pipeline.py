#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Driver of the opt-in desktop-prototype workflow (one group = one job).

  pipeline.py plan --trigger FILE [--requested JSON]
      Decide which architectures to build and print them as GitHub outputs.
      The trigger file is plain text; lines starting with '#' are comments and
      the only setting is 'arches=<comma separated list of amd64, arm64>'.
      Without that line both are built. A workflow_dispatch request (a JSON
      list) wins over the file.
  pipeline.py check
      Verify groups.json against ../providers.json: every provider is built by
      exactly one group, every prerequisite is built in an earlier wave, and
      every stage script exists.
  pipeline.py run GROUP
      Restore the prerequisites of GROUP from the provider cache, run its
      stage scripts in order and write status/ci-<group>.json. A provider that
      is already ok locally is left alone, so a rerun does not rebuild; the
      stage scripts restore and push their own providers themselves.
  pipeline.py summary [ROOT]
      Print a Markdown summary of status/*.json for the job summary.

Environment of 'run':
  TAKX_PROTO_ROOT          work root (default /var/lib/takx-proto)
  TAKX_PROTO_CACHE         provider cache the stages restore from and push to
  TAKX_CI_CACHE_FALLBACK   second, read-only provider cache tried on a miss
                           (the hand-pushed amd64 cache); optional
  TAKX_CI_REPO             repository the stages run from (default: the one
                           that holds this file)

An architecture with override patches (arm64) is built from its patched copy
of the tree (../arch_tree.py): every stage script is started from that copy,
including the ones that have no hand-over of their own, and the cache is asked
through it, because its keys come from the patched recipe files. A shared lock
on the copy is held while the group runs, so it cannot be replaced under it.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROTO = HERE.parent
ARCHES = ('amd64', 'arm64')
# Nothing waits for another job: prerequisites are restored before a stage runs.
NO_WAIT = {'TAKX_PREREQ_TIMEOUT': '0', 'TAKX_LANE2_SQLCIPHER_WAIT_S': '0', 'TAKX_LANE3_WAIT_S': '0',
           'TAKX_CLIENT_WAIT_S': '0'}


class Refusal(Exception):
    pass


def load(path):
    return json.loads(Path(path).read_text())


def groups():
    return load(HERE / 'groups.json')


def manifest(proto=PROTO):
    return load(Path(proto) / 'providers.json')


# ----------------------------------------------------------------------- plan
def parse_trigger(text):
    """The settings of a trigger file: {'arches': [...]} (both when unset)."""
    arches = list(ARCHES)
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key.strip() != 'arches':
            raise Refusal('trigger file line %d is not a comment and not arches=...: %r' % (number, line))
        arches = [item.strip() for item in value.split(',') if item.strip()]
    return {'arches': validate_arches(arches)}


def validate_arches(arches):
    if not isinstance(arches, list) or not arches or any(not isinstance(a, str) for a in arches):
        raise Refusal('the architectures must be a non-empty list')
    unknown = [a for a in arches if a not in ARCHES]
    if unknown:
        raise Refusal('unknown architecture %r (amd64 and arm64 only)' % (unknown[0],))
    return [a for a in ARCHES if a in arches]


def plan(trigger, requested):
    settings = parse_trigger(Path(trigger).read_text()) if trigger and Path(trigger).is_file() else None
    if requested:
        try:
            arches = validate_arches(json.loads(requested))
        except ValueError:
            raise Refusal('the requested architectures are not a JSON list: %r' % (requested,))
    elif settings is None:
        raise Refusal('the trigger file %s does not exist' % trigger)
    else:
        arches = settings['arches']
    return {'arches': arches}


# ---------------------------------------------------------------------- check
def closure(providers, names):
    """names and everything they are built from, prerequisites first."""
    seen, result = set(), []

    def visit(name):
        if name in seen:
            return
        if name not in providers:
            raise Refusal('unknown provider %s' % name)
        seen.add(name)
        for dependency in sorted(providers[name]['prerequisites']):
            visit(dependency)
        result.append(name)
    for name in sorted(names):
        visit(name)
    return result


def restore_list(config, providers):
    """Providers a group needs from the cache before its stages start."""
    if config.get('restore') == 'all':
        return closure(providers, providers)
    built = set(config['builds'])
    return [name for name in closure(providers, built) if name not in built]


def check(data=None, providers=None, stages=None):
    data = data or groups()
    providers = providers or manifest()['providers']
    stages = stages or PROTO / 'stages'
    problems, owner = [], {}
    waves = data['waves']
    for name, config in data['groups'].items():
        if config['wave'] not in waves:
            problems.append('%s: unknown wave %s' % (name, config['wave']))
        for provider in config['builds']:
            if provider not in providers:
                problems.append('%s: unknown provider %s' % (name, provider))
            elif provider in owner:
                problems.append('%s is built by %s and %s' % (provider, owner[provider], name))
            else:
                owner[provider] = name
        for command in config['run']:
            if not (Path(stages) / command[0]).is_file():
                problems.append('%s: no stage script %s' % (name, command[0]))
        if config.get('after') and config['after'] not in data['groups']:
            problems.append('%s: unknown group %s' % (name, config['after']))
    for provider in providers:
        if provider not in owner:
            problems.append('provider %s is built by no group' % provider)
    for name, config in data['groups'].items():
        if any(p not in providers for p in config['builds']):
            continue
        for needed in restore_list(config, providers):
            if needed in owner and waves.index(data['groups'][owner[needed]]['wave']) >= waves.index(config['wave']) \
                    and config['wave'] in waves:
                problems.append('%s (wave %s) needs %s, which %s builds in wave %s' % (
                    name, config['wave'], needed, owner[needed], data['groups'][owner[needed]]['wave']))
    return problems


# ------------------------------------------------------------------------ run
def arch_name(proto):
    return subprocess.check_output(['bash', str(Path(proto) / 'arch.sh'), 'get', 'arch'], text=True, timeout=30).strip()


def effective_proto(proto, arch, root):
    """The desktop-prototype directory the stages of this architecture really run from."""
    if not list((Path(proto) / 'overrides' / arch).glob('*.patch')):
        return Path(proto)
    tree = subprocess.check_output([sys.executable, '-I', '-B', str(Path(proto) / 'arch_tree.py'), 'ensure',
                                    '--arch', arch, '--root', str(root)], text=True, timeout=600).strip()
    return Path(tree) / 'takx' / 'desktop-prototype'


def locally_ok(root, providers, name):
    try:
        state = load(Path(root) / 'status' / (name + '.json')).get('status')
    except (OSError, ValueError):
        return False
    return state == 'ok' and (Path(root) / 'P' / name / providers[name]['marker']).exists()


def restore(cache_py, root, providers, name, prefixes, log):
    """'local', the prefix that had it, or None. Never overwrites a directory."""
    if locally_ok(root, providers, name):
        return 'local'
    for prefix in prefixes:
        env = dict(os.environ, TAKX_PROTO_CACHE=prefix, TAKX_PROTO_ROOT=str(root))
        with open(log, 'ab') as stream:
            stream.write(('\n===== ci restore %s from %s\n' % (name, prefix)).encode())
            stream.flush()
            code = subprocess.run([sys.executable, '-I', str(cache_py), 'restore', name], env=env, stdout=stream,
                                  stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL).returncode
        if code == 0 and locally_ok(root, providers, name):
            return prefix
    return None


def run_group(name, root, proto):
    data = groups()
    if name not in data['groups']:
        raise Refusal('unknown group %s (known: %s)' % (name, ', '.join(sorted(data['groups']))))
    config = data['groups'][name]
    arch = arch_name(proto)
    effective = effective_proto(proto, arch, root)
    providers = manifest(effective)['providers']
    for directory in ('status', 'logs', 'P'):
        (root / directory).mkdir(parents=True, exist_ok=True)
    held = None
    if effective != Path(proto):
        held = open(root / 'overrides' / arch / '.lock', 'a+')
        fcntl.flock(held, fcntl.LOCK_SH)
    log = root / 'logs' / 'provider-cache.log'
    prefixes = [p for p in (os.environ.get('TAKX_PROTO_CACHE'), os.environ.get('TAKX_CI_CACHE_FALLBACK'))
                if p and p != 'off']
    record = {'stage': 'ci-' + name, 'group': name, 'arch': arch, 'stages_from': str(effective), 'restored': {},
              'commands': [], 'status': 'ok', 'seconds': 0, 'outputs': [], 'notes': ''}
    started = time.time()
    missing = []
    for provider in restore_list(config, providers):
        source = restore(effective / 'provider_cache.py', root, providers, provider, prefixes, log)
        record['restored'][provider] = source
        print('ci: prerequisite %-20s %s' % (provider, source or 'NOT IN THE CACHE'), flush=True)
        if source is None:
            missing.append(provider)
    for provider in config['builds']:  # a rerun, or the second cache, may already hold the group's own providers
        source = restore(effective / 'provider_cache.py', root, providers, provider, prefixes[1:], log)
        if source:
            record['restored'][provider] = source
            print('ci: own provider  %-20s %s' % (provider, source), flush=True)
    if missing:
        record.update(status='blocked', notes='prerequisites are not in the provider cache: %s; the group that builds '
                                              'them failed or did not push; nothing was run' % ', '.join(missing))
    else:
        env = dict(os.environ, TAKX_PROTO_ROOT=str(root), **NO_WAIT)
        for command in config['run']:
            argv = [str(effective / 'stages' / command[0])] + command[1:]
            print('ci: running %s' % ' '.join(command), flush=True)
            begun = time.time()
            code = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL).returncode
            record['commands'].append({'command': command, 'exit': code, 'seconds': round(time.time() - begun)})
            print('ci: %s exited %d after %d s' % (' '.join(command), code, time.time() - begun), flush=True)
            if code:
                record['status'] = 'failed'
        failed = [' '.join(c['command']) for c in record['commands'] if c['exit']]
        record['notes'] = ('every stage script exited 0' if not failed else
                           'failed: %s; see status/<stage>.json and logs/' % '; '.join(failed))
    record['seconds'] = round(time.time() - started)
    target = root / 'status' / ('ci-%s.json' % name)
    target.write_text(json.dumps(record, indent=1) + '\n')
    print('ci: group %s on %s: %s (%s)' % (name, arch, record['status'], record['notes']), flush=True)
    if held:
        held.close()
    return 0 if record['status'] == 'ok' else 1


# -------------------------------------------------------------------- summary
def summary(root):
    root = Path(root)
    rows = []
    for path in sorted((root / 'status').glob('*.json')):
        try:
            data = load(path)
        except (OSError, ValueError):
            rows.append((path.stem, 'unreadable', '', ''))
            continue
        if not isinstance(data, dict) or 'status' not in data:
            continue
        notes = ' '.join(str(data.get('notes') or '').split())
        if 'restored_from' in data:
            notes = 'restored from the provider cache: %s. %s' % (data['restored_from'], notes)
        rows.append((data.get('stage') or path.stem, str(data['status']), str(data.get('seconds', '')),
                     notes[:300].replace('|', '\\|')))
    lines = ['### TAKX desktop prototype: stage status', '',
             'Kernel 8.30.5 + project patches and CommonCommo 1.17.2 patched; nothing here is validated or '
             'qualified, on any architecture.', '']
    marker = None
    for candidate in sorted((root / 'overrides').glob('*/tree/.takx-arch-tree.json')) if (root / 'overrides').is_dir() else []:
        marker = load(candidate)
        lines += ['Architecture tree: `%s`, %d override patches (%s).' % (
            marker['arch'], len(marker['patches']), ', '.join(row['name'] for row in marker['patches'])), '']
    if not rows:
        lines.append('No status file was written.')
    else:
        lines += ['| Stage | Status | Seconds | Notes |', '| --- | --- | --- | --- |']
        lines += ['| %s | %s | %s | %s |' % row for row in rows]
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(prog='pipeline.py', description=__doc__.split('\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    planner = commands.add_parser('plan')
    planner.add_argument('--trigger', required=True)
    planner.add_argument('--requested', default='')
    commands.add_parser('check')
    runner = commands.add_parser('run')
    runner.add_argument('group')
    commands.add_parser('summary').add_argument('root', nargs='?', default=os.environ.get('TAKX_PROTO_ROOT')
                                                or '/var/lib/takx-proto')
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan':
            print('arches=' + json.dumps(plan(args.trigger, args.requested)['arches'], separators=(',', ':')))
            return 0
        if args.command == 'check':
            problems = check()
            for problem in problems:
                print('pipeline.py: ' + problem, file=sys.stderr)
            if not problems:
                print('groups.json is consistent with providers.json')
            return 1 if problems else 0
        if args.command == 'run':
            root = Path(os.path.abspath(os.environ.get('TAKX_PROTO_ROOT') or '/var/lib/takx-proto'))
            proto = Path(os.environ['TAKX_CI_REPO']) / 'takx' / 'desktop-prototype' if os.environ.get('TAKX_CI_REPO') \
                else PROTO
            return run_group(args.group, root, proto)
        sys.stdout.write(summary(args.root))
        return 0
    except Refusal as refusal:
        print('pipeline.py: %s' % refusal, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
