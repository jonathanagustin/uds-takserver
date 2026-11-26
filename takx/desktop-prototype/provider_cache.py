#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Input-keyed cache of the desktop-prototype provider outputs.

A provider (P/<name> under the work root) is saved as an image
<registry>/<name>:<key>. The key is derived from the provider's inputs, never
from its outputs (see providers.json):

  * the recipe files and the helper files they load;
  * the invocation template of the lane: argv, the -e variables of the step,
    the manifest 'variables', and the 'wrapper' block (the variables, mounts
    and working directory run-in-toolchain.sh gives every command);
  * the locks and the digests of the fetched inputs;
  * the toolchain fingerprint and the architecture (below);
  * the work root and repository paths (receipts embed them);
  * the keys of the prerequisite providers.

A changed recipe therefore misses the cache, and an identical rebuild on a
fresh machine, with a rebuilt toolchain image, finds it.

Outside the key, deliberately or because nothing here can see it:

  * the text of the lane scripts and of run-in-toolchain.sh beyond what the
    manifest mirrors (test_provider_cache.py runs every lane step and the
    wrapper against stubs and compares what they really execute with the
    manifest, word for word), and the steps a lane runs around the recipe:
    seeding its source cache, its own acceptance checks, 'verify';
  * what the wrapper adds beside the 'wrapper' block: the CA bundle at
    /ca.crt with SSL_CERT_FILE, CURL_CA_BUNDLE, GIT_SSL_CAINFO, PIP_CERT and
    REQUESTS_CA_BUNDLE, TAKX_JAVA_TRUSTSTORE, --init, and with TAKX_PROTO_ARCH
    set --platform and that variable (the architecture itself is in the key);
  * recipe files named test_* and README*;
  * the bytes of the inputs: the upstream/atak-civ checkout, the source trees
    and archives below the work root are named by what the locks and
    status/fetch.json pin (the recipes compare them with their locks), they
    are not read again here;
  * Debian package revisions below the gated versions (the probe records a
    digest of the package list in status/toolchain.json, outside the
    fingerprint), the host kernel, CPU model and docker version;
  * a non-default lane invocation (TAKX_LANE*_FETCH=0, another gis-java
    directory): the lanes switch the cache off for it instead.

Toolchain fingerprint. The key does not contain an image id, which changes
with every rebuild. It contains the SHA-256 of what the toolchain is:

  * the pinned base image digest ('toolchain.base' in providers.json, the
    ARG BASE of the Dockerfile) and the first layer of the local image, which
    is the first layer of that base;
  * the architecture the image reports (dpkg --print-architecture);
  * the image environment the recipes inherit (JAVA_HOME, PATH, locale, TZ);
  * every gated tool and the JDK with the version the Dockerfile asserts
    ('toolchain.gated': name, command, expected text).

It is computed by a probe: `docker run --rm --network none --entrypoint
/bin/bash takx-desktop-proto:toolchain -c "$(provider_cache.py toolchain
--probe-script)"` prints the architecture, the environment, the first three
output lines of each gated command and a digest of the package list; each
gated command must show its expected version, as in the Dockerfile. The result
is cached in status/toolchain.json together with the id of the image that was
probed. Keys are derived from that record. restore, push and migrate first
compare the local image: the same id is accepted as it is, another id is
probed again, and a fingerprint other than the recorded one is a refusal until
`provider_cache.py toolchain --record` accepts the new toolchain on purpose.
Outputs that were already ok before such a change are not pushed.

Usage:
  provider_cache.py key [--explain] [NAME...]   print the key (or its inputs)
  provider_cache.py restore [--no-verify] [NAME...]
                                                pull, verify, install P/<name>
  provider_cache.py restore NAME --verify-with COMMAND [ARG...]
                                                the same, and COMMAND must
                                                accept the restored tree
  provider_cache.py push [NAME...]              save P/<name> when it is ok
  provider_cache.py status [--remote] [--write-index [FILE]]
  provider_cache.py toolchain [--record|--probe-script]
  provider_cache.py migrate [--from-toolchain-id ID] [NAME...]

Environment:
  TAKX_PROTO_ROOT          work root, default /var/lib/takx-proto
  TAKX_PROTO_CACHE         registry prefix, or 'off' to disable restore/push
  TAKX_PROTO_CACHE_TIMEOUT seconds allowed for one docker command (3600)
  TAKX_PROTO_CACHE_VERIFY  'off' skips the post-restore checks of a provider
  TAKX_PROTO_CACHE_VERIFY_TIMEOUT  seconds allowed for each such check (900)
  TAKX_PROTO_TOOLCHAIN_ID  only for migrate: the toolchain image id that keys
                           of schema 1 were derived from; default: the id
                           recorded in status/images.json. It no longer
                           changes a key.

Exit status: 0 done, 3 cleanly skipped (cache off, not logged in, nothing
cached under this key, provider not ok, or already present and ok), 1 anything
else. A restore that does not exit 0 has changed nothing, so the caller builds
instead; a provider directory that exists is never overwritten.

Post-restore check. A provider whose recipe has a cheap re-check of an
existing output names it in providers.json ('verify': the command its lane
runs after a build). restore runs it in the toolchain image after the tree is
in place and before the status file says ok; a rejected tree is taken out
again and the restore fails, so the lane builds. A lane whose acceptance
check is its own (lane1.sh: the receipt check of sqlcipher, cesium-acq, cesium
and kml) hands it over instead: everything after `--verify-with` is one
command, run on the host as it stands (the lane includes its toolchain
wrapper), at the same point and with the same meaning: exit 0 accepts, exit 1
rejects the entry, anything else means the check could not be run and judges
nothing. `--no-verify` and TAKX_PROTO_CACHE_VERIFY=off skip both. A provider
with neither is trusted on the digests of its bundle alone.

