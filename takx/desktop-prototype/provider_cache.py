#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Input-keyed cache of the desktop-prototype provider outputs.

A provider (P/<name> under the work root) is saved as a single-layer image
<registry>/<name>:<key>. The key is derived from the provider's inputs, never
from its outputs: the recipe files, the invocation template, the locks and
fetched-input digests, the toolchain image, the work root and repository
paths, and the keys of the prerequisite providers (see providers.json). A
changed recipe therefore misses the cache, and an identical rebuild on a
fresh machine finds it.

Usage:
  provider_cache.py key [--explain] [NAME...]   print the key (or its inputs)
  provider_cache.py restore [NAME...]           pull, verify, install P/<name>
  provider_cache.py push [NAME...]              save P/<name> when it is ok
  provider_cache.py status [--remote] [--write-index [FILE]]

Environment:
  TAKX_PROTO_ROOT          work root, default /var/lib/takx-proto
  TAKX_PROTO_CACHE         registry prefix, or 'off' to disable restore/push
  TAKX_PROTO_TOOLCHAIN_ID  toolchain image identity; default: the image id
                           recorded in status/images.json
  TAKX_PROTO_CACHE_TIMEOUT seconds allowed for one docker command (3600)

Exit status: 0 done, 3 cleanly skipped (cache off, not logged in, nothing
cached under this key, provider not ok, or already present and ok), 1 anything
else. A restore that does not exit 0 has changed nothing, so the caller builds
instead; a provider directory that exists is never overwritten.

Layout (light hexagonal): the functions up to 'Adapters' are the pure core
(no I/O); WorkTree and DockerRegistry are the adapters behind two small
ports; ProviderCache is the application service; main() is the CLI.

