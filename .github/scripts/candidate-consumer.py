#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Prepare verified fork images for native checks and job-local Zarf pulls."""

import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen
import uuid


SPEC = importlib.util.spec_from_file_location('candidate_artifacts', Path(__file__).with_name('candidate-artifacts.py'))
ARTIFACTS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ARTIFACTS)
IMAGES, ROOT, require = ARTIFACTS.IMAGES, ARTIFACTS.ROOT, ARTIFACTS.require
STATE_SCHEMA = 'uds-takserver/candidate-consumer/v1'
REGISTRY_IMAGE = 'docker.io/library/registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373'
RUN_LABEL = 'dev.uds-takserver.candidate-run'
HEAD_LABEL = 'dev.uds-takserver.candidate-head'


def command(*args):
    return subprocess.check_output(list(args), text=True).strip()


def native_architecture():
    import platform
    machine = platform.machine()
    require(machine in ('x86_64', 'aarch64', 'arm64'), 'unsupported native candidate machine')
    return 'amd64' if machine == 'x86_64' else 'arm64'


def is_fork():
    if os.environ.get('GITHUB_EVENT_NAME') != 'pull_request':
        return False
    event = IMAGES.read_metadata(Path(os.environ['GITHUB_EVENT_PATH']))
    return event['pull_request']['head']['repo']['full_name'] != os.environ['GITHUB_REPOSITORY']


def identity(group, arch, context):
    # Published immutable images have no artifact producer run to trust.
    # These sentinel run fields are used only to validate input syntax.
    return ARTIFACTS.expectation(group, arch, {'repository': context['repository'], 'head_sha': context['head_sha'],
                    'checkout_sha': context['checkout_sha'],
                    'workflow': '.github/workflows/' + ARTIFACTS.PRODUCERS[group], 'run_id': 1, 'run_attempt': 1})


def inspect_remote(ref, missing_ok=False, formatted=False):
    args = ['docker', 'buildx', 'imagetools', 'inspect', ref]
    args.extend(['--format', '{{json .Image}}'] if formatted else ['--raw'])
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        if missing_ok:
            return None
        raise ValueError('could not inspect immutable candidate image ' + ref)
    return IMAGES.read_json(result.stdout)


def published_images(expected):
    images = []
    for name in IMAGES.GROUPS[expected['group']]:
        ref = f"{IMAGES.REPOSITORY}/{name}:{expected['tag']}"
        index = inspect_remote(ref, missing_ok=True)
        if index is None:
            return None
        # A native consumer must never accidentally accept a tag containing
        # another architecture, or an attestation entry, as its own image.
        if 'manifests' in index:
            matches = [item for item in index['manifests'] if item.get('platform') ==
                       {'architecture': expected['architecture'], 'os': 'linux'} or
                       (item.get('platform', {}).get('architecture') == expected['architecture'] and
                        item.get('platform', {}).get('os') == 'linux')]
            require(len(matches) == 1, 'immutable image lacks the requested native architecture')
            manifest_digest = matches[0]['digest']
        else:
            # The immutable shared key is a native multi-platform index.
            raise ValueError('published immutable candidate key is not a multi-platform index')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', manifest_digest) is not None, 'invalid native image manifest digest')
        source = f'{IMAGES.REPOSITORY}/{name}@{manifest_digest}'
        manifest = inspect_remote(source)
        config_digest = manifest.get('config', {}).get('digest', '')
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', config_digest) is not None, 'invalid native image config digest')
        config = inspect_remote(source, formatted=True)
        require(config.get('architecture') == expected['architecture'] and config.get('os') == 'linux',
                'published native image architecture mismatch')
        require(((config.get('config') or {}).get('Labels') or {}).get(IMAGES.INPUTS_LABEL) == expected['inputs'],
                'published native image inputs mismatch')
        images.append({'name': name, 'ref': ref, 'source': source, 'config_digest': config_digest})
    return images


