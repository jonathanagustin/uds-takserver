#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Fetch and verify every public input of the TAKX 5.7 desktop prototype.

Nothing here edits a pin. Each input is checked against the digest in the lock
of the recipe that consumes it; a mismatch is recorded as verified=false and the
script exits non-zero. Digest-named files land in <root>/cache, where the
existing recipes look them up; per-recipe name views are hard links to them.

Layout under the work root (default /var/lib/takx-proto):
  cache/<sha256>                       flat cache used by native-deps `acquire`
                                       and takx/source_inputs.py
  cache-cesium/<port>.tar.gz           takx/native-cesium/acquire.py --cache
  cache-gis-java/<sha256>.tar.gz       GIS_JAVA_CACHE_DIR
  cache-commoncommo-tools/metadata     native-commoncommo --tool-metadata
  cache-commoncommo-tools/root         native-commoncommo --tools-root
  inputs/<recipe>/<file name>          by-name views (ui-java --sources-dir,
                                       kernel-java --classpath-dir, archives
                                       that recipes take by path, ...)
  jars/client-compiler/<root>/<path>   public JARs of the client-compiler lock
  jars/runtime-extras/                 runtime-only JARs named by TAKX gradle
  src/atak-civ                         writable clone of the pinned submodule
  src/atak-civ-lfs/<path>              real bytes of its Git LFS files
  src/takx57                           official TAKX 5.7 checkout (LFS skipped)
  src/WorldWindJava                    NASA WorldWindJava at the locked revision
  status/fetch.json                    one record per input

Architecture (arch.sh; TAKX_PROTO_ARCH or the machine): the native classifier
JARs of the runtime extras and the Debian package index of the CommonCommo
tools are per-architecture files. On arm64 the classifier JARs are also
checked against their own pins in inputs.arm64.lock.json; the Debian pins of
that architecture come from the lock of the patched native-commoncommo copy
this script then runs from (arch_tree.py).
"""
import argparse
import base64
import concurrent.futures
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zlib

HERE = Path(__file__).resolve().parent
TAKX = HERE.parent
REPO = TAKX.parent
TAKX_REPOSITORY = 'https://github.com/TAK-Product-Center/TAKX.git'
TAKX_REVISION = '0b59a9bb2d0e90dc35c9edfff1c2056daab2b713'
LFS_MEDIA = 'https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/'
MAVEN = 'https://repo1.maven.org/maven2/'
JOGAMP = 'https://jogamp.org/deployment/maven/'
# Dated copy of the Debian archive that still serves the pinned trixie 13.7
# InRelease after a point release replaced it on deb.debian.org. It is only
# another location: every file from it is checked against the same pins.
DEBIAN_ARCHIVE = 'https://deb.debian.org/debian'
DEBIAN_SNAPSHOT = 'https://snapshot.debian.org/archive/debian/20260912T143000Z'
# Recipes that are not on the 5.7 prototype path. Their inputs are still tried,
# but a failure there is reported without failing the stage.
OPTIONAL = {'compat', 'sqlite-jdbc', 'registry-cache', 'geos-cache', 'contour-elevation', 'contour-grid',
            'contour-style', 'client58-public-api', 'mission-package', 'cot-conformance', 'elevation-download-compat'}
# Tracked in the repository already; the Cesium recipe reads them from its own directory.
SKIP_LOCKS = {'native-cesium/metadata.lock.json'}
# Maven coordinates read from a downloaded POM become file names; accept plain names only.
SAFE_COORDINATE = re.compile(r'[A-Za-z0-9_][A-Za-z0-9_.+-]*')
LOCK = threading.Lock()
RECORDS = []


def arch_names():
    """The names of this architecture from arch.sh, the one table of the prototype."""
    return json.loads(subprocess.check_output(['bash', str(HERE / 'arch.sh'), 'json'], timeout=30))


def runtime_native_pins(arch):
    """{'group:artifact:version:classifier': sha256} of the architecture's own pins; amd64 has none."""
    path = HERE / f'inputs.{arch}.lock.json'
    if not path.is_file():
        return {}
    return {name: entry['sha256'] for name, entry in json.loads(path.read_text())['runtime_natives']['jars'].items()}


def log(*parts):
    with LOCK:
        print(time.strftime('%H:%M:%S'), *parts, flush=True)


def digest(path, algorithm='sha256'):
    value = hashlib.new(algorithm)
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def record(**fields):
    fields.setdefault('verified', False)
    with LOCK:
        RECORDS.append(fields)
    if not fields['verified']:
        log('NOT VERIFIED', fields.get('name'), fields.get('note', ''))
    return fields


def run(argv, cwd=None, timeout=600, env=None, stdout=subprocess.PIPE):
    return subprocess.run(list(map(str, argv)), cwd=cwd, timeout=timeout, env=env, check=True,
                          stdout=stdout, stderr=subprocess.PIPE)


def git_env():
    env = dict(os.environ)
    env.update(GIT_LFS_SKIP_SMUDGE='1', GIT_TERMINAL_PROMPT='0', GIT_CONFIG_GLOBAL='/dev/null',
               GIT_CONFIG_SYSTEM='/dev/null', LC_ALL='C', TZ='UTC')
    return env


def download(url, target, limit=None, attempts=4):
    """Stream url into target (temporary name, then rename). Returns None or an error string."""
    target.parent.mkdir(parents=True, exist_ok=True)
    error = None
    for attempt in range(attempts):
        descriptor, name = tempfile.mkstemp(prefix='.partial-', dir=target.parent)
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'takx-desktop-prototype-fetch/1'})
            with os.fdopen(descriptor, 'wb') as stream, urllib.request.urlopen(request, timeout=60) as response:
                count = 0
                while block := response.read(1 << 20):
                    count += len(block)
                    if limit is not None and count > limit:
                        raise ValueError('download exceeds locked size')
                    stream.write(block)
            os.replace(name, target)
            return None
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as failure:
            error = f'{type(failure).__name__}: {failure}'
            if isinstance(failure, urllib.error.HTTPError) and failure.code in (401, 403, 404, 410):
                break
            time.sleep(3 * (attempt + 1))
        finally:
            if os.path.exists(name):
                os.unlink(name)
    return error


