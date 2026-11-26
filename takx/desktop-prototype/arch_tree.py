#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Patched copy of the tree for an architecture the recipes do not name.

The recipes under takx/ are never edited. Many of them refuse every machine
but x86_64 or name x86_64 paths, and they verify each other by hashing their
sibling recipes, so one patched recipe cannot be used next to unpatched ones.
On an architecture that has override patches (overrides/<arch>/*.patch) the
stage scripts therefore run from a complete copy:

  <work root>/overrides/<arch>/tree/
    takx/                  copy of the tracked takx/ with every patch applied
    LICENSE                copy (takx/worldwind-java reads it)
    upstream/, .git/       empty; run-in-toolchain.sh mounts the real
                           directories of the base repository there
    .takx-arch-tree.json   receipt: base repository, patches, digests
    .takx-arch-tree.base   the base repository path, one line (for shell)

Each patch is named after the recipe it changes, may only change existing
files below takx/<recipe>/, and is applied with fuzz 0. Because the stage
scripts, provider_cache.py and providers.json are part of the copy, the
provider cache keys of that architecture are derived from the patched recipe
files and from the path of the copy; they can never equal an amd64 key.

A second kind, overrides/<arch>/prototype/<name>.patch, changes files of the
prototype itself that the architecture port does not own and that are still
amd64-only (STAGE_PREFIXES: the assembler). Such a patch is applied to the
copy in the same way, after the recipe patches, but it is not a condition of
the tree: when it no longer applies exactly, because the tracked file moved
on, the copy keeps the tracked file, the receipt records the patch as
'skipped' with the reason, and only the stage that needed it is affected.
No provider cache key depends on these files.

Usage:
  arch_tree.py ensure --arch ARCH [--root ROOT]   build or re-verify the copy
                                                  and print its path
  arch_tree.py check [--arch ARCH]                apply every patch to a
                                                  throw-away copy and report
  arch_tree.py show --arch ARCH [--root ROOT]     print the receipt

On amd64 there are no patches; 'ensure' refuses and the tracked tree is used.
"""
import argparse
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MARKER = '.takx-arch-tree.json'
BASE_FILE = '.takx-arch-tree.base'
SCHEMA = 1
ARCHES = ('amd64', 'arm64')
IGNORED = ('__pycache__', '*.pyc')
# Mount points for the base repository's submodule checkout and Git directory.
MOUNT_POINTS = ('upstream', '.git')
INEXACT = re.compile(r'succeeded at \d+ (?:with fuzz|\(offset)')
# What a patch of overrides/<arch>/prototype/ may change.
STAGE_DIRECTORY = 'prototype'
STAGE_PREFIXES = ('takx/desktop-prototype/assemble/',)


class Refusal(Exception):
    pass


def sha(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def listing(root):
    """{relative path: sha256} of takx/ and LICENSE below root."""
    root = Path(root)
    result = {}
    for path in sorted((root / 'takx').rglob('*')):
        relative = path.relative_to(root)
        if '__pycache__' in relative.parts or path.name.endswith('.pyc'):
            continue
        if path.is_symlink():
            raise Refusal('symbolic link in the tree: %s' % relative)
        if path.is_file():
            result[relative.as_posix()] = sha(path)
    if not result:
        raise Refusal('no takx/ files below %s' % root)
    result['LICENSE'] = sha(root / 'LICENSE')
    return result


def patch_files(repo, arch):
    return sorted((Path(repo) / 'takx/desktop-prototype/overrides' / arch).glob('*.patch'))


def stage_patch_files(repo, arch):
    return sorted((Path(repo) / 'takx/desktop-prototype/overrides' / arch / STAGE_DIRECTORY).glob('*.patch'))


def header_targets(patch, prefixes):
    """Files the headers of a patch name; refuses anything outside the prefixes."""
    old, targets = None, []
    for line in patch.read_text().splitlines():
        if line.startswith('--- '):
            old = line[4:].split('\t')[0]
        elif line.startswith('+++ ') and old is not None:
            new = line[4:].split('\t')[0]
            for name, side in ((old, 'a/'), (new, 'b/')):
                if not name.startswith(tuple(side + prefix for prefix in prefixes)) or '..' in Path(name).parts:
                    raise Refusal('%s: may only change files below %s, not %s' % (patch.name, ' or '.join(prefixes), name))
            if old[2:] != new[2:]:
                raise Refusal('%s: renames are not supported' % patch.name)
            targets.append(new[2:])
            old = None
    if not targets:
        raise Refusal('%s: no file header found' % patch.name)
    return targets


def patch_targets(patch):
    """Files a patch changes; refuses anything outside takx/<patch stem>/."""
    recipe = patch.name[:-len('.patch')]
    if not recipe or recipe == 'desktop-prototype' or '/' in recipe:
        raise Refusal('%s: not named after a recipe under takx/' % patch.name)
    return header_targets(patch, ('takx/%s/' % recipe,))


def stage_patch_targets(patch):
    """Files a prototype patch changes; refuses anything outside STAGE_PREFIXES."""
    return header_targets(patch, STAGE_PREFIXES)


def copy_base(repo, tree):
    shutil.copytree(Path(repo) / 'takx', tree / 'takx', symlinks=True, ignore=shutil.ignore_patterns(*IGNORED))
    shutil.copy2(Path(repo) / 'LICENSE', tree / 'LICENSE')
    for name in MOUNT_POINTS:
        (tree / name).mkdir()


def apply(tree, patches, base):
    """Apply each patch with fuzz 0; return (receipt rows, final listing)."""
    tool = shutil.which('patch')
    if not tool:
        raise Refusal('the patch tool is required to build an architecture tree')
    rows, current = [], dict(base)
    for patch in patches:
        targets = patch_targets(patch)
        for name in targets:
            if name not in current:
                raise Refusal('%s: %s does not exist in the tree' % (patch.name, name))
        result = subprocess.run([tool, '--batch', '--fuzz=0', '--no-backup-if-mismatch', '-p1', '-d', str(tree),
                                 '-i', str(patch)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if result.returncode:
            raise Refusal('%s does not apply with fuzz 0:\n%s' % (patch.name, result.stdout.strip()))
        if INEXACT.search(result.stdout):
            raise Refusal('%s applied inexactly (the tracked recipe changed; regenerate the patch):\n%s'
                          % (patch.name, result.stdout.strip()))
        after = listing(tree)
        changed = sorted(name for name in set(current) | set(after) if current.get(name) != after.get(name))
        if changed != sorted(set(targets)):
            raise Refusal('%s changed %s, its headers name %s' % (patch.name, changed, sorted(set(targets))))
        rows.append({'name': patch.name, 'sha256': sha(patch),
                     'files': {name: {'before': current[name], 'after': after[name]} for name in changed}})
        current = after
    return rows, current


def apply_stage(tree, patches, current):
    """Apply each prototype patch that applies exactly; return (receipt rows, final listing).

    A patch that does not apply is tried with --dry-run only, so it leaves the
    copy as it was, and its row says 'skipped' instead of 'files'.
    """
    tool = shutil.which('patch')
    rows, current = [], dict(current)
    for patch in patches:
        name = '%s/%s' % (STAGE_DIRECTORY, patch.name)
        targets = stage_patch_targets(patch)
        argv = [tool, '--batch', '--fuzz=0', '--no-backup-if-mismatch', '-p1', '-d', str(tree), '-i', str(patch)]
        missing = [target for target in targets if target not in current]
        reason = None
        if missing:
            reason = '%s does not exist in the tree' % missing[0]
        else:
            trial = subprocess.run(argv + ['--dry-run'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if trial.returncode or INEXACT.search(trial.stdout):
                reason = 'does not apply exactly to the tracked file: %s' % ' '.join(trial.stdout.split())[:300]
        if reason:
            rows.append({'name': name, 'sha256': sha(patch), 'skipped': reason})
            print('arch_tree.py: %s is NOT applied (%s); the stage that needs it stays as tracked' % (name, reason),
                  file=sys.stderr)
            continue
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        after = listing(tree)
        changed = sorted(item for item in set(current) | set(after) if current.get(item) != after.get(item))
        if result.returncode or changed != sorted(set(targets)):
            raise Refusal('%s changed %s, its headers name %s:\n%s' % (name, changed, sorted(set(targets)),
                                                                    result.stdout.strip()))
        rows.append({'name': name, 'sha256': sha(patch),
                     'files': {item: {'before': current[item], 'after': after[item]} for item in changed}})
        current = after
    return rows, current


def fingerprint(arch, repo, base, patches, stage_patches=()):
    document = {'schema': SCHEMA, 'arch': arch, 'base_repo': str(repo), 'base': base,
                'patches': {p.name: sha(p) for p in patches}}
    if stage_patches:
        document['stage_patches'] = {p.name: sha(p) for p in stage_patches}
    return hashlib.sha256(canonical(document)).hexdigest()


def read_marker(tree):
    try:
        return json.loads((Path(tree) / MARKER).read_text())
    except (OSError, ValueError):
        return None


def current(tree, wanted):
    marker = read_marker(tree)
    if not marker or marker.get('schema') != SCHEMA or marker.get('fingerprint') != wanted:
        return False
    try:
        return listing(tree) == marker.get('files') and all((Path(tree) / name).is_dir() for name in MOUNT_POINTS)
    except (Refusal, OSError):
        return False


def build(tree, arch, repo, base, patches, wanted, stage_patches=()):
    copy_base(repo, tree)
    copied = listing(tree)
    if copied != base:
        raise Refusal('the repository changed while it was copied')
    rows, files = apply(tree, patches, base)
    stage_rows, files = apply_stage(tree, stage_patches, files)
    (tree / BASE_FILE).write_text(str(repo) + '\n')
    marker = {'schema': SCHEMA, 'arch': arch, 'base_repo': str(repo), 'fingerprint': wanted, 'patches': rows,
              'stage_patches': stage_rows, 'file_count': len(files), 'files': files,
              'note': 'Patched copy for %s; the tracked recipes are unchanged. Nothing built from this tree is '
                      'validated or qualified on %s.' % (arch, arch)}
    (tree / MARKER).write_text(json.dumps(marker, indent=1, sort_keys=True) + '\n')
    return marker


def set_aside(root, tree, arch):
    failed = root / 'failed'
    failed.mkdir(parents=True, exist_ok=True)
    number = 1
    while (failed / ('arch-tree-%s-%d' % (arch, number))).exists():
        number += 1
    os.rename(tree, failed / ('arch-tree-%s-%d' % (arch, number)))
    return failed / ('arch-tree-%s-%d' % (arch, number))


def ensure(arch, root, repo):
    """Return the path of a verified tree, building or replacing it when needed."""
    if (repo / MARKER).is_file():
        return repo  # already running from a tree
    patches = patch_files(repo, arch)
    if not patches:
        raise Refusal('no override patches for %s: the tracked tree is used as it is' % arch)
    home = root / 'overrides' / arch
    if repo in home.parents or home == repo:
        raise Refusal('the work root must be outside the repository')
    home.mkdir(parents=True, exist_ok=True)
    tree = home / 'tree'
    stage_patches = stage_patch_files(repo, arch)
    base = listing(repo)
    wanted = fingerprint(arch, repo, base, patches, stage_patches)
    with open(home / '.lock', 'a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        if current(tree, wanted):
            return tree
        fcntl.flock(lock, fcntl.LOCK_UN)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refusal('%s is out of date but in use by a running stage; let it finish and retry' % tree)
        if current(tree, wanted):
            return tree
        staging = Path(tempfile.mkdtemp(prefix='tree.staging.', dir=home))
        try:
            staging.chmod(0o755)
            marker = build(staging, arch, repo, base, patches, wanted, stage_patches)
            if tree.exists():
                moved = set_aside(root, tree, arch)
                print('arch_tree.py: replaced an outdated tree, kept as %s' % moved, file=sys.stderr)
            os.rename(staging, tree)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        print('arch_tree.py: built %s (%d files, %d patches, %d files changed)' % (
            tree, marker['file_count'], len(marker['patches']), sum(len(r['files']) for r in marker['patches'])),
            file=sys.stderr)
        if marker['stage_patches']:
            print('arch_tree.py: prototype patches applied: %s; skipped: %s' % (
                ', '.join(r['name'] for r in marker['stage_patches'] if 'files' in r) or 'none',
                ', '.join(r['name'] for r in marker['stage_patches'] if 'skipped' in r) or 'none'), file=sys.stderr)
        return tree


def check(arch, repo, stage=False):
    """Apply the patches of one architecture to a throw-away copy; return the rows.

    With stage=True the rows of the prototype patches follow those of the
    recipe patches; a skipped one has 'skipped' instead of 'files'.
    """
    patches = patch_files(repo, arch)
    with tempfile.TemporaryDirectory(prefix='takx-arch-check-') as work:
        tree = Path(work) / 'tree'
        tree.mkdir()
        copy_base(repo, tree)
        rows, files = apply(tree, patches, listing(repo))
        if stage:
            with contextlib.redirect_stderr(io.StringIO()):
                rows = rows + apply_stage(tree, stage_patch_files(repo, arch), files)[0]
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(prog='arch_tree.py', description=__doc__.split('\n')[0])
    parser.add_argument('command', choices=('ensure', 'check', 'show'))
    parser.add_argument('--arch', choices=ARCHES)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('TAKX_PROTO_ROOT') or '/var/lib/takx-proto'))
    parser.add_argument('--repo', type=Path, default=REPO, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    repo, root = args.repo.resolve(), Path(os.path.abspath(args.root))
    try:
        if args.command == 'check':
            for arch in ([args.arch] if args.arch else ARCHES):
                rows = check(arch, repo, stage=True)
                recipe_rows = [row for row in rows if '/' not in row['name']]
                for row in rows:
                    if 'skipped' in row:
                        print('%s %s: NOT APPLIED, %s' % (arch, row['name'], row['skipped']))
                    else:
                        print('%s %s: fuzz 0, %s' % (arch, row['name'], ', '.join(sorted(row['files']))))
                if len(rows) > len(recipe_rows):
                    print('%s: %d of %d prototype patches apply exactly (a skipped one affects only its stage)' % (
                        arch, sum('files' in row for row in rows) - len(recipe_rows), len(rows) - len(recipe_rows)))
                print('%s: %d override patches apply exactly' % (arch, len(recipe_rows)))
            return 0
        if not args.arch:
            parser.error('--arch is required')
        if args.command == 'ensure':
            print(ensure(args.arch, root, repo))
            return 0
        marker = read_marker(root / 'overrides' / args.arch / 'tree')
        if marker is None:
            raise Refusal('no %s tree below %s' % (args.arch, root))
        marker.pop('files')
        print(json.dumps(marker, indent=1, sort_keys=True))
        return 0
    except Refusal as refusal:
        print('arch_tree.py: %s' % refusal, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