def verify_loaded(ref, expected, config_digest):
    details = IMAGES.read_json(command('docker', 'image', 'inspect', ref))[0]
    require(details['Id'] == config_digest and details['Architecture'] == expected['architecture'] and details['Os'] == 'linux',
            f"loaded candidate image identity mismatch for {ref}: "
            f"expected {config_digest} linux/{expected['architecture']}, "
            f"got {details['Id']!r} {details['Os']!r}/{details['Architecture']!r}")
    require(((details.get('Config') or {}).get('Labels') or {}).get(IMAGES.INPUTS_LABEL) == expected['inputs'],
            'loaded candidate image inputs mismatch')


def acquire(group, arch, context, directory, load=False, timeout=10800, wait=True, api=None):
    expected = identity(group, arch, context)
    api = api or ARTIFACTS.GitHub(context['repository'], os.environ.get('GH_TOKEN', ''))
    try:
        ARTIFACTS.select_run(api, context, group)
        scheduled = True
    except ARTIFACTS.Pending:
        scheduled = ARTIFACTS.canonical_triggered(api, context, group)
    if not scheduled:
        published = published_images(expected)
        require(published is not None, 'unchanged image group has no verified published immutable key')
        if load:
            for image in published:
                subprocess.run(['docker', 'pull', '--platform', 'linux/' + arch, image['source']], check=True)
                verify_loaded(image['source'], expected, image['config_digest'])
                image['loaded_ref'] = image['source']
        return expected, published, {'kind': 'published-immutable'}
    if wait:
        artifact, expected = ARTIFACTS.resolve(api, context, group, arch, timeout=timeout)
    else:
        artifact, expected = ARTIFACTS.resolve_once(api, context, group, arch)
    directory = directory.with_name(directory.name + '-' + uuid.uuid4().hex[:8])
    cache = artifact['name'].startswith('candidate-cache-')
    if cache:
        api.download(artifact, directory, cache=True)
        decision = IMAGES.read_metadata(directory / 'cache.json')
        images = verify_cache(decision, expected)
        if load:
            for image in images:
                subprocess.run(['docker', 'pull', '--platform', 'linux/' + arch, image['source']], check=True)
                verify_loaded(image['source'], expected, image['config_digest'])
                image['loaded_ref'] = image['source']
        return expected, images, {'kind': 'producer-cache-decision', 'artifact_id': artifact['id'], 'producer': expected['producer']}
    if not load:
        return expected, [], {'kind': 'artifact', 'artifact_id': artifact['id'], 'producer': expected['producer']}
    api.download(artifact, directory)
    receipt = IMAGES.verify(directory, expected)
    subprocess.run(['docker', 'image', 'load', '--input', str(directory / 'images.tar')], check=True)
    images = []
    for image in receipt['images']:
        verify_loaded(image['ref'], expected, image['config_digest'])
        images.append({'name': image['name'], 'ref': f"{IMAGES.REPOSITORY}/{image['name']}:{expected['tag']}",
                       'loaded_ref': image['ref'], 'config_digest': image['config_digest']})
    # Docker has verified and loaded every layer; retain the small receipt,
    # then release transport space before registry pushes and Zarf packaging.
    (directory / 'images.tar').unlink()
    return expected, images, {'kind': 'artifact', 'artifact_id': artifact['id'], 'producer': expected['producer'],
                              'archive_sha256': receipt['archive']['sha256']}


def export_cache(directory):
    context, producer = ARTIFACTS.producer_context('takserver')
    require(not directory.exists(), 'candidate cache decision directory already exists')
    values = {}
    for arch in ('amd64', 'arm64'):
        expected = ARTIFACTS.expectation('takserver', arch, producer)
        images = published_images(expected)
        require(images is not None, 'cannot reuse missing immutable TAK images')
        values[arch] = images
    receipt = {'schema': 'uds-takserver/candidate-cache/v1', 'producer': producer, 'group': 'takserver',
               'inputs': expected['inputs'], 'tag': expected['tag'], 'platforms': values}
    directory.mkdir(parents=True, mode=0o700)
    (directory / 'cache.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')