def link(source, target):
    """Hard link (regular file, so recipes that refuse symlinks accept it)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        if target.is_file() and not target.is_symlink() and os.path.samefile(source, target):
            return
        target.unlink()
    try:
        os.link(source, target)
    except OSError:
        shutil.copyfile(source, target)


# --------------------------------------------------------------------------- git archive

GITHUB_ARCHIVE = re.compile(r'https://github\.com/([^/]+)/([^/]+)/archive/(?:refs/tags/)?(.+)\.tar\.gz$')
GITHUB_CODELOAD = re.compile(r'https://codeload\.github\.com/([^/]+)/([^/]+)/tar\.gz/(?:refs/tags/)?(.+)$')


def github_archive_parts(url):
    match = GITHUB_ARCHIVE.match(url) or GITHUB_CODELOAD.match(url)
    return match.groups() if match else None


def git_fetch(work, repository, reference):
    env = git_env()
    run(['git', 'init', '-q', '--bare', work], env=env)
    candidates = [reference] if re.fullmatch(r'[0-9a-f]{40}', reference) else ['refs/tags/' + reference, 'refs/heads/' + reference]
    failure = None
    for candidate in candidates:
        for attempt in range(3):
            try:
                run(['git', '-C', work, 'fetch', '-q', '--depth', '1', '--no-tags', repository, candidate], env=env, timeout=900)
                return run(['git', '-C', work, 'rev-parse', 'FETCH_HEAD^{commit}'], env=env).stdout.decode().strip()
            except subprocess.CalledProcessError as error:
                failure = error.stderr.decode(errors='replace').strip()[-300:]
                if "couldn't find remote ref" in failure:
                    break
                time.sleep(5 * (attempt + 1))
    raise RuntimeError('git fetch failed: ' + str(failure))


SMUDGE = r"""import re, shutil, sys
data = sys.stdin.buffer.read()
match = re.search(rb'oid sha256:([0-9a-f]{64})\nsize (\d+)', data)
if data.startswith(b'version https://git-lfs.github.com/spec/v1') and match:
    with open(sys.argv[1] + '/' + match.group(1).decode(), 'rb') as stream:
        shutil.copyfileobj(stream, sys.stdout.buffer)
else:
    sys.stdout.buffer.write(data)
"""


def lfs_objects(work, owner, repository, commit, store):
    """Download the Git LFS objects a commit's tree points at; each is checked against its oid."""
    listing = run(['git', '-C', work, 'ls-tree', '-r', '-l', '-z', commit], env=git_env()).stdout.decode().split('\0')
    count = 0
    for line in listing:
        if not line:
            continue
        meta, path = line.split('\t', 1)
        mode, kind, blob, size = meta.split()
        if kind != 'blob' or int(size) > 300:
            continue
        data = run(['git', '-C', work, 'cat-file', 'blob', blob], env=git_env()).stdout
        match = re.search(rb'oid sha256:([0-9a-f]{64})\nsize (\d+)', data)
        if not (data.startswith(b'version https://git-lfs.github.com/spec/v1') and match):
            continue
        oid, length = match.group(1).decode(), int(match.group(2))
        target = store / oid
        url = f'https://media.githubusercontent.com/media/{owner}/{repository}/{commit}/{urllib.parse.quote(path)}'
        error = download(url, target, limit=length)
        if error or digest(target) != oid:
            raise RuntimeError(f'Git LFS object for {path} unavailable or differs from its pointer: {error}')
        count += 1
    return count


def git_archive(owner, repository, reference, prefix, target, include_lfs=False):
    """Rebuild the archive GitHub serves for owner/repository at reference.

    `git archive --prefix=<root>/ <commit> | gzip -n` is how GitHub produces these
    files, so the result can be compared with a pinned digest. Repositories with
    the "include Git LFS objects in archives" setting get the real object in
    place of each pointer; include_lfs reproduces that with a smudge filter that
    only reads objects already verified against their pointer oid.
    Returns (commit, number of LFS objects included).
    """
    with tempfile.TemporaryDirectory(prefix='git-archive-', dir=target.parent) as work:
        commit = git_fetch(work, f'https://github.com/{owner}/{repository}.git', reference)
        options, count = [], 0
        if include_lfs:
            store = Path(work) / 'lfs-store'
            store.mkdir()
            count = lfs_objects(work, owner, repository, commit, store)
            helper = Path(work) / 'lfs-smudge.py'
            helper.write_text(SMUDGE)
            options = ['-c', f'filter.lfs.smudge={sys.executable} -I {helper} {store}', '-c', 'filter.lfs.required=true']
        tar = Path(work) / 'archive.tar'
        with tar.open('wb') as stream:
            run(['git', '-C', work, *options, 'archive', '--format=tar', '--prefix=' + prefix, commit], env=git_env(),
                stdout=stream, timeout=1800)
        with tar.open('rb') as source, open(str(target) + '.partial', 'wb') as output:
            subprocess.run(['gzip', '-n'], stdin=source, stdout=output, check=True, timeout=1800)
        os.replace(str(target) + '.partial', target)
    return commit, count


def default_prefix(repository, reference):
    name = reference
    if not re.fullmatch(r'[0-9a-f]{40}', reference) and re.match(r'v\d', reference):
        name = reference[1:]
    return f'{repository}-{name}/'