Replacing an entry. An entry under a key is never overwritten, with one
exception. When restore pulls an entry and has to refuse it, it notes the
registry digest of exactly that entry in <root>/cache-state/refused.json. A
later push of the same provider from this work root, after a successful
build, replaces the entry when the tag still names that digest and
  * the entry was refused for what it is (digests, labels, metadata, archive
    members, completion marker, file manifest, or the recipe's own check), or
  * it was refused because it is bound to other prerequisite outputs than the
    local ones, and the local prerequisites are the cached ones (restored
    from the cache under their current keys, or the very outputs this work
    root last pushed there) and are still the ones restore saw. Otherwise the
    entry may be healthy for everyone else and stays.
A refusal that is no judgement of the entry (registry or docker errors, a
full disk, a local race) is not noted. The registry has no compare-and-swap
for tags, so two machines replacing at once is last-writer-wins.

Migration. `migrate` makes bundles saved under schema-1 keys (toolchain image
id in the key) addressable under the current key without uploading their
archive again: it derives the old key from the same local inputs, pulls and
fully verifies the old bundle, and pushes an image that reuses its layer and
adds one small layer with the new meta.json and labels.

Layout (light hexagonal): the functions up to 'Adapters' are the pure core
(no I/O); WorkTree, DockerRegistry and ToolchainRunner are the adapters behind
three small ports, LaneCheck is the adapter for a command a lane brings;
ProviderCache is the application service; main() is the CLI.

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
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = Path(__file__).resolve().parent
DEFAULT_ROOT = '/var/lib/takx-proto'
DEFAULT_REGISTRY = 'ghcr.io/jonathanagustin/uds-takserver/takx-desktop-proto-cache'
TOOLCHAIN = 'takx-desktop-proto:toolchain'
SCHEMA = 2          # manifest, key documents and bundle metadata
LEGACY_SCHEMA = 1   # key documents that named the toolchain by image id
FILES_SCHEMA = 1    # files.json, unchanged since schema 1
TOOLCHAIN_SCHEMA = 1
ARCHES = ('amd64', 'arm64')
DONE, FAILED, SKIPPED = 0, 1, 3
HEX = re.compile(r'\b(?:[0-9a-f]{128}|[0-9a-f]{64}|[0-9a-f]{40})\b')
NAME = re.compile(r'^[a-z0-9][a-z0-9-]*\Z')
KEY = re.compile(r'^[0-9a-f]{64}\Z')
DIGEST = re.compile(r'^sha256:[0-9a-f]{64}\Z')
PINNED = re.compile(r'^[^@\s]+@sha256:[0-9a-f]{64}\Z')
VARIABLE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*\Z')
TOOL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9+._-]*\Z')
PLACEHOLDER = re.compile(r'\{([a-z]+)\}')
IMAGE_FILES = ('status.json', 'tar.sha256', 'files.json', 'meta.json')


class CacheError(Exception):
    """A refusal: the cache must not be used for this provider now."""


class OtherPrerequisites(CacheError):
    """A bundle that is bound to other prerequisite outputs than the local ones."""


class MissingFile(CacheError):
    """A pulled image that lacks one of the files of a bundle."""

    def __init__(self, message, digest=None):
        super().__init__(message)
        self.digest = digest


class Rejected(CacheError):
    """A restored tree that the recipe's own check does not accept."""


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
    _validate_toolchain(manifest.get('toolchain'))
    wrapper = manifest.get('wrapper')
    if not isinstance(wrapper, dict) or sorted(wrapper) != ['env', 'mounts', 'workdir'] \
            or not _strings(wrapper['env']) or not _strings(wrapper['mounts']) \
            or not isinstance(wrapper['workdir'], str):
        raise CacheError('manifest needs a wrapper with env, mounts and workdir')
    known = set(manifest['variables']) | {'root', 'repo'}
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
        check = entry.get('verify')
        if check is not None:
            if not isinstance(check, dict) or not _strings(check.get('argv')) or not check['argv'] \
                    or not _strings(check.get('env')):
                raise CacheError('%s: verify needs argv and env' % name)
            for token in check['argv'] + check['env']:
                if not set(PLACEHOLDER.findall(token)) <= known:
                    raise CacheError('%s: verify names an unknown variable in %r' % (name, token))
    order(providers)
    return manifest