The digests checked on restore are stored in the same image as the archive,
so they detect corruption and mix-ups, not a hostile registry: whoever may
push to the cache namespace is trusted like whoever may run the build.
"""
import argparse
import ctypes
import fcntl
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = '/var/lib/takx-proto'
DEFAULT_REGISTRY = 'ghcr.io/jonathanagustin/uds-takserver/takx-desktop-proto-cache'
TOOLCHAIN = 'takx-desktop-proto:toolchain'
SCHEMA = 1
DONE, FAILED, SKIPPED = 0, 1, 3
HEX = re.compile(r'\b(?:[0-9a-f]{128}|[0-9a-f]{64}|[0-9a-f]{40})\b')
NAME = re.compile(r'^[a-z0-9][a-z0-9-]*\Z')
KEY = re.compile(r'^[0-9a-f]{64}\Z')
IMAGE_FILES = ('status.json', 'tar.sha256', 'files.json', 'meta.json')


class CacheError(Exception):
    """A refusal: the cache must not be used for this provider now."""


class Skip(Exception):
    """A clean skip: nothing is wrong, there is just nothing to do."""


# ---------------------------------------------------------------------------
# Core: pure functions, no I/O.
# ---------------------------------------------------------------------------
def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def validate_manifest(manifest):
    """Return the manifest when it is well formed; raise CacheError otherwise."""
    if manifest.get('schema') != SCHEMA:
        raise CacheError('unsupported manifest schema %r' % (manifest.get('schema'),))
    recipes, providers = manifest.get('recipes'), manifest.get('providers')
    if not isinstance(recipes, dict) or not isinstance(providers, dict) or not providers:
        raise CacheError('manifest needs recipes and providers')
    if not isinstance(manifest.get('variables'), dict) or not isinstance(manifest.get('recipe_exclude'), list):
        raise CacheError('manifest needs variables and recipe_exclude')
    for path, entry in recipes.items():
        _relative(path)
        for used in entry.get('uses', []):
            _relative(used)
    for name, entry in providers.items():
        if not NAME.match(name):
            raise CacheError('bad provider name %r' % (name,))
        for field, kind in (('lane', str), ('recipe', str), ('prerequisites', list), ('locks', list),
                            ('fetched', list), ('marker', str), ('invocation', dict)):
            if not isinstance(entry.get(field), kind):
                raise CacheError('%s: missing or malformed %s' % (name, field))
        if entry['recipe'] not in recipes:
            raise CacheError('%s: unknown recipe %s' % (name, entry['recipe']))
        _relative(entry['marker'])
        for lock in entry['locks']:
            _relative(lock)
        for dep in entry['prerequisites']:
            if dep not in providers:
                raise CacheError('%s: unknown prerequisite %s' % (name, dep))
        call = entry['invocation']
        if not isinstance(call.get('argv'), list) or not call['argv'] or not isinstance(call.get('env'), list):
            raise CacheError('%s: invocation needs argv and env' % name)
        for selector in entry['fetched']:
            if not isinstance(selector, dict) or len(selector) != 1 or \
                    next(iter(selector)) not in ('consumer', 'kind', 'path'):
                raise CacheError('%s: bad fetched selector %r' % (name, selector))
    order(providers)
    return manifest


def _relative(path):
    parts = path.split('/') if isinstance(path, str) else ['']
    if not path or path.startswith('/') or any(part in ('', '.', '..') for part in parts):
        raise CacheError('not a plain relative path: %r' % (path,))
    return path


def order(providers, names=None):
    """Prerequisites first; `names` limits the result to those and nothing else."""
    done, active, result = set(), set(), []

    def visit(name):
        if name in done:
            return
        if name in active:
            raise CacheError('prerequisite cycle at ' + name)
        if name not in providers:
            raise CacheError('unknown provider ' + name)
        active.add(name)
        for dep in sorted(providers[name]['prerequisites']):
            visit(dep)
        active.discard(name)
        done.add(name)
        result.append(name)

    for name in sorted(providers):
        visit(name)
    if names is None:
        return result
    for name in names:
        if name not in providers:
            raise CacheError('unknown provider ' + name)
    return [name for name in result if name in set(names)]


def recipe_sources(manifest, recipe):
    """Every path specification a recipe reads: itself and the closure of 'uses'."""
    recipes, seen, specs = manifest['recipes'], set(), set()

    def visit(path):
        if path in seen:
            return
        seen.add(path)
        specs.add(path)
        for used in recipes.get(path, {}).get('uses', []):
            visit(used) if used in recipes else specs.add(used)

    visit(recipe)
    return sorted(specs)


def excluded(name, patterns):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def lock_digests(text):
    """The digests and revisions a lock file pins, as a sorted list."""
    return sorted(set(HEX.findall(text)))


def select_fetched(records, selectors, root):
    """Digest view of the status/fetch.json records the selectors name.

    Each selector must match at least one record and every match must be
    verified; anything else is a refusal.
    """
    result = []
    for selector in selectors:
        (how, value), = selector.items()
        if how == 'consumer':
            hits = [r for r in records if any(c.get('recipe') == value for c in r.get('consumers', []))]
        elif how == 'kind':
            hits = [r for r in records if r.get('kind') == value]
        else:
            hits = [r for r in records if r.get('path') == root.rstrip('/') + '/' + value]
        if not hits:
            raise CacheError('no fetched input matches %s=%s' % (how, value))
        for record in hits:
            if record.get('verified') is not True:
                raise CacheError('fetched input %s is not verified' % record.get('name'))
            digests = {field: record[field] for field in sorted(record)
                       if field.startswith(('expected_', 'actual_')) and record[field] is not None}
            if not digests:
                raise CacheError('fetched input %s has no digest' % record.get('name'))
            result.append({'kind': record.get('kind'), 'name': record.get('name'), 'digests': digests})
    unique = {canonical(item): item for item in result}
    return [unique[text] for text in sorted(unique)]


def key_document(manifest, name, facts, prerequisite_keys):
    """The canonical description of everything a provider's output depends on.

    facts: recipe_files {path: sha256}, locks {path: {sha256, digests}},
    fetched [..], toolchain, root, repo.
    """
    entry = manifest['providers'][name]
    for field in ('toolchain', 'root', 'repo'):
        if not facts.get(field):
            raise CacheError('%s: no %s identity' % (name, field))
    if not facts['recipe_files']:
        raise CacheError('%s: no recipe files' % name)
    if sorted(prerequisite_keys) != sorted(entry['prerequisites']):
        raise CacheError('%s: prerequisite keys incomplete' % name)
    for dep, key in prerequisite_keys.items():
        if not KEY.match(key):
            raise CacheError('%s: bad key for prerequisite %s' % (name, dep))
    return {
        'schema': manifest['schema'],
        'provider': name,
        'recipe': entry['recipe'],
        'recipe_files': dict(facts['recipe_files']),
        'invocation_sha256': sha256_bytes(canonical({'invocation': entry['invocation'],
                                                     'variables': manifest['variables']})),
        'marker': entry['marker'],
        'locks': dict(facts['locks']),
        'fetched': list(facts['fetched']),
        'toolchain': facts['toolchain'],
        'root': facts['root'],
        'repo': facts['repo'],
        'prerequisites': dict(prerequisite_keys),
    }


def derive_key(document):
    return sha256_bytes(canonical(document))


def closure(providers, names):
    """The named providers and everything they are built from."""
    seen = set()

    def visit(name):
        if name not in providers:
            raise CacheError('unknown provider ' + name)
        if name not in seen:
            seen.add(name)
            for dep in providers[name]['prerequisites']:
                visit(dep)

    for name in names:
        visit(name)
    return seen


def derive_keys(manifest, facts_of, names=None, known=None):
    """{name: (key, document)} for the named providers (default: all) and their
    prerequisites; facts_of(name) supplies the facts, `known` earlier results."""
    providers = manifest['providers']
    wanted = closure(providers, providers if names is None else names)
    result = dict(known or {})
    for name in order(providers):
        if name not in wanted or name in result:
            continue
        deps = {dep: result[dep][0] for dep in providers[name]['prerequisites']}
        document = key_document(manifest, name, facts_of(name), deps)
        result[name] = (derive_key(document), document)
    return result


def image_ref(registry, name, key):
    if not NAME.match(name) or not KEY.match(key) or not registry or registry == 'off':
        raise CacheError('cannot name an image for %r' % (name,))
    return '%s/%s:%s' % (registry.rstrip('/'), name, key)


def check_members(provider, members):
    """Refuse an archive that could write outside <provider>/ when unpacked.

    members: iterable of (name, kind, linkname), kind in file|dir|symlink|
    hardlink|other. Raises CacheError; returns the number of members.
    """
    inside = provider + '/'
    seen, links, files = set(), set(), set()

    def plain(path, what):
        if not path or path.startswith('/') or '\\' in path or '\0' in path:
            raise CacheError('archive %s is not a plain relative path: %r' % (what, path))
        if any(part in ('', '.', '..') for part in path.split('/')):
            raise CacheError('archive %s has an empty, "." or ".." component: %r' % (what, path))
        if path != provider and not path.startswith(inside):
            raise CacheError('archive %s is outside %s/: %r' % (what, provider, path))

    count = 0
    for name, kind, linkname in members:
        count += 1
        name = name[:-1] if kind == 'dir' and name.endswith('/') else name
        plain(name, 'member')
        if name in seen:
            raise CacheError('archive names %r twice' % (name,))
        parent = posixpath.dirname(name)
        while parent:
            if parent in links or parent in files:
                raise CacheError('archive member %r is below a link or file' % (name,))
            parent = posixpath.dirname(parent)
        seen.add(name)
        if kind == 'dir':
            continue
        if name == provider:
            raise CacheError('archive top-level entry is not a directory')
        if kind == 'file':
            files.add(name)
        elif kind == 'symlink':
            if not linkname or linkname.startswith('/') or '\0' in linkname:
                raise CacheError('symlink %r has an absolute or empty target' % (name,))
            # ".." is only followed lexically while it leads the target: after a
            # name (which may itself be a link) it could climb out unnoticed.
            parts = linkname.split('/')
            while parts and parts[0] == '..':
                parts.pop(0)
            if '..' in parts:
                raise CacheError('symlink %r has ".." after a name in its target' % (name,))
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), linkname))
            if target != provider and not target.startswith(inside):
                raise CacheError('symlink %r leaves the provider directory' % (name,))
            links.add(name)
        elif kind == 'hardlink':
            plain(linkname, 'hard link target')
            if linkname not in files:
                raise CacheError('hard link %r does not name an earlier file' % (name,))
            files.add(name)
        else:
            raise CacheError('archive member %r is a device or other special file' % (name,))
    if provider not in seen:
        raise CacheError('archive has no top-level %s directory' % provider)
    return count


def check_entries(entries):
    """Refuse a file manifest whose links leave the tree or that lists specials."""
    members = [('p', 'dir', '')]
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get('path'), str) \
                or not isinstance(entry.get('target', ''), str):
            raise CacheError('malformed file manifest entry %r' % (entry,))
        kind = {'file': 'file', 'dir': 'dir', 'link': 'symlink'}.get(entry.get('type'), 'other')
        members.append(('p/' + entry['path'], kind, entry.get('target', '')))
    return check_members('p', members)


def compare_entries(expected, actual):
    """Differences between two file manifests (lists of entry dicts); [] if equal."""
    want = {entry['path']: entry for entry in expected}
    have = {entry['path']: entry for entry in actual}
    if len(want) != len(expected) or len(have) != len(actual):
        return ['a file manifest lists a path twice']
    problems = ['missing ' + path for path in sorted(set(want) - set(have))]
    problems += ['unexpected ' + path for path in sorted(set(have) - set(want))]
    problems += ['changed ' + path for path in sorted(set(want) & set(have)) if want[path] != have[path]]
    return problems


def decide_push(enabled, status, marker_present, remote):
    """('push'|'skip'|'present'|'refuse', reason) for one provider.

    remote: present|absent|unauthorized|error (None before it is known).
    """
    if not enabled:
        return 'skip', 'cache is off'
    if status != 'ok':
        return 'skip', 'status is %s, not ok' % status
    if not marker_present:
        return 'refuse', 'status is ok but the completion marker is missing'
    if remote == 'present':
        return 'present', 'already cached under this key'
    if remote == 'unauthorized':
        return 'skip', 'docker is not logged in to the cache registry'
    if remote == 'absent':
        return 'push', 'not cached yet'
    return 'refuse', 'the registry could not be queried'


def decide_restore(enabled, directory_exists, prerequisites_ok, remote):
    """('restore'|'skip'|'refuse', reason) for one provider."""
    if not enabled:
        return 'skip', 'cache is off'
    if directory_exists:
        return 'refuse', 'the provider directory already exists and is never overwritten'
    if not prerequisites_ok:
        return 'skip', 'a prerequisite provider is not ok here'
    if remote == 'present':
        return 'restore', 'cached under this key'
    if remote == 'unauthorized':
        return 'skip', 'docker is not logged in to the cache registry'
    if remote == 'absent':
        return 'skip', 'nothing cached under this key'
    return 'refuse', 'the registry could not be queried'


def check_bundle(name, key, root, meta, status, labels, tar_sha256, recorded_tar_sha256,
                 files_sha256, local_markers):
    """Every identity check of a pulled bundle before it is unpacked."""
    def need(condition, message):
        if not condition:
            raise CacheError('%s: %s' % (name, message))

    need(KEY.match(recorded_tar_sha256 or ''), 'the image records no archive digest')
    need(tar_sha256 == recorded_tar_sha256, 'archive digest differs from the recorded one')
    need(labels.get('dev.uds.takx.tar-sha256') == tar_sha256, 'archive digest differs from the image label')
    need(labels.get('dev.uds.takx.provider') == name, 'image is labelled for another provider')
    need(labels.get('dev.uds.takx.key') == key, 'image is labelled with another key')
    need(isinstance(meta, dict) and meta.get('schema') == SCHEMA, 'unsupported bundle schema')
    need(meta.get('provider') == name and meta.get('key') == key, 'bundle is for another provider or key')
    need(meta.get('root') == root, 'bundle was built under another work root')
    need(meta.get('tar_sha256') == tar_sha256, 'bundle metadata names another archive')
    need(meta.get('files_sha256') == files_sha256, 'file manifest differs from the recorded one')
    need(isinstance(status, dict) and status.get('status') == 'ok' and status.get('stage') == name,
         'bundled status is not an ok status of this provider')
    need(meta.get('prerequisite_markers') == local_markers,
         'it was built against other prerequisite outputs than the ones present here')


def index_text(rows, header):
    lines = ['# ' + line for line in header]
    lines.append('# name\tkey\tref\ttar sha256')
    lines += ['\t'.join(row) for row in rows]
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------------------
# Adapters.
# ---------------------------------------------------------------------------
def sha256_file(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def rename_noreplace(source, target):
    """rename(2) that fails when the target exists (also an empty directory)."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        call = libc.renameat2
    except (OSError, AttributeError):
        call = None
    if call is not None:
        at_fdcwd, noreplace = -100, 1
        if call(at_fdcwd, os.fsencode(source), at_fdcwd, os.fsencode(target), noreplace) == 0:
            return
        error = ctypes.get_errno()
        if error not in (38, 22, 95):  # ENOSYS, EINVAL, EOPNOTSUPP: fall back below
            raise OSError(error, os.strerror(error), str(target))
    if os.path.lexists(target):
        raise FileExistsError(str(target))
    os.rename(source, target)