# --------------------------------------------------------------------------- lock walk

def lock_files():
    paths = sorted(set(TAKX.glob('*.lock.json')) | set(TAKX.glob('*/*.lock.json')) | set(TAKX.glob('*/*/*.lock.json')))
    return [p for p in paths if 'desktop-prototype' not in p.parts and p.relative_to(TAKX).as_posix() not in SKIP_LOCKS]


def entry_name(key, entry, url):
    if isinstance(entry.get('file'), str):
        return Path(entry['file']).name
    for candidate in (key, entry.get('name')):
        if isinstance(candidate, str) and re.search(r'\.(jar|tar\.gz|tgz|tar\.xz|tar\.bz2|zip|whl|deb|h|hpp|java|c|cc|cpp|txt|md|xsd|json)$', candidate):
            return Path(candidate).name
    parts = github_archive_parts(url)
    if parts:
        return f'{parts[1]}-{parts[2].replace("/", "_")}.tar.gz'
    return Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name or hashlib.sha256(url.encode()).hexdigest()


def collect_lock_entries():
    """Every {url, sha256} object in every recipe lock, de-duplicated by digest."""
    found = {}
    for path in lock_files():
        relative = path.relative_to(TAKX)
        recipe = relative.parts[0] if len(relative.parts) > 1 else 'commoncommo'
        data = json.loads(path.read_text())

        def walk(node, key):
            if isinstance(node, dict):
                url, sha = node.get('url'), node.get('sha256')
                if (isinstance(url, str) and url.startswith('https://') and isinstance(sha, str)
                        and re.fullmatch(r'[0-9a-f]{64}', sha)):
                    if not (recipe == 'native-cesium' and 'raw.githubusercontent.com' in url):
                        item = found.setdefault(sha, {'sha256': sha, 'url': url, 'bytes': node.get('bytes'), 'consumers': []})
                        if isinstance(node.get('bytes'), int):
                            item['bytes'] = node['bytes']
                        item['consumers'].append({'recipe': recipe, 'lock': 'takx/' + relative.as_posix(),
                                                  'name': entry_name(key, node, url), 'prefix': node.get('prefix')})
                for child_key, child in node.items():
                    walk(child, child_key)
            elif isinstance(node, list):
                for child in node:
                    walk(child, key)
        walk(data, None)
    return found


def method_of(url):
    if url.startswith('https://media.githubusercontent.com/media/'):
        return 'lfs'
    if github_archive_parts(url):
        return 'git-archive'
    return 'direct'


def fetch_lock_entry(item, cache, pointers):
    sha, url, size = item['sha256'], item['url'], item['bytes']
    target = cache / sha
    method = method_of(url)
    note = ''
    required = any(c['recipe'] not in OPTIONAL for c in item['consumers'])
    extra = {}

    def good():
        return (target.is_file() and not target.is_symlink() and digest(target) == sha
                and (not isinstance(size, int) or target.stat().st_size == size))

    cached = good()
    if not cached:
        candidate = cache / ('.candidate-' + sha)
        error = None
        if method == 'git-archive':
            owner, repository, reference = github_archive_parts(url)
            prefix = next((c['prefix'] for c in item['consumers'] if c.get('prefix')), None) or default_prefix(repository, reference)
            try:
                extra['commit'], _ = git_archive(owner, repository, reference, prefix, candidate)
                extra['prefix'] = prefix
            except (RuntimeError, subprocess.SubprocessError, OSError) as failure:
                error = str(failure)
        else:
            gitiles = url.startswith('https://android.googlesource.com/') and url.endswith('?format=TEXT')
            error = download(url, candidate, limit=None if gitiles else size if isinstance(size, int) else None)
            if error is None and gitiles:
                # Gitiles serves file content base64-encoded; the lock pins the decoded file.
                try:
                    candidate.write_bytes(base64.b64decode(candidate.read_bytes(), validate=True))
                    extra['transport'] = 'gitiles base64, decoded before verification'
                except ValueError as failure:
                    error = f'gitiles response is not base64: {failure}'
        if error is None:
            actual = digest(candidate)
            if actual == sha and (not isinstance(size, int) or candidate.stat().st_size == size):
                os.replace(candidate, target)
            else:
                note = f'digest or size differs from lock (bytes {candidate.stat().st_size}, expected {size})'
                extra['actual_sha256'] = actual
                rejected = cache.parent / 'rejected'
                rejected.mkdir(exist_ok=True)
                os.replace(candidate, rejected / (sha + '.got-' + actual[:16]))
        else:
            note = error
            if candidate.exists():
                candidate.unlink()
    verified = good()
    if method == 'lfs' and verified:
        # Second, independent pin: the Git LFS pointer in the pinned submodule.
        match = re.match(re.escape(LFS_MEDIA) + r'([0-9a-f]{40})/(.+)$', url)
        pointer = pointers.get(urllib.parse.unquote(match.group(2))) if match else None
        if pointer is None:
            extra['lfs_pointer'] = 'no pointer at this path in the checked-out submodule'
        else:
            extra['lfs_pointer_oid'] = pointer['oid']
            extra['lfs_pointer_matches'] = pointer['oid'] == sha and pointer['size'] == target.stat().st_size
            if not extra['lfs_pointer_matches']:
                verified, note = False, 'lock digest differs from the Git LFS pointer oid'
    for consumer in item['consumers']:
        if verified:
            link(target, cache.parent / 'inputs' / consumer['recipe'] / consumer['name'])
    record(kind='lock-entry', name=item['consumers'][0]['name'], source=url, method=method,
           expected_sha256=sha, actual_sha256=extra.pop('actual_sha256', sha if verified else None), bytes=size,
           verified=verified, required=required, cached=cached, path=str(target) if verified else None,
           consumers=[{k: v for k, v in c.items() if k != 'prefix'} for c in item['consumers']], note=note, **extra)