def ready(group, arches, context, directory, timeout):
    started = time.monotonic()
    deadline = started + timeout
    api = ARTIFACTS.GitHub(context['repository'], os.environ.get('GH_TOKEN', ''))
    interval = 15
    while True:
        pending = []
        # Inspect every required architecture before waiting. A running AMD
        # build must not hide a terminal ARM failure in the same native gate.
        with api.snapshot():
            for arch in arches:
                try:
                    acquire(group, arch, context, directory / (group + '-' + arch), wait=False, api=api)
                except ARTIFACTS.Pending as error:
                    pending.append(str(error))
        if not pending:
            for arch in arches:
                print(f"candidate-consumer: {group} linux/{arch} ready for {context['head_sha']}")
            return
        delay = ARTIFACTS.pending_delay(started, deadline, pending, interval)
        print(f"candidate-consumer: waiting for {group}: {'; '.join(pending)}", flush=True)
        time.sleep(delay)
        interval = min(120, interval * 2)


def verify_cache(decision, expected):
    require(set(decision) == {'schema', 'producer', 'group', 'inputs', 'tag', 'platforms'} and
            decision['schema'] == 'uds-takserver/candidate-cache/v1' and expected['group'] == 'takserver' and
            all(decision[key] == expected[key] for key in ('producer', 'group', 'inputs', 'tag')) and
            set(decision['platforms']) == {'amd64', 'arm64'}, 'candidate cache decision identity mismatch')
    wanted = set(IMAGES.GROUPS['takserver'])
    for images in decision['platforms'].values():
        require(len(images) == len(wanted) and {image['name'] for image in images} == wanted,
                'candidate cache decision image set mismatch')
        for image in images:
            require(set(image) == {'name', 'ref', 'source', 'config_digest'} and
                    image['ref'] == f"{IMAGES.REPOSITORY}/{image['name']}:{expected['tag']}" and
                    re.fullmatch(re.escape(f"{IMAGES.REPOSITORY}/{image['name']}@") + r'sha256:[0-9a-f]{64}', image['source']) is not None and
                    re.fullmatch(r'sha256:[0-9a-f]{64}', image['config_digest']) is not None,
                    'candidate cache decision digest mismatch')
    return decision['platforms'][expected['architecture']]


def validate_registry(registry):
    require(re.fullmatch(r'127\.0\.0\.1:[1-9][0-9]{0,4}', registry or '') is not None and
            int(registry.split(':')[1]) <= 65535, 'candidate source must be a job-local loopback registry')


def registry_details(name, missing_ok=False):
    require(re.fullmatch(r'uds-candidate-[1-9][0-9]*-[a-f0-9]{12}', name or '') is not None, 'invalid candidate registry name')
    result = subprocess.run(['docker', 'inspect', name], text=True, capture_output=True)
    if result.returncode:
        if missing_ok and any(message in result.stderr.lower() for message in ('no such object', 'no such container', 'no container with name')):
            return None
        raise ValueError('cannot inspect the named candidate registry')
    return IMAGES.read_json(result.stdout)[0]


def loopback_manifest(ref):
    registry, image = ref.split('/', 1)
    validate_registry(registry)
    path, tag = image.rsplit(':', 1)
    require(re.fullmatch(r'[a-z0-9_./-]+', path) is not None and '..' not in path.split('/') and
            re.fullmatch(r'[A-Za-z0-9_.-]+', tag) is not None, 'invalid candidate mirror path')
    request = Request(f'http://{registry}/v2/{path}/manifests/{tag}', headers={'Accept':
                      'application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json'})
    with urlopen(request, timeout=15) as response:
        raw = response.read(IMAGES.MAX_METADATA + 1)
        require(len(raw) <= IMAGES.MAX_METADATA, 'candidate mirror manifest exceeds its size limit')
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        require(response.headers.get('Docker-Content-Digest') == digest, 'candidate registry manifest digest mismatch')
    return IMAGES.read_json(raw), digest


