#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Fetched inputs that the opt-in workflow does not store in its registry.

The workflow keeps the fetched inputs of an architecture as one registry
object (inputs.sh). The packages it writes on GHCR are readable without a
login, so storing a file there is publishing it. inputs-not-stored.json lists
the fetched files whose own terms do not allow that; they stay out of the
stored object, and the one job that needs them downloads them from their
public source and compares them with their pin.

  not_stored.py check
      Verify inputs-not-stored.json: its shape, and that an entry of an
      architecture with a lock (../inputs.<arch>.lock.json) equals that pin.
  not_stored.py scan ROOT [MEMBER...]
      Print, relative to ROOT and one per line, every regular file below the
      members (default: all of ROOT) that has the size and SHA-256 of a listed
      file of any architecture, whatever its name or place. inputs.sh save
      leaves exactly these out.
  not_stored.py complete ROOT ARCH
      Make every listed file of ARCH present at its path below ROOT. A file
      that is there must have the listed size and SHA-256; one that is absent
      is downloaded from its listed URL (../fetch_inputs.py download: Maven
      Central, and its mirror after an HTTP 429), compared, and only then put
      in place. Exit 1 when a file does not match or cannot be had.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROTO = HERE.parent
LIST = HERE / 'inputs-not-stored.json'
ARCHES = ('amd64', 'arm64')
FIELDS = {'coordinates', 'path', 'url', 'sha256', 'sha1', 'bytes', 'reason'}
PLAIN_PATH = re.compile(r'[A-Za-z0-9][A-Za-z0-9._+-]*(/[A-Za-z0-9][A-Za-z0-9._+-]*)*')


class Refusal(Exception):
    pass


def sha256(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def entries(path=LIST):
    """{arch: [entry, ...]} of the tracked list, shape checked."""
    data = json.loads(Path(path).read_text())
    if data.get('schema') != 1 or sorted(data.get('files', {})) != sorted(ARCHES):
        raise Refusal('%s: schema 1 with files for %s expected' % (path, ' and '.join(ARCHES)))
    for arch, items in data['files'].items():
        for item in items:
            if set(item) != FIELDS:
                raise Refusal('%s: an entry of %s has the fields %s' % (path, arch, sorted(item)))
            if not PLAIN_PATH.fullmatch(item['path']) or '..' in item['path'].split('/'):
                raise Refusal('%s: not a plain relative path: %r' % (path, item['path']))
            if not re.fullmatch(r'[0-9a-f]{64}', item['sha256']) or not re.fullmatch(r'[0-9a-f]{40}', item['sha1']):
                raise Refusal('%s: %s has no SHA-256 and SHA-1' % (path, item['path']))
            if not isinstance(item['bytes'], int) or item['bytes'] <= 0 or not item['url'].startswith('https://'):
                raise Refusal('%s: %s needs a size and an https URL' % (path, item['path']))
    return data['files']


def check(path=LIST, proto=PROTO):
    """Problems of the list; an architecture's own lock is the pin its entries must repeat."""
    problems = []
    for arch, items in entries(path).items():
        lock = Path(proto) / ('inputs.%s.lock.json' % arch)
        pins = json.loads(lock.read_text())['runtime_natives']['jars'] if lock.is_file() else {}
        for item in items:
            pin = pins.get(item['coordinates'])
            if lock.is_file() and pin is None:
                problems.append('%s: %s is not pinned by %s' % (arch, item['coordinates'], lock.name))
            elif pin and any(pin[field] != item[field] for field in ('url', 'sha256', 'sha1', 'bytes')):
                problems.append('%s: %s differs from its pin in %s' % (arch, item['coordinates'], lock.name))
    return problems


def scan(root, members=(), listed=None):
    """Relative paths of the files below root (or its members) that are a listed file by size and digest."""
    listed = entries() if listed is None else listed
    wanted = {}
    for items in listed.values():
        for item in items:
            wanted.setdefault(item['bytes'], set()).add(item['sha256'])
    root, found = Path(root), []
    for start in ([root / member for member in members] if members else [root]):
        if start.is_file() and not start.is_symlink():
            candidates = [start]
        else:
            candidates = (Path(base) / name for base, _, names in os.walk(start) for name in names)
        for candidate in candidates:
            if candidate.is_symlink() or not candidate.is_file():
                continue
            if candidate.stat().st_size in wanted and sha256(candidate) in wanted[candidate.stat().st_size]:
                found.append(candidate.relative_to(root).as_posix())
    return sorted(set(found))


def fetcher():
    """download(url, target, limit) of ../fetch_inputs.py: None or an error string."""
    spec = importlib.util.spec_from_file_location('takx_fetch_inputs', PROTO / 'fetch_inputs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.download


def matches(path, item):
    return os.path.getsize(path) == item['bytes'] and sha256(path) == item['sha256']


def complete(root, arch, listed=None, download=None):
    """Lines saying what was done for each listed file of arch; Refusal when one cannot be provided."""
    listed = entries() if listed is None else listed
    if arch not in listed:
        raise Refusal('unknown architecture %r (%s only)' % (arch, ' and '.join(ARCHES)))
    lines = []
    for item in listed[arch]:
        target = Path(root) / item['path']
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise Refusal('%s is not a regular file' % target)
        if target.is_file():
            if not matches(target, item):
                raise Refusal('%s is present but is not the listed file (SHA-256 %s expected)' % (target, item['sha256']))
            lines.append('%s: present, SHA-256 as listed' % item['path'])
            continue
        if not target.parent.is_dir():
            raise Refusal('%s does not exist; the inputs were not restored below %s' % (target.parent, root))
        download = download or fetcher()
        with tempfile.TemporaryDirectory(prefix='.not-stored-', dir=target.parent) as scratch:
            partial = Path(scratch) / 'download'
            error = download(item['url'], partial, item['bytes'])
            if error:
                raise Refusal('%s could not be downloaded from %s: %s' % (item['path'], item['url'], error))
            if not matches(partial, item):
                raise Refusal('the download of %s is not the listed file (SHA-256 %s expected, %s received)' % (
                    item['url'], item['sha256'], sha256(partial)))
            os.chmod(partial, 0o600)
            os.replace(partial, target)
        lines.append('%s: downloaded from its public source (%s), SHA-256 as listed' % (item['path'], item['url']))
    return lines


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] == ['check'] and len(argv) == 1:
            problems = check()
            for problem in problems:
                print('not_stored.py: ' + problem, file=sys.stderr)
            if not problems:
                print('inputs-not-stored.json is consistent with the architecture pins')
            return 1 if problems else 0
        if argv[:1] == ['scan'] and len(argv) >= 2:
            for path in scan(argv[1], argv[2:]):
                print(path)
            return 0
        if argv[:1] == ['complete'] and len(argv) == 3:
            for line in complete(argv[1], argv[2]):
                print('not_stored.py: ' + line, file=sys.stderr)
            return 0
    except Refusal as refusal:
        print('not_stored.py: %s' % refusal, file=sys.stderr)
        return 1
    print('usage: not_stored.py check | scan ROOT [MEMBER...] | complete ROOT ARCH', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