# --------------------------------------------------------------------------- Git LFS

def lfs_pointers(source):
    """Pointer files of the checked-out atak-civ submodule: path -> {oid, size}."""
    listing = run(['git', '-C', source, 'ls-files', '-z'], env=git_env()).stdout.decode().split('\0')
    pointers = {}
    for name in listing:
        path = source / name
        try:
            if not name or path.is_symlink() or not path.is_file() or path.stat().st_size > 300:
                continue
            text = path.read_bytes()
        except OSError:
            continue
        if text.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
            match = re.search(rb'oid sha256:([0-9a-f]{64})\nsize (\d+)\n', text)
            if match:
                pointers[name] = {'oid': match.group(1).decode(), 'size': int(match.group(2))}
    return pointers


def fetch_lfs(name, pointer, revision, cache, tree, pinned):
    target = cache / pointer['oid']
    url = LFS_MEDIA + revision + '/' + urllib.parse.quote(name)

    def good():
        return target.is_file() and target.stat().st_size == pointer['size'] and digest(target) == pointer['oid']

    cached = good()
    note = ''
    actual = None
    if not cached:
        candidate = cache / ('.candidate-lfs-' + pointer['oid'])
        error = download(url, candidate, limit=pointer['size'])
        if error is None:
            actual = digest(candidate)
            if actual == pointer['oid']:
                os.replace(candidate, target)
            else:
                note = 'bytes differ from Git LFS pointer'
                candidate.unlink()
        else:
            note = error
    verified = good()
    if verified:
        link(target, tree / name)
    record(kind='atak-civ-lfs', name=name, source=url, method='lfs', expected_sha256=pointer['oid'],
           actual_sha256=pointer['oid'] if verified else actual, bytes=pointer['size'], verified=verified,
           required=pointer['oid'] in pinned, cached=cached, path=str(tree / name) if verified else None,
           pinned_by_recipe_lock=pointer['oid'] in pinned, note=note)


# --------------------------------------------------------------------------- Cesium

def fetch_cesium(root):
    lock = json.loads((TAKX / 'native-cesium/inputs.lock.json').read_text())
    cache = root / 'cache-cesium'
    cache.mkdir(exist_ok=True)
    entries = [{'port': 'cesium', **lock['cesium']}] + [e for e in lock['ports'] if e['selected_action'] == 'acquire_source']
    tree = {e['path']: e for e in json.loads((TAKX / 'native-cesium/cesium-tree.json').read_text()) if e['type'] == 'blob'}

    def one(entry):
        port, url, expected = entry['port'], entry['source_url'], entry.get('source_sha512')
        target = cache / (port + '.tar.gz')
        owner, repository, reference = github_archive_parts(url)
        prefix = default_prefix(repository, reference)
        note, commit, cached, lfs_count = '', None, target.is_file(), 0
        staging = target
        if not cached:
            staging = root / 'tmp' / ('cesium-' + port + '.tar.gz')
            staging.parent.mkdir(exist_ok=True)
            try:
                commit, _ = git_archive(owner, repository, reference, prefix, staging)
                if expected and digest(staging, 'sha512') != expected:
                    # Second and last attempt: the form GitHub serves when the repository
                    # includes Git LFS objects in its archives. Still judged by the pin alone.
                    plain = digest(staging, 'sha512')
                    commit, lfs_count = git_archive(owner, repository, reference, prefix, staging, include_lfs=True)
                    if digest(staging, 'sha512') != expected:
                        note = f'neither form matches the pin (without LFS objects: sha512 {plain[:32]}...)'
            except (RuntimeError, subprocess.SubprocessError, OSError) as failure:
                note = str(failure)
        verified, actual, check = False, None, None
        if staging.is_file():
            actual = digest(staging, 'sha512')
            if expected:
                verified, check = actual == expected, 'sha512 pinned in takx/native-cesium/inputs.lock.json'
            else:
                # cesium-native has no archive digest in the lock; the recipe binds it to the
                # pinned Git tree instead. Apply the same rule: every regular file is a pinned blob.
                check = 'every archive file equals its pinned Git blob in takx/native-cesium/cesium-tree.json'
                # Anything that is not a directory or a pinned regular file (links, devices, a second
                # member of the same name, a file outside the archive root) makes the archive unverified.
                seen, unpinned = {}, 0
                try:
                    with tarfile.open(staging) as archive:
                        for member in archive:
                            if member.isdir():
                                continue
                            relative = member.name.split('/', 1)[1] if '/' in member.name else None
                            if not member.isfile() or relative is None or relative in seen:
                                unpinned += 1
                                continue
                            data = archive.extractfile(member).read()
                            seen[relative] = (hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest(), len(data))
                except (tarfile.TarError, zlib.error, EOFError, OSError) as failure:
                    unpinned, note = unpinned + 1, f'archive is not readable: {type(failure).__name__}: {failure}'
                wanted = {path: (item['sha'], item['size']) for path, item in tree.items()}
                verified = seen == wanted and not unpinned
                if not verified:
                    note = note or (f'archive has {len(seen)} files, tree has {len(wanted)} blobs, '
                                    f'{sum(seen.get(k) != v for k, v in wanted.items())} differ or are missing, '
                                    f'{unpinned} members are not pinned regular files')
            if verified and staging != target:
                os.replace(staging, target)
            elif not verified and staging != target:
                rejected = root / 'rejected'
                rejected.mkdir(exist_ok=True)
                os.replace(staging, rejected / ('cesium-' + port + '.tar.gz'))
                note = note or 'reconstructed archive differs from the SHA-512 pin'
        record(kind='cesium-source', name=port + '.tar.gz', source=url, method='git-archive', expected_sha512=expected,
               actual_sha512=actual, actual_sha256=digest(target) if verified else None, verified=verified, required=True,
               cached=cached, path=str(target) if verified else None, commit=commit or entry.get('commit'), prefix=prefix,
               lfs_objects_included=lfs_count, verification=check, note=note)
    return entries, one