def start_registry(context, state_path=None, state=None):
    run_id = os.environ['GITHUB_RUN_ID']
    require(re.fullmatch(r'[1-9][0-9]*', run_id) is not None, 'invalid consumer workflow run id')
    name = f'uds-candidate-{run_id}-{uuid.uuid4().hex[:12]}'
    if state_path is not None:
        state['registry_name'] = name
        write_state(state_path, state)  # Cancellation can happen during Docker create/start.
    created = False
    try:
        subprocess.run(['docker', 'run', '-d', '--name', name, '--label', RUN_LABEL + '=' + run_id,
                        '--label', HEAD_LABEL + '=' + context['head_sha'], '-p', '127.0.0.1::5000', REGISTRY_IMAGE], check=True)
        created = True
        details = registry_details(name)
        bindings = details['NetworkSettings']['Ports']['5000/tcp']
        require(len(bindings) == 1 and bindings[0]['HostIp'] == '127.0.0.1', 'candidate registry must bind loopback only')
        registry = '127.0.0.1:' + bindings[0]['HostPort']
        validate_registry(registry)
        for _ in range(30):
            try:
                with urlopen('http://' + registry + '/v2/', timeout=1) as response:
                    if response.status == 200:
                        return name, registry
            except OSError:
                pass
            time.sleep(1)
        raise ValueError('job-local candidate registry did not become ready')
    except BaseException:
        if not created:
            # The daemon can create an object even if start/client completion
            # fails. Inspect ownership before removing an ambiguous name.
            details = registry_details(name, missing_ok=True)
            labels = ((details or {}).get('Config') or {}).get('Labels') or {}
            created = labels.get(RUN_LABEL) == run_id and labels.get(HEAD_LABEL) == context['head_sha']
        if created:
            subprocess.run(['docker', 'rm', '-f', name], check=False)
        raise


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    require('\n' not in str(path) and '\r' not in str(path), 'invalid candidate state path')
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n')
    path.chmod(0o600)


def prepare(state_path, timeout):
    context = ARTIFACTS.context_from_environment()
    arch = native_architecture()
    if state_path.exists():
        state = validate_state(state_path, context)
        return state
    state = {'schema': STATE_SCHEMA, **context, 'consumer_run_id': os.environ['GITHUB_RUN_ID'],
             'consumer_run_attempt': os.environ['GITHUB_RUN_ATTEMPT'], 'architecture': arch,
             'registry_name': '', 'registry': '', 'groups': {}}
    try:
        name, registry = start_registry(context, state_path=state_path, state=state)
        state.update({'registry_name': name, 'registry': registry})
        write_state(state_path, state)  # Allows cancellation cleanup during transfer.
        for group in ARTIFACTS.PRODUCERS:
            directory = state_path.parent / ('candidate-' + group)
            expected, images, origin = acquire(group, arch, context, directory, load=True, timeout=timeout)
            for image in images:
                target = image['ref'].replace('ghcr.io/', registry + '/', 1)
                subprocess.run(['docker', 'image', 'tag', image['loaded_ref'], target], check=True)
                subprocess.run(['docker', 'push', target], check=True)
                manifest, mirror_digest = loopback_manifest(target)
                require(manifest.get('config', {}).get('digest') == image['config_digest'], 'job-local candidate config changed during transfer')
                image['mirror'] = target
                image['mirror_manifest_digest'] = mirror_digest
            state['groups'][group] = {'inputs': expected['inputs'], 'tag': expected['tag'], 'origin': origin, 'images': images}
            write_state(state_path, state)
        validate_state(state_path, context)
    except BaseException:
        if state.get('registry_name'):
            details = registry_details(state['registry_name'], missing_ok=True)
            labels = ((details or {}).get('Config') or {}).get('Labels') or {}
            if labels.get(RUN_LABEL) == os.environ['GITHUB_RUN_ID'] and labels.get(HEAD_LABEL) == context['head_sha']:
                subprocess.run(['docker', 'rm', '-f', state['registry_name']], check=False)
        raise
    environment = os.environ.get('GITHUB_ENV')
    if environment:
        with open(environment, 'a') as output:
            output.write(f'CANDIDATE_IMAGE_STATE={state_path}\nDEV_PACKAGE_REUSE=false\n')
    return state