def _strings(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_toolchain(toolchain):
    if not isinstance(toolchain, dict) or not isinstance(toolchain.get('base'), str) \
            or not PINNED.match(toolchain['base']):
        raise CacheError('manifest needs a toolchain with a base image pinned by digest')
    environment, gated = toolchain.get('environment'), toolchain.get('gated')
    if not isinstance(environment, dict) or not all(
            VARIABLE.match(name) and isinstance(value, str) and '\n' not in value and '\t' not in value
            for name, value in environment.items()):
        raise CacheError('toolchain environment must map variable names to one-line values')
    if not isinstance(gated, list) or not gated:
        raise CacheError('toolchain needs its gated tools')
    names = set()
    for gate in gated:
        if not isinstance(gate, dict) or sorted(gate) != ['argv', 'expect', 'name'] \
                or not isinstance(gate['name'], str) or not TOOL.match(gate['name']) \
                or not _strings(gate['argv']) or not gate['argv'] \
                or not isinstance(gate['expect'], str) or not gate['expect'].strip():
            raise CacheError('malformed gated tool %r' % (gate,))
        if gate['name'] in names:
            raise CacheError('gated tool %s is listed twice' % gate['name'])
        names.add(gate['name'])


def _relative(path):
    # '' and '/x' have an empty part too; anything that is not text counts as one.
    parts = path.split('/') if isinstance(path, str) else ['']
    if any(part in ('', '.', '..') for part in parts):
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


def render(token, values):
    """A manifest template with its {names} filled in from `values`."""
    def value(match):
        if match.group(1) not in values:
            raise CacheError('unknown variable {%s} in %r' % (match.group(1), token))
        return values[match.group(1)]

    return PLACEHOLDER.sub(value, token)


def probe_script(toolchain):
    """The bash program that reports what a toolchain image is.

    One line per fact, fields separated by tabs: 'arch', 'env NAME value',
    'tool name <first three output lines>' and 'packages <sha256>'. Like the
    Dockerfile's own assertions it reads standard output and standard error.
    """
    lines = ['set -u', 'printf \'arch\\t%s\\n\' "$(dpkg --print-architecture)"']
    for name in sorted(toolchain['environment']):
        lines.append('printf \'env\\t%%s\\t%%s\\n\' %s "${%s-}"' % (name, name))
    for gate in toolchain['gated']:
        command = ' '.join(shlex.quote(word) for word in gate['argv'])
        lines.append('printf \'tool\\t%%s\\t%%s\\n\' %s "$(%s 2>&1 | head -n 3 | tr \'\\n\\t\' \'  \')"'
                     % (gate['name'], command))
    lines.append('printf \'packages\\t%s\\n\' "$(dpkg-query -W -f \'${Package} ${Version} ${Architecture}\\n\''
                 ' | sha256sum | cut -d\' \' -f1)"')
    return '\n'.join(lines) + '\n'


def parse_probe(text):
    """{'arch', 'environment', 'tools', 'packages'} from the probe's output."""
    facts = {'arch': None, 'environment': {}, 'tools': {}, 'packages': None}
    for line in text.split('\n'):
        fields = line.split('\t')
        if fields[0] == 'arch' and len(fields) == 2:
            facts['arch'] = fields[1]
        elif fields[0] == 'env' and len(fields) == 3:
            facts['environment'][fields[1]] = fields[2]
        elif fields[0] == 'tool' and len(fields) == 3:
            facts['tools'][fields[1]] = fields[2]
        elif fields[0] == 'packages' and len(fields) == 2:
            facts['packages'] = fields[1]
        elif line:
            raise CacheError('unexpected line in the toolchain probe output: %r' % (line[:80],))
    return facts


def expected_toolchain(toolchain, arch, base_layer):
    """The fingerprint document of this manifest's toolchain on `arch`."""
    return {'schema': TOOLCHAIN_SCHEMA, 'arch': arch, 'base': toolchain['base'].rsplit('@', 1)[1],
            'base_layer': base_layer, 'environment': dict(toolchain['environment']),
            'gated': [{'name': gate['name'], 'argv': list(gate['argv']), 'expect': gate['expect']}
                      for gate in toolchain['gated']]}


def toolchain_document(toolchain, observed, base_layer):
    """What a probed image is, for the fingerprint; CacheError when it is not
    the toolchain the manifest describes."""
    if observed['arch'] not in ARCHES:
        raise CacheError('the toolchain image reports architecture %r (amd64 and arm64 only)' % (observed['arch'],))
    if not isinstance(base_layer, str) or not DIGEST.match(base_layer):
        raise CacheError('the toolchain image names no first layer')
    for name, value in sorted(toolchain['environment'].items()):
        if observed['environment'].get(name) != value:
            raise CacheError('the toolchain image sets %s to %r, not to %r'
                             % (name, observed['environment'].get(name), value))
    for gate in toolchain['gated']:
        got = observed['tools'].get(gate['name'])
        if got is None or gate['expect'] not in got:
            raise CacheError('gated tool %s: wanted %r in %r' % (gate['name'], gate['expect'], got))
    return expected_toolchain(toolchain, observed['arch'], base_layer)


def recorded_toolchain(toolchain, record):
    """(fingerprint, arch, image id) of a status/toolchain.json record that
    still describes this manifest's toolchain, else None."""
    if not isinstance(record, dict) or record.get('status') != 'ok':
        return None
    document, image_id = record.get('document'), record.get('image_id')
    if not isinstance(document, dict) or document.get('arch') not in ARCHES \
            or not isinstance(document.get('base_layer'), str) or not DIGEST.match(document['base_layer']) \
            or not isinstance(image_id, str) or not image_id:
        return None
    if document != expected_toolchain(toolchain, document['arch'], document['base_layer']) \
            or record.get('fingerprint') != derive_key(document):
        return None
    return record['fingerprint'], document['arch'], image_id


def key_document(manifest, name, facts, prerequisite_keys):
    """The canonical description of everything a provider's output depends on.

    facts: recipe_files {path: sha256}, locks {path: {sha256, digests}},
    fetched [..], toolchain (fingerprint), arch, root, repo.
    """
    entry = manifest['providers'][name]
    for field in ('toolchain', 'arch', 'root', 'repo'):
        if not facts.get(field):
            raise CacheError('%s: no %s identity' % (name, field))
    if not KEY.match(facts['toolchain']) or facts['arch'] not in ARCHES:
        raise CacheError('%s: the toolchain is not named by a fingerprint and an architecture' % name)
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
                                                     'variables': manifest['variables'],
                                                     'wrapper': manifest['wrapper']})),
        'marker': entry['marker'],
        'locks': dict(facts['locks']),
        'fetched': list(facts['fetched']),
        'toolchain': facts['toolchain'],
        'arch': facts['arch'],
        'root': facts['root'],
        'repo': facts['repo'],
        'prerequisites': dict(prerequisite_keys),
    }