# --------------------------------------------------------------------------- git sources

def checkout(target, repository, revision, name, lfs_note=False, required=True):
    env = git_env()
    note = ''
    try:
        if not (target / '.git').exists():
            target.mkdir(parents=True, exist_ok=True)
            run(['git', 'init', '-q', target], env=env)
            run(['git', '-C', target, 'remote', 'add', 'origin', repository], env=env)
        head = subprocess.run(['git', '-C', str(target), 'rev-parse', '-q', '--verify', 'HEAD'], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip()
        if head != revision:
            for attempt in range(3):
                try:
                    run(['git', '-C', target, 'fetch', '-q', '--depth', '1', '--no-tags', 'origin', revision], env=env, timeout=1500)
                    break
                except subprocess.CalledProcessError as error:
                    if attempt == 2:
                        raise RuntimeError(error.stderr.decode(errors='replace')[-300:]) from None
                    time.sleep(10)
            run(['git', '-C', target, '-c', 'advice.detachedHead=false', 'checkout', '-q', '--detach', revision], env=env, timeout=900)
        head = run(['git', '-C', target, 'rev-parse', 'HEAD'], env=env).stdout.decode().strip()
        dirty = run(['git', '-C', target, 'status', '--porcelain', '--untracked-files=all'], env=env).stdout.decode().strip()
    except (RuntimeError, subprocess.SubprocessError, OSError) as failure:
        head, dirty, note = None, '', str(failure)
    extra = {}
    if head == revision and lfs_note:
        pointers = lfs_pointers(target)
        listing = target.parent / (target.name + '.lfs-pointers.tsv')
        listing.write_text(''.join(f'{v["oid"]}\t{v["size"]}\t{k}\n' for k, v in sorted(pointers.items())))
        extra = {'lfs_pointer_files': len(pointers), 'lfs_pointer_bytes': sum(v['size'] for v in pointers.values()),
                 'lfs_pointer_list': str(listing)}
    if dirty:
        note = 'work tree differs from the revision: ' + dirty[:200]
    record(kind='git-source', name=name, source=repository, method='git', expected_revision=revision, actual_revision=head,
           verified=head == revision and not dirty, required=required, path=str(target), note=note, **extra)


# --------------------------------------------------------------------------- JARs

def maven_candidates(file_name):
    """'group--artifact-version.jar' -> possible (url, name) pairs, most likely first."""
    group, rest = file_name[:-4].split('--', 1)
    result = []
    for match in re.finditer(r'-(?=\d)', rest):
        artifact, version = rest[:match.start()], rest[match.end():]
        result.append(f'{MAVEN}{group.replace(".", "/")}/{artifact}/{version}/{artifact}-{version}.jar')
        # classifier form: artifact-version-classifier.jar
        parts = version.split('-')
        for cut in range(1, len(parts)):
            base = '-'.join(parts[:cut])
            result.append(f'{MAVEN}{group.replace(".", "/")}/{artifact}/{base}/{artifact}-{version}.jar')
    return result


def fetch_client_compiler_jars(root, cache):
    lock = json.loads((TAKX / 'client-compiler/inputs.lock.json').read_text())
    references = sorted(k for k in lock['files'] if k.endswith('.jar'))
    base = root / 'jars/client-compiler'

    def one(reference):
        sha = lock['files'][reference]
        family, path = reference.split(':', 1)
        target = base / family / path
        cached = cache / sha
        source, note = None, ''
        if not (cached.is_file() and digest(cached) == sha):
            if path.startswith('maven-public/'):
                candidate = cache / ('.candidate-jar-' + sha)
                for url in maven_candidates(Path(path).name):
                    if download(url, candidate, attempts=2) is None:
                        if digest(candidate) == sha:
                            os.replace(candidate, cached)
                            source = url
                            break
                        note = 'a Maven Central candidate exists but its SHA-256 differs from the lock'
                if candidate.exists():
                    candidate.unlink()
            else:
                note = 'no public URL in any recipe lock: produced by a prototype build stage, not fetched'
        else:
            with LOCK:
                source = next((r['source'] for r in RECORDS if r.get('expected_sha256') == sha and r.get('source')), None)
            if source is None and path.startswith('maven-public/'):
                source = 'Maven Central (already cached)'
        verified = cached.is_file() and digest(cached) == sha
        if verified:
            link(cached, target)
        public = verified or path.startswith('maven-public/')
        record(kind='client-compiler-jar', name=reference, source=source, method='direct' if public else 'not-fetched',
               expected_sha256=sha, actual_sha256=sha if verified else None, verified=verified, required=public,
               built_by_prototype=not public, path=str(target) if verified else None, note=note)
    return references, one


def gradle_versions(takx):
    text = (takx / 'gradle/versions.gradle').read_text()
    return dict(re.findall(r"^\s*(\w+)\s*=\s*'([^']+)'", text, re.M))


def pom_dependencies(repository, group, artifact, version, memo):
    """Compile/runtime, non-optional dependencies of one POM (parents and properties followed)."""
    def load(g, a, v):
        key = (g, a, v)
        if key not in memo:
            url = f'{repository}{g.replace(".", "/")}/{a}/{v}/{a}-{v}.pom'
            try:
                with urllib.request.urlopen(url, timeout=60) as response:
                    memo[key] = ET.fromstring(re.sub(rb'xmlns="[^"]+"', b'', response.read(), count=1))
            except (urllib.error.URLError, http.client.HTTPException, ET.ParseError, OSError):
                memo[key] = None
        return memo[key]

    chain, current = [], (group, artifact, version)
    while current and len(chain) < 8:
        pom = load(*current)
        if pom is None:
            break
        chain.append((current, pom))
        parent = pom.find('parent')
        current = (parent.findtext('groupId'), parent.findtext('artifactId'), parent.findtext('version')) if parent is not None else None
    if not chain:
        return None
    properties = {'project.version': version, 'project.groupId': group, 'pom.version': version}
    if len(chain) > 1:
        properties['project.parent.version'] = chain[1][0][2]
    managed = {}
    for (g, a, v), pom in reversed(chain):
        for item in pom.findall('properties/*'):
            properties[item.tag] = (item.text or '').strip()
        for item in pom.findall('dependencyManagement/dependencies/dependency'):
            managed[(item.findtext('groupId'), item.findtext('artifactId'))] = item.findtext('version')

    def expand(text):
        for _ in range(5):
            text = re.sub(r'\$\{([^}]+)\}', lambda m: properties.get(m.group(1), m.group(0)), text or '')
        return text

    result = []
    for item in chain[0][1].findall('dependencies/dependency'):
        if (item.findtext('scope') or 'compile') not in ('compile', 'runtime') or item.findtext('optional') == 'true':
            continue
        if item.findtext('type') not in (None, 'jar') or item.findtext('classifier'):
            continue
        g, a = expand(item.findtext('groupId')), expand(item.findtext('artifactId'))
        v = expand(item.findtext('version') or managed.get((item.findtext('groupId'), item.findtext('artifactId'))) or '')
        result.append((g, a, v))
    return result


def fetch_runtime_extras(root, takx, closure_names):
    """Runtime-only artifacts TAKX's Gradle build adds outside the compile closure.

    No recipe lock pins these. Each is checked against the SHA-1 that its Maven
    repository publishes next to it, and its SHA-256 is recorded for later pinning.
    """
    versions = gradle_versions(takx)
    names = arch_names()
    jogamp, bytedeco, pins = names['jogamp'], names['bytedeco'], runtime_native_pins(names['arch'])
    directory = root / 'jars/runtime-extras'
    directory.mkdir(parents=True, exist_ok=True)
    wanted = [  # (repository, group, artifact, version property, classifier), from the cited Gradle files
        (MAVEN, 'com.adobe.xmp', 'xmpcore', 'xmpcoreVersion', None),
        (MAVEN, 'com.github.jai-imageio', 'jai-imageio-core', 'jaiImageIOVersion', None),
        (MAVEN, 'com.koloboke', 'koloboke-impl-jdk8', 'kolobokeVersion', None),
        (MAVEN, 'com.zaxxer', 'HikariCP', 'hikariCPVersion', None),
        (MAVEN, 'commons-logging', 'commons-logging', 'commonsLoggingVersion', None),
        (MAVEN, 'net.sf.joost', 'joost', 'joostVersion', None),
        (MAVEN, 'org.hibernate.common', 'hibernate-commons-annotations', 'hibernateAnnotationsVersion', None),
        (MAVEN, 'org.jboss.logging', 'jboss-logging', 'jbossLoggingVersion', None),
        (MAVEN, 'org.jboss.weld', 'weld-spi', 'weldApiVersion', None),
        (MAVEN, 'org.ow2.asm', 'asm', 'ow2Version', None),
        (MAVEN, 'org.ow2.asm', 'asm-commons', 'ow2Version', None),
        (MAVEN, 'org.hibernate.orm', 'hibernate-hikaricp', 'hibernateCoreVersion', None),
        (MAVEN, 'org.aspectj', 'aspectjweaver', 'aspectjVersion', None),
        (JOGAMP, 'org.jogamp.jocl', 'jocl-main', 'joclVersion', None),
        (JOGAMP, 'org.jogamp.jocl', 'jocl', 'joclVersion', None),
        (JOGAMP, 'org.jogamp.jocl', 'jocl', 'joclVersion', jogamp),
        (JOGAMP, 'org.jogamp.gluegen', 'gluegen-rt', 'joglVersion', None),
        (JOGAMP, 'org.jogamp.gluegen', 'gluegen-rt', 'joglVersion', jogamp),
        (JOGAMP, 'org.jogamp.jogl', 'jogl-all', 'joglVersion', jogamp),
        (MAVEN, 'org.bytedeco', 'ffmpeg', 'ffmpegVersion', None),
        (MAVEN, 'org.bytedeco', 'ffmpeg', 'ffmpegVersion', bytedeco),
    ]
    queue, done, memo = [], set(), {}
    for repository, group, artifact, key, classifier in wanted:
        if key not in versions:
            record(kind='runtime-extra-jar', name=f'{group}:{artifact}', source='gradle/versions.gradle', method='direct',
                   verified=False, required=False, note=f'version property {key} not found in the official TAKX checkout')
            continue
        queue.append((repository, group, artifact, versions[key], classifier, 'declared in TAKX Gradle runtime configuration'))
    while queue:
        repository, group, artifact, version, classifier, why = queue.pop(0)
        if (group, artifact, version, classifier) in done:
            continue
        done.add((group, artifact, version, classifier))
        file_name = f'{artifact}-{version}{"-" + classifier if classifier else ""}.jar'
        url = f'{repository}{group.replace(".", "/")}/{artifact}/{version}/{file_name}'
        target = directory / file_name
        note, published, actual = '', None, None
        try:
            with urllib.request.urlopen(url + '.sha1', timeout=60) as response:
                published = response.read().decode().split()[0].lower()
        except (urllib.error.URLError, http.client.HTTPException, OSError, IndexError) as failure:
            note = f'no published SHA-1: {failure}'
        if published and not (target.is_file() and digest(target, 'sha1') == published):
            error = download(url, target)
            if error:
                note = error
        verified = bool(published) and target.is_file() and digest(target, 'sha1') == published
        coordinate = f'{group}:{artifact}:{version}' + (':' + classifier if classifier else '')
        pin = pins.get(coordinate)
        extra = {}
        if pin:  # this architecture's own pin (inputs.<arch>.lock.json)
            extra = {'expected_sha256': pin, 'pinned_by': f'takx/desktop-prototype/inputs.{names["arch"]}.lock.json'}
            if verified and digest(target) != pin:
                verified, note = False, 'differs from the architecture pin'
        if verified:
            actual = digest(target)
        elif target.exists():
            target.unlink()
        in_closure = file_name in closure_names
        record(kind='runtime-extra-jar', name=coordinate,
               source=url, method='direct', expected_sha1=published, actual_sha256=actual, verified=verified, required=False,
               pinned_by_recipe_lock=False, also_in_compile_closure=in_closure, reason=why, path=str(target) if verified else None,
               verification='SHA-1 published by the Maven repository; no project pin exists', note=note, **extra)
        if classifier is None and verified:
            dependencies = pom_dependencies(repository, group, artifact, version, memo)
            for g, a, v in dependencies or []:
                if not v or '$' in v or '[' in v:
                    record(kind='runtime-extra-jar', name=f'{g}:{a}:{v or "?"}', source=f'POM of {group}:{artifact}:{version}',
                           method='direct', verified=False, required=False, note='transitive version not resolvable from the POM chain')
                    continue
                if not all(SAFE_COORDINATE.fullmatch(part) for part in (g, a, v)):
                    record(kind='runtime-extra-jar', name=f'{g}:{a}:{v}', source=f'POM of {group}:{artifact}:{version}',
                           method='direct', verified=False, required=False, note='transitive coordinate is not a plain Maven name; refused')
                    continue
                origin = repository if g.startswith('org.jogamp') else MAVEN
                queue.append((origin, g, a, v, None, f'POM dependency of {group}:{artifact}:{version}'))
                if g == 'org.bytedeco':  # JavaCPP presets load their natives from the classifier JAR
                    queue.append((origin, g, a, v, bytedeco, f'linux natives of {g}:{a}:{v}'))


# --------------------------------------------------------------------------- Debian tools

def fetch_commoncommo_tools(root):
    """Signed trixie metadata plus the ant and dos2unix packages native-commoncommo verifies."""
    tools = json.loads((TAKX / 'native-commoncommo/inputs.lock.json').read_text())['tools']
    metadata = root / 'cache-commoncommo-tools/metadata'
    metadata.mkdir(parents=True, exist_ok=True)
    base = tools['metadata_url'].rsplit('/dists/', 1)[0]
    index = f'dists/trixie/main/binary-{arch_names()["debian"]}'
    # (name, URL, sha256, size, path below a dated snapshot of the same archive)
    items = [('InRelease', tools['metadata_url'], tools['inrelease_sha256'], None, 'dists/trixie/InRelease'),
             ('Packages.xz', f'{base}/{index}/by-hash/SHA256/{tools["packages_sha256"]}',
              tools['packages_sha256'], tools['packages_bytes'], f'{index}/Packages.xz')]
    for package in tools['packages']:
        items.append((Path(package['Filename']).name, f'{base}/{package["Filename"]}', package['SHA256'], int(package['Size']),
                      package['Filename']))
    complete = True
    for name, url, sha, size, path in items:
        target = metadata / name
        note = ''
        if not (target.is_file() and digest(target) == sha):
            candidate = metadata / ('.candidate-' + name)
            error = download(url, candidate)
            if error is None and digest(candidate) == sha:
                os.replace(candidate, target)
            else:
                note = error or ('live file differs from the pin (a Debian point release replaces InRelease; '
                                 'the pinned bytes then only exist on snapshot.debian.org)')
                if candidate.exists():
                    candidate.unlink()
                if base == DEBIAN_ARCHIVE:
                    # The pin is unchanged; only the location differs.
                    snapshot = f'{DEBIAN_SNAPSHOT}/{path}'
                    error = download(snapshot, candidate)
                    if error is None and digest(candidate) == sha:
                        os.replace(candidate, target)
                        url, note = snapshot, f'{note}; the pinned bytes were fetched from the dated snapshot instead'
                    else:
                        note = f'{note}; snapshot {snapshot}: {error or "differs from the pin"}'
                        if candidate.exists():
                            candidate.unlink()
        verified = target.is_file() and digest(target) == sha and (size is None or target.stat().st_size == size)
        complete &= verified
        record(kind='debian-tool-metadata', name=name, source=url, method='direct', expected_sha256=sha,
               actual_sha256=sha if verified else None, verified=verified, required=True, path=str(target) if verified else None,
               consumers=[{'recipe': 'native-commoncommo', 'lock': 'takx/native-commoncommo/inputs.lock.json'}], note=note)
    extracted = root / 'cache-commoncommo-tools/root'
    if complete and shutil.which('dpkg-deb') and not extracted.exists():
        staging = root / 'cache-commoncommo-tools/.root-staging'
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        for package in tools['packages']:
            run(['dpkg-deb', '-x', metadata / Path(package['Filename']).name, staging])
        staging.rename(extracted)


# --------------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--root', type=Path, default=Path(os.environ.get('TAKX_PROTO_ROOT', '/var/lib/takx-proto')))
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--only', action='append', choices=['locks', 'lfs', 'cesium', 'git', 'jars', 'extras', 'debian'],
                        help='limit to some groups (default: all); the status file is only written for a full run')
    args = parser.parse_args()
    root = args.root.resolve()
    if REPO in root.parents or root == REPO:
        raise SystemExit('the work root must be outside the repository')
    groups = set(args.only or ['locks', 'lfs', 'cesium', 'git', 'jars', 'extras', 'debian'])
    started = time.time()
    cache = root / 'cache'
    for name in ('cache', 'src', 'status', 'logs', 'inputs', 'jars', 'tmp'):
        (root / name).mkdir(parents=True, exist_ok=True)
    for stale in list(cache.glob('.candidate-*')) + list(cache.glob('.partial-*')):
        stale.unlink()

    submodule = REPO / 'upstream/atak-civ'
    revision = run(['git', '-C', submodule, 'rev-parse', 'HEAD'], env=git_env()).stdout.decode().strip()
    locked = json.loads((TAKX / 'sources.lock.json').read_text())['atak_source']['revision']
    if revision != locked:
        raise SystemExit(f'upstream/atak-civ is at {revision}, takx/sources.lock.json pins {locked}')
    pointers = lfs_pointers(submodule)
    entries = collect_lock_entries()
    pinned = set(entries) | set(re.findall(r'[0-9a-f]{64}', ''.join(p.read_text() for p in lock_files())))
    log(f'{len(entries)} lock entries, {len(pointers)} LFS pointers at {revision}')

    with concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        futures = []
        if 'locks' in groups:
            futures += [pool.submit(fetch_lock_entry, item, cache, pointers) for item in entries.values()]
        if 'lfs' in groups:
            tree = root / 'src/atak-civ-lfs'
            futures += [pool.submit(fetch_lfs, name, pointer, revision, cache, tree, pinned) for name, pointer in sorted(pointers.items())]
        if 'cesium' in groups:
            cesium_entries, cesium_one = fetch_cesium(root)
            futures += [pool.submit(cesium_one, entry) for entry in cesium_entries]
        if 'debian' in groups:
            futures.append(pool.submit(fetch_commoncommo_tools, root))
        if 'git' in groups:
            worldwind = json.loads((TAKX / 'worldwind-java/inputs.lock.json').read_text())
            futures.append(pool.submit(checkout, root / 'src/takx57', TAKX_REPOSITORY, TAKX_REVISION, 'TAKX 5.7 official source', True))
            futures.append(pool.submit(checkout, root / 'src/WorldWindJava', worldwind['origin'], worldwind['revision'], 'NASA WorldWindJava'))
            futures.append(pool.submit(checkout, root / 'src/atak-civ', 'file://' + str(submodule), revision,
                                       'atak-civ writable clone (LFS pointers, real bytes in src/atak-civ-lfs)'))
        for future in concurrent.futures.as_completed(futures):
            future.result()
        # Second wave: needs the first wave's cache and the TAKX checkout.
        futures = []
        closure_names = set()
        if 'jars' in groups:
            references, jar_one = fetch_client_compiler_jars(root, cache)
            closure_names = {re.sub(r'^.*--', '', Path(r).name) for r in references}
            futures += [pool.submit(jar_one, reference) for reference in references]
        for future in concurrent.futures.as_completed(futures):
            future.result()
    if 'extras' in groups:
        if (root / 'src/takx57/gradle/versions.gradle').is_file():
            fetch_runtime_extras(root, root / 'src/takx57', closure_names)
        else:
            record(kind='runtime-extra-jar', name='runtime extras', source=TAKX_REPOSITORY, method='direct', verified=False,
                   required=False, note='official TAKX checkout missing; versions unknown')
    # gis-java looks its archives up as <sha256>.tar.gz
    gis = json.loads((TAKX / 'gis-java/sources.lock.json').read_text())['archives']
    for entry in gis.values():
        if (cache / entry['sha256']).is_file():
            link(cache / entry['sha256'], root / 'cache-gis-java' / (entry['sha256'] + '.tar.gz'))
    shutil.rmtree(root / 'tmp', ignore_errors=True)

    RECORDS.sort(key=lambda r: (r['kind'], str(r['name'])))
    failed_required = [r for r in RECORDS if not r['verified'] and r.get('required') and not r.get('built_by_prototype')]
    failed_optional = [r for r in RECORDS if not r['verified'] and not r.get('required') and not r.get('built_by_prototype')]
    counts = {}
    for item in RECORDS:
        bucket = counts.setdefault(item['kind'], {'total': 0, 'verified': 0})
        bucket['total'] += 1
        bucket['verified'] += bool(item['verified'])
    full = args.only is None
    status = {
        'stage': 'fetch', 'status': 'ok' if not failed_required else 'failed', 'seconds': round(time.time() - started),
        'outputs': [str(root / n) for n in ('cache', 'cache-cesium', 'cache-gis-java', 'cache-commoncommo-tools', 'inputs',
                                            'jars', 'src/atak-civ', 'src/atak-civ-lfs', 'src/takx57', 'src/WorldWindJava')],
        'notes': (f'{sum(c["verified"] for c in counts.values())} of {len(RECORDS)} records verified; '
                  f'{len(failed_required)} required and {len(failed_optional)} optional inputs unverified; '
                  f'{sum(1 for r in RECORDS if r.get("built_by_prototype"))} client-compiler JARs are build products, not fetched'),
        'counts': counts,
        'failed_required': [r['name'] for r in failed_required],
        'failed_optional': [r['name'] for r in failed_optional],
        'inputs': RECORDS,
    }
    name = 'fetch.json' if full else 'fetch-partial.json'
    temporary = root / 'status' / ('.' + name)
    temporary.write_text(json.dumps(status, indent=1) + '\n')
    temporary.replace(root / 'status' / name)
    log(json.dumps({k: status[k] for k in ('status', 'seconds', 'notes', 'counts', 'failed_required', 'failed_optional')}, indent=1))
    return 0 if not failed_required else 1


if __name__ == '__main__':
    sys.exit(main())