def validate_state(path, context=None):
    state = IMAGES.read_metadata(path)
    context = context or ARTIFACTS.context_from_environment()
    require(state.get('schema') == STATE_SCHEMA and all(state.get(key) == value for key, value in context.items()) and
            state.get('consumer_run_id') == os.environ['GITHUB_RUN_ID'] and
            state.get('consumer_run_attempt') == os.environ['GITHUB_RUN_ATTEMPT'] and state.get('architecture') == native_architecture(),
            'candidate consumer source identity mismatch')
    validate_registry(state.get('registry'))
    details = registry_details(state.get('registry_name'))
    labels = (details.get('Config') or {}).get('Labels') or {}
    require(labels.get(RUN_LABEL) == os.environ['GITHUB_RUN_ID'] and labels.get(HEAD_LABEL) == context['head_sha'],
            'candidate registry belongs to another source run')
    require(details.get('State', {}).get('Running') is True, 'candidate registry is not running')
    require(details.get('NetworkSettings', {}).get('Ports', {}).get('5000/tcp') ==
            [{'HostIp': '127.0.0.1', 'HostPort': state['registry'].split(':')[1]}], 'candidate registry binding changed')
    require(set(state.get('groups', {})) == set(ARTIFACTS.PRODUCERS), 'candidate image preparation is incomplete')
    for group, entry in state['groups'].items():
        expected = identity(group, state['architecture'], context)
        require(entry['inputs'] == expected['inputs'] and entry['tag'] == expected['tag'], 'candidate state image inputs changed')
        if entry['origin']['kind'] != 'published-immutable':
            # UDS tasks can shadow GH_TOKEN with an included task variable.
            token = os.environ.get('CANDIDATE_API_TOKEN') or os.environ.get('GH_TOKEN', '')
            api = ARTIFACTS.GitHub(context['repository'], token)
            selected = ARTIFACTS.select_run(api, context, group)
            producer = {**expected['producer'], 'run_id': selected['id'], 'run_attempt': selected['run_attempt']}
            require(entry['origin']['producer'] == producer, 'canonical producer selection changed after candidate preparation')
        wanted = {f"{IMAGES.REPOSITORY}/{name}:{expected['tag']}" for name in IMAGES.GROUPS[group]}
        require({image['ref'] for image in entry['images']} == wanted and len(entry['images']) == len(wanted),
                'candidate state has an unexpected image set')
        for image in entry['images']:
            mirror = image['ref'].replace('ghcr.io/', state['registry'] + '/', 1)
            require(image['mirror'] == mirror and re.fullmatch(r'sha256:[0-9a-f]{64}', image['config_digest']) is not None,
                    'candidate mirror identity mismatch')
            manifest, mirror_digest = loopback_manifest(mirror)
            require(manifest.get('config', {}).get('digest') == image['config_digest'] and
                    mirror_digest == image['mirror_manifest_digest'], 'candidate mirror bytes changed')
    return state