def legacy_key_document(manifest, name, facts, prerequisite_keys):
    """The schema-1 key document: the toolchain is an image id (facts
    'toolchain'), and neither the architecture nor the wrapper is named. Only
    `migrate` derives it, to find what was saved under those keys."""
    entry = manifest['providers'][name]
    if not isinstance(facts.get('toolchain'), str) or not DIGEST.match(facts['toolchain']):
        raise CacheError('%s: a schema-1 key needs the toolchain image id' % name)
    if sorted(prerequisite_keys) != sorted(entry['prerequisites']):
        raise CacheError('%s: prerequisite keys incomplete' % name)
    return {
        'schema': LEGACY_SCHEMA,
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


def derive_keys(manifest, facts_of, names=None, known=None, describe=key_document):
    """{name: (key, document)} for the named providers (default: all) and their
    prerequisites; facts_of(name) supplies the facts, `known` earlier results,
    `describe` the document (key_document or legacy_key_document)."""
    providers = manifest['providers']
    wanted = closure(providers, providers if names is None else names)
    result = dict(known or {})
    for name in order(providers):
        if name not in wanted or name in result:
            continue
        deps = {dep: result[dep][0] for dep in providers[name]['prerequisites']}
        document = describe(manifest, name, facts_of(name), deps)
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


def decide_replace(refusal, key, remote_digest, markers, prerequisites_cached):
    """('replace'|'keep'|'forget', reason) for an entry already cached under `key`.

    refusal: what restore noted when it refused the entry under this
    reference here ({key, digest, kind, reason, markers}), or None.
    remote_digest: the registry digest the reference names now. markers: the
    local prerequisite markers now. 'forget' keeps the entry and drops a note
    that no longer describes it.
    """
    present = 'already cached under this key'
    if not isinstance(refusal, dict) or refusal.get('key') != key \
            or not isinstance(refusal.get('digest'), str) or not DIGEST.match(refusal['digest']):
        return 'keep', present
    if not isinstance(remote_digest, str) or not DIGEST.match(remote_digest):
        return 'keep', present + '; the registry did not name the entry, so the one refused here is not replaced'
    if remote_digest != refusal['digest']:
        return 'forget', present + '; it is not the entry that was refused here'
    if refusal.get('kind') == 'bundle':
        return 'replace', 'restore refused the cached entry (%s)' % refusal.get('reason')
    if refusal.get('kind') == 'prerequisites':
        if refusal.get('markers') != markers:
            return 'forget', present + '; the prerequisites here changed since it was refused'
        if not prerequisites_cached:
            return 'keep', present + '; it is bound to other prerequisite outputs, and the ones here are not ' \
                                     'the cached ones, so it may be usable elsewhere and is not replaced'
        return 'replace', 'the cached entry is bound to other prerequisite outputs than the cached ones'
    return 'keep', present


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
                 files_sha256, local_markers, schema=SCHEMA):
    """Every identity check of a pulled bundle before it is unpacked."""
    def need(condition, message):
        if not condition:
            raise CacheError('%s: %s' % (name, message))

    need(KEY.match(recorded_tar_sha256 or ''), 'the image records no archive digest')
    need(tar_sha256 == recorded_tar_sha256, 'archive digest differs from the recorded one')
    need(labels.get('dev.uds.takx.tar-sha256') == tar_sha256, 'archive digest differs from the image label')
    need(labels.get('dev.uds.takx.provider') == name, 'image is labelled for another provider')
    need(labels.get('dev.uds.takx.key') == key, 'image is labelled with another key')
    need(isinstance(meta, dict) and meta.get('schema') == schema, 'unsupported bundle schema')
    need(meta.get('provider') == name and meta.get('key') == key, 'bundle is for another provider or key')
    need(meta.get('root') == root, 'bundle was built under another work root')
    need(meta.get('tar_sha256') == tar_sha256, 'bundle metadata names another archive')
    need(meta.get('files_sha256') == files_sha256, 'file manifest differs from the recorded one')
    need(isinstance(status, dict) and status.get('status') == 'ok' and status.get('stage') == name,
         'bundled status is not an ok status of this provider')
    if meta.get('prerequisite_markers') != local_markers:
        raise OtherPrerequisites('%s: it was built against other prerequisite outputs than the ones present here'
                                 % name)


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

    def legacy_toolchain(self):
        """The toolchain image id of status/images.json, which schema-1 keys named."""
        data = self._json('status/images.json')
        value = data.get('image_ids', {}).get(TOOLCHAIN) if data.get('status') == 'ok' else None
        if not isinstance(value, str) or not value:
            raise CacheError('status/images.json records no toolchain image id')
        return value

    def toolchain_record(self):
        """status/toolchain.json as a dict, or None when there is no usable one."""
        try:
            data = self._json('status/toolchain.json')
        except CacheError:
            return None
        return data if isinstance(data, dict) else None

    def record_toolchain(self, record):
        self._write_json(self.root / 'status' / 'toolchain.json', record)

    @staticmethod
    def _write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name('%s.tmp.%d' % (path.name, os.getpid()))
        try:
            temporary.write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
            os.replace(temporary, path)
        finally:
            if temporary.is_file() and not temporary.is_symlink():
                temporary.unlink()

    def status_bytes(self, name):
        try:
            return (self.root / 'status' / (name + '.json')).read_bytes()
        except OSError:
            return None

    def status_json(self, name):
        """The status file of a provider as a dict ({} when there is none)."""
        try:
            data = json.loads(self.status_bytes(name).decode())
        except (AttributeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def status_of(self, name):
        try:
            return json.loads(self.status_bytes(name).decode()).get('status', 'missing')
        except (AttributeError, ValueError):
            return 'missing'

    def status_time(self, name):
        """When the status file of a provider was written (None without one)."""
        try:
            return os.stat(self.root / 'status' / (name + '.json')).st_mtime
        except OSError:
            return None

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

    def install(self, source, name, status_text, check=None):
        """Publish an unpacked provider and its status; undo on any failure.

        `check` runs while the tree is in place and the status file does not
        say ok yet; when it raises, the tree is taken out again.
        """
        (self.root / 'P').mkdir(exist_ok=True)
        (self.root / 'status').mkdir(exist_ok=True)
        target = self.provider_dir(name)
        status = self.root / 'status' / (name + '.json')
        temporary = status.with_name('%s.json.tmp.%d' % (name, os.getpid()))
        rename_noreplace(source, target)
        try:
            if check is not None:
                check()
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
        return {(row[0], row[1]): row[3] for row in rows if len(row) in (4, 5)}

    def pushed_markers(self):
        """{reference: completion marker sha256} of the outputs this work root
        last pushed, adopted or replaced there (rows written since schema 2)."""
        try:
            text = (self.root / 'cache-state' / 'pushed.tsv').read_text()
        except OSError:
            return {}
        rows = (line.split('\t') for line in text.splitlines())
        return {row[2]: row[4] for row in rows if len(row) == 5}

    def refusals(self):
        """{reference: note} of the cached entries restore refused here."""
        try:
            data = json.loads((self.root / 'cache-state' / 'refused.json').read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def note_refusal(self, ref, note):
        """Record (or with None forget) the refusal of the entry under `ref`."""
        folder = self.root / 'cache-state'
        folder.mkdir(exist_ok=True)
        handle = os.open(folder, os.O_RDONLY)  # the directory is the lock: no extra file
        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
            notes = self.refusals()
            if note is None:
                notes.pop(ref, None)
            else:
                notes[ref] = note
            if notes or (folder / 'refused.json').exists():
                self._write_json(folder / 'refused.json', notes)
        finally:
            os.close(handle)


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

    def base_layer(self, image):
        """The first layer (diff id) of a local image, or None."""
        done = self._run('image', 'inspect', '--format', '{{json .RootFS.Layers}}', image, check=False)
        try:
            layers = json.loads(done.stdout.decode()) if done.returncode == 0 else None
        except ValueError:
            layers = None
        return layers[0] if isinstance(layers, list) and layers and isinstance(layers[0], str) else None

    def probe_toolchain(self, image, script):
        """Standard output of the fingerprint probe run in `image`, offline."""
        return self._run('run', '--rm', '--network', 'none', '--entrypoint', '/bin/bash', image,
                         '-c', script).stdout.decode('utf-8', 'replace')

    @staticmethod
    def _insecure(ref):
        # The daemon talks plain HTTP to a loopback registry on its own; the
        # CLI-side manifest query needs to be told. Never for any other host.
        loopback = ref.split('/', 1)[0].rsplit(':', 1)[0] in ('localhost', '127.0.0.1', '[::1]')
        return ['--insecure'] if loopback else []

    def digest(self, ref):
        """The digest the registry serves under `ref` now, or None when it
        cannot be told (absent, an image index, or any error)."""
        done = self._run('manifest', 'inspect', '--verbose', *self._insecure(ref), ref, check=False)
        try:
            value = json.loads(done.stdout.decode())['Descriptor']['digest'] if done.returncode == 0 else None
        except (ValueError, KeyError, TypeError):
            value = None
        return value if isinstance(value, str) and DIGEST.match(value) else None

    def probe(self, ref):
        done = self._run('manifest', 'inspect', *self._insecure(ref), ref, check=False)
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
                try:
                    process.stdin.close()
                except OSError:
                    pass  # what was still buffered for a command that is gone
                code = 'interrupted: %s' % error
            if code != 0:
                errors.seek(0)
                raise CacheError('docker import failed (%s): %s'
                                 % (code, errors.read().decode('utf-8', 'replace').strip()[-400:]))
        try:
            self._run('push', '-q', ref)
        finally:
            self._run('rmi', ref, check=False)

    def fetch(self, ref, target, files, keep=False):
        """Pull `ref`, copy `files` out of it into `target`; return its labels
        and the registry digest it was pulled as (None when docker names none).

        A file the image does not hold is a MissingFile carrying that digest.
        With `keep` the image stays for adopt(); the caller then calls forget().
        """
        self._run('pull', '-q', ref)
        try:
            labels = json.loads(self._run('image', 'inspect', '--format', '{{json .Config.Labels}}',
                                          ref).stdout.decode() or 'null') or {}
            digest = self._pulled_digest(ref)
            container = self._run('create', ref, '/nonexistent').stdout.decode().strip()
            try:
                for name in files:
                    done = self._run('cp', '%s:/%s' % (container, name), str(Path(target) / name), check=False)
                    if done.returncode != 0:
                        text = done.stderr.decode('utf-8', 'replace').strip()[-400:]
                        if 'could not find the file' in text.lower() or 'no such file' in text.lower():
                            raise MissingFile('the image holds no /%s' % name, digest)
                        raise CacheError('docker cp failed: %s' % text)
            finally:
                self._run('rm', '-f', container, check=False)
        except BaseException:
            self._run('rmi', ref, check=False)
            raise
        if not keep:
            self._run('rmi', ref, check=False)
        return labels, digest

    def _pulled_digest(self, ref):
        done = self._run('image', 'inspect', '--format', '{{json .RepoDigests}}', ref, check=False)
        try:
            listed = json.loads(done.stdout.decode()) if done.returncode == 0 else []
        except ValueError:
            listed = []
        repository = ref.rsplit(':', 1)[0] + '@'
        found = sorted({item[len(repository):] for item in listed or []
                        if isinstance(item, str) and item.startswith(repository)
                        and DIGEST.match(item[len(repository):])})
        return found[0] if len(found) == 1 else None

    def forget(self, ref):
        self._run('rmi', ref, check=False)

    def adopt(self, source, ref, context, files, labels):
        """Push `ref`: the layers of the local image `source` plus one layer
        that holds `files` (from `context`) at /, with `labels` set.

        The layers of `source` are in the repository already, so only the
        small new layer, the configuration and the manifest are uploaded.
        """
        container = self._run('create', source, '/nonexistent').stdout.decode().strip()
        try:
            for name in files:
                self._run('cp', str(Path(context) / name), '%s:/%s' % (container, name))
            args = ['commit']
            for label, value in sorted(labels.items()):
                args += ['--change', 'LABEL %s=%s' % (json.dumps(label), json.dumps(value))]
            self._run(*args, container, ref)
        finally:
            self._run('rm', '-f', container, check=False)
        try:
            self._run('push', '-q', ref)
        finally:
            self._run('rmi', ref, check=False)


def run_logged(command, log, heading, timeout, name, stopped=None):
    """Exit status of `command`: no input, its own process group, its output
    appended to `log` below `heading`. A command that does not finish in
    `timeout` seconds is killed with everything it started, `stopped` (if
    any) is called, and that is a CacheError, as is one that cannot start."""
    try:
        with open(log, 'ab') as stream:
            stream.write(('\n===== provider-cache restore %s: %s\n' % (name, heading)).encode())
            stream.flush()
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
            try:
                return process.wait(timeout)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, 9)
                except OSError:
                    pass  # it ended in between
                process.wait()
                if stopped is not None:
                    stopped()
                raise CacheError('the check of %s did not finish in %d s' % (name, timeout))
    except OSError as error:
        raise CacheError('the check of %s could not be started: %s' % (name, error))


class ToolchainRunner:
    """Port for the recipe's own re-check: one command in the toolchain image,
    through run-in-toolchain.sh like every lane step."""

    def __init__(self, wrapper, timeout=900, docker='docker'):
        self.wrapper, self.timeout, self.docker = str(wrapper), timeout, docker

    def run(self, name, env, argv, log):
        """Exit status of `argv`; its output is appended to `log`."""
        container = 'takx-verify-%s-%d' % (name, os.getpid())
        command = [self.wrapper, '--name', container]
        for item in env:
            command += ['-e', item]
        command += ['--', *argv]

        def stopped():
            subprocess.run([self.docker, 'rm', '-f', container], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

        return run_logged(command, log, ' '.join(argv), self.timeout, name, stopped)


class LaneCheck:
    """A check the lane brings itself (restore --verify-with): one command on
    the host, word for word. The lane includes its toolchain wrapper, so a
    container of a command that had to be killed is the lane's to remove."""

    def __init__(self, command, timeout=900):
        self.command, self.timeout = [str(word) for word in command], timeout

    def run(self, name, log):
        """Exit status of the command; its output is appended to `log`."""
        heading = 'the lane\'s own check (%s, %d words)' % (self.command[0], len(self.command))
        return run_logged(self.command, log, heading, self.timeout, name)


# ---------------------------------------------------------------------------
# Application service.
# ---------------------------------------------------------------------------
class ProviderCache:
    def __init__(self, manifest, tree, registry, registry_prefix, legacy_toolchain='', log=None, runner=None,
                 verify=True):
        self.manifest = validate_manifest(manifest)
        self.tree, self.registry, self.runner = tree, registry, runner
        self.prefix = registry_prefix or DEFAULT_REGISTRY
        self.enabled = self.prefix != 'off'
        self.legacy_id = legacy_toolchain
        self.verify, self.rerecord, self.lane_check = verify, False, None
        self.log = log or (lambda message: print(message, file=sys.stderr))
        self._keys, self._context, self._legacy = {}, None, {}

    # -- toolchain --------------------------------------------------------------
    def toolchain(self, local=False):
        """(fingerprint, architecture) of the toolchain the keys name.

        Without `local` a record that still describes the manifest's toolchain
        is taken as it is. With it the local image must be that toolchain:
        the recorded image id is accepted, any other image is probed.
        """
        want = self.manifest['toolchain']
        raw = self.tree.toolchain_record()
        known = recorded_toolchain(want, raw)
        if known and not local and not self.rerecord:
            return known[:2]
        if not self.registry.available():
            raise CacheError('no toolchain fingerprint is recorded for this manifest (status/toolchain.json) '
                             'and docker is not available to probe %s' % TOOLCHAIN)
        image_id = self.registry.image_id(TOOLCHAIN)
        if not image_id:
            raise CacheError('there is no local %s image to fingerprint' % TOOLCHAIN)
        if known and known[2] == image_id and not self.rerecord:
            return known[:2]
        observed = parse_probe(self.registry.probe_toolchain(TOOLCHAIN, probe_script(want)))
        document = toolchain_document(want, observed, self.registry.base_layer(TOOLCHAIN))
        fingerprint = derive_key(document)
        previous = raw.get('fingerprint') if raw else None
        changed = bool(previous) and previous != fingerprint
        if changed and not self.rerecord:
            raise CacheError('the local %s image (%s) has toolchain fingerprint %s, not the recorded %s; '
                             'if the toolchain was changed on purpose, accept it with '
                             '`provider_cache.py toolchain --record`' % (TOOLCHAIN, image_id, fingerprint, previous))
        before = raw.get('outputs_before') if raw else None
        self.tree.record_toolchain({
            'stage': 'toolchain', 'status': 'ok', 'outputs': [TOOLCHAIN], 'seconds': 0,
            'notes': 'toolchain fingerprint of the provider cache keys (provider_cache.py toolchain); '
                     'observed and packages_sha256 are what the probe saw and are not part of it',
            'image': TOOLCHAIN, 'image_id': image_id, 'fingerprint': fingerprint, 'document': document,
            'observed': observed['tools'], 'packages_sha256': observed['packages'],
            'outputs_before': int(time.time()) + 1 if changed else before})
        self.log('provider-cache: toolchain fingerprint %s (%s, image %s)' % (fingerprint, document['arch'], image_id))
        return fingerprint, document['arch']

    def _check_toolchain(self):
        """The local image must be the recorded toolchain before the cache is
        read or written; keys derived earlier from another record are dropped."""
        value = self.toolchain(local=True)
        if self._context is not None and self._context[1] != value:
            self._keys, self._context = {}, None
        return value

    # -- keys -----------------------------------------------------------------
    def _facts(self, records, recipes, toolchain, arch):
        root, repo = str(self.tree.root), str(self.tree.repo)

        def facts(name):
            entry = self.manifest['providers'][name]
            recipe = entry['recipe']
            if recipe not in recipes:  # several providers share one recipe
                recipes[recipe] = self.tree.recipe_files(recipe_sources(self.manifest, recipe),
                                                         self.manifest['recipe_exclude'])
            return {'recipe_files': recipes[recipe], 'locks': self.tree.locks(entry['locks']),
                    'fetched': select_fetched(records, entry['fetched'], root),
                    'toolchain': toolchain, 'arch': arch, 'root': root, 'repo': repo}

        return facts

    def keys(self, names=None):
        """{name: (key, document)}; only the named providers and their
        prerequisites are derived, so one provider's missing input does not
        take the cache away from the others."""
        if self._context is None:
            self._context = (self.tree.fetched_records(), self.toolchain(), {})
        records, (toolchain, arch), recipes = self._context
        self._keys = derive_keys(self.manifest, self._facts(records, recipes, toolchain, arch), names, self._keys)
        return self._keys

    def key(self, name):
        return self.keys([name])[name][0]

    def explain(self, name):
        return self.keys([name])[name][1]

    def ref(self, name):
        return image_ref(self.prefix, name, self.key(name))

    def legacy_keys(self, names=None):
        """{name: (key, document)} as schema 1 derived them from the same inputs."""
        image_id = self.legacy_id or self.tree.legacy_toolchain()
        self.keys(names)
        records, _, recipes = self._context
        self._legacy = derive_keys(self.manifest, self._facts(records, recipes, image_id, None), names,
                                   self._legacy, legacy_key_document)
        return self._legacy

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

    def _prerequisites_cached(self, name):
        """Whether every local prerequisite is the output the cache serves:
        restored from its current reference, or the very output (by its
        completion marker) that this work root last pushed there."""
        pushed, providers = self.tree.pushed_markers(), self.manifest['providers']
        for dep in providers[name]['prerequisites']:
            status = self.tree.status_json(dep)
            restored = status.get('restored_from') == self.ref(dep) and status.get('cache_key') == self.key(dep)
            marker = self.tree.marker_sha256(dep, providers[dep]['marker'])
            if not restored and (marker is None or pushed.get(self.ref(dep)) != marker):
                return False
        return True

    # -- push -------------------------------------------------------------------
    def _bundle(self, name, key, entry, context, markers, status_bytes, marker):
        """Write the files of a bundle into `context`; (entries, tar sha256)."""
        top = self.tree.provider_dir(name)
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
        files_text = json.dumps({'schema': FILES_SCHEMA, 'provider': name, 'entries': entries},
                                sort_keys=True, separators=(',', ':')) + '\n'
        meta = {'schema': SCHEMA, 'provider': name, 'key': key, 'root': str(self.tree.root),
                'repo': str(self.tree.repo), 'marker': entry['marker'], 'marker_sha256': marker,
                'tar_sha256': tar_sha, 'files_sha256': sha256_bytes(files_text.encode()),
                'prerequisite_markers': markers, 'key_inputs': self.explain(name)}
        (context / 'files.json').write_text(files_text)
        (context / 'meta.json').write_text(json.dumps(meta, sort_keys=True, indent=1) + '\n')
        (context / 'status.json').write_bytes(status_bytes)
        (context / 'tar.sha256').write_text(tar_sha + '\n')
        return entries, tar_sha

    @staticmethod
    def _labels(name, key, tar_sha):
        return {'org.opencontainers.image.source': 'https://github.com/jonathanagustin/uds-takserver',
                'org.opencontainers.image.licenses': 'GPL-3.0-only',
                'dev.uds.takx.provider': name, 'dev.uds.takx.key': key,
                'dev.uds.takx.tar-sha256': tar_sha}

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
        before = (self.tree.toolchain_record() or {}).get('outputs_before')
        written = self.tree.status_time(name)
        if isinstance(before, int) and (written is None or written < before):
            raise CacheError('%s: it was ok before the toolchain record was replaced, so it is not an output of '
                             'the toolchain its key would name; rebuild it or restore it' % name)
        key, ref = self.key(name), self.ref(name)
        action, reason = decide_push(True, state, True, self.registry.probe(ref))
        replaced = None
        if action == 'present':
            refusal = self.tree.refusals().get(ref)
            if refusal is not None:
                action, reason = decide_replace(refusal, key, self.registry.digest(ref), self._markers(name),
                                                self._prerequisites_cached(name))
                if action == 'forget':
                    self.tree.note_refusal(ref, None)
            if action != 'replace':
                self.log('provider-cache: %s is %s: %s' % (name, reason, ref))
                return ref
            replaced = refusal['digest']
            self.log('provider-cache: %s: %s; replacing %s@%s' % (name, reason, ref, replaced))
        elif action == 'skip':
            raise Skip(reason)
        elif action != 'push':
            raise CacheError('%s: %s' % (name, reason))
        markers = self._markers(name)
        if markers is None:
            raise CacheError('%s: a prerequisite provider is not ok here, so its outputs cannot be bound' % name)
        context = self.tree.scratch('.push-%s.' % name)
        try:
            entries, tar_sha = self._bundle(name, key, entry, context, markers, status_bytes, marker)
            self.registry.publish(ref, context, [name + '.tar', *IMAGE_FILES], self._labels(name, key, tar_sha))
        finally:
            shutil.rmtree(context, ignore_errors=True)
        if self.registry.probe(ref) != 'present':
            raise CacheError('%s: pushed, but the registry does not show %s' % (name, ref))
        if replaced is not None:
            if self.registry.digest(ref) == replaced:
                raise CacheError('%s: pushed, but %s still names the refused entry %s' % (name, ref, replaced))
            self.tree.note_refusal(ref, None)
        self.tree.record_push((name, key, ref, tar_sha, marker))
        self.log('provider-cache: %s %s (%d entries, tar sha256 %s)'
                 % ('pushed' if replaced is None else 'replaced', ref, len(entries), tar_sha))
        return ref

    # -- restore ----------------------------------------------------------------
    def _examine(self, name, key, work, labels, markers, schema=SCHEMA):
        """Every check of the bundle files in `work` short of installing them.

        Returns (meta, status, recorded entries, the unpacked tree). Raises
        CacheError or tarfile.TarError; each of them is a judgement of the bundle.
        """
        entry = self.manifest['providers'][name]
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
        recorded = (work / 'tar.sha256').read_bytes().decode('ascii', 'replace').strip()
        check_bundle(name, key, str(self.tree.root), meta, status, labels, sha256_file(tar),
                     recorded, sha256_bytes(files_bytes), markers, schema)
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
        return meta, status, expected, out / name

    def _refused(self, name, key, ref, digest, kind, markers, error):
        """Note that the entry `ref` names now was judged unusable here."""
        if digest is None:
            self.log('provider-cache: %s: docker named no registry digest for %s, so the refusal is not noted '
                     'and a later push will not replace the entry' % (name, ref))
            return
        self.tree.note_refusal(ref, {'name': name, 'key': key, 'digest': digest, 'kind': kind, 'markers': markers,
                                     'reason': str(error)[:300], 'at': int(time.time())})

    def _checker(self, name):
        """(callable or None, what passed) for the checks of a restored
        P/<name>: the recipe's own re-check (providers.json 'verify'), then
        the command a lane gave with --verify-with."""
        log = self.tree.root / 'logs' / (name + '-verify.log')
        checks = []
        call = self.manifest['providers'][name].get('verify')
        if call is not None and self.verify and self.runner is not None:
            values = dict(self.manifest['variables'], root=str(self.tree.root), repo=str(self.tree.repo))
            env = [render(token, values) for token in call['env']]
            argv = [render(token, values) for token in call['argv']]
            checks.append(('the recipe\'s own check', lambda: self.runner.run(name, env, argv, log)))
        if self.lane_check is not None and self.verify:
            checks.append(('the lane\'s own check', lambda: self.lane_check.run(name, log)))
        if not checks:
            return None, ''

        def check():
            (self.tree.root / 'logs').mkdir(exist_ok=True)
            for whose, run in checks:
                code = run()
                if code == 1:
                    raise Rejected('%s: %s rejects the restored output; see logs/%s-verify.log' % (name, whose, name))
                if code != 0:
                    raise CacheError('%s: %s could not be run here (exit %s); see logs/%s-verify.log'
                                     % (name, whose, code, name))

        return check, ''.join(', %s passed' % whose for whose, _ in checks)

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
            try:
                labels, digest = self.registry.fetch(ref, work, [name + '.tar', *IMAGE_FILES])
            except MissingFile as error:
                self._refused(name, key, ref, error.digest, 'bundle', markers, error)
                raise
            try:
                _, status, expected, tree = self._examine(name, key, work, labels, markers)
            except OtherPrerequisites as error:
                self._refused(name, key, ref, digest, 'prerequisites', markers, error)
                raise
            except (CacheError, tarfile.TarError) as error:
                self._refused(name, key, ref, digest, 'bundle', markers, error)
                raise
            if self._markers(name) != markers:
                raise CacheError('%s: a prerequisite changed during the restore' % name)
            status['restored_from'] = ref
            status['cache_key'] = key
            check, passed = self._checker(name)
            try:
                self.tree.install(tree, name, json.dumps(status, indent=1) + '\n', check)
            except FileExistsError:
                raise CacheError('%s: the provider directory appeared meanwhile; not overwritten' % name)
            except Rejected as error:
                self._refused(name, key, ref, digest, 'bundle', markers, error)
                raise
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self.log('provider-cache: restored %s from %s (%d entries verified%s)' % (name, ref, len(expected), passed))
        return ref

    # -- migrate ----------------------------------------------------------------
    def migrate(self, name):
        """Make the bundle saved under the schema-1 key of `name` addressable
        under its current key, without uploading its archive again."""
        entry = self.manifest['providers'].get(name)
        if entry is None:
            raise CacheError('unknown provider ' + name)
        if not self.enabled:
            raise Skip('cache is off')
        markers = self._markers(name)
        if markers is None:
            raise Skip('a prerequisite provider is not ok here')
        if not self.registry.available():
            raise Skip('docker is not available')
        self._check_toolchain()
        key, ref = self.key(name), self.ref(name)
        state = self.registry.probe(ref)
        if state == 'present':
            raise Skip('already cached under the current key (%s)' % ref)
        if state == 'unauthorized':
            raise Skip('docker is not logged in to the cache registry')
        if state != 'absent':
            raise CacheError('%s: the registry could not be queried' % name)
        old_key, old_document = self.legacy_keys([name])[name]
        old_ref = image_ref(self.prefix, name, old_key)
        state = self.registry.probe(old_ref)
        if state == 'absent':
            raise Skip('nothing cached under the schema-1 key (%s)' % old_ref)
        if state != 'present':
            raise CacheError('%s: the registry could not be queried for %s' % (name, old_ref))
        work = self.tree.scratch('.migrate-%s.' % name)
        kept = False
        try:
            labels, digest = self.registry.fetch(old_ref, work, [name + '.tar', *IMAGE_FILES], keep=True)
            kept = True
            meta, _, expected, _ = self._examine(name, old_key, work, labels, markers, LEGACY_SCHEMA)
            if meta.get('key_inputs') != old_document:
                raise CacheError('%s: the bundle under %s records other key inputs than the ones derived here'
                                 % (name, old_ref))
            if self._markers(name) != markers:
                raise CacheError('%s: a prerequisite changed during the migration' % name)
            meta.update(schema=SCHEMA, key=key, key_inputs=self.explain(name),
                        adopted_from={'schema': LEGACY_SCHEMA, 'key': old_key, 'ref': old_ref, 'digest': digest,
                                      'toolchain_image_id': old_document['toolchain']})
            context = work / 'adopt'
            context.mkdir()
            (context / 'meta.json').write_text(json.dumps(meta, sort_keys=True, indent=1) + '\n')
            self.registry.adopt(old_ref, ref, context, ['meta.json'], self._labels(name, key, meta['tar_sha256']))
        finally:
            if kept:
                self.registry.forget(old_ref)
            shutil.rmtree(work, ignore_errors=True)
        if self.registry.probe(ref) != 'present':
            raise CacheError('%s: adopted, but the registry does not show %s' % (name, ref))
        self.tree.record_push((name, key, ref, meta['tar_sha256'], meta['marker_sha256']))
        self.log('provider-cache: %s is now also %s (was %s, %d entries verified, tar sha256 %s)'
                 % (name, ref, old_ref, len(expected), meta['tar_sha256']))
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
    runner = ToolchainRunner(HERE / 'run-in-toolchain.sh', int(environ.get('TAKX_PROTO_CACHE_VERIFY_TIMEOUT') or 900))
    return ProviderCache(manifest, WorkTree(os.path.abspath(root), HERE.parent.parent),
                         DockerRegistry(timeout), environ.get('TAKX_PROTO_CACHE') or DEFAULT_REGISTRY,
                         environ.get('TAKX_PROTO_TOOLCHAIN_ID') or '', runner=runner,
                         verify=environ.get('TAKX_PROTO_CACHE_VERIFY') != 'off')


def main(argv=None, service=None, out=None):
    out = out or sys.stdout
    argv, command = list(sys.argv[1:] if argv is None else argv), None
    if '--verify-with' in argv:  # everything after it is the lane's command, whatever its words look like
        argv, command = argv[:argv.index('--verify-with')], argv[argv.index('--verify-with') + 1:]
    parser = argparse.ArgumentParser(prog='provider_cache.py', description=__doc__.split('\n')[0])
    commands = parser.add_subparsers(dest='command', required=True)
    key = commands.add_parser('key', help='print the cache key of each named provider')
    key.add_argument('--explain', action='store_true', help='print the key inputs as JSON')
    key.add_argument('names', nargs='*')
    restore = commands.add_parser('restore', help='pull, verify and install each named provider')
    restore.add_argument('--no-verify', action='store_true', help='skip the post-restore checks')
    restore.add_argument('names', nargs='*', help='with one name: --verify-with COMMAND [ARG...] may follow; '
                                                  'that command must accept the restored tree')
    commands.add_parser('push', help='save each named provider that is ok').add_argument('names', nargs='*')
    migrate = commands.add_parser('migrate', help='re-address bundles saved under schema-1 keys')
    migrate.add_argument('--from-toolchain-id', metavar='ID', help='the image id those keys named')
    migrate.add_argument('names', nargs='*')
    status = commands.add_parser('status', help='key, local state and cache state of every provider')
    status.add_argument('--remote', action='store_true', help='ask the registry for each key')
    status.add_argument('--write-index', nargs='?', const=str(HERE / 'cache-index.tsv'), metavar='FILE',
                        help='rewrite the listing of cached providers (default: cache-index.tsv)')
    toolchain = commands.add_parser('toolchain', help='print the toolchain fingerprint of the keys')
    toolchain.add_argument('--record', action='store_true',
                           help='probe the local image and accept it as the toolchain of this work root')
    toolchain.add_argument('--probe-script', action='store_true', help='print the probe and do nothing else')
    args = parser.parse_args(argv)
    if command is not None and (args.command != 'restore' or len(args.names) != 1 or not command):
        parser.error('--verify-with needs `restore`, one NAME before it and a command after it')
    try:
        service = service or build_service()
        providers = service.manifest['providers']
        if args.command == 'toolchain':
            if args.probe_script:
                out.write(probe_script(service.manifest['toolchain']))
                return DONE
            service.rerecord = args.record
            fingerprint, arch = service.toolchain(local=args.record)
            print('%s\t%s' % (fingerprint, arch), file=out)
            return DONE
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
                fingerprint, arch = service.toolchain()
                header = ['Copyright 2026 uds-takserver authors', 'SPDX-License-Identifier: GPL-3.0-only',
                          'Generated by provider_cache.py status --write-index; do not edit.',
                          'Providers cached under their input keys. Restore needs no index: it',
                          'derives the key and pulls <ref>. Keys are specific to:',
                          'work root ' + str(service.tree.root), 'repository ' + str(service.tree.repo),
                          'toolchain fingerprint ' + fingerprint, 'architecture ' + arch]
                Path(args.write_index).write_text(index_text(listed, header))
            return DONE
    except CacheError as error:
        print('provider-cache: ' + str(error), file=sys.stderr)
        return FAILED
    worst = DONE
    if args.command == 'restore' and args.no_verify:
        service.verify = False
    if command is not None:
        service.lane_check = LaneCheck(command, getattr(service.runner, 'timeout', 900))
    if args.command == 'migrate' and args.from_toolchain_id:
        service.legacy_id = args.from_toolchain_id
    action = {'restore': service.restore, 'push': service.push, 'migrate': service.migrate}[args.command]
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