class WorkTree:
    """Filesystem port: the repository (read) and the work root."""

    def __init__(self, root, repo):
        self.root, self.repo = Path(root), Path(repo)

    # -- repository ---------------------------------------------------------
    def recipe_files(self, specs, exclude):
        result = {}
        for spec in specs:
            path = self.repo / spec
            if any(char in spec for char in '*?['):
                hits = sorted(self.repo.glob(spec))
                if not hits:
                    raise CacheError('no recipe file matches ' + spec)
                for hit in hits:
                    result[hit.relative_to(self.repo).as_posix()] = self._digest(hit)
            elif path.is_dir() and not path.is_symlink():
                found = False
                for directory, names, files in os.walk(path):
                    names[:] = sorted(n for n in names if not excluded(n, exclude))
                    for name in sorted(files) + [n for n in names if (Path(directory) / n).is_symlink()]:
                        if excluded(name, exclude):
                            continue
                        item = Path(directory) / name
                        result[item.relative_to(self.repo).as_posix()] = self._digest(item)
                        found = True
                if not found:
                    raise CacheError('recipe directory %s is empty' % spec)
            elif path.is_file() or path.is_symlink():
                result[spec] = self._digest(path)
            else:
                raise CacheError('recipe path %s does not exist' % spec)
        return dict(sorted(result.items()))

    @staticmethod
    def _digest(path):
        if path.is_symlink():
            return 'link:' + os.readlink(path)
        if not path.is_file():
            raise CacheError('recipe path %s is not a regular file' % path)
        return sha256_file(path)

    def locks(self, paths):
        result = {}
        for rel in paths:
            try:
                data = (self.repo / rel).read_bytes()
            except OSError as error:
                raise CacheError('lock %s cannot be read: %s' % (rel, error))
            result[rel] = {'sha256': sha256_bytes(data),
                           'digests': lock_digests(data.decode('utf-8', 'replace'))}
        return result

    # -- work root metadata ---------------------------------------------------
    def _json(self, rel):
        try:
            with open(self.root / rel, 'rb') as stream:
                return json.loads(stream.read().decode())
        except (OSError, ValueError) as error:
            raise CacheError('%s cannot be read: %s' % (rel, error))

    def fetched_records(self):
        data = self._json('status/fetch.json')
        if data.get('status') != 'ok' or not isinstance(data.get('inputs'), list):
            raise CacheError('status/fetch.json is not an ok fetch record')
        return data['inputs']

    def recorded_toolchain(self):
        data = self._json('status/images.json')
        value = data.get('image_ids', {}).get(TOOLCHAIN) if data.get('status') == 'ok' else None
        if not isinstance(value, str) or not value:
            raise CacheError('status/images.json records no toolchain image id')
        return value

    def status_bytes(self, name):
        try:
            return (self.root / 'status' / (name + '.json')).read_bytes()
        except OSError:
            return None

    def status_of(self, name):
        try:
            return json.loads(self.status_bytes(name).decode()).get('status', 'missing')
        except (AttributeError, ValueError):
            return 'missing'

    def provider_dir(self, name):
        return self.root / 'P' / name

    def has_directory(self, name):
        return os.path.lexists(self.provider_dir(name))

    def marker_sha256(self, name, marker):
        path = self.provider_dir(name)
        if path.is_symlink() or not path.is_dir():
            return None
        path = path / marker
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            return None
        return sha256_file(path)

    # -- trees and archives ---------------------------------------------------
    @staticmethod
    def entries(top):
        """File manifest of a tree: every directory, regular file and symlink."""
        result = []

        def walk(directory, prefix):
            with os.scandir(directory) as scan:
                items = sorted(scan, key=lambda item: item.name)
            for item in items:
                rel = prefix + item.name
                info = item.stat(follow_symlinks=False)
                mode = '%04o' % stat.S_IMODE(info.st_mode)
                if stat.S_ISLNK(info.st_mode):
                    result.append({'path': rel, 'type': 'link', 'target': os.readlink(item.path)})
                elif stat.S_ISDIR(info.st_mode):
                    result.append({'path': rel, 'type': 'dir', 'mode': mode})
                    walk(item.path, rel + '/')
                elif stat.S_ISREG(info.st_mode):
                    result.append({'path': rel, 'type': 'file', 'mode': mode, 'size': info.st_size,
                                   'sha256': sha256_file(item.path)})
                else:
                    result.append({'path': rel, 'type': 'special', 'mode': mode})

        walk(top, '')
        return result

    @staticmethod
    def write_tar(top, name, entries, target):
        with tarfile.open(target, 'w', format=tarfile.PAX_FORMAT) as archive:
            archive.add(top, arcname=name, recursive=False)
            for entry in entries:
                archive.add(os.path.join(top, entry['path']), arcname=name + '/' + entry['path'],
                            recursive=False)
                archive.inodes.clear()  # store every file in full, never as a hard link

    @staticmethod
    def tar_members(path):
        kinds = {tarfile.REGTYPE: 'file', tarfile.AREGTYPE: 'file', tarfile.DIRTYPE: 'dir',
                 tarfile.SYMTYPE: 'symlink', tarfile.LNKTYPE: 'hardlink'}
        with tarfile.open(path, 'r:') as archive:
            return [(member.name, kinds.get(member.type, 'other'), member.linkname) for member in archive]

    @staticmethod
    def tar_entries(path, name):
        """File manifest of an archive read member by member, without unpacking."""
        result = []
        with tarfile.open(path, 'r:') as archive:
            for member in archive:
                if member.name == name:
                    continue
                rel = member.name[len(name) + 1:]
                mode = '%04o' % stat.S_IMODE(member.mode)
                if member.issym():
                    result.append({'path': rel, 'type': 'link', 'target': member.linkname})
                elif member.isdir():
                    result.append({'path': rel, 'type': 'dir', 'mode': mode})
                elif member.isreg():
                    value = hashlib.sha256()
                    stream = archive.extractfile(member)
                    for block in iter(lambda: stream.read(1 << 20), b''):
                        value.update(block)
                    result.append({'path': rel, 'type': 'file', 'mode': mode, 'size': member.size,
                                   'sha256': value.hexdigest()})
                else:
                    result.append({'path': rel, 'type': 'special', 'mode': mode})
        return result

    @staticmethod
    def unpack(path, target):
        with tarfile.open(path, 'r:') as archive:
            try:
                archive.extractall(target, numeric_owner=True, filter='fully_trusted')
            except TypeError:  # Python without extraction filters
                archive.extractall(target, numeric_owner=True)

    def scratch(self, prefix):
        self.root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self.root))

    def install(self, source, name, status_text):
        """Publish an unpacked provider and its status; undo on any failure."""
        (self.root / 'P').mkdir(exist_ok=True)
        (self.root / 'status').mkdir(exist_ok=True)
        target = self.provider_dir(name)
        status = self.root / 'status' / (name + '.json')
        temporary = status.with_name('%s.json.tmp.%d' % (name, os.getpid()))
        rename_noreplace(source, target)
        try:
            temporary.write_text(status_text)
            os.replace(temporary, status)
        except BaseException:
            os.rename(target, source)
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()
            raise

    def record_push(self, row):
        folder = self.root / 'cache-state'
        folder.mkdir(exist_ok=True)
        with open(folder / 'pushed.tsv', 'a+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            stream.seek(0)
            if '\t'.join(row) + '\n' not in stream.readlines():
                stream.write('\t'.join(row) + '\n')

    def pushed(self):
        try:
            text = (self.root / 'cache-state' / 'pushed.tsv').read_text()
        except OSError:
            return {}
        rows = (line.split('\t') for line in text.splitlines())
        return {(row[0], row[1]): row[3] for row in rows if len(row) == 4}


class DockerRegistry:
    """Registry port over the docker CLI. Credentials stay with docker."""

    def __init__(self, timeout=3600, docker='docker'):
        self.timeout, self.docker = timeout, docker

    def _run(self, *args, check=True):
        try:
            done = subprocess.run([self.docker, *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CacheError('docker %s: %s' % (args[0], error))
        if check and done.returncode != 0:
            raise CacheError('docker %s failed: %s' % (args[0], done.stderr.decode('utf-8', 'replace').strip()[-400:]))
        return done

    def available(self):
        return shutil.which(self.docker) is not None

    def image_id(self, image):
        """Local image id, or None when the image is absent."""
        done = self._run('image', 'inspect', '--format', '{{.Id}}', image, check=False)
        return done.stdout.decode().strip() if done.returncode == 0 else None

    def probe(self, ref):
        # The daemon talks plain HTTP to a loopback registry on its own; the
        # CLI-side manifest query needs to be told. Never for any other host.
        loopback = ref.split('/', 1)[0].rsplit(':', 1)[0] in ('localhost', '127.0.0.1', '[::1]')
        done = self._run('manifest', 'inspect', *(['--insecure'] if loopback else []), ref, check=False)
        if done.returncode == 0:
            return 'present'
        text = done.stderr.decode('utf-8', 'replace').lower() + done.stdout.decode('utf-8', 'replace').lower()
        if 'manifest unknown' in text or 'no such manifest' in text or 'name unknown' in text:
            return 'absent'
        if 'unauthorized' in text or 'denied' in text or 'authentication required' in text \
                or 'no basic auth credentials' in text:
            return 'unauthorized'
        return 'error'

    def publish(self, ref, context, files, labels):
        """One layer holding `files` at /, streamed to `docker import`.

        No `docker build`: its context and layer would stay in the build cache,
        twice the size of every provider pushed.
        """
        args = [self.docker, 'import']
        for label, value in sorted(labels.items()):
            args += ['--change', 'LABEL %s=%s' % (json.dumps(label), json.dumps(value))]
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(args + ['-', ref], stdin=subprocess.PIPE,
                                           stdout=subprocess.DEVNULL, stderr=errors)
            except OSError as error:
                raise CacheError('docker import: %s' % error)
            try:
                with tarfile.open(fileobj=process.stdin, mode='w|', format=tarfile.PAX_FORMAT) as layer:
                    for name in files:
                        layer.add(str(Path(context) / name), arcname=name, recursive=False)
                process.stdin.close()
                code = process.wait(self.timeout)
            except (OSError, subprocess.TimeoutExpired) as error:
                process.kill()
                process.wait()
                code = 'interrupted: %s' % error
            if code != 0:
                errors.seek(0)
                raise CacheError('docker import failed (%s): %s'
                                 % (code, errors.read().decode('utf-8', 'replace').strip()[-400:]))
        try:
            self._run('push', '-q', ref)
        finally:
            self._run('rmi', ref, check=False)

    def fetch(self, ref, target, files):
        """Pull `ref`, copy `files` out of it into `target`, return its labels."""
        self._run('pull', '-q', ref)
        try:
            labels = json.loads(self._run('image', 'inspect', '--format', '{{json .Config.Labels}}',
                                          ref).stdout.decode() or 'null') or {}
            container = self._run('create', ref, '/nonexistent').stdout.decode().strip()
            try:
                for name in files:
                    self._run('cp', '%s:/%s' % (container, name), str(Path(target) / name))
            finally:
                self._run('rm', '-f', container, check=False)
        finally:
            self._run('rmi', ref, check=False)
        return labels


# ---------------------------------------------------------------------------
# Application service.
# ---------------------------------------------------------------------------
class ProviderCache:
    def __init__(self, manifest, tree, registry, registry_prefix, toolchain_override='', log=None):
        self.manifest = validate_manifest(manifest)
        self.tree, self.registry = tree, registry
        self.prefix = registry_prefix or DEFAULT_REGISTRY
        self.enabled = self.prefix != 'off'
        self.override = toolchain_override
        self.log = log or (lambda message: print(message, file=sys.stderr))
        self._keys, self._context = {}, None

    # -- keys -----------------------------------------------------------------
    def toolchain(self):
        return self.override or self.tree.recorded_toolchain()

    def keys(self, names=None):
        """{name: (key, document)}; only the named providers and their
        prerequisites are derived, so one provider's missing input does not
        take the cache away from the others."""
        if self._context is None:
            self._context = (self.tree.fetched_records(), self.toolchain(), {})
        records, toolchain, recipes = self._context
        root, repo = str(self.tree.root), str(self.tree.repo)

        def facts(name):
            entry = self.manifest['providers'][name]
            recipe = entry['recipe']
            if recipe not in recipes:  # several providers share one recipe
                recipes[recipe] = self.tree.recipe_files(recipe_sources(self.manifest, recipe),
                                                         self.manifest['recipe_exclude'])
            return {'recipe_files': recipes[recipe], 'locks': self.tree.locks(entry['locks']),
                    'fetched': select_fetched(records, entry['fetched'], root),
                    'toolchain': toolchain, 'root': root, 'repo': repo}

        self._keys = derive_keys(self.manifest, facts, names, self._keys)
        return self._keys

    def key(self, name):
        return self.keys([name])[name][0]

    def explain(self, name):
        return self.keys([name])[name][1]

    def ref(self, name):
        return image_ref(self.prefix, name, self.key(name))

    def _check_toolchain(self):
        """A stale status/images.json must not address the cache."""
        if self.override:
            return
        actual = self.registry.image_id(TOOLCHAIN)
        if actual != self.tree.recorded_toolchain():
            raise CacheError('the local %s image (%s) is not the one recorded in status/images.json'
                             % (TOOLCHAIN, actual or 'absent'))

    def _markers(self, name):
        """{prerequisite: marker sha256}, or None when one is not ok here."""
        result = {}
        providers = self.manifest['providers']
        for dep in providers[name]['prerequisites']:
            digest = self.tree.marker_sha256(dep, providers[dep]['marker'])
            if digest is None or self.tree.status_of(dep) != 'ok':
                return None
            result[dep] = digest
        return result

    # -- push -------------------------------------------------------------------
    def push(self, name):
        entry = self.manifest['providers'].get(name)
        if entry is None:
            raise CacheError('unknown provider ' + name)
        if not self.enabled:
            raise Skip('cache is off')
        status_bytes = self.tree.status_bytes(name)
        state = self.tree.status_of(name)
        marker = self.tree.marker_sha256(name, entry['marker'])
        action, reason = decide_push(True, state, marker is not None, 'absent')
        if action == 'skip':
            raise Skip(reason)
        if action != 'push':
            raise CacheError('%s: %s' % (name, reason))
        if not self.registry.available():
            raise Skip('docker is not available')
        self._check_toolchain()
        key, ref = self.key(name), self.ref(name)
        action, reason = decide_push(True, state, True, self.registry.probe(ref))
        if action == 'present':
            self.log('provider-cache: %s is %s: %s' % (name, reason, ref))
            return ref
        if action == 'skip':
            raise Skip(reason)
        if action != 'push':
            raise CacheError('%s: %s' % (name, reason))
        markers = self._markers(name)
        if markers is None:
            raise CacheError('%s: a prerequisite provider is not ok here, so its outputs cannot be bound' % name)
        top = self.tree.provider_dir(name)
        context = self.tree.scratch('.push-%s.' % name)
        try:
            entries = self.tree.entries(top)
            check_entries(entries)
            tar = context / (name + '.tar')
            self.tree.write_tar(str(top), name, entries, tar)
            check_members(name, self.tree.tar_members(tar))
            problems = compare_entries(entries, self.tree.tar_entries(tar, name))
            if problems:
                raise CacheError('%s changed while it was archived: %s' % (name, ', '.join(problems[:5])))
            if self.tree.status_bytes(name) != status_bytes or \
                    self.tree.marker_sha256(name, entry['marker']) != marker:
                raise CacheError('%s: status or marker changed while it was archived' % name)
            tar_sha = sha256_file(tar)
            files_text = json.dumps({'schema': SCHEMA, 'provider': name, 'entries': entries},
                                    sort_keys=True, separators=(',', ':')) + '\n'
            meta = {'schema': SCHEMA, 'provider': name, 'key': key, 'root': str(self.tree.root),
                    'repo': str(self.tree.repo), 'marker': entry['marker'], 'marker_sha256': marker,
                    'tar_sha256': tar_sha, 'files_sha256': sha256_bytes(files_text.encode()),
                    'prerequisite_markers': markers, 'key_inputs': self.explain(name)}
            (context / 'files.json').write_text(files_text)
            (context / 'meta.json').write_text(json.dumps(meta, sort_keys=True, indent=1) + '\n')
            (context / 'status.json').write_bytes(status_bytes)
            (context / 'tar.sha256').write_text(tar_sha + '\n')
            self.registry.publish(ref, context, [name + '.tar', *IMAGE_FILES], {
                'org.opencontainers.image.source': 'https://github.com/jonathanagustin/uds-takserver',
                'org.opencontainers.image.licenses': 'GPL-3.0-only',
                'dev.uds.takx.provider': name, 'dev.uds.takx.key': key,
                'dev.uds.takx.tar-sha256': tar_sha})
        finally:
            shutil.rmtree(context, ignore_errors=True)
        if self.registry.probe(ref) != 'present':
            raise CacheError('%s: pushed, but the registry does not show %s' % (name, ref))
        self.tree.record_push((name, key, ref, tar_sha))
        self.log('provider-cache: pushed %s (%d entries, tar sha256 %s)' % (ref, len(entries), tar_sha))
        return ref

    # -- restore ----------------------------------------------------------------
    def restore(self, name):
        entry = self.manifest['providers'].get(name)
        if entry is None:
            raise CacheError('unknown provider ' + name)
        if not self.enabled:
            raise Skip('cache is off')
        if self.tree.status_of(name) == 'ok' and self.tree.marker_sha256(name, entry['marker']):
            raise Skip('already present and ok here')
        markers = self._markers(name)
        action, reason = decide_restore(True, self.tree.has_directory(name), markers is not None, 'present')
        if action == 'skip':
            raise Skip(reason)
        if action == 'refuse':
            raise CacheError('%s: %s' % (name, reason))
        if not self.registry.available():
            raise Skip('docker is not available')
        self._check_toolchain()
        key, ref = self.key(name), self.ref(name)
        action, reason = decide_restore(True, False, True, self.registry.probe(ref))
        if action == 'skip':
            raise Skip('%s (%s)' % (reason, ref))
        if action != 'restore':
            raise CacheError('%s: %s' % (name, reason))
        work = self.tree.scratch('.restore-%s.' % name)
        try:
            labels = self.registry.fetch(ref, work, [name + '.tar', *IMAGE_FILES])
            for item in (name + '.tar', *IMAGE_FILES):
                info = os.lstat(work / item)
                if not stat.S_ISREG(info.st_mode):
                    raise CacheError('%s: image file %s is not a regular file' % (name, item))
            tar = work / (name + '.tar')
            files_bytes = (work / 'files.json').read_bytes()
            status_bytes = (work / 'status.json').read_bytes()
            try:
                meta = json.loads((work / 'meta.json').read_text())
                status = json.loads(status_bytes.decode())
                listing = json.loads(files_bytes.decode())
            except ValueError as error:
                raise CacheError('%s: bundle metadata is not JSON: %s' % (name, error))
            check_bundle(name, key, str(self.tree.root), meta, status, labels, sha256_file(tar),
                         (work / 'tar.sha256').read_bytes().decode('ascii', 'replace').strip(), sha256_bytes(files_bytes), markers)
            if not isinstance(listing, dict) or listing.get('provider') != name \
                    or not isinstance(listing.get('entries'), list):
                raise CacheError('%s: malformed file manifest' % name)
            expected = listing['entries']
            check_entries(expected)
            check_members(name, self.tree.tar_members(tar))
            out = work / 'out'
            out.mkdir()
            self.tree.unpack(tar, out)
            if sorted(os.listdir(out)) != [name]:
                raise CacheError('%s: archive unpacked to something else than %s/' % (name, name))
            marker = out / name / entry['marker']
            if marker.is_symlink() or not marker.is_file() or marker.stat().st_size == 0:
                raise CacheError('%s: completion marker %s is missing' % (name, entry['marker']))
            if sha256_file(marker) != meta.get('marker_sha256'):
                raise CacheError('%s: completion marker differs from the recorded one' % name)
            problems = compare_entries(expected, self.tree.entries(out / name))
            if problems:
                raise CacheError('%s: unpacked files differ from the recorded manifest: %s (%d differences)'
                                 % (name, ', '.join(problems[:5]), len(problems)))
            if self._markers(name) != markers:
                raise CacheError('%s: a prerequisite changed during the restore' % name)
            status['restored_from'] = ref
            status['cache_key'] = key
            try:
                self.tree.install(out / name, name, json.dumps(status, indent=1) + '\n')
            except FileExistsError:
                raise CacheError('%s: the provider directory appeared meanwhile; not overwritten' % name)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.log('provider-cache: restored %s from %s (%d entries verified)' % (name, ref, len(expected)))
        return ref

    # -- status -----------------------------------------------------------------
    def rows(self, remote=False):
        pushed = self.tree.pushed()
        result = []
        for name in order(self.manifest['providers']):
            local = self.tree.status_of(name) if self.tree.has_directory(name) else 'absent'
            try:
                key = self.key(name)
            except CacheError as error:
                self.log('provider-cache: %s has no key: %s' % (name, error))
                result.append({'name': name, 'key': '-', 'ref': '-', 'local': local, 'remote': 'no-key',
                               'tar_sha256': '-'})
                continue
            ref = image_ref(self.prefix, name, key) if self.enabled else '-'
            state = (self.registry.probe(ref) if remote and self.enabled
                     else 'pushed' if (name, key) in pushed else 'unknown')
            result.append({'name': name, 'key': key, 'ref': ref, 'local': local, 'remote': state,
                           'tar_sha256': pushed.get((name, key), '-')})
        return result


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------
def build_service(environ=None):
    environ = os.environ if environ is None else environ
    root = environ.get('TAKX_PROTO_ROOT') or DEFAULT_ROOT
    manifest = json.loads((HERE / 'providers.json').read_text())
    timeout = int(environ.get('TAKX_PROTO_CACHE_TIMEOUT') or 3600)
    return ProviderCache(manifest, WorkTree(os.path.abspath(root), HERE.parent.parent),
                         DockerRegistry(timeout), environ.get('TAKX_PROTO_CACHE') or DEFAULT_REGISTRY,
                         environ.get('TAKX_PROTO_TOOLCHAIN_ID') or '')


def main(argv=None, service=None, out=None):
    out = out or sys.stdout
    parser = argparse.ArgumentParser(prog='provider_cache.py', description=__doc__.split('\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    key = commands.add_parser('key', help='print the cache key of each named provider')
    key.add_argument('--explain', action='store_true', help='print the key inputs as JSON')
    key.add_argument('names', nargs='*')
    for command in ('restore', 'push'):
        commands.add_parser(command).add_argument('names', nargs='*')
    status = commands.add_parser('status', help='key, local state and cache state of every provider')
    status.add_argument('--remote', action='store_true', help='ask the registry for each key')
    status.add_argument('--write-index', nargs='?', const=str(HERE / 'cache-index.tsv'), metavar='FILE',
                        help='rewrite the listing of cached providers (default: cache-index.tsv)')
    args = parser.parse_args(argv)
    try:
        service = service or build_service()
        providers = service.manifest['providers']
        if args.command == 'key':
            names = order(providers, args.names or None)
            for name in names:
                if args.explain:
                    print(json.dumps({'key': service.key(name), 'inputs': service.explain(name)},
                                     indent=1, sort_keys=True), file=out)
                elif len(args.names) == 1:
                    print(service.key(name), file=out)
                else:
                    print('%s\t%s' % (name, service.key(name)), file=out)
            return DONE
        if args.command == 'status':
            rows = service.rows(args.remote)
            for row in rows:
                print('\t'.join((row['name'], row['key'], row['local'], row['remote'], row['tar_sha256'])),
                      file=out)
            if args.write_index:
                listed = [(r['name'], r['key'], r['ref'], r['tar_sha256']) for r in rows
                          if r['remote'] in ('present', 'pushed')]
                header = ['Copyright 2026 uds-takserver authors', 'SPDX-License-Identifier: GPL-3.0-only',
                          'Generated by provider_cache.py status --write-index; do not edit.',
                          'Providers cached under their input keys. Restore needs no index: it',
                          'derives the key and pulls <ref>. Keys are specific to:',
                          'work root ' + str(service.tree.root), 'repository ' + str(service.tree.repo),
                          'toolchain ' + service.toolchain()]
                Path(args.write_index).write_text(index_text(listed, header))
            return DONE
    except CacheError as error:
        print('provider-cache: ' + str(error), file=sys.stderr)
        return FAILED
    worst = DONE
    action = service.restore if args.command == 'restore' else service.push
    try:
        names = order(providers, args.names or None)
    except CacheError as error:
        print('provider-cache: ' + str(error), file=sys.stderr)
        return FAILED
    for name in names:
        try:
            action(name)
        except Skip as skip:
            print('provider-cache: %s %s skipped: %s' % (args.command, name, skip), file=sys.stderr)
            worst = max(worst, SKIPPED) if worst != FAILED else FAILED
        except (CacheError, OSError, tarfile.TarError) as error:
            print('provider-cache: %s %s failed: %s' % (args.command, name, error), file=sys.stderr)
            worst = FAILED
    return worst


if __name__ == '__main__':
    sys.exit(main())