def create_options(state_path, extra):
    state = validate_state(state_path)
    require('\n' not in extra and '\r' not in extra, 'candidate create options must fit on one line')
    tokens = shlex.split(extra)
    for index, token in enumerate(tokens):
        if token.startswith('--registry-override='):
            value = token.split('=', 1)[1]
        elif token == '--registry-override':
            require(index + 1 < len(tokens), 'registry override value is missing')
            value = tokens[index + 1]
        else:
            continue
        try:
            entries = list(csv.reader([value], strict=True))
        except csv.Error as error:
            raise ValueError('invalid registry override CSV') from error
        require(len(entries) == 1 and bool(entries[0]), 'empty registry override CSV')
        sources = [item.split('=', 1)[0].lower() for item in entries[0]]
        require(all(not source.startswith('ghcr.io') and not 'ghcr.io'.startswith(source) for source in sources),
                'additional options cannot override the validated candidate source')
    return shlex.join([*tokens, '--registry-override', 'ghcr.io=' + state['registry']])


def scan_manifest(state_path, output):
    state = validate_state(state_path)
    text = (ROOT / 'zarf.yaml').read_text()
    for group in state['groups'].values():
        for image in group['images']:
            source = '"' + image['ref'] + '"'
            require(source in text, 'canonical scan image reference is missing')
            # No tag in this current-side-only source. The workflow supplies
            # explicit name@sha256 comparison mappings for uds-pk 0.2.0.
            mirror = image['mirror'].rsplit(':', 1)[0] + '@' + image['mirror_manifest_digest']
            text = text.replace(source, '"' + mirror + '"')
    require(not output.exists(), 'candidate scan manifest already exists')
    output.write_text(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('is-fork', 'ready', 'load', 'prepare', 'options', 'cleanup', 'cache-decision', 'scan-manifest'))
    parser.add_argument('--group', choices=ARTIFACTS.PRODUCERS)
    parser.add_argument('--architecture', choices=('amd64', 'arm64'))
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('RUNNER_TEMP', tempfile.gettempdir())) / 'uds-candidate' / 'state.json')
    parser.add_argument('--extra', default='')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timeout', type=int, default=10800)
    args = parser.parse_args()
    if args.command == 'scan-manifest':
        require(args.output is not None, 'candidate scan manifest needs an output path')
        scan_manifest(args.state, args.output)
        return
    if args.command == 'cache-decision':
        export_cache(args.state.parent / 'cache-decision')
        return
    if args.command == 'is-fork':
        print('true' if is_fork() else 'false')
        return
    if args.command == 'options':
        print(create_options(args.state, args.extra))
        return
    if args.command == 'cleanup':
        if args.state.exists():
            state = IMAGES.read_metadata(args.state)
            details = registry_details(state.get('registry_name'), missing_ok=True)
            if details is None:
                return
            require(((details.get('Config') or {}).get('Labels') or {}).get(RUN_LABEL) == os.environ['GITHUB_RUN_ID'],
                    'refusing to remove another run candidate registry')
            subprocess.run(['docker', 'rm', '-f', state['registry_name']], check=True)
        return
    require(args.timeout > 0, 'candidate wait timeout must be positive')
    if args.command == 'prepare':
        prepare(args.state, args.timeout)
        return
    require(args.group is not None, 'native candidate group is required')
    context = ARTIFACTS.context_from_environment()
    arches = [args.architecture] if args.architecture else ['amd64', 'arm64']
    if args.command == 'ready':
        ready(args.group, arches, context, args.state.parent, args.timeout)
        return
    for arch in arches:
        if args.command == 'load':
            require(arch == native_architecture(), 'candidate load must use the native architecture')
        expected, images, _ = acquire(args.group, arch, context, args.state.parent / ('check-' + args.group + '-' + arch),
                                      load=args.command == 'load', timeout=args.timeout)
        if args.command == 'load':
            for image in images:
                ref = f"{IMAGES.REPOSITORY}/{image['name']}:{expected['tag']}-{arch}"
                subprocess.run(['docker', 'image', 'tag', image['loaded_ref'], ref], check=True)
        print(f"candidate-consumer: {args.group} linux/{arch} ready for {context['head_sha']}")


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'candidate-consumer: {error}') from error
