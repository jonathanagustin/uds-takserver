# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests for provider_cache.py, providers.json and the lane hooks.

Nothing here starts docker or reaches a registry: the registry port is an
in-memory fake, and every work root and repository is a temporary directory.
"""
import copy
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
spec = importlib.util.spec_from_file_location('provider_cache_under_test', HERE / 'provider_cache.py')
PC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PC)

EXPECTED = {'sqlcipher', 'cesium-acq', 'cesium', 'kml', 'proj', 'geos', 'assimp', 'spatial', 'crypto',
            'commoncommo', 'draco', 'las', 'mdb', 'gles', 'scene', 'gdal-jni', 'curl', 'gis-java',
            'kernel-java', 'worldwind-java', 'worldwind-interop', 'consumers57', 'ui-original',
            'ui-wizard-reference'}
TOOLCHAIN_ID = 'sha256:' + '1' * 64   # image id: what schema-1 keys named, and what the record says was probed
BASE_LAYER = 'sha256:' + 'b' * 64


def toolchain_block():
    return {'base': 'registry.example/debian:trixie@sha256:' + 'd' * 64,
            'environment': {'JAVA_HOME': '/jdk', 'TZ': 'UTC'},
            'gated': [{'name': 'gcc', 'argv': ['/usr/bin/gcc', '--version'], 'expect': ' 14.2.0'},
                      {'name': 'g++', 'argv': ['/usr/bin/g++', '--version'], 'expect': ' 14.2.0'},
                      {'name': 'java', 'argv': ['/jdk/bin/java', '-version'], 'expect': 'Temurin-17.0.21+9'}],
            'build_packages': {'sections': ['devel', 'libdevel'], 'names': ['libc6', '*-dev']}}


def wrapper_block():
    return {'env': ['TAKX_PROTO_ROOT={root}', 'TAKX_PROTO_REPO={repo}', 'HOME=/tmp'],
            'mounts': ['{repo}:{repo}:ro', '{root}:{root}'], 'workdir': '{root}'}


def probe_text(arch='amd64', **changes):
    """What the probe prints in an image that is toolchain_block()."""
    lines = {'arch': None if arch is None else 'arch\t' + arch,
             'JAVA_HOME': 'env\tJAVA_HOME\t/jdk', 'TZ': 'env\tTZ\tUTC',
             'gcc': 'tool\tgcc\tgcc (Debian 14.2.0-19) 14.2.0 Copyright (C) 2024 ',
             'g++': 'tool\tg++\tg++ (Debian 14.2.0-19) 14.2.0 Copyright (C) 2024 ',
             'java': 'tool\tjava\topenjdk version "17.0.21" OpenJDK Runtime Environment Temurin-17.0.21+9 ',
             'packages': 'packages\t' + 'e' * 64,
             'gcc-14': 'pkg\tii \tgcc-14\t14.2.0-19\tamd64\tdevel',
             'libc6': 'pkg\tii \tlibc6\t2.41-12+deb13u4\tamd64\tlibs',
             'libc6-dev': 'pkg\tii \tlibc6-dev\t2.41-12+deb13u4\tamd64\tlibdevel',
             'libegl-dev': 'pkg\tii \tlibegl-dev\t1.7.0-1+b2\tamd64\tlibs',
             'linux-libc-dev': 'pkg\tii \tlinux-libc-dev\t6.12.117-1\tall\tdevel',
             'openssl': 'pkg\tii \topenssl\t3.5.7-1~deb13u3\tamd64\tutils',
             'oldpkg': 'pkg\trc \tlibold-dev\t1.0\tamd64\tlibdevel'}
    lines.update(changes)
    return ''.join(line + '\n' for line in lines.values() if line is not None)


# The build packages toolchain_block() selects from probe_text(), and their digest.
BUILD_PACKAGES = ['gcc-14 14.2.0-19 amd64', 'libc6 2.41-12+deb13u4 amd64', 'libc6-dev 2.41-12+deb13u4 amd64',
                  'libegl-dev 1.7.0-1+b2 amd64', 'linux-libc-dev 6.12.117-1 all']
BUILD_DIGEST = hashlib.sha256(''.join(line + '\n' for line in BUILD_PACKAGES).encode()).hexdigest()
ATTRIBUTE = {'packages_sha256': 'e' * 64, 'build_packages': BUILD_PACKAGES, 'build_packages_sha256': BUILD_DIGEST}
DRIFTED = dict(**{'libc6-dev': 'pkg\tii \tlibc6-dev\t2.41-12+deb13u5\tamd64\tlibdevel',
                  'packages': 'packages\t' + 'f' * 64})


def fingerprint(arch='amd64', layer=BASE_LAYER, block=None):
    return PC.derive_key(PC.expected_toolchain(block or toolchain_block(), arch, layer))


# The fingerprint of toolchain_block() on amd64 over BASE_LAYER, pinned like GOLDEN below.
FINGERPRINT = '4d9436b46e47938df438c4db49ab9f4deccd660a13065f9f0b31a458f9802dd5'


def toolchain_record(image_id=TOOLCHAIN_ID, arch='amd64', layer=BASE_LAYER, block=None, **changes):
    """A status/toolchain.json as provider_cache.py writes it."""
    document = PC.expected_toolchain(block or toolchain_block(), arch, layer)
    record = {'stage': 'toolchain', 'status': 'ok', 'outputs': [PC.TOOLCHAIN], 'seconds': 0, 'notes': 'n',
              'image': PC.TOOLCHAIN, 'image_id': image_id, 'fingerprint': PC.derive_key(document),
              'document': document, 'observed': {}, 'packages_sha256': 'e' * 64, 'outputs_before': None,
              'build_packages': list(BUILD_PACKAGES), 'build_packages_sha256': BUILD_DIGEST,
              'build_selection': (block or toolchain_block())['build_packages']}
    record.update(changes)
    return record


def manifest():
    """Two recipes, three providers: base <- top, and an unrelated side."""
    def provider(recipe, deps, script):
        return {'lane': 'lane1.sh', 'recipe': recipe, 'prerequisites': deps,
                'locks': [recipe + '/inputs.lock.json'], 'fetched': [{'consumer': recipe.split('/')[1]}],
                'marker': 'complete.json',
                'invocation': {'env': [], 'argv': ['python3', '{repo}/' + recipe + '/' + script,
                                                   '--output', '{root}/P/x']}}
    return {'schema': 2, 'variables': {'jdk': '/jdk'}, 'recipe_exclude': ['__pycache__', '*.pyc', 'README*', 'test_*'],
            'toolchain': toolchain_block(), 'wrapper': wrapper_block(),
            'recipes': {'takx/one': {'uses': []}, 'takx/two': {'uses': ['takx/one', 'takx/shared.py']}},
            'providers': {'base': provider('takx/one', [], 'build.py'),
                          'top': provider('takx/two', ['base'], 'build.py'),
                          'side': provider('takx/one', [], 'other.py')}}


def facts(**changes):
    value = {'recipe_files': {'takx/one/build.py': 'a' * 64}, 'locks': {'l': {'sha256': 'b' * 64, 'digests': []}},
             'fetched': [{'kind': 'lock-entry', 'name': 'n', 'digests': {'expected_sha256': 'c' * 64}}],
             'toolchain': FINGERPRINT, 'arch': 'amd64', 'root': '/var/lib/takx-proto', 'repo': '/repo'}
    value.update(changes)
    return value


class Core(unittest.TestCase):
    def keys(self, document=None, **per_provider):
        document = document or manifest()
        derived = PC.derive_keys(document, lambda name: per_provider.get(name, facts()))
        return {name: key for name, (key, _) in derived.items()}

    def test_keys_are_stable_and_distinct_per_provider(self):
        first, second = self.keys(), self.keys()
        self.assertEqual(first, second)
        self.assertEqual(len(set(first.values())), 3)
        for key in first.values():
            self.assertRegex(key, r'^[0-9a-f]{64}$')

    def test_every_input_changes_the_key(self):
        reference = self.keys()
        changes = {
            'recipe file': facts(recipe_files={'takx/one/build.py': 'd' * 64}),
            'recipe file set': facts(recipe_files={'takx/one/build.py': 'a' * 64, 'takx/one/x.c': 'a' * 64}),
            'lock': facts(locks={'l': {'sha256': 'e' * 64, 'digests': []}}),
            'pinned digest': facts(locks={'l': {'sha256': 'b' * 64, 'digests': ['f' * 64]}}),
            'fetched input': facts(fetched=[{'kind': 'lock-entry', 'name': 'n',
                                             'digests': {'expected_sha256': 'd' * 64}}]),
            'toolchain': facts(toolchain='2' * 64),
            'architecture': facts(arch='arm64'),
            'work root': facts(root='/srv/other'),
            'repository': facts(repo='/elsewhere'),
        }
        for what, changed in changes.items():
            with self.subTest(what=what):
                self.assertNotEqual(self.keys(base=changed)['base'], reference['base'])

    def test_upstream_change_invalidates_downstream_only(self):
        reference = self.keys()
        changed = self.keys(base=facts(recipe_files={'takx/one/build.py': 'd' * 64}))
        self.assertNotEqual(changed['base'], reference['base'])
        self.assertNotEqual(changed['top'], reference['top'])
        self.assertEqual(changed['side'], reference['side'])

    def test_invocation_template_variables_marker_and_schema_are_part_of_the_key(self):
        reference = self.keys()
        for what, edit in (
                ('argv', lambda m: m['providers']['base']['invocation']['argv'].append('--jobs')),
                ('env', lambda m: m['providers']['base']['invocation']['env'].append('A=1')),
                ('variables', lambda m: m['variables'].update(jdk='/other')),
                ('wrapper variable', lambda m: m['wrapper']['env'].append('A=1')),
                ('wrapper variable value', lambda m: m['wrapper']['env'].__setitem__(2, 'HOME=/root')),
                ('wrapper mount', lambda m: m['wrapper']['mounts'].__setitem__(0, '{repo}:{repo}')),
                ('wrapper working directory', lambda m: m['wrapper'].update(workdir='/')),
                ('marker', lambda m: m['providers']['base'].update(marker='done.json'))):
            document = manifest()
            edit(document)
            with self.subTest(what=what):
                self.assertNotEqual(self.keys(document)['base'], reference['base'])
        document = PC.key_document(manifest(), 'base', facts(), {})
        self.assertEqual(document['schema'], 2)
        self.assertNotEqual(PC.derive_key(dict(document, schema=3)), PC.derive_key(document))

    def test_the_post_restore_check_is_not_part_of_the_key(self):
        document = manifest()
        document['providers']['base']['verify'] = {'env': ['PROOF={root}/P/base'], 'argv': ['check', '{repo}']}
        self.assertEqual(self.keys(document), self.keys())

    def test_key_needs_every_identity_and_every_prerequisite_key(self):
        for field in ('toolchain', 'arch', 'root', 'repo'):
            with self.subTest(field=field), self.assertRaises(PC.CacheError):
                PC.key_document(manifest(), 'base', facts(**{field: ''}), {})
        for what, wrong in (('an image id', {'toolchain': TOOLCHAIN_ID}), ('a short digest', {'toolchain': 'a' * 63}),
                            ('an upper-case digest', {'toolchain': 'A' * 64}),
                            ('another architecture', {'arch': 'riscv64'}),
                            ('a machine name', {'arch': 'x86_64'})):
            with self.subTest(what=what), self.assertRaisesRegex(
                    PC.CacheError, '^base: the toolchain is not named by a fingerprint and an architecture$'):
                PC.key_document(manifest(), 'base', facts(**wrong), {})
        for arch in ('amd64', 'arm64'):
            self.assertEqual(PC.key_document(manifest(), 'base', facts(arch=arch), {})['arch'], arch)
        with self.assertRaises(PC.CacheError):
            PC.key_document(manifest(), 'base', facts(recipe_files={}), {})
        with self.assertRaises(PC.CacheError):
            PC.key_document(manifest(), 'top', facts(), {})
        with self.assertRaises(PC.CacheError):
            PC.key_document(manifest(), 'top', facts(), {'base': 'not-a-key'})

    def test_order_puts_prerequisites_first_and_refuses_cycles(self):
        providers = manifest()['providers']
        self.assertEqual(PC.order(providers), ['base', 'side', 'top'])
        self.assertEqual(PC.order(providers, ['top', 'base']), ['base', 'top'])
        with self.assertRaises(PC.CacheError):
            PC.order(providers, ['nope'])
        providers['base']['prerequisites'] = ['top']
        with self.assertRaises(PC.CacheError):
            PC.order(providers)

    def test_manifest_validation_refuses_malformed_entries(self):
        PC.validate_manifest(manifest())
        for what, edit in (
                ('schema', lambda m: m.update(schema=1)),
                ('schema of the future', lambda m: m.update(schema=3)),
                ('unknown prerequisite', lambda m: m['providers']['top'].update(prerequisites=['ghost'])),
                ('unknown recipe', lambda m: m['providers']['top'].update(recipe='takx/ghost')),
                ('absolute marker', lambda m: m['providers']['top'].update(marker='/etc/passwd')),
                ('dot-dot lock', lambda m: m['providers']['top'].update(locks=['../x'])),
                ('dot-dot use', lambda m: m['recipes']['takx/two'].update(uses=['../../etc'])),
                ('provider name', lambda m: m['providers'].update({'Bad/../name': m['providers']['side']})),
                ('empty argv', lambda m: m['providers']['top']['invocation'].update(argv=[])),
                ('selector', lambda m: m['providers']['top'].update(fetched=[{'glob': '*'}])),
                ('no marker', lambda m: m['providers']['top'].pop('marker'))):
            document = manifest()
            edit(document)
            with self.subTest(what=what), self.assertRaises(PC.CacheError):
                PC.validate_manifest(document)

    def test_recipe_sources_follow_uses_transitively(self):
        document = manifest()
        document['recipes']['takx/one']['uses'] = ['takx/two', 'takx/base.lock']
        self.assertEqual(PC.recipe_sources(document, 'takx/two'),
                         ['takx/base.lock', 'takx/one', 'takx/shared.py', 'takx/two'])

    def test_fetched_selection_is_exact_sorted_and_fails_closed(self):
        records = [
            {'kind': 'lock-entry', 'name': 'b', 'verified': True, 'expected_sha256': '2' * 64,
             'actual_sha256': '2' * 64, 'bytes': 7, 'path': '/root/cache/x', 'consumers': [{'recipe': 'one'}]},
            {'kind': 'lock-entry', 'name': 'a', 'verified': True, 'expected_sha256': '1' * 64,
             'consumers': [{'recipe': 'one'}, {'recipe': 'two'}]},
            {'kind': 'git-source', 'name': 'src', 'verified': True, 'expected_revision': '3' * 40,
             'path': '/root/src/tree'},
            {'kind': 'lock-entry', 'name': 'bad', 'verified': False, 'expected_sha256': '4' * 64,
             'consumers': [{'recipe': 'broken'}]},
            {'kind': 'odd', 'name': 'nodigest', 'verified': True},
        ]
        got = PC.select_fetched(records, [{'consumer': 'one'}, {'path': 'src/tree'}, {'consumer': 'two'}], '/root')
        self.assertEqual(sorted(item['name'] for item in got), ['a', 'b', 'src'])
        self.assertEqual(got, PC.select_fetched(records[::-1], [{'consumer': 'two'}, {'path': 'src/tree'},
                                                                {'consumer': 'one'}], '/root'))
        by_name = {item['name']: item for item in got}
        self.assertEqual(by_name['b']['digests'], {'actual_sha256': '2' * 64, 'expected_sha256': '2' * 64})
        self.assertNotIn('/root', json.dumps(got))
        for selector in ({'consumer': 'nobody'}, {'consumer': 'broken'}, {'kind': 'odd'}, {'path': 'src'}):
            with self.subTest(selector=selector), self.assertRaises(PC.CacheError):
                PC.select_fetched(records, [selector], '/root')

    def test_lock_digests_are_the_pinned_hex_values(self):
        text = json.dumps({'a': '1' * 64, 'b': '2' * 40, 'c': '3' * 128, 'd': '4' * 63, 'e': '1' * 64})
        self.assertEqual(PC.lock_digests(text), ['1' * 64, '2' * 40, '3' * 128])

    def test_archive_members_inside_the_provider_are_accepted(self):
        members = [('p', 'dir', ''), ('p/a', 'dir', ''), ('p/a/f', 'file', ''), ('p/a/.libs', 'dir', ''),
                   ('p/a/.libs/l.la', 'symlink', '../l.la'), ('p/bin', 'symlink', 'a'),
                   ('p/a/h', 'hardlink', 'p/a/f'), ('p/..x', 'file', '')]
        self.assertEqual(PC.check_members('p', members), len(members))

    def test_archive_members_that_could_escape_are_refused(self):
        top = [('p', 'dir', '')]
        cases = {
            'absolute': [('/etc/passwd', 'file', '')],
            'dot-dot': [('p/../x', 'file', '')],
            'other top': [('q/x', 'file', '')],
            'sibling prefix': [('pp', 'file', '')],
            'dot component': [('p/./x', 'file', '')],
            'device': [('p/dev', 'other', '')],
            'absolute symlink': [('p/l', 'symlink', '/etc')],
            'escaping symlink': [('p/a', 'dir', ''), ('p/a/l', 'symlink', '../../x')],
            'empty symlink': [('p/l', 'symlink', '')],
            'hardlink outside': [('p/h', 'hardlink', 'etc/passwd')],
            'hardlink dot-dot': [('p/h', 'hardlink', 'p/../x')],
            'hardlink to nothing': [('p/h', 'hardlink', 'p/missing')],
            'duplicate': [('p/f', 'file', ''), ('p/f', 'file', '')],
            'below a symlink': [('p/l', 'symlink', 'a'), ('p/l/f', 'file', '')],
            'below a file': [('p/f', 'file', ''), ('p/f/g', 'file', '')],
            'backslash': [('p/a\\b', 'file', '')],
        }
        for what, extra in cases.items():
            with self.subTest(what=what), self.assertRaises(PC.CacheError):
                PC.check_members('p', top + extra)
        with self.assertRaises(PC.CacheError):
            PC.check_members('p', [('p/f', 'file', '')])
        with self.assertRaises(PC.CacheError):
            PC.check_members('p', [('p', 'file', '')])

    def test_file_manifests_compare_exactly(self):
        one = [{'path': 'a', 'type': 'file', 'mode': '0644', 'size': 1, 'sha256': 'x'},
               {'path': 'l', 'type': 'link', 'target': 'a'}]
        self.assertEqual(PC.compare_entries(one, copy.deepcopy(one)[::-1]), [])
        self.assertEqual(PC.compare_entries(one, one[:1]), ['missing l'])
        self.assertEqual(PC.compare_entries(one[:1], one), ['unexpected l'])
        for field, value in (('sha256', 'y'), ('size', 2), ('mode', '0755'), ('type', 'dir')):
            changed = copy.deepcopy(one)
            changed[0][field] = value
            self.assertEqual(PC.compare_entries(one, changed), ['changed a'])
        self.assertTrue(PC.compare_entries(one + one[:1], one))
        with self.assertRaises(PC.CacheError):
            PC.check_entries([{'path': 'l', 'type': 'link', 'target': '../x'}])
        with self.assertRaises(PC.CacheError):
            PC.check_entries([{'path': 'd', 'type': 'special', 'mode': '0600'}])

    def test_push_decisions(self):
        self.assertEqual(PC.decide_push(False, 'ok', True, 'absent')[0], 'skip')
        self.assertEqual(PC.decide_push(True, 'failed', True, 'absent')[0], 'skip')
        self.assertEqual(PC.decide_push(True, 'missing', True, 'absent')[0], 'skip')
        self.assertEqual(PC.decide_push(True, 'ok', False, 'absent')[0], 'refuse')
        self.assertEqual(PC.decide_push(True, 'ok', True, 'present')[0], 'present')
        self.assertEqual(PC.decide_push(True, 'ok', True, 'unauthorized')[0], 'skip')
        self.assertEqual(PC.decide_push(True, 'ok', True, 'absent')[0], 'push')
        self.assertEqual(PC.decide_push(True, 'ok', True, 'error')[0], 'refuse')

    def test_restore_decisions(self):
        self.assertEqual(PC.decide_restore(False, False, True, 'present')[0], 'skip')
        self.assertEqual(PC.decide_restore(True, True, True, 'present')[0], 'refuse')
        self.assertEqual(PC.decide_restore(True, False, False, 'present')[0], 'skip')
        self.assertEqual(PC.decide_restore(True, False, True, 'present')[0], 'restore')
        self.assertEqual(PC.decide_restore(True, False, True, 'absent')[0], 'skip')
        self.assertEqual(PC.decide_restore(True, False, True, 'unauthorized')[0], 'skip')
        self.assertEqual(PC.decide_restore(True, False, True, 'error')[0], 'refuse')

    def test_bundle_identity_checks(self):
        key, tar, files = 'a' * 64, 'b' * 64, 'c' * 64
        good = dict(name='p', key=key, root='/r',
                    meta={'schema': 2, 'provider': 'p', 'key': key, 'root': '/r', 'tar_sha256': tar,
                          'files_sha256': files, 'prerequisite_markers': {'d': 'e' * 64}},
                    status={'stage': 'p', 'status': 'ok'},
                    labels={'dev.uds.takx.provider': 'p', 'dev.uds.takx.key': key, 'dev.uds.takx.tar-sha256': tar},
                    tar_sha256=tar, recorded_tar_sha256=tar, files_sha256=files, local_markers={'d': 'e' * 64})
        PC.check_bundle(**good)
        # A bundle of schema 1 is only accepted where the caller asks for one (migrate).
        legacy = dict(good, meta=dict(good['meta'], schema=1))
        PC.check_bundle(**legacy, schema=1)
        for arguments in (legacy, dict(good, schema=1)):
            with self.assertRaisesRegex(PC.CacheError, '^p: unsupported bundle schema$'):
                PC.check_bundle(**arguments)
        # Only the prerequisite binding is the refusal that depends on the local tree.
        with self.assertRaises(PC.OtherPrerequisites) as caught:
            PC.check_bundle(**dict(good, local_markers={'d': '0' * 64}))
        self.assertEqual(str(caught.exception),
                         'p: it was built against other prerequisite outputs than the ones present here')
        with self.assertRaises(PC.OtherPrerequisites):
            PC.check_bundle(**dict(good, local_markers=None))
        bad = {
            'tar': dict(tar_sha256='0' * 64),
            'recorded': dict(recorded_tar_sha256=''),
            'label tar': dict(labels=dict(good['labels'], **{'dev.uds.takx.tar-sha256': '0' * 64})),
            'label provider': dict(labels=dict(good['labels'], **{'dev.uds.takx.provider': 'q'})),
            'label key': dict(labels=dict(good['labels'], **{'dev.uds.takx.key': '0' * 64})),
            'no labels': dict(labels={}),
            'meta key': dict(meta=dict(good['meta'], key='0' * 64)),
            'meta root': dict(meta=dict(good['meta'], root='/other')),
            'meta tar': dict(meta=dict(good['meta'], tar_sha256='0' * 64)),
            'meta schema': dict(meta=dict(good['meta'], schema=3)),
            'files': dict(files_sha256='0' * 64),
            'status failed': dict(status={'stage': 'p', 'status': 'failed'}),
            'status other': dict(status={'stage': 'q', 'status': 'ok'}),
            'prerequisite': dict(local_markers={'d': '0' * 64}),
            'not a dict': dict(meta=[]),
        }
        for what, change in bad.items():
            with self.subTest(what=what), self.assertRaises(PC.CacheError) as caught:
                PC.check_bundle(**dict(good, **change))
            self.assertEqual(isinstance(caught.exception, PC.OtherPrerequisites), what == 'prerequisite', what)

    def test_image_reference_and_index(self):
        self.assertEqual(PC.image_ref('reg/x/', 'gdal-jni', 'a' * 64), 'reg/x/gdal-jni:' + 'a' * 64)
        for registry, name, key in (('off', 'a', 'a' * 64), ('', 'a', 'a' * 64), ('r', 'A/b', 'a' * 64),
                                    ('r', 'a', 'short')):
            with self.assertRaises(PC.CacheError):
                PC.image_ref(registry, name, key)
        text = PC.index_text([('a', 'k', 'r', 't')], ['one'])
        self.assertEqual(text, '# one\n# name\tkey\tref\ttar sha256\na\tk\tr\tt\n')


def reordered(value):
    """The same JSON value with every dict built in the opposite order."""
    if isinstance(value, dict):
        return {key: reordered(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [reordered(item) for item in value]
    return value


def snapshot(top):
    """Everything below `top`, read without the module under test."""
    result = {}
    for directory, names, files in os.walk(top):
        for name in names + files:
            path = os.path.join(directory, name)
            info = os.lstat(path)
            rel = os.path.relpath(path, top)
            if stat.S_ISLNK(info.st_mode):
                result[rel] = ('link', os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                result[rel] = ('dir', stat.S_IMODE(info.st_mode))
            elif stat.S_ISREG(info.st_mode):
                with open(path, 'rb') as stream:
                    result[rel] = ('file', stat.S_IMODE(info.st_mode), hashlib.sha256(stream.read()).hexdigest())
            else:
                result[rel] = ('special', info.st_mode)
    return result


def settled(top, root='root'):
    """snapshot() without the note a refused restore leaves for a later push."""
    result = snapshot(top)
    result.pop(os.path.join(root, 'cache-state', 'refused.json'), None)
    return result


def chain():
    """a <- b <- c <- d, a diamond a <- (b, e) <- f, and an unrelated z."""
    def provider(deps):
        return {'lane': 'lane1.sh', 'recipe': 'takx/one', 'prerequisites': deps, 'locks': [], 'fetched': [],
                'marker': 'complete.json', 'invocation': {'env': [], 'argv': ['build']}}
    return {'schema': 2, 'variables': {}, 'recipe_exclude': [], 'recipes': {'takx/one': {'uses': []}},
            'toolchain': toolchain_block(), 'wrapper': wrapper_block(),
            'providers': {'d': provider(['c']), 'c': provider(['b']), 'b': provider(['a']), 'a': provider([]),
                          'e': provider(['a']), 'f': provider(['e', 'b']), 'z': provider([])}}


# Keys of manifest() with facts() for every provider. They pin the canonical
# form: a change here gives every cached provider a new name, so it must be
# deliberate (and then SCHEMA should change with it).
GOLDEN = {
    'base': '2dbe4b6f1d58aa5c2897fbfca977df1047dda52eee28275346506b7a09fd7e97',
    'side': '78a05eb413155dec778fb518e1c93f00f1aabc937a9b326e700f935fda84fa5d',
    'top': '7888f99e4013bd4c72fd66dc43abd0f1606642ab44b4336b06cf3cc03b41cac1',
}
# The keys schema 1 gave the same manifest and facts with the toolchain named
# by TOOLCHAIN_ID. These are the values the suite pinned before schema 2:
# `migrate` finds the bundles saved under such keys only while
# legacy_key_document() reproduces them exactly.
LEGACY_GOLDEN = {
    'base': '88363aae55cf1f04d0eb168957fce37cc727309e135028a7c01e31a537d4d2cf',
    'side': '5af48161ea0a6a1473491230f46d097b0587e88f36706b76bdaa2a8b2ba6c804',
    'top': 'f1a6ae3ab289a0b5645771283d29dcb2cc6f38ee830af92bb68ced94536ec36d',
}
KEY_SNIPPET = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location('provider_cache_child', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
manifest, facts = json.loads(sys.stdin.read())
derived = module.derive_keys(manifest, lambda name: facts)
print(json.dumps({name: key for name, (key, _) in derived.items()}))
"""


class KeyProperties(unittest.TestCase):
    """The key as a pure function of its inputs."""

    def keys(self, document=None, names=None, **per_provider):
        derived = PC.derive_keys(document or manifest(), lambda name: per_provider.get(name, facts()), names)
        return {name: key for name, (key, _) in derived.items()}

    def test_keys_have_the_pinned_values(self):
        self.assertEqual(self.keys(), GOLDEN)
        self.assertEqual(fingerprint(), FINGERPRINT)

    def test_schema_1_keys_are_still_derived_exactly(self):
        def old(name):
            return facts(toolchain=TOOLCHAIN_ID, arch=None)
        derived = PC.derive_keys(manifest(), old, describe=PC.legacy_key_document)
        self.assertEqual({name: key for name, (key, _) in derived.items()}, LEGACY_GOLDEN)
        document = derived['top'][1]
        self.assertEqual(document, {
            'schema': 1, 'provider': 'top', 'recipe': 'takx/two', 'recipe_files': facts()['recipe_files'],
            'invocation_sha256': PC.sha256_bytes(PC.canonical({
                'invocation': manifest()['providers']['top']['invocation'], 'variables': {'jdk': '/jdk'}})),
            'marker': 'complete.json', 'locks': facts()['locks'], 'fetched': facts()['fetched'],
            'toolchain': TOOLCHAIN_ID, 'root': '/var/lib/takx-proto', 'repo': '/repo',
            'prerequisites': {'base': LEGACY_GOLDEN['base']}})
        # What schema 2 added is no input of a schema-1 key.
        changed = manifest()
        changed['wrapper']['env'].append('A=1')
        changed['toolchain']['gated'][0]['expect'] = ' 15.1.0'
        derived = PC.derive_keys(changed, old, describe=PC.legacy_key_document)
        self.assertEqual({name: key for name, (key, _) in derived.items()}, LEGACY_GOLDEN)
        source = old('base')
        document = PC.legacy_key_document(manifest(), 'base', source, {})
        document['recipe_files']['x'] = document['locks']['x'] = 1
        document['fetched'].append(1)
        self.assertEqual(source, old('base'))  # the document is a copy
        for what, wrong in (('a fingerprint', FINGERPRINT), ('nothing', ''), ('no string', None),
                            ('a short id', 'sha256:' + '1' * 63), ('a trailing newline', TOOLCHAIN_ID + '\n')):
            with self.subTest(what=what), self.assertRaisesRegex(
                    PC.CacheError, '^base: a schema-1 key needs the toolchain image id$'):
                PC.legacy_key_document(manifest(), 'base', facts(toolchain=wrong), {})
        for bad in ({}, {'side': LEGACY_GOLDEN['side']},
                    {'base': LEGACY_GOLDEN['base'], 'side': LEGACY_GOLDEN['side']}):
            with self.subTest(bad=bad), self.assertRaisesRegex(PC.CacheError, '^top: prerequisite keys incomplete$'):
                PC.legacy_key_document(manifest(), 'top', old('top'), bad)

    def test_canonical_form_is_compact_sorted_ascii_json(self):
        self.assertEqual(PC.canonical({'b': [1, 'é'], 'a': {'d': None, 'c': True}}),
                         b'{"a":{"c":true,"d":null},"b":[1,"\\u00e9"]}')
        self.assertEqual(PC.sha256_bytes(b'abc'),
                         'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
        document = PC.key_document(manifest(), 'base', facts(), {})
        self.assertEqual(PC.derive_key(document), hashlib.sha256(
            json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest())

    def test_key_does_not_depend_on_dict_ordering(self):
        self.assertEqual(self.keys(reordered(manifest())), GOLDEN)
        backwards = reordered(facts())
        self.assertEqual(self.keys(base=backwards, side=backwards, top=backwards), GOLDEN)
        document = PC.key_document(manifest(), 'top', facts(), {'base': GOLDEN['base']})
        self.assertEqual(PC.derive_key(reordered(document)), GOLDEN['top'])
        self.assertEqual(list(reordered(document)), list(document)[::-1])

    def test_key_does_not_depend_on_which_providers_are_asked_for_or_in_which_order(self):
        for names in (['top'], ['top', 'base'], ['base', 'top'], ['side', 'top', 'base'], ['top', 'top']):
            with self.subTest(names=names):
                wanted = set(names) | ({'base'} if 'top' in names else set())
                self.assertEqual(self.keys(names=names), {name: GOLDEN[name] for name in wanted})
        self.assertEqual(self.keys(names=['side']), {'side': GOLDEN['side']})
        self.assertEqual(self.keys(names=[]), {})

    def test_key_does_not_depend_on_the_order_of_prerequisites(self):
        document = chain()
        reference = self.keys(document)
        document['providers']['f']['prerequisites'] = ['b', 'e']
        self.assertEqual(self.keys(document), reference)
        self.assertEqual(len(set(reference.values())), len(reference))

    def test_key_is_the_same_in_another_process_with_another_hash_seed(self):
        payload = json.dumps([manifest(), facts()]).encode()
        for seed in ('0', '1', '4242'):
            done = subprocess.run([sys.executable, '-B', '-c', KEY_SNIPPET, str(HERE / 'provider_cache.py')],
                                  input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                                  env={'PYTHONHASHSEED': seed, 'PATH': '/nonexistent'})
            with self.subTest(seed=seed):
                self.assertEqual(done.returncode, 0, done.stderr.decode())
                self.assertEqual(json.loads(done.stdout), GOLDEN)

    def test_known_keys_are_reused_and_not_derived_again(self):
        asked = []

        def facts_of(name):
            asked.append(name)
            return facts()
        first = PC.derive_keys(manifest(), facts_of, ['base'])
        self.assertEqual(asked, ['base'])
        second = PC.derive_keys(manifest(), facts_of, ['top'], first)
        self.assertEqual(asked, ['base', 'top'])
        self.assertEqual({name: key for name, (key, _) in second.items()},
                         {name: GOLDEN[name] for name in ('base', 'top')})
        self.assertEqual(first, {'base': second['base']})  # the earlier result is not modified

    def test_each_single_change_gives_another_key(self):
        changes = {
            'one recipe byte': facts(recipe_files={'takx/one/build.py': 'a' * 63 + 'b'}),
            'added recipe file': facts(recipe_files={'takx/one/build.py': 'a' * 64, 'takx/one/new.c': 'a' * 64}),
            'renamed recipe file': facts(recipe_files={'takx/one/built.py': 'a' * 64}),
            'lock file': facts(locks={'l': {'sha256': 'b' * 63 + 'c', 'digests': []}}),
            'lock digest': facts(locks={'l': {'sha256': 'b' * 64, 'digests': ['f' * 64]}}),
            'lock name': facts(locks={'m': {'sha256': 'b' * 64, 'digests': []}}),
            'no lock': facts(locks={}),
            'fetched digest': facts(fetched=[{'kind': 'lock-entry', 'name': 'n',
                                              'digests': {'expected_sha256': 'c' * 63 + 'd'}}]),
            'fetched name': facts(fetched=[{'kind': 'lock-entry', 'name': 'm',
                                            'digests': {'expected_sha256': 'c' * 64}}]),
            'no fetched input': facts(fetched=[]),
            'toolchain': facts(toolchain=FINGERPRINT[:-1] + '0'),
            'architecture': facts(arch='arm64'),
            'toolchain of the other architecture': facts(toolchain=fingerprint('arm64'), arch='arm64'),
            'work root': facts(root='/var/lib/takx-proto2'),
            'repository': facts(repo='/repo2'),
        }
        seen = {GOLDEN['base']}
        for what, changed in changes.items():
            with self.subTest(what=what):
                key = self.keys(base=changed)['base']
                self.assertNotIn(key, seen)
                seen.add(key)
        removed = facts(recipe_files={'takx/one/build.py': 'a' * 64, 'takx/one/new.c': 'a' * 64})
        self.assertNotEqual(self.keys(base=removed)['base'], GOLDEN['base'])  # read backwards: a removed file

    def test_each_single_manifest_change_gives_another_key(self):
        edits = {
            'argv value': lambda m: m['providers']['base']['invocation']['argv'].__setitem__(3, '{root}/P/y'),
            'argv added': lambda m: m['providers']['base']['invocation']['argv'].append('--jobs'),
            'argv order': lambda m: m['providers']['base']['invocation']['argv'].reverse(),
            'env': lambda m: m['providers']['base']['invocation']['env'].append('A=1'),
            'variable value': lambda m: m['variables'].update(jdk='/jdk2'),
            'variable added': lambda m: m['variables'].update(cmake='/cmake'),
            'wrapper variable': lambda m: m['wrapper']['env'].append('A=1'),
            'wrapper variable removed': lambda m: m['wrapper']['env'].pop(),
            'wrapper mount': lambda m: m['wrapper']['mounts'].pop(),
            'wrapper working directory': lambda m: m['wrapper'].update(workdir='/tmp'),
            'marker': lambda m: m['providers']['base'].update(marker='done.json'),
            'recipe': lambda m: (m['recipes'].update({'takx/three': {'uses': []}}),
                                 m['providers']['base'].update(recipe='takx/three')),
        }
        seen = {GOLDEN['base']}
        for what, edit in edits.items():
            document = manifest()
            edit(document)
            with self.subTest(what=what):
                key = self.keys(PC.validate_manifest(document))['base']
                self.assertNotIn(key, seen)
                seen.add(key)

    def test_what_is_not_an_input_does_not_change_the_key(self):
        document = manifest()
        document['providers']['base']['lane'] = 'lane9.sh'
        document['description'] = ['other words']
        document['recipe_exclude'].append('*.orig')  # only what the files then are counts
        document['providers']['base']['verify'] = {'env': [], 'argv': ['check']}  # run after a restore, not by a build
        # The toolchain block reaches the key through the fingerprint in the facts only.
        document['toolchain']['gated'][0]['expect'] = ' 15.1.0'
        self.assertEqual(self.keys(PC.validate_manifest(document)), GOLDEN)

    def test_schema_version_is_part_of_the_key(self):
        document = dict(manifest(), schema=3)
        self.assertEqual(PC.key_document(document, 'base', facts(), {})['schema'], 3)
        self.assertNotEqual(PC.derive_key(PC.key_document(document, 'base', facts(), {})), GOLDEN['base'])

    def test_prerequisite_key_is_part_of_the_key(self):
        def top(key):
            return PC.derive_key(PC.key_document(manifest(), 'top', facts(), {'base': key}))
        self.assertEqual(top(GOLDEN['base']), GOLDEN['top'])
        self.assertNotEqual(top(GOLDEN['base'][:-1] + '0'), GOLDEN['top'])
        self.assertNotEqual(top(GOLDEN['side']), GOLDEN['top'])
        for bad in ({}, {'base': GOLDEN['base'], 'side': GOLDEN['side']}, {'side': GOLDEN['side']},
                    {'base': GOLDEN['base'].upper()}, {'base': GOLDEN['base'] + '\n'}, {'base': GOLDEN['base'][:-1]},
                    {'base': GOLDEN['base'] + '0'}):
            with self.subTest(bad=bad), self.assertRaises(PC.CacheError):
                PC.key_document(manifest(), 'top', facts(), bad)
        with self.assertRaises(PC.CacheError):
            PC.key_document(manifest(), 'base', facts(), {'side': GOLDEN['side']})

    def test_key_document_names_every_input_and_nothing_else(self):
        document = PC.key_document(manifest(), 'top', facts(), {'base': GOLDEN['base']})
        self.assertEqual(document, {
            'schema': 2, 'provider': 'top', 'recipe': 'takx/two', 'recipe_files': facts()['recipe_files'],
            'invocation_sha256': PC.sha256_bytes(PC.canonical({
                'invocation': manifest()['providers']['top']['invocation'], 'variables': {'jdk': '/jdk'},
                'wrapper': {'env': ['TAKX_PROTO_ROOT={root}', 'TAKX_PROTO_REPO={repo}', 'HOME=/tmp'],
                            'mounts': ['{repo}:{repo}:ro', '{root}:{root}'], 'workdir': '{root}'}})),
            'marker': 'complete.json', 'locks': facts()['locks'], 'fetched': facts()['fetched'],
            'toolchain': FINGERPRINT, 'arch': 'amd64', 'root': '/var/lib/takx-proto', 'repo': '/repo',
            'prerequisites': {'base': GOLDEN['base']}})
        source = facts()
        document = PC.key_document(manifest(), 'base', source, {})
        document['recipe_files']['x'] = document['locks']['x'] = 1
        document['fetched'].append(1)
        self.assertEqual(source, facts())  # the document is a copy

    def test_change_reaches_everything_downstream_and_nothing_else(self):
        document = chain()
        reference = self.keys(document)
        changed = facts(recipe_files={'takx/one/build.py': 'd' * 64})
        downstream = {'a': 'abcdef', 'b': 'bcdf', 'c': 'cd', 'd': 'd', 'e': 'ef', 'f': 'f', 'z': 'z'}
        for name, affected in downstream.items():
            with self.subTest(changed=name):
                got = self.keys(document, **{name: changed})
                self.assertEqual({other for other in got if got[other] != reference[other]}, set(affected))
        self.assertEqual(self.keys(document, names=['c']), {name: reference[name] for name in 'abc'})
        self.assertEqual(self.keys(document, names=['f']), {name: reference[name] for name in 'abef'})

    def test_closure_and_order_follow_the_prerequisites(self):
        providers = chain()['providers']
        self.assertEqual(PC.closure(providers, ['d']), set('abcd'))
        self.assertEqual(PC.closure(providers, ['f', 'z']), set('abefz'))
        self.assertEqual(PC.closure(providers, []), set())
        with self.assertRaises(PC.CacheError):
            PC.closure(providers, ['nope'])
        self.assertEqual(PC.order(providers), ['a', 'b', 'c', 'd', 'e', 'f', 'z'])
        self.assertEqual(PC.order(reordered(providers)), ['a', 'b', 'c', 'd', 'e', 'f', 'z'])
        self.assertEqual(PC.order(providers, ['z', 'd', 'a']), ['a', 'd', 'z'])
        self.assertEqual(PC.order(providers, []), [])
        providers['a']['prerequisites'] = ['a']
        with self.assertRaisesRegex(PC.CacheError, 'cycle at a'):
            PC.order(providers)
        providers['a']['prerequisites'] = ['nope']
        with self.assertRaisesRegex(PC.CacheError, 'unknown provider nope'):
            PC.order(providers)


class FakeRegistry:
    """In-memory registry port."""

    def __init__(self):
        self.images, self.logged_in, self.docker, self.published = {}, True, True, 0
        self.toolchain, self.state = TOOLCHAIN_ID, None
        self.probe_output, self.layer = probe_text(), BASE_LAYER
        self.calls, self.fetched, self.digests, self.kept = [], [], {}, []

    def available(self):
        return self.docker

    def image_id(self, image):
        self.calls.append(('image_id', image))
        return self.toolchain

    def probe_toolchain(self, image, script):
        self.calls.append(('probe_toolchain', image))
        self.script = script
        return self.probe_output

    def base_layer(self, image):
        return self.layer

    def digest(self, ref):
        self.calls.append(('digest', ref))
        return self.digests.get(ref)

    def forget(self, ref):
        self.calls.append(('forget', ref))
        self.kept.remove(ref)

    def adopt(self, source, ref, context, files, labels):
        self.calls.append(('adopt', source, ref))
        assert source in self.kept, 'adopt needs the source image kept by fetch'
        old_labels, content = self.images[source]
        content = dict(content)
        for name in files:
            content[name] = (Path(context) / name).read_bytes()
        self.store(ref, dict(old_labels, **labels), content)

    def store(self, ref, labels, content):
        """Put an image under a reference; every stored image gets a new digest."""
        self.published += 1
        self.images[ref] = (labels, content)
        self.digests[ref] = 'sha256:%064x' % self.published

    def probe(self, ref):
        self.calls.append(('probe', ref))
        if self.state:
            return self.state
        if not self.logged_in:
            return 'unauthorized'
        return 'present' if ref in self.images else 'absent'

    def publish(self, ref, context, files, labels):
        self.calls.append(('publish', ref))
        self.store(ref, dict(labels), {name: (Path(context) / name).read_bytes() for name in files})

    def fetch(self, ref, target, files, keep=False):
        self.calls.append(('fetch', ref))
        self.fetched.append((ref, list(files)))
        labels, content = self.images[ref]
        for name in files:
            if name not in content:
                raise PC.MissingFile('the image holds no /%s' % name, self.digests.get(ref))
            (Path(target) / name).write_bytes(content[name])
        if keep:
            self.kept.append(ref)
        return json.loads(json.dumps(labels)), self.digests.get(ref)  # new objects, as when docker prints them


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root, self.repo = base / 'root', base / 'repo'
        for recipe in ('one', 'two'):
            folder = self.repo / 'takx' / recipe
            (folder / '__pycache__').mkdir(parents=True)
            (folder / 'build.py').write_text('# %s\n' % recipe)
            (folder / 'other.py').write_text('# other\n')
            (folder / 'inputs.lock.json').write_text(json.dumps({'sha256': '5' * 64}))
            (folder / 'README.md').write_text('readme\n')
            (folder / 'test_build.py').write_text('tests\n')
            (folder / '__pycache__' / 'build.pyc').write_text('bytecode\n')
        (self.repo / 'takx/shared.py').write_text('# shared\n')
        (self.root / 'status').mkdir(parents=True)
        (self.root / 'P').mkdir()
        self.write_json('status/images.json', {'status': 'ok', 'image_ids': {PC.TOOLCHAIN: TOOLCHAIN_ID}})
        self.write_json('status/toolchain.json', toolchain_record())
        self.write_json('status/fetch.json', {'status': 'ok', 'inputs': [
            {'kind': 'lock-entry', 'name': recipe, 'verified': True, 'expected_sha256': '5' * 64,
             'consumers': [{'recipe': recipe}]} for recipe in ('one', 'two')]})
        self.registry = FakeRegistry()
        self.messages, self.builds = [], 0
        self.service = self.new_service()

    def new_service(self, prefix='reg.example/cache', legacy='', root=None, **options):
        return PC.ProviderCache(manifest(), PC.WorkTree(root or self.root, self.repo), self.registry, prefix,
                                legacy, self.messages.append, **options)

    def write_json(self, rel, value):
        (self.root / rel).write_text(json.dumps(value))

    def make(self, name, status='ok', marker=True):
        top = self.root / 'P' / name
        (top / 'install/lib/.libs').mkdir(parents=True)
        (top / 'empty').mkdir()
        self.builds += 1  # every build differs from the one before, the same way in every run
        (top / 'install/lib/lib.a').write_bytes(hashlib.sha256(b'%s %d' % (name.encode(), self.builds)).digest() * 94)
        (top / 'install/tool').write_text('#!/bin/sh\n')
        os.chmod(top / 'install/tool', 0o755)
        os.symlink('../lib.a', top / 'install/lib/.libs/lib.a')
        os.symlink('tool', top / 'install/tool-1')
        if marker:
            (top / 'complete.json').write_text(json.dumps({'provider': name, 'build': self.builds}))
        self.write_json('status/%s.json' % name, {'stage': name, 'status': status, 'outputs': [str(top)],
                                                  'seconds': 1, 'notes': 'n'})
        return top

    def set_aside(self, name):
        target = Path(self.tmp.name) / ('aside-%s-%d' % (name, len(os.listdir(self.tmp.name))))
        os.rename(self.root / 'P' / name, target)
        os.unlink(self.root / 'status' / (name + '.json'))
        return target

    def leftovers(self):
        return sorted(name for name in os.listdir(self.root) if name.startswith('.'))

    def bundle(self, name):
        return self.registry.images[self.service.ref(name)]

    def retar(self, name, mutate):
        """Rewrite the stored archive with consistent digests everywhere."""
        labels, content = self.bundle(name)
        source = tarfile.open(fileobj=io.BytesIO(content[name + '.tar']))
        target_bytes = io.BytesIO()
        with tarfile.open(fileobj=target_bytes, mode='w', format=tarfile.PAX_FORMAT) as target:
            for member in source:
                target.addfile(member, source.extractfile(member) if member.isreg() else None)
            mutate(target)
        content[name + '.tar'] = target_bytes.getvalue()
        digest = PC.sha256_bytes(content[name + '.tar'])
        content['tar.sha256'] = (digest + '\n').encode()
        labels['dev.uds.takx.tar-sha256'] = digest
        meta = json.loads(content['meta.json'])
        meta['tar_sha256'] = digest
        content['meta.json'] = json.dumps(meta).encode()

    def refused(self, name='base'):
        with self.assertRaises(PC.CacheError) as caught:
            self.service.restore(name)
        self.assertFalse(os.path.lexists(self.root / 'P' / name))
        self.assertFalse((self.root / 'status' / (name + '.json')).exists())
        self.assertEqual(self.leftovers(), [])
        self.assertNotIn('%s', str(caught.exception))
        return str(caught.exception)


class Keys(ServiceCase):
    def test_tests_readmes_and_bytecode_do_not_change_the_key(self):
        reference = self.service.key('base')
        (self.repo / 'takx/one/README.md').write_text('changed\n')
        (self.repo / 'takx/one/test_build.py').write_text('changed\n')
        (self.repo / 'takx/one/test_new.py').write_text('new\n')
        (self.repo / 'takx/one/__pycache__/other.pyc').write_text('new\n')
        self.assertEqual(self.new_service().key('base'), reference)

    def test_recipe_lock_helper_toolchain_and_root_change_the_key(self):
        reference = {name: self.service.key(name) for name in ('base', 'top', 'side')}
        (self.repo / 'takx/one/build.py').write_text('# changed\n')
        changed = self.new_service()
        self.assertTrue(all(changed.key(name) != reference[name] for name in reference))
        (self.repo / 'takx/one/build.py').write_text('# one\n')
        self.assertEqual(self.new_service().key('top'), reference['top'])
        (self.repo / 'takx/shared.py').write_text('# changed\n')
        changed = self.new_service()
        self.assertNotEqual(changed.key('top'), reference['top'])
        self.assertEqual(changed.key('base'), reference['base'])
        (self.repo / 'takx/shared.py').write_text('# shared\n')
        (self.repo / 'takx/one/new.c').write_text('int x;\n')
        self.assertNotEqual(self.new_service().key('base'), reference['base'])
        os.unlink(self.repo / 'takx/one/new.c')
        # The image id that schema 1 put into the key does not change one any more.
        self.assertEqual(self.new_service(legacy='sha256:' + '9' * 64).key('base'), reference['base'])
        self.write_json('status/images.json', {'status': 'ok', 'image_ids': {PC.TOOLCHAIN: 'sha256:' + '9' * 64}})
        self.write_json('status/toolchain.json', toolchain_record(image_id='sha256:' + '9' * 64))
        self.assertEqual(self.new_service().key('base'), reference['base'])
        # Another toolchain or another architecture does.
        for what, record in (('architecture', toolchain_record(arch='arm64')),
                             ('base image', toolchain_record(layer='sha256:' + 'c' * 64))):
            self.write_json('status/toolchain.json', record)
            with self.subTest(what=what):
                self.assertNotEqual(self.new_service().key('base'), reference['base'])
        self.write_json('status/toolchain.json', toolchain_record())
        other = Path(self.tmp.name).resolve() / 'other-root'
        os.rename(self.root, other)
        self.assertNotEqual(self.new_service(root=other).key('base'), reference['base'])

    def test_key_fails_closed_without_its_inputs(self):
        def no_fingerprint():
            os.unlink(self.root / 'status/toolchain.json')
            self.registry.docker = False

        for what, action in (
                ('toolchain.json and no docker', no_fingerprint),
                ('fetch.json', lambda: self.write_json('status/fetch.json', {'status': 'failed', 'inputs': []})),
                ('unverified input', lambda: self.write_json('status/fetch.json', {'status': 'ok', 'inputs': [
                    {'kind': 'lock-entry', 'name': r, 'verified': r == 'two', 'expected_sha256': '5' * 64,
                     'consumers': [{'recipe': r}]} for r in ('one', 'two')]})),
                ('lock', lambda: os.unlink(self.repo / 'takx/two/inputs.lock.json')),
                ('helper', lambda: os.unlink(self.repo / 'takx/shared.py'))):
            saved = {path: path.read_bytes() for path in (self.root / 'status/toolchain.json',
                                                          self.root / 'status/fetch.json',
                                                          self.repo / 'takx/two/inputs.lock.json',
                                                          self.repo / 'takx/shared.py')}
            action()
            with self.subTest(what=what), self.assertRaises(PC.CacheError):
                self.new_service().key('top')
            for path, data in saved.items():
                path.write_bytes(data)
            self.registry.docker = True
        self.assertEqual(self.registry.calls, [])  # a key from the record asks docker nothing
        # The image record of schema 1 is no input any more.
        reference = self.new_service().key('top')
        os.unlink(self.root / 'status/images.json')
        self.assertEqual(self.new_service().key('top'), reference)


class PushAndRestore(ServiceCase):
    def test_round_trip_restores_the_exact_tree_and_marks_the_status(self):
        top = self.make('base')
        before = PC.WorkTree.entries(top)
        original_status = json.loads((self.root / 'status/base.json').read_text())
        ref = self.service.push('base')
        self.assertEqual(ref, 'reg.example/cache/base:' + self.service.key('base'))
        self.assertEqual(self.leftovers(), [])
        self.set_aside('base')
        self.assertEqual(self.service.restore('base'), ref)
        self.assertEqual(PC.WorkTree.entries(top), before)
        self.assertEqual(os.readlink(top / 'install/lib/.libs/lib.a'), '../lib.a')
        self.assertTrue(os.access(top / 'install/tool', os.X_OK))
        self.assertTrue((top / 'empty').is_dir())
        status = json.loads((self.root / 'status/base.json').read_text())
        self.assertEqual(status, dict(original_status, restored_from=ref, cache_key=self.service.key('base')))
        self.assertEqual(self.leftovers(), [])

    def test_push_is_idempotent_and_recorded(self):
        self.make('base')
        self.service.push('base')
        self.service.push('base')
        self.assertEqual(self.registry.published, 1)
        key = self.service.key('base')
        self.assertEqual(list(self.service.tree.pushed()), [('base', key)])
        rows = {row['name']: row for row in self.service.rows()}
        self.assertEqual((rows['base']['local'], rows['base']['remote']), ('ok', 'pushed'))
        self.assertEqual((rows['top']['local'], rows['top']['remote'], rows['top']['tar_sha256']),
                         ('absent', 'unknown', '-'))
        self.assertEqual({row['name']: row['remote'] for row in self.service.rows(remote=True)},
                         {'base': 'present', 'side': 'absent', 'top': 'absent'})

    def test_push_only_for_ok_status_with_marker(self):
        self.make('base', status='failed')
        with self.assertRaises(PC.Skip):
            self.service.push('base')
        self.make('side', marker=False)
        with self.assertRaises(PC.CacheError):
            self.service.push('side')
        with self.assertRaises(PC.Skip):
            self.service.push('top')
        with self.assertRaises(PC.CacheError):
            self.service.push('ghost')
        self.assertEqual(self.registry.published, 0)

    def test_push_refuses_a_provider_that_is_a_symlink_or_holds_escaping_links(self):
        self.make('base')
        os.symlink('/etc', self.root / 'P/base/install/etc')
        with self.assertRaises(PC.CacheError):
            self.service.push('base')
        os.unlink(self.root / 'P/base/install/etc')
        os.mkfifo(self.root / 'P/base/pipe')
        with self.assertRaises(PC.CacheError):
            self.service.push('base')
        self.make('side')
        os.rename(self.root / 'P/side', self.root / 'P/real')
        os.symlink('real', self.root / 'P/side')
        with self.assertRaises(PC.CacheError):
            self.service.push('side')
        self.assertEqual(self.registry.published, 0)
        self.assertEqual(self.leftovers(), [])

    def test_off_no_docker_and_no_login_skip_cleanly(self):
        self.make('base')
        off = self.new_service(prefix='off')
        for action in (off.push, off.restore):
            with self.assertRaises(PC.Skip):
                action('base')
        self.registry.logged_in = False
        with self.assertRaises(PC.Skip):
            self.service.push('base')
        self.registry.logged_in, self.registry.docker = True, False
        with self.assertRaises(PC.Skip):
            self.service.push('base')
        self.set_aside('base')
        with self.assertRaises(PC.Skip):
            self.service.restore('base')
        self.registry.docker, self.registry.logged_in = True, False
        with self.assertRaises(PC.Skip):
            self.service.restore('base')
        self.assertEqual((self.registry.published, self.leftovers()), (0, []))

    def test_unreachable_registry_neither_pushes_nor_restores(self):
        self.make('base')
        self.registry.state = 'error'
        with self.assertRaises(PC.CacheError):
            self.service.push('base')
        self.set_aside('base')
        self.refused()
        self.assertEqual(self.registry.published, 0)

    def test_restore_misses_when_nothing_is_cached_or_the_recipe_changed(self):
        with self.assertRaises(PC.Skip):
            self.service.restore('base')
        self.make('base')
        self.service.push('base')
        self.set_aside('base')
        (self.repo / 'takx/one/build.py').write_text('# changed recipe\n')
        self.service = self.new_service()
        with self.assertRaises(PC.Skip):
            self.service.restore('base')
        self.assertFalse(os.path.lexists(self.root / 'P/base'))
        self.assertEqual(self.leftovers(), [])

    def test_restore_never_overwrites_an_existing_directory(self):
        top = self.make('base')
        self.service.push('base')
        before = PC.WorkTree.entries(top)
        with self.assertRaises(PC.Skip):  # present and ok: nothing to do
            self.service.restore('base')
        self.write_json('status/base.json', {'stage': 'base', 'status': 'failed'})
        with self.assertRaises(PC.CacheError):  # present and not ok: the lane sets it aside
            self.service.restore('base')
        self.assertEqual(PC.WorkTree.entries(top), before)
        self.assertEqual(json.loads((self.root / 'status/base.json').read_text())['status'], 'failed')
        self.write_json('status/base.json', {'stage': 'base', 'status': 'ok'})
        self.set_aside('base')
        (self.root / 'P/base').mkdir()
        with self.assertRaises(PC.CacheError):
            self.service.restore('base')
        self.assertEqual(os.listdir(self.root / 'P/base'), [])
        os.rmdir(self.root / 'P/base')
        os.symlink('/nonexistent', self.root / 'P/base')
        with self.assertRaises(PC.CacheError):
            self.service.restore('base')
        self.assertTrue(os.path.islink(self.root / 'P/base'))

    def test_rename_never_replaces_even_an_empty_directory(self):
        source, target = self.root / 'src-dir', self.root / 'dst-dir'
        source.mkdir()
        target.mkdir()
        with self.assertRaises(OSError):
            PC.rename_noreplace(source, target)
        self.assertTrue(source.is_dir())
        os.rmdir(target)
        PC.rename_noreplace(source, target)
        self.assertTrue(target.is_dir() and not source.exists())

    def test_directory_appearing_during_the_restore_is_kept(self):
        self.make('base')
        self.service.push('base')
        self.set_aside('base')
        unpack = self.service.tree.unpack

        def racing(path, target):
            unpack(path, target)
            (self.root / 'P/base').mkdir()
        self.service.tree.unpack = racing
        with self.assertRaises(PC.CacheError):
            self.service.restore('base')
        self.assertEqual(os.listdir(self.root / 'P/base'), [])
        self.assertEqual(self.leftovers(), [])

    def test_failed_status_write_takes_the_directory_back_out(self):
        self.make('base')
        self.service.push('base')
        self.set_aside('base')
        (self.root / 'status/base.json').mkdir()  # os.replace onto a directory fails
        with self.assertRaises((PC.CacheError, OSError)):
            self.service.restore('base')
        self.assertFalse(os.path.lexists(self.root / 'P/base'))
        self.assertEqual(self.leftovers(), [])

    def prepared(self):
        self.make('base')
        self.service.push('base')
        self.set_aside('base')
        return self.bundle('base')

    def test_corrupt_archive_is_refused(self):
        labels, content = self.prepared()
        data = bytearray(content['base.tar'])
        data[len(data) // 2] ^= 1
        content['base.tar'] = bytes(data)
        self.assertIn('archive digest', self.refused())

    def test_recorded_digest_label_and_metadata_must_agree(self):
        labels, content = self.prepared()
        saved = dict(content), dict(labels)
        content['tar.sha256'] = b'0' * 64 + b'\n'
        self.refused()
        content.update(saved[0])
        labels['dev.uds.takx.tar-sha256'] = '0' * 64
        self.refused()
        labels.update(saved[1])
        labels['dev.uds.takx.key'] = '0' * 64
        self.refused()
        labels.update(saved[1])
        content['files.json'] = content['files.json'].replace(b'0755', b'0700')
        self.assertIn('file manifest', self.refused())
        content.update(saved[0])
        content['status.json'] = json.dumps({'stage': 'base', 'status': 'failed'}).encode()
        self.refused()
        content.update(saved[0])
        content['meta.json'] = b'not json'
        self.refused()
        content.update(saved[0])
        self.service.restore('base')

    def test_archive_that_differs_from_the_file_manifest_is_refused(self):
        self.prepared()

        def extra(target):
            info = tarfile.TarInfo('base/install/planted.so')
            info.size = 4
            target.addfile(info, io.BytesIO(b'evil'))
        self.retar('base', extra)
        self.assertIn('unexpected install/planted.so', self.refused())

    def test_archive_with_escaping_members_is_refused_before_unpacking(self):
        def member(name, kind=tarfile.REGTYPE, link=''):
            def mutate(target):
                info = tarfile.TarInfo(name)
                info.type, info.linkname = kind, link
                data = b'evil' if kind == tarfile.REGTYPE else b''
                info.size = len(data)
                target.addfile(info, io.BytesIO(data) if data else None)
            return mutate
        outside = Path(self.tmp.name) / 'escaped'
        for what, mutate in (
                ('dot-dot', member('base/../../escaped')),
                ('absolute', member(str(outside))),
                ('other top', member('other/escaped')),
                ('absolute symlink', member('base/evil', tarfile.SYMTYPE, str(outside))),
                ('escaping symlink', member('base/evil', tarfile.SYMTYPE, '../../escaped')),
                ('hard link', member('base/evil', tarfile.LNKTYPE, '../escaped')),
                ('device', member('base/evil', tarfile.CHRTYPE)),
                ('fifo', member('base/evil', tarfile.FIFOTYPE))):
            with self.subTest(what=what):
                self.registry.images.clear()
                if os.path.lexists(self.root / 'P/base'):
                    self.set_aside('base')
                self.prepared()
                self.retar('base', mutate)
                self.refused()
                self.assertFalse(os.path.lexists(outside))
                self.assertFalse(os.path.lexists(self.root / 'escaped'))

    def test_missing_or_changed_marker_is_refused(self):
        labels, content = self.prepared()
        meta = json.loads(content['meta.json'])
        meta['marker_sha256'] = '0' * 64
        content['meta.json'] = json.dumps(meta).encode()
        self.assertIn('marker', self.refused())
        # A bundle pushed without its marker in the tree (and a consistent manifest).
        self.registry.images.clear()
        top = self.make('side')
        self.service.push('side')
        labels, content = self.bundle('side')
        os.unlink(top / 'complete.json')
        entries = PC.WorkTree.entries(top)
        tar = Path(self.tmp.name) / 'side.tar'
        PC.WorkTree.write_tar(str(top), 'side', entries, tar)
        self.set_aside('side')
        content['side.tar'] = tar.read_bytes()
        digest = PC.sha256_bytes(content['side.tar'])
        content['files.json'] = json.dumps({'schema': 1, 'provider': 'side', 'entries': entries}).encode()
        content['tar.sha256'] = digest.encode()
        labels['dev.uds.takx.tar-sha256'] = digest
        meta = json.loads(content['meta.json'])
        meta.update(tar_sha256=digest, files_sha256=PC.sha256_bytes(content['files.json']))
        content['meta.json'] = json.dumps(meta).encode()
        self.assertIn('completion marker', self.refused('side'))

    def test_image_file_that_is_not_a_regular_file_is_refused(self):
        self.prepared()
        fetch = self.registry.fetch

        def linked(ref, target, files):
            labels = fetch(ref, target, files)
            os.unlink(Path(target) / 'status.json')
            os.symlink('/etc/hostname', Path(target) / 'status.json')
            return labels
        self.registry.fetch = linked
        self.assertIn('regular file', self.refused())

    def test_downstream_is_bound_to_the_prerequisite_outputs_it_was_built_against(self):
        self.make('base')
        self.make('top')
        self.service.push('top')
        self.set_aside('top')
        # No prerequisite here: nothing to check against, so build instead.
        aside = self.set_aside('base')
        with self.assertRaises(PC.Skip):
            self.service.restore('top')
        # Another build of the prerequisite (same key, other bytes): refused.
        self.make('base')
        self.assertIn('prerequisite', self.refused('top'))
        # The very outputs it was built against: restored.
        self.set_aside('base')
        os.rename(aside, self.root / 'P/base')
        self.write_json('status/base.json', {'stage': 'base', 'status': 'ok'})
        self.service.restore('top')
        self.assertTrue((self.root / 'P/top/complete.json').is_file())

    def test_push_needs_ok_prerequisites(self):
        self.make('top')
        with self.assertRaises(PC.CacheError):
            self.service.push('top')
        self.make('base', status='failed')
        with self.assertRaises(PC.CacheError):
            self.service.push('top')
        self.assertEqual(self.registry.published, 0)

    def test_one_provider_without_its_inputs_does_not_take_the_cache_from_the_others(self):
        self.write_json('status/fetch.json', {'status': 'ok', 'inputs': [
            {'kind': 'lock-entry', 'name': 'one', 'verified': True, 'expected_sha256': '5' * 64,
             'consumers': [{'recipe': 'one'}]}]})
        self.service = self.new_service()
        self.make('base')
        self.service.push('base')
        with self.assertRaises(PC.CacheError):
            self.service.key('top')
        rows = {row['name']: row for row in self.service.rows()}
        self.assertEqual((rows['top']['key'], rows['top']['remote']), ('-', 'no-key'))
        self.assertEqual(rows['base']['remote'], 'pushed')
        self.set_aside('base')
        self.service.restore('base')

    def test_push_that_the_registry_does_not_show_afterwards_is_an_error(self):
        self.make('base')
        self.registry.publish = lambda ref, context, files, labels: None
        with self.assertRaises(PC.CacheError):
            self.service.push('base')
        self.assertEqual(self.service.tree.pushed(), {})
        self.assertEqual(self.leftovers(), [])

    def test_a_rebuilt_toolchain_image_keeps_the_keys_and_another_toolchain_is_refused(self):
        self.make('base')
        key = self.service.key('base')
        # The same toolchain under another image id: probed once, then recorded.
        self.registry.toolchain = 'sha256:' + '7' * 64
        rebuilt = self.new_service()
        ref = rebuilt.push('base')
        self.assertEqual(ref, 'reg.example/cache/base:' + key)
        self.assertEqual(self.registry.calls[:2], [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)])
        record = json.loads((self.root / 'status/toolchain.json').read_text())
        self.assertEqual((record['image_id'], record['fingerprint']), ('sha256:' + '7' * 64, FINGERPRINT))
        del self.registry.calls[:]
        self.new_service().push('base')
        self.assertNotIn(('probe_toolchain', PC.TOOLCHAIN), self.registry.calls)
        # Another toolchain, or none: nothing is read from or written to the cache.
        self.registry.toolchain, self.registry.layer = 'sha256:' + '8' * 64, 'sha256:' + 'c' * 64
        for action in (self.new_service().push, self.new_service().restore, self.new_service().migrate):
            del self.registry.calls[:]
            with self.assertRaisesRegex(PC.CacheError, 'has toolchain fingerprint [0-9a-f]{64}, not the recorded '
                                        + FINGERPRINT):
                action('top' if action.__name__ != 'push' else 'base')
            self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)])
        self.registry.toolchain = None
        with self.assertRaisesRegex(PC.CacheError, '^there is no local takx-desktop-proto:toolchain image'):
            self.new_service().push('base')
        self.assertEqual(self.registry.published, 1)
        self.assertEqual(json.loads((self.root / 'status/toolchain.json').read_text()), record)


class CommandLine(ServiceCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        code = PC.main(list(argv), service=self.service, out=out)
        return code, out.getvalue()

    def test_key_prints_one_key_or_a_table(self):
        code, text = self.run_cli('key', 'top')
        self.assertEqual((code, text), (0, self.service.key('top') + '\n'))
        code, text = self.run_cli('key')
        self.assertEqual(code, 0)
        self.assertEqual([line.split('\t')[0] for line in text.splitlines()], ['base', 'side', 'top'])
        code, text = self.run_cli('key', '--explain', 'top')
        document = json.loads(text)
        self.assertEqual(PC.derive_key(document['inputs']), document['key'])
        self.assertEqual(document['inputs']['prerequisites'], {'base': self.service.key('base')})
        self.assertEqual(self.run_cli('key', 'ghost')[0], 1)

    def test_exit_status_distinguishes_done_skipped_and_failed(self):
        self.assertEqual(self.run_cli('restore', 'base')[0], 3)
        self.assertEqual(self.run_cli('push', 'base')[0], 3)
        self.make('base')
        self.assertEqual(self.run_cli('push', 'base')[0], 0)
        self.assertEqual(self.run_cli('restore', 'base')[0], 3)
        os.unlink(self.root / 'status/base.json')
        self.assertEqual(self.run_cli('restore', 'base')[0], 1)
        self.write_json('status/base.json', {'stage': 'base', 'status': 'ok'})
        self.set_aside('base')
        self.assertEqual(self.run_cli('restore', 'base')[0], 0)
        self.assertEqual(self.run_cli('push')[0], 3)
        self.assertEqual(self.run_cli('push', 'ghost')[0], 1)

    def test_status_lists_every_provider_and_writes_the_index(self):
        self.make('base')
        self.service.push('base')
        index = Path(self.tmp.name) / 'index.tsv'
        code, text = self.run_cli('status', '--write-index', str(index))
        self.assertEqual(code, 0)
        self.assertEqual([line.split('\t')[0] for line in text.splitlines()], ['base', 'side', 'top'])
        lines = index.read_text().splitlines()
        self.assertEqual(lines[:2], ['# Copyright 2026 uds-takserver authors',
                                     '# SPDX-License-Identifier: GPL-3.0-only'])
        rows = [line.split('\t') for line in lines if not line.startswith('#')]
        self.assertEqual([row[:3] for row in rows],
                         [['base', self.service.key('base'), self.service.ref('base')]])
        self.assertRegex(rows[0][3], r'^[0-9a-f]{64}$')

    def test_environment_selects_root_registry_and_toolchain(self):
        service = PC.build_service({'TAKX_PROTO_ROOT': str(self.root), 'TAKX_PROTO_CACHE': 'off',
                                    'TAKX_PROTO_TOOLCHAIN_ID': 'sha256:' + '3' * 64})
        self.assertEqual((service.tree.root, service.enabled, service.legacy_id),
                         (self.root, False, 'sha256:' + '3' * 64))
        self.assertEqual(service.tree.repo, REPO)
        default = PC.build_service({})
        self.assertEqual((str(default.tree.root), default.prefix), (PC.DEFAULT_ROOT, PC.DEFAULT_REGISTRY))


class KeySensitivity(ServiceCase):
    """Single changes on disk, and which providers get another key for them."""

    def setUp(self):
        super().setUp()
        (self.repo / 'takx/one/src/deep').mkdir(parents=True)
        (self.repo / 'takx/one/src/deep/a.c').write_text('int a;\n')
        (self.repo / 'takx/one/src/deep/README').write_text('notes\n')
        os.symlink('build.py', self.repo / 'takx/one/alias.py')
        (self.repo / 'pins').mkdir()
        (self.repo / 'pins/base.lock.json').write_text(json.dumps({'archive': {'sha256': '6' * 64}}))
        self.write_json('status/fetch.json', {'status': 'ok', 'inputs': self.records()})

    def records(self):
        return [{'kind': 'lock-entry', 'name': 'one', 'verified': True, 'expected_sha256': '5' * 64,
                 'bytes': 10, 'url': 'https://example.invalid/one', 'consumers': [{'recipe': 'one'}]},
                {'kind': 'lock-entry', 'name': 'two', 'verified': True, 'expected_sha256': '5' * 64,
                 'consumers': [{'recipe': 'two'}]},
                {'kind': 'git-source', 'name': 'tree', 'verified': True, 'expected_revision': '3' * 40,
                 'path': str(self.root / 'inputs/tree')},
                {'kind': 'toolchain', 'name': 'jdk', 'verified': True, 'expected_sha256': '4' * 64},
                {'kind': 'lock-entry', 'name': 'unrelated', 'verified': True, 'expected_sha256': '7' * 64,
                 'consumers': [{'recipe': 'three'}]}]

    def document(self):
        value = manifest()
        value['providers']['base']['locks'] = ['pins/base.lock.json']
        value['providers']['side']['fetched'] = [{'kind': 'toolchain'}]
        value['providers']['top']['fetched'] = [{'consumer': 'two'}, {'path': 'inputs/tree'}]
        return value

    def new_service(self, prefix='reg.example/cache', legacy='', root=None, document=None, repo=None):
        return PC.ProviderCache(document or self.document(), PC.WorkTree(root or self.root, repo or self.repo),
                                self.registry, prefix, legacy, self.messages.append)

    def changed(self, action=None, **service):
        """The providers whose key differs after `action` (and/or with another service)."""
        reference = {name: self.new_service().key(name) for name in ('base', 'side', 'top')}
        if action:
            action()
        after = self.new_service(**service)
        return ''.join(sorted(name[0] for name in reference if after.key(name) != reference[name]))

    def records_with(self, index, **fields):
        records = self.records()
        records[index].update(fields)
        return lambda: self.write_json('status/fetch.json', {'status': 'ok', 'inputs': records})

    def single_changes(self):
        repo = self.repo

        def flip(path):
            def action():
                data = bytearray(path.read_bytes())
                data[-2] ^= 1
                path.write_bytes(bytes(data))
            return action

        def retarget():
            os.unlink(repo / 'takx/one/alias.py')
            os.symlink('other.py', repo / 'takx/one/alias.py')
        cases = (  # what, action, initials of the providers that must get another key
            ('one recipe byte', flip(repo / 'takx/one/build.py'), 'bst'),
            ('one byte of the other script of the recipe', flip(repo / 'takx/one/other.py'), 'bst'),
            ('one byte in a nested recipe file', flip(repo / 'takx/one/src/deep/a.c'), 'bst'),
            ('one byte in the downstream recipe', flip(repo / 'takx/two/build.py'), 't'),
            ('one byte in a helper file', flip(repo / 'takx/shared.py'), 't'),
            ('one appended byte', lambda: (repo / 'takx/one/build.py').write_text('# one\n\n'), 'bst'),
            ('added recipe file', lambda: (repo / 'takx/one/new.c').write_text(''), 'bst'),
            ('added nested recipe file', lambda: (repo / 'takx/one/src/deep/b.c').write_text(''), 'bst'),
            ('removed recipe file', lambda: os.unlink(repo / 'takx/one/other.py'), 'bst'),
            ('removed nested recipe file', lambda: os.unlink(repo / 'takx/one/src/deep/a.c'), 'bst'),
            ('renamed recipe file', lambda: os.rename(repo / 'takx/one/other.py', repo / 'takx/one/another.py'), 'bst'),
            ('moved recipe file', lambda: os.rename(repo / 'takx/one/src/deep/a.c', repo / 'takx/one/src/a.c'), 'bst'),
            ('added link', lambda: os.symlink('build.py', repo / 'takx/one/second.py'), 'bst'),
            ('link to a directory added', lambda: os.symlink('src', repo / 'takx/one/sources'), 'bst'),
            ('link target', retarget, 'bst'),
            ('link replaced by a copy', lambda: (os.unlink(repo / 'takx/one/alias.py'),
                                                 (repo / 'takx/one/alias.py').write_text('# one\n')), 'bst'),
            ('lock digest', lambda: (repo / 'pins/base.lock.json').write_text(
                json.dumps({'archive': {'sha256': '6' * 63 + '7'}})), 'bt'),
            ('lock bytes without a digest change', lambda: (repo / 'pins/base.lock.json').write_text(
                json.dumps({'archive': {'sha256': '6' * 64}}) + '\n'), 'bt'),
            ('lock inside the recipe', flip(repo / 'takx/two/inputs.lock.json'), 't'),
            ('fetched digest', self.records_with(0, expected_sha256='5' * 63 + '6'), 'bt'),
            ('fetched actual digest', self.records_with(0, actual_sha256='5' * 64), 'bt'),
            ('fetched name', self.records_with(0, name='uno'), 'bt'),
            ('fetched revision selected by path', self.records_with(2, expected_revision='3' * 39 + '4'), 't'),
            ('fetched input selected by kind', self.records_with(3, expected_sha256='4' * 63 + '5'), 's'),
            ('toolchain of the other architecture',
             lambda: self.write_json('status/toolchain.json', toolchain_record(arch='arm64')), 'bst'),
            ('toolchain over another base layer',
             lambda: self.write_json('status/toolchain.json', toolchain_record(layer=BASE_LAYER[:-1] + 'c')), 'bst'),
        )
        return cases

    def test_single_changes(self):
        for index in range(len(self.single_changes())):
            what, action, expected = self.single_changes()[index]  # built for this fixture
            with self.subTest(what=what):
                self.assertEqual(self.changed(action), expected)
            self.doCleanups()
            self.setUp()

    def changes_that_are_not_inputs(self):
        repo = self.repo
        return (
            ('readme', lambda: (repo / 'takx/one/README.md').write_text('changed\n')),
            ('readme removed', lambda: os.unlink(repo / 'takx/one/README.md')),
            ('nested readme', lambda: (repo / 'takx/one/src/deep/README').write_text('changed\n')),
            ('readme added', lambda: (repo / 'takx/two/README.rst').write_text('new\n')),
            ('test changed', lambda: (repo / 'takx/one/test_build.py').write_text('changed\n')),
            ('test added', lambda: (repo / 'takx/one/src/test_more.py').write_text('new\n')),
            ('test removed', lambda: os.unlink(repo / 'takx/two/test_build.py')),
            ('bytecode', lambda: (repo / 'takx/one/__pycache__/build.pyc').write_text('changed\n')),
            ('bytecode added', lambda: (repo / 'takx/one/__pycache__/new.cpython-313.pyc').write_text('new\n')),
            ('bytecode outside the cache directory', lambda: (repo / 'takx/one/src/build.pyc').write_text('new\n')),
            ('nested bytecode directory', lambda: ((repo / 'takx/one/src/deep/__pycache__').mkdir(),
                                                   (repo / 'takx/one/src/deep/__pycache__/a.txt').write_text('x'))),
            ('bytecode directory removed', lambda: (os.unlink(repo / 'takx/one/__pycache__/build.pyc'),
                                                    os.rmdir(repo / 'takx/one/__pycache__'))),
            ('empty directory added', lambda: (repo / 'takx/one/src/empty').mkdir()),
            ('file time', lambda: os.utime(repo / 'takx/one/build.py', (1, 1))),
            ('file outside the recipes', lambda: (repo / 'takx/unrelated.py').write_text('new\n')),
            ('unrelated fetched input', self.records_with(4, expected_sha256='8' * 64)),
            ('fetched size and url', self.records_with(0, bytes=11, url='https://mirror.invalid/one')),
            ('order of the fetch record', lambda: self.write_json('status/fetch.json', {
                'status': 'ok', 'inputs': self.records()[::-1], 'seconds': 3})),
            ('another image in the image record', lambda: self.write_json('status/images.json', {
                'status': 'ok', 'image_ids': {'other:image': 'sha256:' + '9' * 64, PC.TOOLCHAIN: TOOLCHAIN_ID}})),
            ('toolchain image id in the image record', lambda: self.write_json('status/images.json', {
                'status': 'ok', 'image_ids': {PC.TOOLCHAIN: 'sha256:' + '1' * 63 + '2'}})),
            ('no image record', lambda: os.unlink(self.root / 'status/images.json')),
            ('the same toolchain rebuilt as another image', lambda: self.write_json(
                'status/toolchain.json', toolchain_record(image_id='sha256:' + '1' * 63 + '2'))),
            ('what the probe saw beside the gates', lambda: self.write_json('status/toolchain.json', toolchain_record(
                observed={'gcc': 'gcc (Debian 14.2.0-20) 14.2.0'}, packages_sha256='f' * 64, outputs_before=5))),
            ('provider outputs and status', lambda: self.make('base')),
            ('push records', lambda: self.service.tree.record_push(('base', 'k', 'r', 't'))),
        )

    def test_changes_that_are_not_inputs(self):
        for index in range(len(self.changes_that_are_not_inputs())):
            what, action = self.changes_that_are_not_inputs()[index]
            with self.subTest(what=what):
                self.assertEqual(self.changed(action), '')
            self.doCleanups()
            self.setUp()

    def test_identity_of_toolchain_root_and_repository(self):
        self.assertEqual(self.changed(legacy=TOOLCHAIN_ID), '')
        self.assertEqual(self.changed(legacy='sha256:' + '9' * 64), '')  # an image id is no key input any more
        other = self.root.with_name('root2')

        def moved():  # the same work root under another path, with its fetch record
            os.rename(self.root, other)
            record = other / 'status/fetch.json'
            record.write_text(record.read_text().replace(str(self.root) + '/', str(other) + '/'))
        self.assertEqual(self.changed(moved, root=other), 'bst')
        shutil.rmtree(other)
        self.doCleanups()
        self.setUp()
        elsewhere = self.repo.with_name('repo2')
        self.assertEqual(self.changed(lambda: os.rename(self.repo, elsewhere), repo=elsewhere), 'bst')
        os.rename(elsewhere, self.repo)
        self.assertEqual(self.changed(root=str(self.root) + '/'), '')  # one work root, one spelling

    def test_manifest_changes(self):
        def edited(edit):
            document = self.document()
            edit(document)
            return self.changed(document=document)
        self.assertEqual(edited(lambda m: m['providers']['base']['invocation']['argv'].append('-j4')), 'bt')
        self.assertEqual(edited(lambda m: m['providers']['top']['invocation']['argv'].append('-j4')), 't')
        self.assertEqual(edited(lambda m: m['providers']['side']['invocation']['env'].append('A=1')), 's')
        self.assertEqual(edited(lambda m: m['variables'].update(jdk='/jdk18')), 'bst')
        self.assertEqual(edited(lambda m: m['providers']['base'].update(marker='done.json')), 'bt')
        self.assertEqual(edited(lambda m: m['providers']['side'].update(locks=['pins/base.lock.json'])), 's')
        self.assertEqual(edited(lambda m: m['providers']['side'].update(prerequisites=['base'])), 's')
        self.assertEqual(edited(lambda m: m['recipes']['takx/one'].update(uses=['takx/shared.py'])), 'bst')
        self.assertEqual(edited(lambda m: m['recipe_exclude'].remove('README*')), 'bst')
        self.assertEqual(edited(lambda m: m['recipe_exclude'].append('*.c')), 'bst')
        self.assertEqual(edited(lambda m: m['providers']['base'].update(lane='lane7.sh')), '')
        self.assertEqual(edited(lambda m: m['providers']['top'].update(
            fetched=[{'path': 'inputs/tree'}, {'consumer': 'two'}, {'consumer': 'two'}])), '')
        self.assertEqual(edited(lambda m: m['recipes']['takx/two'].update(uses=['takx/shared.py', 'takx/one'])), '')

    def test_helpers_are_followed_through_other_recipes(self):
        document = self.document()
        document['recipes']['takx/three'] = {'uses': ['takx/two']}
        document['providers']['side']['recipe'] = 'takx/three'
        (self.repo / 'takx/three').mkdir()
        (self.repo / 'takx/three/build.py').write_text('# three\n')
        before = self.new_service(document=document).explain('side')
        self.assertEqual(sorted(before['recipe_files']), [
            'takx/one/alias.py', 'takx/one/build.py', 'takx/one/inputs.lock.json', 'takx/one/other.py',
            'takx/one/src/deep/a.c', 'takx/shared.py', 'takx/three/build.py', 'takx/two/build.py',
            'takx/two/inputs.lock.json', 'takx/two/other.py'])
        self.assertEqual(before['recipe_files']['takx/one/alias.py'], 'link:build.py')
        self.assertEqual(before['recipe_files']['takx/one/build.py'], hashlib.sha256(b'# one\n').hexdigest())
        (self.repo / 'takx/one/src/deep/a.c').write_text('int b;\n')
        self.assertNotEqual(self.new_service(document=document).key('side'), PC.derive_key(before))

    def test_explained_inputs_are_what_is_on_disk(self):
        document = self.new_service()
        inputs = document.explain('top')
        self.assertEqual(inputs['locks'], {'takx/two/inputs.lock.json': {
            'sha256': hashlib.sha256(json.dumps({'sha256': '5' * 64}).encode()).hexdigest(),
            'digests': ['5' * 64]}})
        self.assertEqual(inputs['fetched'], [
            {'kind': 'git-source', 'name': 'tree', 'digests': {'expected_revision': '3' * 40}},
            {'kind': 'lock-entry', 'name': 'two', 'digests': {'expected_sha256': '5' * 64}}])
        self.assertEqual((inputs['toolchain'], inputs['arch'], inputs['root'], inputs['repo']),
                         (FINGERPRINT, 'amd64', str(self.root), str(self.repo)))
        self.assertEqual(inputs['prerequisites'], {'base': document.key('base')})
        self.assertEqual(PC.derive_key(inputs), document.key('top'))
        self.assertEqual(document.ref('top'), 'reg.example/cache/top:' + document.key('top'))

    def test_recipe_paths_that_cannot_be_hashed_are_refused(self):
        tree = PC.WorkTree(self.root, self.repo)
        (self.repo / 'takx/empty/__pycache__').mkdir(parents=True)
        (self.repo / 'takx/empty/README').write_text('only this\n')
        os.mkfifo(self.repo / 'takx/pipe')
        for spec, message in (('takx/missing', 'does not exist'), ('takx/empty', 'is empty'),
                              ('takx/*.nothing', 'no recipe file matches'), ('takx/pipe', 'does not exist'),
                              ('takx/pi?e', 'not a regular file'), ('takx/on[e]', 'not a regular file')):
            with self.subTest(spec=spec), self.assertRaisesRegex(PC.CacheError, message):
                tree.recipe_files([spec], ['__pycache__', 'README*'])
        self.assertEqual(tree.recipe_files(['takx/sh*.py', 'takx/one/build.py', 'takx/one/alias.py'], []), {
            'takx/one/alias.py': 'link:build.py', 'takx/one/build.py': hashlib.sha256(b'# one\n').hexdigest(),
            'takx/shared.py': hashlib.sha256(b'# shared\n').hexdigest()})
        with self.assertRaisesRegex(PC.CacheError, 'recipe directory takx/two is empty'):
            tree.recipe_files(['takx/one/build.py', 'takx/two'], ['*'])
        # An explicitly named file is an input even when its name is excluded in directories.
        self.assertEqual(list(tree.recipe_files(['takx/one/README.md'], ['README*'])), ['takx/one/README.md'])
        with self.assertRaisesRegex(PC.CacheError, 'cannot be read'):
            tree.locks(['takx/missing.lock'])
        self.assertEqual(tree.locks([]), {})

    def test_fetch_and_image_records_must_be_ok(self):
        for what, rel, value in (
                ('failed fetch', 'status/fetch.json', {'status': 'failed', 'inputs': self.records()}),
                ('fetch without inputs', 'status/fetch.json', {'status': 'ok', 'inputs': {}}),
                ('fetch without status', 'status/fetch.json', {'inputs': self.records()})):
            saved = (self.root / rel).read_bytes()
            self.write_json(rel, value)
            with self.subTest(what=what), self.assertRaises(PC.CacheError):
                self.new_service().key('base')
            (self.root / rel).write_bytes(saved)
        (self.root / 'status/fetch.json').write_text('{not json')
        with self.assertRaisesRegex(PC.CacheError, 'status/fetch.json cannot be read'):
            self.new_service().key('base')
        self.write_json('status/fetch.json', {'status': 'ok', 'inputs': self.records()})
        os.unlink(self.root / 'status/images.json')
        self.assertRegex(self.new_service().key('base'), '^[0-9a-f]{64}$')


class Tamper(ServiceCase):
    """Every way a cached bundle can be wrong: refused, and nothing left behind."""

    def setUp(self):
        super().setUp()
        self.top = self.make('base')
        self.good = snapshot(self.top)
        self.ref = self.service.push('base')
        self.set_aside('base')
        self.before = settled(self.tmp.name)
        self.stored = copy.deepcopy(self.registry.images[self.ref])

    def pristine(self):
        labels, content = self.registry.images[self.ref]
        labels.clear()
        labels.update(self.stored[0])
        content.clear()
        content.update(self.stored[1])

    def reseal(self, members=None, entries=None, marker=None):
        """Edit the stored archive and/or file manifest; every digest agrees again."""
        labels, content = self.registry.images[self.ref]
        meta = json.loads(content['meta.json'])
        if members:
            source = tarfile.open(fileobj=io.BytesIO(content['base.tar']))
            items = members([(member, source.extractfile(member).read() if member.isreg() else None)
                             for member in source])
            data = io.BytesIO()
            with tarfile.open(fileobj=data, mode='w', format=tarfile.PAX_FORMAT) as target:
                for member, payload in items:
                    member.size = len(payload) if payload is not None else 0
                    target.addfile(member, io.BytesIO(payload) if payload is not None else None)
            content['base.tar'] = data.getvalue()
        if entries:
            listing = json.loads(content['files.json'])
            listing['entries'] = entries(listing['entries'])
            content['files.json'] = json.dumps(listing).encode()
            meta['files_sha256'] = hashlib.sha256(content['files.json']).hexdigest()
        if marker is not None:
            meta['marker_sha256'] = hashlib.sha256(marker).hexdigest()
        digest = hashlib.sha256(content['base.tar']).hexdigest()
        content['tar.sha256'] = (digest + '\n').encode()
        labels['dev.uds.takx.tar-sha256'] = meta['tar_sha256'] = digest
        content['meta.json'] = json.dumps(meta).encode()

    def assert_refused(self, message=None, errors=None, noted='bundle'):
        (self.root / 'cache-state/refused.json').unlink(missing_ok=True)
        with self.assertRaises(errors or PC.CacheError) as caught:
            self.service.restore('base')
        self.assertEqual(settled(self.tmp.name), self.before)  # no directory, no status, no scratch, no escape
        # The one thing a refusal leaves: which entry was judged, for the push after the rebuild.
        notes = self.service.tree.refusals()
        self.assertEqual({ref: (note['kind'], note['digest'], note['key']) for ref, note in notes.items()},
                         {self.ref: (noted, self.registry.digests[self.ref], self.service.key('base'))} if noted
                         else {})
        self.assertFalse(os.path.lexists(self.root / 'P/base'))
        self.assertEqual(sorted(os.listdir(self.root / 'status')), ['fetch.json', 'images.json', 'toolchain.json'])
        self.assertEqual(os.listdir(self.root / 'P'), [])
        if message:
            self.assertIn(message, str(caught.exception))
        self.assertNotIn('%s', str(caught.exception))

    @staticmethod
    def member(name, kind=tarfile.REGTYPE, link='', mode=0o644):
        info = tarfile.TarInfo(name)
        info.type, info.linkname, info.mode = kind, link, mode
        return info

    @staticmethod
    def file_entry(path, data, mode='0644'):
        return {'path': path, 'type': 'file', 'mode': mode, 'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest()}

    def edit_file(self, name, change):
        return lambda items: [(member, change(member, data) if member.name == name else data)
                              for member, data in items]

    def test_resealed_but_unchanged_bundle_is_restored(self):
        self.reseal(members=lambda items: items, entries=lambda entries: entries)
        self.assertEqual(self.service.restore('base'), self.ref)
        self.assertEqual(snapshot(self.top), self.good)

    def test_archive_digest_mismatch(self):
        labels, content = self.registry.images[self.ref]
        original = dict(content)
        for what, position in (('first byte', 0), ('middle', len(original['base.tar']) // 2),
                               ('last byte', len(original['base.tar']) - 1)):
            data = bytearray(original['base.tar'])
            data[position] ^= 0x01
            content['base.tar'] = bytes(data)
            with self.subTest(what=what):
                self.assert_refused('archive digest differs from the recorded one')
        content['base.tar'] = original['base.tar'] + b'\0' * 512
        self.assert_refused('archive digest differs from the recorded one')
        content['base.tar'] = original['base.tar'][:-512]
        self.assert_refused('archive digest differs from the recorded one')
        content['base.tar'] = b''
        self.assert_refused('archive digest differs from the recorded one')
        content.update(original)
        for what, value in (('other digest', b'0' * 64 + b'\n'), ('empty', b''), ('upper case', None),
                            ('with a name', None), ('not ascii', b'\xff\xfe' + original['tar.sha256']),
                            ('two digests', original['tar.sha256'] * 2)):
            digest = original['tar.sha256'].strip()
            content['tar.sha256'] = value if value is not None else (
                digest.upper() if what == 'upper case' else digest + b'  base.tar\n')
            with self.subTest(what=what):
                self.assert_refused('archive digest')
        content.update(original)
        self.service.restore('base')

    def test_label_and_metadata_mismatch(self):
        labels, content = self.registry.images[self.ref]
        original = dict(labels), dict(content)
        meta = json.loads(content['meta.json'])
        key = self.service.key('base')

        def with_meta(**fields):
            value = dict(meta, **fields)
            for field in [field for field in value if value[field] is ...]:
                del value[field]
            content['meta.json'] = json.dumps(value).encode()

        def with_label(label, value):
            if value is None:
                del labels[label]
            else:
                labels[label] = value
        cases = (
            ('label digest', lambda: with_label('dev.uds.takx.tar-sha256', '0' * 64), 'image label'),
            ('no label digest', lambda: with_label('dev.uds.takx.tar-sha256', None), 'image label'),
            ('label provider', lambda: with_label('dev.uds.takx.provider', 'side'), 'another provider'),
            ('no label provider', lambda: with_label('dev.uds.takx.provider', None), 'another provider'),
            ('label key', lambda: with_label('dev.uds.takx.key', key[:-1] + ('0' if key[-1] != '0' else '1')),
             'another key'),
            ('no label key', lambda: with_label('dev.uds.takx.key', None), 'another key'),
            ('schema of the past', lambda: with_meta(schema=1), 'bundle schema'),
            ('schema of the future', lambda: with_meta(schema=3), 'bundle schema'),
            ('no schema', lambda: with_meta(schema=...), 'bundle schema'),
            ('meta provider', lambda: with_meta(provider='side'), 'another provider or key'),
            ('meta key', lambda: with_meta(key=self.service.key('side')), 'another provider or key'),
            ('no meta key', lambda: with_meta(key=...), 'another provider or key'),
            ('root', lambda: with_meta(root=str(self.root) + '2'), 'another work root'),
            ('no root', lambda: with_meta(root=...), 'another work root'),
            ('meta digest', lambda: with_meta(tar_sha256='0' * 64), 'names another archive'),
            ('no meta digest', lambda: with_meta(tar_sha256=...), 'names another archive'),
            ('files digest', lambda: with_meta(files_sha256='0' * 64), 'file manifest differs'),
            ('no files digest', lambda: with_meta(files_sha256=...), 'file manifest differs'),
            ('prerequisite markers', lambda: with_meta(prerequisite_markers={'side': '0' * 64}),
             'other prerequisite outputs'),
            ('no prerequisite markers', lambda: with_meta(prerequisite_markers=...), 'other prerequisite outputs'),
            ('marker digest', lambda: with_meta(marker_sha256='0' * 64), 'completion marker differs'),
            ('no marker digest', lambda: with_meta(marker_sha256=...), 'completion marker differs'),
            ('meta is a list', lambda: content.update({'meta.json': b'[]'}), 'bundle schema'),
            ('meta is not json', lambda: content.update({'meta.json': b'{'}), 'not JSON'),
            ('meta is not text', lambda: content.update({'meta.json': b'\xff'}), 'not JSON'),
            ('status failed', lambda: content.update(
                {'status.json': json.dumps({'stage': 'base', 'status': 'failed'}).encode()}), 'not an ok status'),
            ('status of another stage', lambda: content.update(
                {'status.json': json.dumps({'stage': 'side', 'status': 'ok'}).encode()}), 'not an ok status'),
            ('status without stage', lambda: content.update(
                {'status.json': json.dumps({'status': 'ok'}).encode()}), 'not an ok status'),
            ('status is a list', lambda: content.update({'status.json': b'["ok"]'}), 'not an ok status'),
            ('status is not json', lambda: content.update({'status.json': b'ok'}), 'not JSON'),
            ('status is not text', lambda: content.update({'status.json': b'\xff'}), 'not JSON'),
            ('files is not json', lambda: content.update({'files.json': b'{'}), 'not JSON'),
            ('files changed', lambda: content.update(
                {'files.json': content['files.json'].replace(b'0755', b'0700')}), 'file manifest differs'),
            ('files with a trailing byte', lambda: content.update(
                {'files.json': content['files.json'] + b'\n'}), 'file manifest differs'),
        )
        for what, change, message in cases:
            change()
            with self.subTest(what=what):
                # Only the binding to prerequisites is a refusal that depends on this work root.
                self.assert_refused(message, noted='prerequisites' if 'prerequisite' in what else 'bundle')
            labels.clear()
            labels.update(original[0])
            content.update(original[1])
        self.service.restore('base')

    def test_missing_image_file_or_one_of_another_type(self):
        fetch = self.registry.fetch
        for item in ('base.tar', 'status.json', 'tar.sha256', 'files.json', 'meta.json'):
            for what in ('missing', 'symlink', 'directory', 'fifo'):
                def changed(ref, target, files, item=item, what=what):
                    labels = fetch(ref, target, files)
                    path = Path(target) / item
                    data = path.read_bytes()
                    os.unlink(path)
                    if what == 'symlink':
                        (Path(target) / 'real').write_bytes(data)
                        os.symlink('real', path)
                    elif what == 'directory':
                        path.mkdir()
                    elif what == 'fifo':
                        os.mkfifo(path)
                    return labels
                self.registry.fetch = changed
                with self.subTest(item=item, what=what):
                    if what == 'missing':  # lost here after the copy: an error of this machine, no judgement
                        self.assert_refused(errors=OSError, noted=None)
                    else:
                        self.assert_refused('image file %s is not a regular file' % item)
        self.registry.fetch = fetch
        self.assertEqual(self.registry.fetched[-1][1], ['base.tar', 'status.json', 'tar.sha256', 'files.json',
                                                        'meta.json'])

    def test_failed_pull_leaves_nothing(self):
        def failing(ref, target, files):
            (Path(target) / 'base.tar').write_bytes(b'partial')
            raise PC.CacheError('docker pull failed: connection reset')
        self.registry.fetch = failing
        self.assert_refused('connection reset', noted=None)

    def test_file_content_differs_from_the_manifest(self):
        self.reseal(members=self.edit_file('base/install/lib/lib.a',
                                           lambda member, data: bytes([data[0] ^ 1]) + data[1:]))
        self.assert_refused('base: unpacked files differ from the recorded manifest: '
                            'changed install/lib/lib.a (1 differences)')

    def test_file_size_differs_from_the_manifest(self):
        for what, change in (('longer', lambda member, data: data + b'\0'), ('shorter', lambda member, data: data[:-1]),
                             ('empty', lambda member, data: b'')):
            self.reseal(members=self.edit_file('base/install/lib/lib.a', change))
            with self.subTest(what=what):
                self.assert_refused('changed install/lib/lib.a (1 differences)')
            self.pristine()

    def test_file_mode_differs_from_the_manifest(self):
        def setuid(member, data):
            member.mode = 0o4755
            return data
        self.reseal(members=self.edit_file('base/install/tool', setuid))
        self.assert_refused('changed install/tool (1 differences)')

        def open_directory(items):
            for member, data in items:
                if member.name == 'base/empty':
                    member.mode = 0o777
            return items
        self.reseal(members=open_directory)
        self.assert_refused('changed empty, changed install/tool (2 differences)')

    def test_file_missing_from_the_archive(self):
        self.reseal(members=lambda items: [item for item in items if item[0].name != 'base/install/tool'])
        self.assert_refused('missing install/tool (1 differences)')

    def test_directory_or_link_missing_from_the_archive(self):
        self.reseal(members=lambda items: [item for item in items if item[0].name not in (
            'base/empty', 'base/install/tool-1')])
        self.assert_refused('missing empty, missing install/tool-1 (2 differences)')

    def test_extra_entries_in_the_archive(self):
        for what, extra, path in (
                ('file', (self.member('base/install/planted.so'), b'evil'), 'install/planted.so'),
                ('empty file', (self.member('base/planted'), b''), 'planted'),
                ('hidden file', (self.member('base/.planted'), b'evil'), '.planted'),
                ('directory', (self.member('base/planted', tarfile.DIRTYPE, mode=0o755), None), 'planted'),
                ('link', (self.member('base/planted', tarfile.SYMTYPE, 'install/tool'), None), 'planted'),
                ('dangling link', (self.member('base/planted', tarfile.SYMTYPE, 'nowhere'), None), 'planted')):
            self.reseal(members=lambda items: items + [extra])
            with self.subTest(what=what):
                self.assert_refused('unexpected %s (1 differences)' % path)
            self.pristine()
        self.service.restore('base')

    def test_entry_of_another_type_or_target(self):
        def retarget(items):
            for member, data in items:
                if member.name == 'base/install/tool-1':
                    member.linkname = 'lib/lib.a'
            return items
        self.reseal(members=retarget)
        self.assert_refused('changed install/tool-1 (1 differences)')
        self.pristine()

        def swap(items):
            return [(self.member('base/install/tool-1'), b'#!/bin/sh\n') if member.name == 'base/install/tool-1'
                    else (member, data) for member, data in items]
        self.reseal(members=swap)
        self.assert_refused('changed install/tool-1 (1 differences)')
        self.pristine()

        def link_for_file(items):
            return [(self.member('base/install/tool', tarfile.SYMTYPE, 'lib/lib.a'), None)
                    if member.name == 'base/install/tool' else (member, data) for member, data in items]
        self.reseal(members=link_for_file)
        self.assert_refused('changed install/tool (1 differences)')

    def test_hard_link_to_another_file_in_the_archive(self):
        def linked(items):
            return [(self.member('base/install/tool', tarfile.LNKTYPE, 'base/complete.json'), None)
                    if member.name == 'base/install/tool' else (member, data) for member, data in items]
        self.reseal(members=linked)
        self.assert_refused('changed install/tool (1 differences)')

    def test_manifest_differs_from_the_archive(self):
        def edit(path, **fields):
            def change(entries):
                (entry,) = [entry for entry in entries if entry['path'] == path]
                entry.update(fields)
                return entries
            return change
        for what, change, message in (
                ('content', edit('install/tool', sha256='0' * 64), 'changed install/tool (1 differences)'),
                ('size', edit('install/tool', size=11), 'changed install/tool (1 differences)'),
                ('mode', edit('install/tool', mode='0644'), 'changed install/tool (1 differences)'),
                ('target', edit('install/tool-1', target='lib/lib.a'), 'changed install/tool-1 (1 differences)'),
                ('type', edit('empty', type='file'), 'changed empty (1 differences)'),
                ('additional field', edit('empty', owner='root'), 'changed empty (1 differences)'),
                ('entry removed', lambda entries: [e for e in entries if e['path'] != 'install/tool'],
                 'unexpected install/tool (1 differences)'),
                ('entry added', lambda entries: entries + [self.file_entry('install/more', b'more')],
                 'missing install/more (1 differences)'),
                ('entry twice', lambda entries: entries + [dict(entries[-1])], 'twice'),
                ('no entries', lambda entries: [],
                 'unexpected complete.json, unexpected empty, unexpected install, unexpected install/lib, '
                 'unexpected install/lib/.libs (9 differences)'),
                ('escaping link', lambda entries: entries + [{'path': 'out', 'type': 'link', 'target': '../x'}],
                 'leaves the provider directory'),
                ('absolute link', lambda entries: entries + [{'path': 'out', 'type': 'link', 'target': '/etc'}],
                 'absolute or empty target'),
                ('link without target', lambda entries: entries + [{'path': 'out', 'type': 'link'}],
                 'absolute or empty target'),
                ('special file', lambda entries: entries + [{'path': 'dev', 'type': 'special', 'mode': '0600'}],
                 'device or other special file'),
                ('unknown type', lambda entries: entries + [{'path': 'dev', 'type': 'socket'}],
                 'device or other special file'),
                ('no type', lambda entries: entries + [{'path': 'dev'}], 'device or other special file'),
                ('dot-dot path', lambda entries: entries + [self.file_entry('../x', b'')], '".."'),
                ('absolute path', lambda entries: entries + [self.file_entry('/x', b'')], 'empty, "." or ".."'),
                ('entry that is no object', lambda entries: entries + ['install/more'],
                 'malformed file manifest entry'),
                ('entry without path', lambda entries: entries + [{'type': 'file'}], 'malformed file manifest entry'),
                ('path that is no text', lambda entries: entries + [{'path': 7, 'type': 'file'}],
                 'malformed file manifest entry'),
                ('target that is no text', lambda entries: entries + [{'path': 'l', 'type': 'link', 'target': ['x']}],
                 'malformed file manifest entry')):
            self.reseal(entries=change)
            with self.subTest(what=what):
                self.assert_refused(message)
            self.pristine()
        self.service.restore('base')

    def test_malformed_manifest_document(self):
        labels, content = self.registry.images[self.ref]
        entries = json.loads(content['files.json'])['entries']
        for what, listing in (('another provider', {'schema': 1, 'provider': 'side', 'entries': entries}),
                              ('no provider', {'schema': 1, 'entries': entries}),
                              ('entries is an object', {'schema': 1, 'provider': 'base', 'entries': {}}),
                              ('no entries', {'schema': 1, 'provider': 'base'}),
                              ('a list', entries), ('null', None)):
            content['files.json'] = json.dumps(listing).encode()
            self.reseal()
            meta = json.loads(content['meta.json'])
            meta['files_sha256'] = hashlib.sha256(content['files.json']).hexdigest()
            content['meta.json'] = json.dumps(meta).encode()
            with self.subTest(what=what):
                self.assert_refused('malformed file manifest')

    def without_marker(self, items):
        return [item for item in items if item[0].name != 'base/complete.json']

    def test_missing_completion_marker(self):
        self.reseal(members=self.without_marker,
                    entries=lambda entries: [e for e in entries if e['path'] != 'complete.json'])
        self.assert_refused('completion marker complete.json is missing')

    def test_empty_completion_marker(self):
        self.reseal(members=self.edit_file('base/complete.json', lambda member, data: b''),
                    entries=lambda entries: [self.file_entry('complete.json', b'') if e['path'] == 'complete.json'
                                             else e for e in entries], marker=b'')
        self.assert_refused('completion marker complete.json is missing')

    def test_completion_marker_that_is_a_link_or_a_directory(self):
        marker = [data for member, data in self.archive() if member.name == 'base/complete.json'][0]
        for what, member, entry in (
                ('link', self.member('base/complete.json', tarfile.SYMTYPE, 'real.json'),
                 {'path': 'complete.json', 'type': 'link', 'target': 'real.json'}),
                ('directory', self.member('base/complete.json', tarfile.DIRTYPE, mode=0o755),
                 {'path': 'complete.json', 'type': 'dir', 'mode': '0755'})):
            self.reseal(
                members=lambda items: self.without_marker(items) + [
                    (member, None), (self.member('base/real.json'), marker)],
                entries=lambda entries: [e for e in entries if e['path'] != 'complete.json'] + [
                    entry, self.file_entry('real.json', marker)])
            with self.subTest(what=what):
                self.assert_refused('completion marker complete.json is missing')
            self.pristine()

    def test_changed_completion_marker(self):
        changed = b'{"provider": "base", "nonce": "other"}'
        self.reseal(members=self.edit_file('base/complete.json', lambda member, data: changed),
                    entries=lambda entries: [self.file_entry('complete.json', changed) if e['path'] == 'complete.json'
                                             else e for e in entries])
        self.assert_refused('completion marker differs from the recorded one')

    def archive(self):
        source = tarfile.open(fileobj=io.BytesIO(self.registry.images[self.ref][1]['base.tar']))
        return [(member, source.extractfile(member).read() if member.isreg() else None) for member in source]

    def test_members_that_would_be_written_outside(self):
        outside = Path(self.tmp.name) / 'escaped'
        cases = (
            ('path traversal', [(self.member('base/../../escaped'), b'evil')], '".."'),
            ('path traversal at the start', [(self.member('../escaped'), b'evil')], '".."'),
            ('path traversal into the work root', [(self.member('base/../../status/fetch.json'), b'{}')], '".."'),
            ('dot component', [(self.member('base/./escaped'), b'evil')], '"."'),
            ('absolute member', [(self.member(str(outside)), b'evil')], 'not a plain relative path'),
            ('absolute directory', [(self.member(str(outside), tarfile.DIRTYPE), None)], 'not a plain relative path'),
            ('another top-level name', [(self.member('escaped'), b'evil')], 'outside base/'),
            ('another top-level directory', [(self.member('other', tarfile.DIRTYPE), None),
                                             (self.member('other/escaped'), b'evil')], 'outside base/'),
            ('name with the provider as prefix', [(self.member('base2/escaped'), b'evil')], 'outside base/'),
            ('backslash', [(self.member('base\\..\\escaped'), b'evil')], 'not a plain relative path'),
            ('absolute symlink', [(self.member('base/evil', tarfile.SYMTYPE, str(outside)), None)],
             'absolute or empty target'),
            ('escaping symlink', [(self.member('base/evil', tarfile.SYMTYPE, '../escaped'), None)],
             'leaves the provider directory'),
            ('escaping symlink from below', [(self.member('base/install/evil', tarfile.SYMTYPE, '../../../etc'), None)],
             'leaves the provider directory'),
            ('symlink to the parent', [(self.member('base/evil', tarfile.SYMTYPE, '..'), None)],
             'leaves the provider directory'),
            ('symlink that climbs after a name', [(self.member('base/evil', tarfile.SYMTYPE, 'install/../..'), None)],
             '".." after a name'),
            ('file through a symlink', [(self.member('base/evil', tarfile.SYMTYPE, 'install'), None),
                                        (self.member('base/evil/planted'), b'evil')], 'below a link or file'),
            ('file below a file', [(self.member('base/install/tool/planted'), b'evil')], 'below a link or file'),
            ('hard link to the outside', [(self.member('base/evil', tarfile.LNKTYPE, '../escaped'), None)], '".."'),
            ('hard link to an absolute path', [(self.member('base/evil', tarfile.LNKTYPE, '/etc/hostname'), None)],
             'not a plain relative path'),
            ('hard link to a later member', [(self.member('base/evil', tarfile.LNKTYPE, 'base/later'), None),
                                             (self.member('base/later'), b'x')], 'does not name an earlier file'),
            ('hard link to a directory', [(self.member('base/evil', tarfile.LNKTYPE, 'base/install'), None)],
             'does not name an earlier file'),
            ('character device', [(self.member('base/evil', tarfile.CHRTYPE), None)], 'device or other special file'),
            ('block device', [(self.member('base/evil', tarfile.BLKTYPE), None)], 'device or other special file'),
            ('fifo', [(self.member('base/evil', tarfile.FIFOTYPE), None)], 'device or other special file'),
            ('member named twice', [(self.member('base/install/tool'), b'evil')], 'twice'),
            ('directory named twice', [(self.member('base/install/', tarfile.DIRTYPE), None)], 'twice'),
            ('top level named twice', [(self.member('base', tarfile.DIRTYPE), None)], 'twice'),
        )
        for what, extra, message in cases:
            self.reseal(members=lambda items: items + extra)
            with self.subTest(what=what):
                self.assert_refused(message)
                self.assertFalse(os.path.lexists(outside))
            self.pristine()
        self.service.restore('base')

    def test_members_are_checked_before_anything_is_unpacked(self):
        self.reseal(members=lambda items: items + [(self.member('base/evil', tarfile.SYMTYPE, '../escaped'), None)])
        self.service.tree.unpack = lambda path, target: self.fail('unpacked a refused archive')
        self.assert_refused('leaves the provider directory')

    def test_top_level_entry_must_be_the_provider_directory(self):
        for what, members, message in (
                ('a file', lambda items: [(self.member('base'), b'evil')], 'top-level entry is not a directory'),
                ('a link', lambda items: [(self.member('base', tarfile.SYMTYPE, 'elsewhere'), None)],
                 'top-level entry is not a directory'),
                ('absent', lambda items: items[1:], 'no top-level base directory'),
                ('empty archive', lambda items: [], 'no top-level base directory'),
                ('another provider', lambda items: [(self.member('side', tarfile.DIRTYPE), None)], 'outside base/')):
            self.reseal(members=members)
            with self.subTest(what=what):
                self.assert_refused(message)
            self.pristine()

    def test_link_that_leaves_through_another_link_is_refused(self):
        # 'self' is the provider directory itself, so each 'self' cancels a '..'
        # only on paper: self/self/../.. is really two levels above.
        links = [('self', '.'), ('out', 'self/self/../..')]
        self.reseal(
            members=lambda items: items + [(self.member('base/' + path, tarfile.SYMTYPE, target), None)
                                           for path, target in links],
            entries=lambda entries: entries + [{'path': path, 'type': 'link', 'target': target}
                                               for path, target in links])
        self.assert_refused('".." after a name')
        with self.assertRaises(PC.CacheError):
            PC.check_members('p', [('p', 'dir', ''), ('p/...', 'symlink', '.'), ('p/out', 'symlink', '.../..')])
        self.assertEqual(PC.check_members('p', [('p', 'dir', ''), ('p/a', 'dir', ''), ('p/a/b', 'dir', ''),
                                                ('p/a/b/up', 'symlink', '../../a/b'), ('p/a/b/top', 'symlink', '../..'),
                                                ('p/a/dots', 'symlink', '..x/x..')]), 6)

    def test_archive_that_is_not_a_plain_tar(self):
        labels, content = self.registry.images[self.ref]
        original = content['base.tar']
        for what, data in (('garbage', b'not a tar archive' * 100), ('truncated', original[:700]),
                           ('compressed', gzip.compress(original)), ('empty', b'')):
            content['base.tar'] = data
            self.reseal()
            with self.subTest(what=what):
                self.assert_refused(errors=(PC.CacheError, tarfile.TarError))

    def test_interrupted_restore_leaves_nothing(self):
        for step in ('unpack', 'entries', 'install'):
            service = self.new_service()

            def interrupted(*args, **kwargs):
                raise KeyboardInterrupt
            setattr(service.tree, step, interrupted)
            with self.subTest(step=step), self.assertRaises(KeyboardInterrupt):
                service.restore('base')
            self.assertEqual(settled(self.tmp.name), self.before)

    def test_prerequisite_that_changes_during_the_restore(self):
        aside = self.root.parent / 'aside-top'
        self.make('top')
        self.make('base')
        ref = self.service.push('top')
        os.rename(self.root / 'P/top', aside)
        os.unlink(self.root / 'status/top.json')
        before = settled(self.tmp.name)
        marker = (self.root / 'P/base/complete.json').read_bytes()
        unpack = self.service.tree.unpack

        def rebuilt(path, target):
            unpack(path, target)
            (self.root / 'P/base/complete.json').write_text('{"rebuilt": true}')
        self.service.tree.unpack = rebuilt
        with self.assertRaisesRegex(PC.CacheError, '^top: a prerequisite changed during the restore$'):
            self.service.restore('top')
        (self.root / 'P/base/complete.json').write_bytes(marker)
        self.assertEqual(settled(self.tmp.name), before)
        self.assertIn(ref, self.registry.images)


class Promotion(ServiceCase):
    """A restored provider appears in one step, complete, and only then its status."""

    def setUp(self):
        super().setUp()
        self.top = self.make('base')
        self.good = snapshot(self.top)
        self.ref = self.service.push('base')
        self.set_aside('base')
        self.status = self.root / 'status/base.json'

    def test_nothing_is_visible_until_one_rename_publishes_the_verified_tree(self):
        events = []
        rename, replace = PC.rename_noreplace, os.replace
        tree = self.service.tree
        for step in ('unpack', 'entries', 'tar_members'):
            def watched(*args, step=step, call=getattr(tree, step)):
                events.append(step)
                self.assertFalse(os.path.lexists(self.top))
                self.assertFalse(self.status.exists())
                return call(*args)
            setattr(tree, step, watched)

        def renamed(source, target):
            events.append('rename')
            self.assertEqual(Path(target), self.top)
            self.assertFalse(os.path.lexists(self.top))
            self.assertFalse(self.status.exists())
            self.assertEqual(snapshot(source), self.good)
            self.assertEqual(Path(source).parent.parent.parent, self.root)
            self.assertRegex(Path(source).parent.parent.name, r'^\.restore-base\.')
            self.assertEqual(os.stat(source).st_dev, os.stat(self.root / 'P').st_dev)
            rename(source, target)
            self.assertFalse(os.path.lexists(source))
            self.assertEqual(snapshot(self.top), self.good)

        def replaced(source, target):
            if Path(target) == self.status:
                events.append('status')
                self.assertEqual(snapshot(self.top), self.good)
                self.assertEqual(Path(source).parent, self.status.parent)
                self.assertEqual(json.loads(Path(source).read_text())['restored_from'], self.ref)
                self.assertFalse(self.status.exists())
            replace(source, target)
        with mock.patch.object(PC, 'rename_noreplace', renamed), mock.patch.object(PC.os, 'replace', replaced):
            self.assertEqual(self.service.restore('base'), self.ref)
        self.assertEqual(events, ['tar_members', 'unpack', 'entries', 'rename', 'status'])
        self.assertEqual(sorted(os.listdir(self.root / 'status')),
                         ['base.json', 'fetch.json', 'images.json', 'toolchain.json'])
        self.assertEqual(os.listdir(self.root / 'P'), ['base'])
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(self.messages[-1], 'provider-cache: restored base from %s (9 entries verified)' % self.ref)

    def test_status_is_written_whole_or_not_at_all(self):
        for error in (OSError(28, 'No space left on device'), KeyboardInterrupt()):
            def failing(source, target, error=error):
                self.assertTrue(Path(source).is_file())
                raise error
            with self.subTest(error=error), mock.patch.object(PC.os, 'replace', failing), \
                    self.assertRaises(type(error)):
                self.service.restore('base')
            self.assertFalse(os.path.lexists(self.top))
            self.assertEqual(sorted(os.listdir(self.root / 'status')), ['fetch.json', 'images.json', 'toolchain.json'])
            self.assertEqual(os.listdir(self.root / 'P'), [])
            self.assertEqual(self.leftovers(), [])
        self.service.restore('base')
        self.assertEqual(snapshot(self.top), self.good)

    def test_status_of_an_earlier_failed_build_is_replaced_only_on_success(self):
        failed = json.dumps({'stage': 'base', 'status': 'failed', 'notes': 'earlier'})
        self.status.write_text(failed)
        with mock.patch.object(PC.os, 'replace', side_effect=OSError(5, 'Input/output error')), \
                self.assertRaises(OSError):
            self.service.restore('base')
        self.assertEqual(self.status.read_text(), failed)
        self.assertFalse(os.path.lexists(self.top))
        labels, content = self.registry.images[self.ref]
        saved = content['base.tar']
        content['base.tar'] = saved + b'\0'
        with self.assertRaises(PC.CacheError):
            self.service.restore('base')
        self.assertEqual(self.status.read_text(), failed)
        content['base.tar'] = saved
        self.service.restore('base')
        self.assertEqual(json.loads(self.status.read_text())['status'], 'ok')

    def test_work_root_without_p_and_status_is_created(self):
        service = self.new_service()
        self.assertEqual(service.ref('base'), self.ref)  # the inputs are read before the directory goes
        os.rmdir(self.root / 'P')
        shutil.rmtree(self.root / 'status')
        service.restore('base')  # without a record the image is probed and recorded again
        self.assertEqual(snapshot(self.top), self.good)
        self.assertEqual(sorted(os.listdir(self.root / 'status')), ['base.json', 'toolchain.json'])
        self.assertEqual(json.loads((self.root / 'status/toolchain.json').read_text())['fingerprint'], FINGERPRINT)

    def test_existing_destination_of_any_kind_is_kept(self):
        cases = {
            'empty directory': lambda: self.top.mkdir(),
            'directory with files': lambda: (self.top.mkdir(), (self.top / 'keep').write_text('mine')),
            'file': lambda: self.top.write_text('mine'),
            'dangling link': lambda: os.symlink('/nonexistent', self.top),
            'link to a directory': lambda: os.symlink(self.tmp.name, self.top),
            'fifo': lambda: os.mkfifo(self.top),
        }
        for what, create in cases.items():
            create()
            before = settled(self.tmp.name)
            for state in (None, 'failed', 'ok'):
                if state:
                    self.status.write_text(json.dumps({'stage': 'base', 'status': state}))
                    before = settled(self.tmp.name)
                with self.subTest(what=what, state=state):
                    with self.assertRaisesRegex(PC.CacheError, 'already exists and is never overwritten'):
                        self.service.restore('base')
                    self.assertEqual(settled(self.tmp.name), before)
                    self.assertEqual(self.registry.fetched, [])
            os.unlink(self.status)
            if self.top.is_dir() and not self.top.is_symlink():
                shutil.rmtree(self.top)
            else:
                os.unlink(self.top)

    def test_destination_that_appears_at_the_last_moment_is_kept(self):
        cases = {
            'empty directory': lambda: self.top.mkdir(),
            'directory with files': lambda: (self.top.mkdir(), (self.top / 'keep').write_text('mine')),
            'file': lambda: self.top.write_text('mine'),
            'dangling link': lambda: os.symlink('/nonexistent', self.top),
        }
        rename = PC.rename_noreplace
        for what, create in cases.items():
            def racing(source, target, create=create):
                create()
                self.racing = snapshot(self.root / 'P')
                rename(source, target)
            with self.subTest(what=what):
                with mock.patch.object(PC, 'rename_noreplace', racing), \
                        self.assertRaisesRegex(PC.CacheError, 'appeared meanwhile; not overwritten'):
                    self.service.restore('base')
                self.assertEqual(snapshot(self.root / 'P'), self.racing)
                self.assertFalse(self.status.exists())
                self.assertEqual(self.leftovers(), [])
            if self.top.is_dir() and not self.top.is_symlink():
                shutil.rmtree(self.top)
            else:
                os.unlink(self.top)

    def test_rename_refuses_any_existing_target_with_and_without_renameat2(self):
        class NoLibc:
            def __getattr__(self, name):
                raise AttributeError(name)

        def libc(code):
            class Libc:
                @staticmethod
                def renameat2(*args):
                    return -1
            return mock.patch.multiple(PC.ctypes, CDLL=lambda *args, **kwargs: Libc(), get_errno=lambda: code)
        variants = {
            'renameat2': mock.patch.object(PC.ctypes, 'get_errno', PC.ctypes.get_errno),
            'no libc': mock.patch.object(PC.ctypes, 'CDLL', side_effect=OSError('no libc')),
            'no renameat2': mock.patch.object(PC.ctypes, 'CDLL', return_value=NoLibc()),
            'ENOSYS': libc(38), 'EINVAL': libc(22), 'EOPNOTSUPP': libc(95),
        }
        base = Path(self.tmp.name)
        for how, patch in variants.items():
            for what in ('empty directory', 'full directory', 'file', 'dangling link'):
                source, target = base / 'source', base / 'target'
                source.mkdir()
                (source / 'data').write_text('new')
                if what == 'file':
                    target.write_text('old')
                elif what == 'dangling link':
                    os.symlink('/nonexistent', target)
                else:
                    target.mkdir()
                    if what == 'full directory':
                        (target / 'old').write_text('old')
                before = snapshot(base)
                with self.subTest(how=how, what=what), patch:
                    with self.assertRaises(FileExistsError):
                        PC.rename_noreplace(source, target)
                    self.assertEqual(snapshot(base), before)
                shutil.rmtree(source)
                shutil.rmtree(target) if target.is_dir() and not target.is_symlink() else os.unlink(target)
            source, target = base / 'source', base / 'target'
            source.mkdir()
            (source / 'data').write_text('new')
            with self.subTest(how=how, what='free'), patch:
                PC.rename_noreplace(source, target)
                self.assertEqual((target / 'data').read_text(), 'new')
                self.assertFalse(os.path.lexists(source))
            shutil.rmtree(target)

    def test_rename_reports_other_errors_and_does_not_fall_back(self):
        base = Path(self.tmp.name)
        with self.assertRaises(FileNotFoundError):
            PC.rename_noreplace(base / 'missing', base / 'target')
        self.assertFalse(os.path.lexists(base / 'target'))

        class Libc:
            @staticmethod
            def renameat2(*args):
                self.arguments = args
                return -1
        (base / 'source').mkdir()
        for code, error in ((13, PermissionError), (17, FileExistsError), (18, OSError), (30, OSError)):
            with self.subTest(code=code), mock.patch.multiple(
                    PC.ctypes, CDLL=lambda *args, **kwargs: Libc(), get_errno=lambda: code), \
                    self.assertRaises(error) as caught:
                PC.rename_noreplace(base / 'source', base / 'target')
            self.assertEqual(caught.exception.errno, code)
            self.assertTrue((base / 'source').is_dir())
            self.assertFalse(os.path.lexists(base / 'target'))
        self.assertEqual(self.arguments, (-100, os.fsencode(base / 'source'), -100, os.fsencode(base / 'target'), 1))


class Push(ServiceCase):
    def assert_nothing_pushed(self):
        self.assertEqual((self.registry.published, self.registry.images, self.leftovers()), (0, {}, []))
        self.assertEqual(self.service.tree.pushed(), {})
        self.assertFalse((self.root / 'cache-state').exists())

    def test_provider_that_is_not_ok_is_skipped_without_asking_the_registry(self):
        top = self.make('base')
        before = snapshot(top)
        for what, text in (('failed', '{"stage": "base", "status": "failed"}'),
                           ('running', '{"stage": "base", "status": "running"}'),
                           ('ok in another case', '{"stage": "base", "status": "OK"}'),
                           ('ok with a space', '{"stage": "base", "status": "ok "}'),
                           ('no status field', '{"stage": "base"}'), ('status true', '{"status": true}'),
                           ('not json', 'ok'), ('empty', ''), ('a list', '["ok"]'), ('not text', None)):
            if text is None:
                (self.root / 'status/base.json').write_bytes(b'\xff\xfe')
            else:
                (self.root / 'status/base.json').write_text(text)
            with self.subTest(what=what), self.assertRaisesRegex(PC.Skip, 'status is .*, not ok'):
                self.service.push('base')
        os.unlink(self.root / 'status/base.json')
        with self.assertRaisesRegex(PC.Skip, 'status is missing, not ok'):
            self.service.push('base')
        with self.assertRaisesRegex(PC.Skip, 'status is missing, not ok'):
            self.service.push('side')  # never built
        self.assertEqual(self.registry.calls, [])
        self.assert_nothing_pushed()
        self.assertEqual(snapshot(top), before)

    def test_ok_provider_without_a_usable_marker_is_refused(self):
        cases = {
            'missing': lambda marker: os.unlink(marker),
            'empty': lambda marker: marker.write_text(''),
            'directory': lambda marker: (os.unlink(marker), marker.mkdir()),
            'link': lambda marker: (os.rename(marker, marker.with_name('real.json')),
                                    os.symlink('real.json', marker)),
            'dangling link': lambda marker: (os.unlink(marker), os.symlink('nowhere', marker)),
            'fifo': lambda marker: (os.unlink(marker), os.mkfifo(marker)),
        }
        for index, (what, change) in enumerate(cases.items()):
            self.doCleanups()
            self.setUp()
            change(self.make('base') / 'complete.json')
            with self.subTest(what=what), self.assertRaisesRegex(
                    PC.CacheError, 'base: status is ok but the completion marker is missing'):
                self.service.push('base')
            self.assertEqual(self.registry.calls, [])
            self.assert_nothing_pushed()
        self.doCleanups()
        self.setUp()
        (self.root / 'P/base').mkdir()
        self.write_json('status/base.json', {'stage': 'base', 'status': 'ok'})
        with self.assertRaises(PC.CacheError):
            self.service.push('base')
        os.rmdir(self.root / 'P/base')
        with self.assertRaises(PC.CacheError):  # ok on paper, no directory at all
            self.service.push('base')
        self.assert_nothing_pushed()

    def test_pushed_bundle_holds_the_archive_and_everything_needed_to_check_it(self):
        top = self.make('base')
        self.make('top')
        status_bytes = (self.root / 'status/top.json').read_bytes()
        ref = self.service.push('top')
        key = self.service.key('top')
        self.assertEqual(ref, 'reg.example/cache/top:' + key)
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe', ref), ('publish', ref),
                                               ('probe', ref)])
        labels, content = self.registry.images[ref]
        self.assertEqual(list(content), ['top.tar', 'status.json', 'tar.sha256', 'files.json', 'meta.json'])
        tar_sha = hashlib.sha256(content['top.tar']).hexdigest()
        self.assertEqual(labels, {
            'org.opencontainers.image.source': 'https://github.com/jonathanagustin/uds-takserver',
            'org.opencontainers.image.licenses': 'GPL-3.0-only', 'dev.uds.takx.provider': 'top',
            'dev.uds.takx.key': key, 'dev.uds.takx.tar-sha256': tar_sha})
        self.assertEqual(content['tar.sha256'], (tar_sha + '\n').encode())
        self.assertEqual(content['status.json'], status_bytes)
        marker_sha = hashlib.sha256((self.root / 'P/top/complete.json').read_bytes()).hexdigest()
        self.assertEqual(json.loads(content['meta.json']), {
            'schema': 2, 'provider': 'top', 'key': key, 'root': str(self.root), 'repo': str(self.repo),
            'marker': 'complete.json', 'marker_sha256': marker_sha, 'tar_sha256': tar_sha,
            'files_sha256': hashlib.sha256(content['files.json']).hexdigest(),
            'prerequisite_markers': {'base': hashlib.sha256((top / 'complete.json').read_bytes()).hexdigest()},
            'key_inputs': self.service.explain('top'), 'toolchain': dict(ATTRIBUTE, image_id=TOOLCHAIN_ID)})
        self.assertTrue(content['meta.json'].endswith(b'\n') and content['files.json'].endswith(b'\n'))
        listing = json.loads(content['files.json'])
        self.assertEqual((listing['schema'], listing['provider']), (1, 'top'))
        on_disk = snapshot(self.root / 'P/top')
        self.assertEqual({entry['path'] for entry in listing['entries']}, set(on_disk))
        for entry in listing['entries']:
            kind = on_disk[entry['path']]
            if entry['type'] == 'link':
                self.assertEqual(entry, {'path': entry['path'], 'type': 'link', 'target': kind[1]})
            elif entry['type'] == 'dir':
                self.assertEqual(entry, {'path': entry['path'], 'type': 'dir', 'mode': '%04o' % kind[1]})
            else:
                size = os.path.getsize(self.root / 'P/top' / entry['path'])
                self.assertEqual(entry, {'path': entry['path'], 'type': 'file', 'mode': '%04o' % kind[1],
                                         'size': size, 'sha256': kind[2]})
        self.assertEqual([entry['path'] for entry in listing['entries']], [
            'complete.json', 'empty', 'install', 'install/lib', 'install/lib/.libs', 'install/lib/.libs/lib.a',
            'install/lib/lib.a', 'install/tool', 'install/tool-1'])
        with tarfile.open(fileobj=io.BytesIO(content['top.tar'])) as archive:
            self.assertEqual(archive.getnames(), ['top'] + ['top/' + entry['path'] for entry in listing['entries']])
            self.assertEqual({member.type for member in archive},
                             {tarfile.DIRTYPE, tarfile.REGTYPE, tarfile.SYMTYPE})
            self.assertEqual(archive.extractfile('top/install/tool').read(), b'#!/bin/sh\n')
        self.assertEqual(self.messages, ['provider-cache: pushed %s (9 entries, tar sha256 %s)' % (ref, tar_sha)])
        self.assertEqual((self.root / 'cache-state/pushed.tsv').read_text(),
                         '\t'.join(('top', key, ref, tar_sha, marker_sha)) + '\n')
        self.assertEqual(self.service.tree.pushed_markers(), {ref: marker_sha})
        self.assertEqual(self.leftovers(), [])

    def test_hard_linked_files_are_stored_in_full(self):
        top = self.make('base')
        os.link(top / 'install/lib/lib.a', top / 'install/lib/copy.a')
        ref = self.service.push('base')
        with tarfile.open(fileobj=io.BytesIO(self.registry.images[ref][1]['base.tar'])) as archive:
            self.assertEqual([member.name for member in archive if member.islnk()], [])
            self.assertEqual(archive.extractfile('base/install/lib/copy.a').read(),
                             (top / 'install/lib/lib.a').read_bytes())
        good = snapshot(top)
        self.set_aside('base')
        self.service.restore('base')
        self.assertEqual(snapshot(top), good)

    def test_second_push_does_nothing(self):
        top = self.make('base')
        first = self.service.push('base')
        stored = copy.deepcopy(self.registry.images)
        record = (self.root / 'cache-state/pushed.tsv').read_text()
        del self.registry.calls[:]
        for service in (self.service, self.new_service()):
            del self.messages[:]
            self.assertEqual(service.push('base'), first)
            self.assertEqual(self.messages, ['provider-cache: base is already cached under this key: ' + first])
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe', first)] * 2)
        self.assertEqual((self.registry.published, self.registry.images), (1, stored))
        self.assertEqual((self.root / 'cache-state/pushed.tsv').read_text(), record)
        self.assertEqual(self.leftovers(), [])
        # An identical rebuild (other bytes, same inputs) is recognised too and does not replace the image.
        (top / 'install/lib/lib.a').write_bytes(b'rebuilt')
        self.assertEqual(self.service.push('base'), first)
        self.assertEqual((self.registry.published, self.registry.images), (1, stored))

    def test_push_after_the_image_was_lost_is_recorded_once(self):
        self.make('base')
        service = self.service
        first = service.push('base')
        record = (self.root / 'cache-state/pushed.tsv').read_text()
        self.registry.images.clear()
        self.assertEqual(service.push('base'), first)
        self.assertEqual(self.registry.published, 2)
        self.assertEqual((self.root / 'cache-state/pushed.tsv').read_text(), record)
        self.assertEqual(len(record.splitlines()), 1)
        service.tree.record_push(('base', 'k' * 64, 'ref2', 't' * 64))
        with open(self.root / 'cache-state/pushed.tsv', 'a') as stream:
            stream.write('short\trow\n\none\ttwo\tthree\tfour\tfive\tsix\n')
        self.assertEqual(service.tree.pushed(), {('base', service.key('base')): record.split('\t')[3],
                                                 ('base', 'k' * 64): 't' * 64})

    def test_provider_that_changes_while_it_is_archived_is_not_pushed(self):
        top = self.make('base')
        write_tar = PC.WorkTree.write_tar
        changes = (  # what, before or after the archive is written, the change, the refusal
            ('file content', 'before', lambda top: (top / 'install/tool').write_text('#!/bin/sh\nexit 1\n'),
             'base changed while it was archived: changed install/tool'),
            ('file size', 'before', lambda top: (top / 'install/lib/lib.a').write_bytes(b''),
             'base changed while it was archived: changed install/lib/lib.a'),
            ('file removed', 'before', lambda top: os.unlink(top / 'install/tool'), None),
            ('marker rewritten', 'before', lambda top: (top / 'complete.json').write_text('{"rebuilt": true}'),
             'base changed while it was archived: changed complete.json'),
            ('status', 'after', lambda top: self.write_json('status/base.json', {'stage': 'base', 'status': 'ok',
                                                                                  'run': 2}),
             'base: status or marker changed while it was archived'),
            ('status gone', 'after', lambda top: os.unlink(self.root / 'status/base.json'),
             'base: status or marker changed while it was archived'),
            ('marker afterwards', 'after', lambda top: (top / 'complete.json').write_text('{"rebuilt": true}'),
             'base: status or marker changed while it was archived'),
            ('marker gone afterwards', 'after', lambda top: os.unlink(top / 'complete.json'),
             'base: status or marker changed while it was archived'),
        )
        for what, when, change, message in changes:
            self.doCleanups()
            self.setUp()
            top = self.make('base')

            def racing(source, name, entries, target):
                if when == 'before':
                    change(top)
                write_tar(source, name, entries, target)
                if when == 'after':
                    change(top)
            self.service.tree.write_tar = racing
            with self.subTest(what=what), self.assertRaises((PC.CacheError, OSError)) as caught:
                self.service.push('base')
            if message:
                self.assertIn(message, str(caught.exception))
            self.assert_nothing_pushed()

    def test_file_added_after_the_listing_is_not_published(self):
        # The archive is written from the listing, so a later file is simply not in it;
        # what is published is exactly what was listed and verified.
        top = self.make('base')
        write_tar = PC.WorkTree.write_tar

        def racing(source, name, entries, target):
            write_tar(source, name, entries, target)
            (top / 'late').write_text('late')
        self.service.tree.write_tar = racing
        ref = self.service.push('base')
        with tarfile.open(fileobj=io.BytesIO(self.registry.images[ref][1]['base.tar'])) as archive:
            self.assertNotIn('base/late', archive.getnames())
        self.assertNotIn('"late"', self.registry.images[ref][1]['files.json'].decode())

    def test_failed_publish_is_an_error_and_is_not_recorded(self):
        self.make('base')

        def failing(ref, context, files, labels):
            self.context = Path(context)
            self.assertEqual(sorted(os.listdir(context)), sorted(files))
            self.assertEqual(self.context.parent, self.root)
            raise PC.CacheError('docker push failed: denied')
        self.registry.publish = failing
        with self.assertRaisesRegex(PC.CacheError, 'denied'):
            self.service.push('base')
        self.assertFalse(self.context.exists())
        self.assertRegex(self.context.name, r'^\.push-base\.')
        self.assert_nothing_pushed()

    def test_registry_states_after_publishing(self):
        for state in ('absent', 'error', 'unauthorized'):
            self.doCleanups()
            self.setUp()
            self.make('base')
            publish = self.registry.publish

            def published(ref, context, files, labels, state=state):
                publish(ref, context, files, labels)
                self.registry.state = state
            self.registry.publish = published
            with self.subTest(state=state), self.assertRaisesRegex(
                    PC.CacheError, 'base: pushed, but the registry does not show reg.example/cache/base:'):
                self.service.push('base')
            self.assertEqual(self.service.tree.pushed(), {})
            self.assertEqual(self.leftovers(), [])

    def test_prerequisites_must_be_ok_with_their_markers(self):
        cases = {
            'never built': lambda: None,
            'failed': lambda: self.make('base', status='failed'),
            'no marker': lambda: self.make('base', marker=False),
            'no status': lambda: (self.make('base'), os.unlink(self.root / 'status/base.json')),
        }
        for what, prepare in cases.items():
            self.doCleanups()
            self.setUp()
            self.make('top')
            prepare()
            with self.subTest(what=what), self.assertRaisesRegex(
                    PC.CacheError, 'top: a prerequisite provider is not ok here'):
                self.service.push('top')
            self.assert_nothing_pushed()

    def test_toolchain_image_must_be_the_recorded_toolchain(self):
        self.make('base')
        self.registry.layer = 'sha256:' + 'c' * 64   # an image of another toolchain
        for actual, message in (('sha256:' + '7' * 64, r'^the local takx-desktop-proto:toolchain image '
                                 r'\(sha256:7{64}\) has toolchain fingerprint %s, not the recorded %s; if the '
                                 r'toolchain was changed on purpose, accept it with `provider_cache.py toolchain '
                                 r'--record`$' % (fingerprint(layer='sha256:' + 'c' * 64), FINGERPRINT)),
                                (None, '^there is no local takx-desktop-proto:toolchain image to fingerprint$'),
                                ('', '^there is no local takx-desktop-proto:toolchain image to fingerprint$')):
            self.registry.toolchain = actual
            with self.subTest(actual=actual), self.assertRaisesRegex(PC.CacheError, message):
                self.service.push('base')
            self.set_aside('base')
            with self.subTest(actual=actual, action='restore'), self.assertRaisesRegex(PC.CacheError, message):
                self.service.restore('base')
            self.make('base')
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)] * 2
                         + [('image_id', PC.TOOLCHAIN)] * 4)
        self.assert_nothing_pushed()
        self.assertEqual(json.loads((self.root / 'status/toolchain.json').read_text()), toolchain_record())
        # The image id that was probed is accepted without another probe.
        self.registry.toolchain = TOOLCHAIN_ID
        del self.registry.calls[:]
        ref = self.new_service().push('base')
        self.assertEqual([call[0] for call in self.registry.calls], ['image_id', 'probe', 'publish', 'probe'])
        self.assertEqual(ref.rsplit(':', 1)[1], self.service.key('base'))


class OffAndLogin(ServiceCase):
    """The cache switched off, docker absent, docker not logged in: clean skips."""

    def setUp(self):
        super().setUp()
        self.make('base')
        self.service.push('base')
        self.make('side')
        os.rename(self.root / 'P/base', self.root.parent / 'aside')
        os.unlink(self.root / 'status/base.json')
        del self.registry.calls[:]
        del self.messages[:]
        self.before = settled(self.tmp.name)
        self.stored = copy.deepcopy(self.registry.images)

    def assert_untouched(self, calls):
        self.assertEqual(self.registry.calls, calls)
        self.assertEqual(settled(self.tmp.name), self.before)
        self.assertEqual((self.registry.published, self.registry.images), (1, self.stored))
        self.assertEqual(self.messages, [])

    def test_cache_off_never_touches_registry_or_disk(self):
        class Untouchable:
            def __getattr__(self, name):
                raise AssertionError('registry used while the cache is off: ' + name)
        off = PC.ProviderCache(manifest(), PC.WorkTree(self.root, self.repo), Untouchable(), 'off', '',
                               self.messages.append)
        self.assertFalse(off.enabled)
        for action, name in ((off.restore, 'base'), (off.push, 'side'), (off.restore, 'side'), (off.push, 'base')):
            with self.subTest(action=action.__name__, name=name), self.assertRaisesRegex(PC.Skip, '^cache is off$'):
                action(name)
        with self.assertRaises(PC.CacheError):
            off.ref('base')
        self.assertEqual([(row['name'], row['ref'], row['remote']) for row in off.rows(remote=True)],
                         [('base', '-', 'pushed'), ('side', '-', 'unknown'), ('top', '-', 'unknown')])
        for action in (off.restore, off.push):
            with self.assertRaisesRegex(PC.CacheError, 'unknown provider ghost'):
                action('ghost')
        self.assert_untouched([])

    def test_only_the_word_off_switches_the_cache_off(self):
        for prefix, enabled in (('off', False), ('OFF', True), ('off/', True), ('reg/off', True), ('', True)):
            service = self.new_service(prefix=prefix)
            self.assertEqual(service.enabled, enabled, prefix)
        self.assertEqual(self.new_service(prefix='').prefix, PC.DEFAULT_REGISTRY)
        self.assertEqual(self.new_service(prefix='').ref('base'),
                         PC.DEFAULT_REGISTRY + '/base:' + self.service.key('base'))

    def test_without_docker_nothing_is_asked(self):
        self.registry.docker = False
        with self.assertRaisesRegex(PC.Skip, '^docker is not available$'):
            self.service.restore('base')
        with self.assertRaisesRegex(PC.Skip, '^docker is not available$'):
            self.service.push('side')
        self.assert_untouched([])

    def test_not_logged_in_is_a_skip(self):
        self.registry.logged_in = False
        ref = 'reg.example/cache/base:' + self.service.key('base')
        with self.assertRaisesRegex(
                PC.Skip, r'^docker is not logged in to the cache registry \(%s\)$' % re.escape(ref)):
            self.service.restore('base')
        with self.assertRaisesRegex(PC.Skip, '^docker is not logged in to the cache registry$'):
            self.service.push('side')
        self.assert_untouched([('image_id', PC.TOOLCHAIN), ('probe', ref), ('image_id', PC.TOOLCHAIN),
                               ('probe', 'reg.example/cache/side:' + self.service.key('side'))])

    def test_nothing_cached_is_a_skip_and_an_unreachable_registry_an_error(self):
        self.registry.images.clear()
        self.stored = {}
        with self.assertRaisesRegex(PC.Skip, r'^nothing cached under this key \(reg.example/cache/base:'):
            self.service.restore('base')
        self.registry.state = 'error'
        with self.assertRaisesRegex(PC.CacheError, '^base: the registry could not be queried$'):
            self.service.restore('base')
        with self.assertRaisesRegex(PC.CacheError, '^side: the registry could not be queried$'):
            self.service.push('side')
        self.registry.state = 'something new'
        with self.assertRaises(PC.CacheError):
            self.service.restore('base')
        with self.assertRaises(PC.CacheError):
            self.service.push('side')
        self.assertEqual(settled(self.tmp.name), self.before)
        self.assertEqual((self.registry.published, self.registry.fetched), (1, []))

    def test_provider_that_is_ok_here_is_not_fetched(self):
        with self.assertRaisesRegex(PC.Skip, '^already present and ok here$'):
            self.service.restore('side')
        self.assert_untouched([])

    def test_restore_waits_for_its_prerequisites(self):
        with self.assertRaisesRegex(PC.Skip, '^a prerequisite provider is not ok here$'):
            self.service.restore('top')
        self.assert_untouched([])


class ExitCodes(ServiceCase):
    """0 done, 3 cleanly skipped, 1 anything else; 1 wins over 3."""

    def run_cli(self, *argv, service=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(PC.sys, 'stderr', err):
            code = PC.main(list(argv), service=service or self.service, out=out)
        return code, out.getvalue(), err.getvalue()

    def cached(self, *names):
        for name in names:
            self.make(name)
            self.service.push(name)
            self.set_aside(name)

    def test_codes_are_the_documented_ones(self):
        self.assertEqual((PC.DONE, PC.FAILED, PC.SKIPPED), (0, 1, 3))

    def test_restore(self):
        self.cached('base')
        self.assertEqual(self.run_cli('restore', 'base'), (0, '', ''))
        self.assertEqual(self.run_cli('restore', 'base'),
                         (3, '', 'provider-cache: restore base skipped: already present and ok here\n'))
        code, out, err = self.run_cli('restore', 'side')
        self.assertEqual((code, out), (3, ''))
        self.assertRegex(err, r'^provider-cache: restore side skipped: nothing cached under this key \(.*\)\n$')
        self.write_json('status/base.json', {'stage': 'base', 'status': 'failed'})
        self.assertEqual(self.run_cli('restore', 'base'), (
            1, '', 'provider-cache: restore base failed: base: the provider directory already exists and is '
                   'never overwritten\n'))
        self.assertEqual(self.run_cli('restore', 'ghost'), (1, '', 'provider-cache: unknown provider ghost\n'))
        self.assertEqual(self.run_cli('restore', 'side', 'ghost')[0], 1)
        self.assertFalse(os.path.lexists(self.root / 'P/side'))

    def test_restore_of_a_tampered_bundle_fails(self):
        self.cached('base')
        labels, content = self.bundle('base')
        saved = content['base.tar']
        content['base.tar'] = saved + b'\0'
        code, out, err = self.run_cli('restore', 'base')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('restore base failed: base: archive digest differs', err)
        content['base.tar'] = b'junk'
        digest = hashlib.sha256(b'junk').hexdigest()
        content['tar.sha256'] = digest.encode()
        labels['dev.uds.takx.tar-sha256'] = digest
        meta = json.loads(content['meta.json'])
        content['meta.json'] = json.dumps(dict(meta, tar_sha256=digest)).encode()
        code, out, err = self.run_cli('restore', 'base')  # tarfile's own error is reported, not raised
        self.assertEqual((code, out), (1, ''))
        self.assertIn('restore base failed: ', err)
        self.registry.fetch = mock.Mock(side_effect=OSError(28, 'No space left on device'))
        code, out, err = self.run_cli('restore', 'base')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('No space left on device', err)
        self.assertEqual(self.leftovers(), [])
        self.assertFalse(os.path.lexists(self.root / 'P/base'))

    def test_push(self):
        self.assertEqual(self.run_cli('push', 'base'),
                         (3, '', 'provider-cache: push base skipped: status is missing, not ok\n'))
        self.make('base')
        self.assertEqual(self.run_cli('push', 'base'), (0, '', ''))
        self.assertEqual(self.run_cli('push', 'base'), (0, '', ''))  # idempotent, still done
        self.make('side', status='failed')
        self.assertEqual(self.run_cli('push', 'side'),
                         (3, '', 'provider-cache: push side skipped: status is failed, not ok\n'))
        self.make('top', marker=False)
        self.assertEqual(self.run_cli('push', 'top'), (
            1, '', 'provider-cache: push top failed: top: status is ok but the completion marker is missing\n'))
        self.assertEqual(self.run_cli('push', 'ghost'), (1, '', 'provider-cache: unknown provider ghost\n'))
        self.assertEqual(self.registry.published, 1)

    def test_failure_wins_over_skip_and_done_in_any_order(self):
        self.make('base')                  # push: done
        self.make('top', marker=False)     # push: failed
        # side was never built            # push: skipped
        for names, expected in ((['base'], 0), (['side'], 3), (['top'], 1), (['base', 'side'], 3),
                                (['side', 'top'], 1), (['base', 'top'], 1), (['base', 'side', 'top'], 1), ([], 1)):
            with self.subTest(names=names):
                self.assertEqual(self.run_cli('push', *names)[0], expected)
        # The providers are handled in prerequisite order, whatever the order of the arguments:
        # with top (failed) last, a skip after a failure would otherwise never be seen.
        document = manifest()
        document['providers']['zz'] = copy.deepcopy(document['providers']['side'])
        service = PC.ProviderCache(document, PC.WorkTree(self.root, self.repo), self.registry, 'reg.example/cache',
                                   '', self.messages.append)
        code, out, err = self.run_cli('push', 'top', 'zz', service=service)
        self.assertEqual(code, 1)
        self.assertEqual([line.split()[2] for line in err.splitlines()], ['top', 'zz'])
        code, out, err = self.run_cli('push', service=service)
        self.assertEqual(code, 1)
        self.assertEqual([line.split()[2:4] for line in err.splitlines()],
                         [['side', 'skipped:'], ['top', 'failed:'], ['zz', 'skipped:']])

    def test_every_name_is_attempted_after_a_failure(self):
        self.cached('base', 'side')
        self.registry.images[self.service.ref('base')][1]['base.tar'] += b'\0'
        code, out, err = self.run_cli('restore', 'base', 'side')
        self.assertEqual(code, 1)
        self.assertFalse(os.path.lexists(self.root / 'P/base'))
        self.assertTrue((self.root / 'P/side/complete.json').is_file())

    def test_cache_off_no_docker_and_no_login_exit_3(self):
        self.cached('base')
        self.make('side')
        off = self.new_service(prefix='off')
        self.assertEqual(self.run_cli('restore', 'base', service=off),
                         (3, '', 'provider-cache: restore base skipped: cache is off\n'))
        self.assertEqual(self.run_cli('push', 'side', service=off),
                         (3, '', 'provider-cache: push side skipped: cache is off\n'))
        self.assertEqual(self.run_cli('restore', service=off)[0], 3)
        self.assertEqual(self.run_cli('push', service=off)[0], 3)
        self.registry.logged_in = False
        self.assertEqual(self.run_cli('restore', 'base')[0], 3)
        self.assertEqual(self.run_cli('push', 'side'),
                         (3, '', 'provider-cache: push side skipped: docker is not logged in to the cache registry\n'))
        self.registry.logged_in, self.registry.docker = True, False
        self.assertEqual(self.run_cli('restore', 'base'),
                         (3, '', 'provider-cache: restore base skipped: docker is not available\n'))
        self.assertEqual(self.run_cli('push', 'side')[0], 3)
        self.registry.docker, self.registry.state = True, 'error'
        self.assertEqual(self.run_cli('restore', 'base')[0], 1)
        self.assertEqual(self.run_cli('push', 'side')[0], 1)
        self.assertEqual(self.registry.published, 1)
        self.assertFalse(os.path.lexists(self.root / 'P/base'))

    def test_key(self):
        key = {name: self.service.key(name) for name in ('base', 'side', 'top')}
        self.assertEqual(self.run_cli('key', 'top'), (0, key['top'] + '\n', ''))
        self.assertEqual(self.run_cli('key'), (0, ''.join('%s\t%s\n' % (name, key[name])
                                                          for name in ('base', 'side', 'top')), ''))
        self.assertEqual(self.run_cli('key', 'top', 'base'),
                         (0, 'base\t%s\ntop\t%s\n' % (key['base'], key['top']), ''))
        code, out, err = self.run_cli('key', '--explain', 'top', 'base')
        self.assertEqual((code, err), (0, ''))
        decoder, documents, position = json.JSONDecoder(), [], 0
        while position < len(out):
            document, position = decoder.raw_decode(out, position)
            documents.append(document)
            position += 1
        self.assertEqual([(d['inputs']['provider'], d['key']) for d in documents],
                         [('base', key['base']), ('top', key['top'])])
        self.assertEqual(documents[1], {'key': key['top'], 'inputs': self.service.explain('top')})
        self.assertEqual(out, ''.join(json.dumps(d, indent=1, sort_keys=True) + '\n' for d in documents))
        self.assertEqual(self.run_cli('key', 'ghost'), (1, '', 'provider-cache: unknown provider ghost\n'))
        self.assertEqual(self.run_cli('key', 'base', 'ghost'), (1, '', 'provider-cache: unknown provider ghost\n'))
        os.unlink(self.root / 'status/fetch.json')
        code, out, err = self.run_cli('key', 'base', service=self.new_service())
        self.assertEqual((code, out), (1, ''))
        self.assertRegex(err, '^provider-cache: status/fetch.json cannot be read: ')

    def test_status(self):
        self.make('base')
        self.service.push('base')
        self.make('side', status='failed')
        (self.root / 'P/top').mkdir()
        key = {name: self.service.key(name) for name in ('base', 'side', 'top')}
        tar_sha = self.service.tree.pushed()[('base', key['base'])]
        code, out, err = self.run_cli('status')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out, 'base\t%s\tok\tpushed\t%s\nside\t%s\tfailed\tunknown\t-\ntop\t%s\tmissing\tunknown\t-\n'
                         % (key['base'], tar_sha, key['side'], key['top']))
        del self.registry.calls[:]
        self.assertEqual(self.run_cli('status')[0], 0)
        self.assertEqual(self.registry.calls, [])  # no registry query unless asked for
        code, out, err = self.run_cli('status', '--remote')
        self.assertEqual(code, 0)
        self.assertEqual([line.split('\t')[3] for line in out.splitlines()], ['present', 'absent', 'absent'])
        self.assertEqual([call[0] for call in self.registry.calls], ['probe'] * 3)

    def test_status_writes_the_index_of_what_is_cached(self):
        self.make('base')
        self.service.push('base')
        self.make('side')
        key = self.service.key('base')
        tar_sha = self.service.tree.pushed()[('base', key)]
        index = Path(self.tmp.name) / 'index.tsv'
        header = ('# Copyright 2026 uds-takserver authors\n# SPDX-License-Identifier: GPL-3.0-only\n'
                  '# Generated by provider_cache.py status --write-index; do not edit.\n'
                  '# Providers cached under their input keys. Restore needs no index: it\n'
                  '# derives the key and pulls <ref>. Keys are specific to:\n'
                  '# work root %s\n# repository %s\n# toolchain fingerprint %s\n# architecture amd64\n'
                  '# toolchain packages mode report\n'
                  '# name\tkey\tref\ttar sha256\n' % (self.root, self.repo, FINGERPRINT))
        row = 'base\t%s\treg.example/cache/base:%s\t%s\n' % (key, key, tar_sha)
        self.assertEqual(self.run_cli('status', '--write-index', str(index))[0], 0)
        self.assertEqual(index.read_text(), header + row)
        self.registry.images.clear()
        self.assertEqual(self.run_cli('status', '--remote', '--write-index', str(index))[0], 0)
        self.assertEqual(index.read_text(), header)  # the registry is asked, and it has nothing
        self.registry.state = 'present'
        self.assertEqual(self.run_cli('status', '--write-index', str(index), '--remote')[0], 0)
        self.assertEqual(len(index.read_text().splitlines()), 11 + 3)
        os.unlink(self.root / 'status/toolchain.json')
        self.registry.docker = False
        index.write_text('kept')
        code, out, err = self.run_cli('status', '--write-index', str(index), service=self.new_service())
        self.assertEqual(code, 1)
        self.assertEqual(index.read_text(), 'kept')

    def test_default_index_is_next_to_the_module(self):
        written = {}
        with mock.patch.object(PC.Path, 'write_text', lambda path, text: written.update({path: text})):
            self.assertEqual(self.run_cli('status', '--write-index')[0], 0)
        self.assertEqual(list(written), [HERE / 'cache-index.tsv'])

    def test_usage_errors_exit_2(self):
        for argv in ([], ['frobnicate'], ['key', '--nope'], ['restore', '--explain'], ['status', 'base']):
            with self.subTest(argv=argv), mock.patch.object(PC.sys, 'stderr', io.StringIO()), \
                    self.assertRaises(SystemExit) as caught:
                PC.main(argv, service=self.service)
            self.assertEqual(caught.exception.code, 2)

    def run_script(self, *argv, **environment):
        empty = Path(self.tmp.name) / 'empty-bin'
        empty.mkdir(exist_ok=True)
        env = {'PATH': str(empty), 'HOME': self.tmp.name, 'TAKX_PROTO_ROOT': str(self.root)}
        env.update(environment)
        done = subprocess.run([sys.executable, '-B', str(HERE / 'provider_cache.py'), *argv], env=env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return done.returncode, done.stdout.decode(), done.stderr.decode()

    def test_the_script_exits_with_the_code(self):
        # The real manifest against a temporary work root; docker is not on PATH.
        self.assertEqual(self.run_script('push', 'sqlcipher', TAKX_PROTO_CACHE='off'),
                         (3, '', 'provider-cache: push sqlcipher skipped: cache is off\n'))
        self.assertEqual(self.run_script('restore', 'sqlcipher', TAKX_PROTO_CACHE='off'),
                         (3, '', 'provider-cache: restore sqlcipher skipped: cache is off\n'))
        self.assertEqual(self.run_script('restore', 'sqlcipher'),
                         (3, '', 'provider-cache: restore sqlcipher skipped: docker is not available\n'))
        self.assertEqual(self.run_script('push', 'sqlcipher'),
                         (3, '', 'provider-cache: push sqlcipher skipped: status is missing, not ok\n'))
        self.assertEqual(self.run_script('restore', 'ghost'), (1, '', 'provider-cache: unknown provider ghost\n'))
        # The record in this work root describes another manifest's toolchain, and there is no docker.
        self.assertEqual(self.run_script('key', 'sqlcipher'), (
            1, '', 'provider-cache: no toolchain fingerprint is recorded for this manifest (status/toolchain.json) '
                   'and docker is not available to probe takx-desktop-proto:toolchain\n'))
        self.assertEqual(self.run_script('toolchain')[0], 1)
        real = json.loads((HERE / 'providers.json').read_text())['toolchain']
        self.write_json('status/toolchain.json', toolchain_record(block=real))
        self.assertEqual(self.run_script('toolchain'), (0, fingerprint(block=real) + '\tamd64\n', ''))
        code, out, err = self.run_script('toolchain', '--probe-script')
        self.assertEqual((code, err), (0, ''))
        self.assertEqual(out, PC.probe_script(real))
        code, out, err = self.run_script('key', 'sqlcipher')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('no fetched input matches', err)
        self.assertEqual(self.run_script()[0], 2)
        code, out, err = self.run_script('status', TAKX_PROTO_CACHE='off')
        self.assertEqual(code, 0)
        self.assertEqual({line.split('\t')[0] for line in out.splitlines()}, EXPECTED)
        self.assertEqual({tuple(line.split('\t')[1:]) for line in out.splitlines()},
                         {('-', 'absent', 'no-key', '-')})
        self.assertEqual(sorted(os.listdir(self.root)), ['P', 'status'])


class Details(ServiceCase):
    """Smaller promises: messages, formats and defences that are hard to reach."""

    def test_status_and_metadata_are_written_in_the_readable_form(self):
        self.make('base')
        original = json.loads((self.root / 'status/base.json').read_text())
        ref = self.service.push('base')
        labels, content = self.bundle('base')
        self.assertEqual(content['meta.json'].decode(),
                         json.dumps(json.loads(content['meta.json']), sort_keys=True, indent=1) + '\n')
        self.assertEqual(content['files.json'].decode(), json.dumps(
            json.loads(content['files.json']), sort_keys=True, separators=(',', ':')) + '\n')
        self.set_aside('base')
        self.service.restore('base')
        self.assertEqual((self.root / 'status/base.json').read_text(), json.dumps(
            dict(original, restored_from=ref, cache_key=self.service.key('base')), indent=1) + '\n')

    def test_archive_that_unpacks_to_something_else_is_refused(self):
        self.make('base')
        self.service.push('base')
        self.set_aside('base')
        unpack = self.service.tree.unpack

        def more(path, target):
            unpack(path, target)
            (Path(target) / 'beside').mkdir()
        self.service.tree.unpack = more
        self.assertEqual(self.refused(), 'base: archive unpacked to something else than base/')
        self.service.tree.unpack = lambda path, target: None
        self.assertEqual(self.refused(), 'base: archive unpacked to something else than base/')

    def test_unpack_without_extraction_filters_keeps_numeric_owners(self):
        calls = []

        def extractall(archive, path, **options):
            calls.append(options)
            if 'filter' in options:
                raise TypeError("extractall() got an unexpected keyword argument 'filter'")
        tar = Path(self.tmp.name) / 'x.tar'
        with tarfile.open(tar, 'w'):
            pass
        with mock.patch.object(tarfile.TarFile, 'extractall', extractall):
            PC.WorkTree.unpack(tar, self.tmp.name)
        self.assertEqual(calls, [{'numeric_owner': True, 'filter': 'fully_trusted'}, {'numeric_owner': True}])

    def test_unpack_reads_only_plain_tar(self):
        tar = Path(self.tmp.name) / 'x.tar.gz'
        with tarfile.open(tar, 'w:gz') as archive:
            archive.addfile(tarfile.TarInfo('p'))
        for reader in (lambda: PC.WorkTree.unpack(tar, self.tmp.name), lambda: PC.WorkTree.tar_members(tar),
                       lambda: PC.WorkTree.tar_entries(tar, 'p')):
            with self.assertRaises(tarfile.ReadError):
                reader()

    def test_refusal_messages_name_what_is_wrong(self):
        records = [{'kind': 'odd', 'name': 'nodigest', 'verified': True, 'expected_sha256': None},
                   {'kind': 'bad', 'name': 'unchecked', 'verified': 'yes', 'expected_sha256': '1' * 64}]
        for selector, message in (({'kind': 'odd'}, '^fetched input nodigest has no digest$'),
                                  ({'kind': 'bad'}, '^fetched input unchecked is not verified$'),
                                  ({'kind': 'none'}, '^no fetched input matches kind=none$'),
                                  ({'path': 'x'}, '^no fetched input matches path=x$')):
            with self.subTest(selector=selector), self.assertRaisesRegex(PC.CacheError, message):
                PC.select_fetched(records, [selector], '/root')
        for name, key, message in (('p', 'short', "^cannot name an image for 'p'$"),
                                   ('P', 'a' * 64, "^cannot name an image for 'P'$")):
            with self.assertRaisesRegex(PC.CacheError, message):
                PC.image_ref('reg', name, key)
        with self.assertRaisesRegex(PC.CacheError, '^unknown provider ghost$'):
            self.service.push('ghost')
        with self.assertRaisesRegex(PC.CacheError, '^unknown provider ghost$'):
            self.service.restore('ghost')
        with self.assertRaisesRegex(PC.CacheError, '^unknown provider ghost$'):
            self.service.key('ghost')
        tree = PC.WorkTree(self.root, self.repo)
        os.mkfifo(self.repo / 'takx/pipe')
        with self.assertRaisesRegex(PC.CacheError, '^recipe path %s is not a regular file$'
                                    % re.escape(str(self.repo / 'takx/pipe'))):
            tree.recipe_files(['takx/pip?'], [])
        with self.assertRaisesRegex(PC.CacheError, r'^lock takx/none\.lock cannot be read: .*No such file'):
            tree.locks(['takx/none.lock'])
        os.unlink(self.root / 'status/images.json')
        with self.assertRaisesRegex(PC.CacheError, r'^status/images\.json cannot be read: .*No such file'):
            tree.legacy_toolchain()

    def test_key_document_refusals_name_the_provider_and_the_input(self):
        for field in ('toolchain', 'arch', 'root', 'repo'):
            for empty in ('', None):
                with self.subTest(field=field), self.assertRaisesRegex(PC.CacheError, '^base: no %s identity$' % field):
                    PC.key_document(manifest(), 'base', facts(**{field: empty}), {})
            missing = facts()
            del missing[field]
            with self.assertRaisesRegex(PC.CacheError, '^base: no %s identity$' % field):
                PC.key_document(manifest(), 'base', missing, {})
        with self.assertRaisesRegex(PC.CacheError, '^base: no recipe files$'):
            PC.key_document(manifest(), 'base', facts(recipe_files={}), {})
        with self.assertRaisesRegex(PC.CacheError, '^top: prerequisite keys incomplete$'):
            PC.key_document(manifest(), 'top', facts(), {})
        with self.assertRaisesRegex(PC.CacheError, '^top: bad key for prerequisite base$'):
            PC.key_document(manifest(), 'top', facts(), {'base': 'x'})

    def test_member_checks_at_the_edges(self):
        top = [('p', 'dir', '')]
        self.assertEqual(PC.check_members('p', top + [('p/d/', 'dir', ''), ('p/d/f', 'file', '')]), 3)
        self.assertEqual(PC.check_members('p', [('p/', 'dir', '')]), 1)
        self.assertEqual(PC.check_members('p', iter(top)), 1)
        cases = (
            ([('a/x', 'file', '')], "^archive member is outside p/: 'a/x'$"),
            ([('q/x', 'file', '')], "^archive member is outside p/: 'q/x'$"),
            ([('a', 'dir', '')], "^archive member is outside p/: 'a'$"),
            ([('', 'file', '')], "^archive member is not a plain relative path: ''$"),
            ([('p/x\0y', 'file', '')], '^archive member is not a plain relative path'),
            ([('p/d', 'dir', ''), ('p/d/l', 'symlink', '../a/..')], r"^symlink 'p/d/l' has \"..\" after a name"),
            ([('p/l', 'symlink', 'a/..')], r"^symlink 'p/l' has \"..\" after a name in its target$"),
            ([('p/l', 'symlink', 'a\0b')], "^symlink 'p/l' has an absolute or empty target$"),
            ([('p/l', 'symlink', '../p2')], "^symlink 'p/l' leaves the provider directory$"),
            ([('p/l', 'symlink', '../pp/x')], "^symlink 'p/l' leaves the provider directory$"),
            ([('p/f', 'file', ''), ('p/h', 'hardlink', '')], '^archive hard link target is not a plain relative path'),
            ([('p/f', 'file', ''), ('p/h', 'hardlink', 'q/f')], "^archive hard link target is outside p/: 'q/f'$"),
            ([('p/h', 'hardlink', 'p')], "^hard link 'p/h' does not name an earlier file$"),
            ([('p/l', 'symlink', 'x'), ('p/h', 'hardlink', 'p/l')], "^hard link 'p/h' does not name an earlier file$"),
            ([('p/f', 'file', ''), ('p/h', 'hardlink', 'p/f'), ('p/h/x', 'file', '')],
             "^archive member 'p/h/x' is below a link or file$"),
            ([('p/a', 'dir', ''), ('p/a/l', 'symlink', '.'), ('p/a/l/b/c', 'file', '')],
             "^archive member 'p/a/l/b/c' is below a link or file$"),
            ([('p/f', 'file', ''), ('p/f', 'dir', '')], "^archive names 'p/f' twice$"),
            ([('p/d', 'dir', ''), ('p/d/', 'dir', '')], "^archive names 'p/d' twice$"),
            ([('p/x', 'weird', '')], "^archive member 'p/x' is a device or other special file$"),
        )
        for extra, message in cases:
            with self.subTest(extra=extra), self.assertRaisesRegex(PC.CacheError, message):
                PC.check_members('p', top + extra)
        for members, message in (([], '^archive has no top-level p directory$'),
                                 ([('p/f', 'file', '')], '^archive has no top-level p directory$'),
                                 ([('p', 'file', '')], '^archive top-level entry is not a directory$'),
                                 ([('p', 'symlink', 'x')], '^archive top-level entry is not a directory$'),
                                 ([('p', 'other', '')], '^archive top-level entry is not a directory$')):
            with self.subTest(members=members), self.assertRaisesRegex(PC.CacheError, message):
                PC.check_members('p', members)
        # A member whose name merely starts like the provider is outside it.
        with self.assertRaisesRegex(PC.CacheError, "outside base/: 'base-2/x'"):
            PC.check_members('base', [('base', 'dir', ''), ('base-2/x', 'file', '')])


class DockerDetails(unittest.TestCase):
    """More of DockerRegistry against a stub docker; no daemon involved."""

    STUB = r"""#!/bin/bash
dir="$(dirname "$0")"
printf '%s\n' "$*" >> "$dir/calls"
if [[ -f "$dir/script-$1" ]]; then source "$dir/script-$1"; fi
exit 0
"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / 'docker').write_text(self.STUB)
        os.chmod(self.dir / 'docker', 0o755)
        self.registry = PC.DockerRegistry(30, str(self.dir / 'docker'))
        self.context = self.dir / 'context'
        self.context.mkdir()
        (self.context / 'p.tar').write_bytes(b'archive')

    def script(self, command, text):
        (self.dir / ('script-' + command)).write_text(text + '\n')

    def calls(self):
        path = self.dir / 'calls'
        return path.read_text().splitlines() if path.exists() else []

    def test_defaults(self):
        registry = PC.DockerRegistry()
        self.assertEqual((registry.timeout, registry.docker), (3600, 'docker'))

    def test_commands_get_no_input_and_their_output_is_captured(self):
        self.script('image', 'if read -r -t 1 line; then echo "got input"; else echo "sha256:abc  "; fi\n'
                             'echo noise >&2')
        (self.dir / 'input').write_text('a line the command must not get\n')
        saved = os.dup(0)
        try:
            with open(self.dir / 'input') as stream:
                os.dup2(stream.fileno(), 0)
            self.assertEqual(self.registry.image_id('some:image'), 'sha256:abc')
        finally:
            os.dup2(saved, 0)
            os.close(saved)
        self.assertEqual(self.calls(), ['image inspect --format {{.Id}} some:image'])

    def test_failure_reports_the_command_and_the_end_of_its_errors(self):
        text = ''.join('%04d ' % number for number in range(200))
        self.script('pull', 'printf "%%s\\n  " "%s" >&2; exit 7' % text)
        with self.assertRaises(PC.CacheError) as caught:
            self.registry.fetch('reg/p:tag', self.dir, ['p.tar'])
        self.assertEqual(str(caught.exception), 'docker pull failed: ' + text.strip()[-400:])
        self.assertEqual(len(text.strip()[-400:]), 400)
        self.script('import', 'cat > /dev/null; printf "%%s\\n  " "%s" >&2; exit 7' % text)
        with self.assertRaises(PC.CacheError) as caught:
            self.registry.publish('reg/p:tag', self.context, ['p.tar'], {})
        self.assertEqual(str(caught.exception), 'docker import failed (7): ' + text.strip()[-400:])
        self.script('pull', 'printf "\\377\\376 bad bytes" >&2; exit 1')
        with self.assertRaisesRegex(PC.CacheError, '^docker pull failed: .* bad bytes$'):
            self.registry.fetch('reg/p:tag', self.dir, ['p.tar'])

    def test_missing_docker_is_an_error_of_the_command(self):
        missing = PC.DockerRegistry(30, str(self.dir / 'missing'))
        with self.assertRaisesRegex(PC.CacheError, r'^docker manifest: \[Errno 2\]'):
            missing.probe('reg/p:tag')
        with self.assertRaisesRegex(PC.CacheError, r'^docker import: \[Errno 2\]'):
            missing.publish('reg/p:tag', self.context, ['p.tar'], {})
        with self.assertRaisesRegex(PC.CacheError, r'^docker pull: \[Errno 2\]'):
            missing.fetch('reg/p:tag', self.dir, ['p.tar'])
        with self.assertRaisesRegex(PC.CacheError, r'^docker image: \[Errno 2\]'):
            missing.image_id('some:image')

    def test_command_that_takes_too_long_is_an_error(self):
        slow = PC.DockerRegistry(0.3, str(self.dir / 'docker'))
        self.script('manifest', 'exec sleep 20')
        with self.assertRaisesRegex(PC.CacheError, '^docker manifest: .*timed out after 0.3 seconds'):
            slow.probe('reg/p:tag')
        self.script('import', 'cat > /dev/null; exec sleep 20')
        with self.assertRaisesRegex(
                PC.CacheError, r'^docker import failed \(interrupted: .*timed out after 0.3 seconds'):
            slow.publish('reg/p:tag', self.context, ['p.tar'], {})
        self.assertNotIn('push', [call.split()[0] for call in self.calls()])

    def test_import_that_stops_reading_is_an_error(self):
        (self.context / 'p.tar').write_bytes(b'\0' * (4 << 20))
        self.script('import', 'echo "no space left" >&2; exit 1')
        with self.assertRaisesRegex(PC.CacheError, r'^docker import failed \(.*\): no space left$'):
            self.registry.publish('reg/p:tag', self.context, ['p.tar'], {})
        self.assertEqual([call.split()[0] for call in self.calls()], ['import'])

    def test_import_whose_pipe_cannot_even_be_closed_is_still_a_refusal(self):
        events = []

        class Pipe:
            def write(self, data):
                raise BrokenPipeError(32, 'Broken pipe')

            def close(self):
                events.append('close')
                raise BrokenPipeError(32, 'Broken pipe')

        class Process:
            stdin = Pipe()

            def kill(self):
                events.append('kill')

            def wait(self, timeout=None):
                events.append('wait')
                return -9

        def popen(args, stdin=None, stdout=None, stderr=None):
            stderr.write(b'daemon went away\n')
            return Process()
        with mock.patch.object(PC.subprocess, 'Popen', popen), self.assertRaises(PC.CacheError) as caught:
            self.registry.publish('reg/p:tag', self.context, ['p.tar'], {})
        self.assertEqual(str(caught.exception),
                         'docker import failed (interrupted: [Errno 32] Broken pipe): daemon went away')
        self.assertEqual(events, ['kill', 'wait', 'close'])
        self.assertEqual(self.calls(), [])  # nothing is pushed or removed after a failed import

    def test_probe_reads_both_streams_in_any_case(self):
        ref = 'ghcr.io/owner/cache/p:tag'
        for text, expected in (('echo "MANIFEST UNKNOWN" >&2', 'absent'), ('echo "Manifest Unknown"', 'absent'),
                               ('echo "No such manifest: x"', 'absent'), ('echo "NAME UNKNOWN: repo" >&2', 'absent'),
                               ('echo "name unknown"', 'absent'),
                               ('echo "UNAUTHORIZED" >&2', 'unauthorized'), ('echo "Unauthorized"', 'unauthorized'),
                               ('echo "DENIED: requested access" >&2', 'unauthorized'),
                               ('echo "denied"', 'unauthorized'),
                               ('echo "Authentication Required" >&2', 'unauthorized'),
                               ('echo "authentication required"', 'unauthorized'),
                               ('echo "No Basic Auth Credentials" >&2', 'unauthorized'),
                               ('echo "no basic auth credentials"', 'unauthorized'),
                               ('printf "\\377 manifest unknown" >&2', 'absent'),
                               ('printf "\\377 denied"', 'unauthorized'),
                               ('echo "i/o timeout" >&2', 'error'), ('echo "unknown"', 'error'), ('true', 'error')):
            self.script('manifest', text + '; exit 1')
            with self.subTest(text=text):
                self.assertEqual(self.registry.probe(ref), expected)
        # A tag that exists is present whatever the command prints.
        self.script('manifest', 'echo "manifest unknown"; echo denied >&2')
        self.assertEqual(self.registry.probe(ref), 'present')
        # 'not found' answers that name the manifest win over an authentication hint.
        self.script('manifest', 'echo "manifest unknown: denied" >&2; exit 1')
        self.assertEqual(self.registry.probe(ref), 'absent')

    def test_loopback_detection_looks_at_the_host_only(self):
        for ref, insecure in (('localhost/p:t', True), ('localhost:5000/a/p:t', True), ('127.0.0.1/p:t', True),
                              ('127.0.0.1:5000/p:t', True), ('[::1]:5000/p:t', True), ('[::1]/p:t', False),
                              ('ghcr.io/localhost/p:t', False), ('ghcr.io/127.0.0.1:5000/p:t', False),
                              ('localhost.evil.example/p:t', False), ('evil.example:localhost/p:t', False),
                              ('127.0.0.10:5000/p:t', False), ('xlocalhost:5000/p:t', False)):
            self.registry.probe(ref)
            with self.subTest(ref=ref):
                self.assertEqual(self.calls()[-1], 'manifest inspect %s%s' % ('--insecure ' if insecure else '', ref))

    def test_fetch_reads_the_labels_of_the_image(self):
        for text, expected in (('echo null', {}), ('true', {}), ('echo "{}"', {}),
                               ("""echo '{"a":"1","b":"two words"}'""", {'a': '1', 'b': 'two words'})):
            self.script('image', 'case "$*" in *Labels*) %s;; esac' % text)
            with self.subTest(text=text):
                self.assertEqual(self.registry.fetch('reg/p:tag', self.dir, []), (expected, None))
        self.script('create', 'echo "  abc123  "')
        self.script('cp', 'echo "$2 $3" >> "$dir/copies"')
        self.registry.fetch('reg/p:tag', self.dir / 'target', ['p.tar', 'meta.json'])
        self.assertEqual((self.dir / 'copies').read_text().splitlines(),
                         ['abc123:/p.tar %s/target/p.tar' % self.dir,
                          'abc123:/meta.json %s/target/meta.json' % self.dir])
        self.assertEqual(self.calls()[-2:], ['rm -f abc123', 'rmi reg/p:tag'])

    def test_fetch_names_the_registry_digest_that_was_pulled(self):
        one, two = 'sha256:' + '1' * 64, 'sha256:' + '2' * 64
        for listed, expected in (
                ('["reg/p@%s"]' % one, one),
                ('["other/p@%s","reg/p@%s"]' % (two, one), one),
                ('["reg/p@%s","reg/p@%s"]' % (one, one), one),
                ('["reg/p@%s","reg/p@%s"]' % (one, two), None),      # two digests: which one is not known
                ('["reg/pp@%s","xreg/p@%s","reg/p:tag@%s"]' % (one, one, one), None),
                ('["reg/p@sha256:abc","reg/p@%s0","reg/p@%s"]' % (one, one.upper()), None),
                ('[7,null,{"reg/p@%s":1}]' % one, None),
                ('"reg/p@%s"' % one, None), ('null', None), ('[]', None), ('{', None), ('', None)):
            self.script('image', 'case "$*" in *RepoDigests*) echo \'%s\';; *) echo "{}";; esac' % listed)
            with self.subTest(listed=listed):
                self.assertEqual(self.registry.fetch('reg/p:tag', self.dir, []), ({}, expected))
        self.assertEqual(self.calls()[-5:-2], ['image inspect --format {{json .Config.Labels}} reg/p:tag',
                                               'image inspect --format {{json .RepoDigests}} reg/p:tag',
                                               'create reg/p:tag /nonexistent'])
        # A registry with a port: the repository is everything before the tag.
        self.script('image', 'case "$*" in *RepoDigests*) echo \'["127.0.0.1:5000/c/p@%s"]\';; *) echo "{}";; esac'
                    % two)
        self.assertEqual(self.registry.fetch('127.0.0.1:5000/c/p:tag', self.dir, []), ({}, two))
        # The second inspection failing means "not known", never an error of the pull.
        for ending in ('exit 1', 'kill -9 $$'):
            self.script('image', 'case "$*" in *RepoDigests*) echo \'["reg/p@%s"]\'; %s;; *) echo "{}";; esac'
                        % (one, ending))
            self.assertEqual(self.registry.fetch('reg/p:tag', self.dir, []), ({}, None), ending)

    def test_fetch_can_keep_the_image_and_tells_a_missing_file_from_a_failed_copy(self):
        digest = 'sha256:' + '3' * 64
        self.script('image', 'case "$*" in *RepoDigests*) echo \'["reg/p@%s"]\';; *) echo "{}";; esac' % digest)
        self.registry.fetch('reg/p:tag', self.dir, ['p.tar'], keep=True)
        self.assertEqual(self.calls()[-2:], ['cp :/p.tar %s/p.tar' % self.dir, 'rm -f '])
        self.registry.forget('reg/p:tag')
        self.assertEqual(self.calls()[-1], 'rmi reg/p:tag')
        self.script('rmi', 'echo "image is being used" >&2; exit 1')
        self.registry.forget('reg/p:tag')  # cleaning up may fail
        # ... and a failing clean-up does not hide what went wrong before it.
        self.script('cp', 'echo "write: broken pipe" >&2; exit 1')
        with self.assertRaisesRegex(PC.CacheError, '^docker cp failed: write: broken pipe$'):
            self.registry.fetch('reg/p:tag', self.dir, ['p.tar'])
        (self.dir / 'script-rmi').unlink()
        (self.dir / 'script-cp').unlink()
        for text in ('Error response from daemon: Could not find the file /meta.json in container abc',
                     'lstat /var/lib/docker/overlay2/x/merged/meta.json: no such file or directory'):
            self.script('cp', 'echo "%s" >&2; exit 1' % text)
            for keep in (False, True):  # a failure never leaves the image behind
                with self.subTest(text=text, keep=keep), self.assertRaisesRegex(
                        PC.MissingFile, '^the image holds no /meta.json$') as caught:
                    self.registry.fetch('reg/p:tag', self.dir, ['meta.json', 'p.tar'], keep=keep)
                self.assertEqual(caught.exception.digest, digest)
                self.assertEqual(self.calls()[-3:], ['cp :/meta.json %s/meta.json' % self.dir, 'rm -f ',
                                                     'rmi reg/p:tag'])
        self.script('cp', 'echo "write %s/p.tar: no space left on device" >&2; exit 1' % self.dir)
        with self.assertRaisesRegex(PC.CacheError, '^docker cp failed: write .* no space left on device$') as caught:
            self.registry.fetch('reg/p:tag', self.dir, ['p.tar'], keep=True)
        self.assertNotIsInstance(caught.exception, PC.MissingFile)
        self.assertEqual(self.calls()[-1], 'rmi reg/p:tag')
        # Only the end of a long message is kept, and what is said there decides.
        self.script('cp', 'printf "%s" >&2; printf "y%%.0s" {1..400} >&2; exit 1' % 'no such file ')
        with self.assertRaises(PC.CacheError) as caught:
            self.registry.fetch('reg/p:tag', self.dir, ['p.tar'])
        self.assertEqual(str(caught.exception), 'docker cp failed: ' + 'y' * 400)
        self.assertNotIsInstance(caught.exception, PC.MissingFile)
        self.script('cp', 'printf "x%.0s" {1..600} >&2; echo " No Such File or directory" >&2; exit 1')
        with self.assertRaisesRegex(PC.MissingFile, '^the image holds no /p.tar$'):
            self.registry.fetch('reg/p:tag', self.dir, ['p.tar'])
        self.script('cp', 'echo " COULD NOT FIND THE FILE /p.tar in container" >&2; exit 1')
        with self.assertRaisesRegex(PC.MissingFile, '^the image holds no /p.tar$'):
            self.registry.fetch('reg/p:tag', self.dir, ['p.tar'])

    def test_digest_is_what_the_registry_serves_now_or_nothing(self):
        digest = 'sha256:' + '4' * 64
        for text, expected in (
                ('echo \'{"Ref":"reg/p:tag","Descriptor":{"digest":"%s","size":480}}\'' % digest, digest),
                ('echo \'[{"Descriptor":{"digest":"%s"}}]\'' % digest, None),   # an image index
                ('echo \'{"Descriptor":{"digest":"sha256:abc"}}\'', None),
                ('echo \'{"Descriptor":{"digest":7}}\'', None),
                ('echo \'{"Descriptor":null}\'', None), ('echo \'{"digest":"%s"}\'' % digest, None),
                ('echo "{"', None), ('true', None),
                ('echo \'{"Descriptor":{"digest":"%s"}}\'; exit 1' % digest, None),
                ('echo \'{"Descriptor":{"digest":"%s"}}\'; kill -9 $$' % digest, None)):
            self.script('manifest', text)
            with self.subTest(text=text):
                self.assertEqual(self.registry.digest('reg/p:tag'), expected)
        self.assertEqual(self.calls()[-1], 'manifest inspect --verbose reg/p:tag')
        for ref, insecure in (('localhost:5000/c/p:tag', True), ('127.0.0.1:5000/c/p:tag', True),
                              ('ghcr.io/c/p:tag', False), ('localhost.example:5000/c/p:tag', False)):
            self.registry.digest(ref)
            self.assertEqual(self.calls()[-1], 'manifest inspect --verbose %s%s' % ('--insecure ' if insecure else '',
                                                                                    ref))

    def test_toolchain_probe_runs_offline_and_reads_the_first_layer(self):
        self.script('run', 'echo "arch	amd64"; echo warning >&2')
        self.assertEqual(self.registry.probe_toolchain('some:image', 'printf x'), 'arch\tamd64\n')
        self.assertEqual(self.calls()[-1], 'run --rm --network none --entrypoint /bin/bash some:image -c printf x')
        self.script('run', 'echo "no such image" >&2; exit 125')
        with self.assertRaisesRegex(PC.CacheError, '^docker run failed: no such image$'):
            self.registry.probe_toolchain('some:image', 'true')
        one, two = 'sha256:' + '5' * 64, 'sha256:' + '6' * 64
        for text, expected in (('echo \'["%s","%s"]\'' % (one, two), one), ('echo \'["%s"]\'' % two, two),
                               ('echo "[]"', None), ('echo null', None), ('echo \'"%s"\'' % one, None),
                               ('echo "[7]"', None), ('echo "{"', None), ('true', None),
                               ('echo \'[7,"%s"]\'' % one, None), ('echo \'[null,"%s"]\'' % one, None),
                               ('echo \'["%s"]\'; exit 1' % one, None), ('echo \'["%s"]\'; kill -9 $$' % one, None)):
            self.script('image', text)
            with self.subTest(text=text):
                self.assertEqual(self.registry.base_layer('some:image'), expected)
        self.assertEqual(self.calls()[-1], 'image inspect --format {{json .RootFS.Layers}} some:image')

    def test_adopt_adds_one_layer_to_a_local_image_and_pushes_it(self):
        (self.context / 'meta.json').write_text('{}')
        self.script('create', 'echo "  abc123  "')
        self.registry.adopt('reg/p:old', 'reg/p:new', self.context, ['meta.json', 'p.tar'],
                            {'z.label': 'a "quoted" value', 'a.label': 'new'})
        self.assertEqual(self.calls(), [
            'create reg/p:old /nonexistent', 'cp %s/meta.json abc123:/meta.json' % self.context,
            'cp %s/p.tar abc123:/p.tar' % self.context,
            'commit --change LABEL "a.label"="new" --change LABEL "z.label"="a \\"quoted\\" value" abc123 reg/p:new',
            'rm -f abc123', 'push -q reg/p:new', 'rmi reg/p:new'])
        for failing, expected in (('create', ['create']), ('cp', ['create', 'cp', 'rm']),
                                  ('commit', ['create', 'cp', 'cp', 'commit', 'rm']),
                                  ('push', ['create', 'cp', 'cp', 'commit', 'rm', 'push', 'rmi'])):
            self.script(failing, 'echo broken >&2; exit 1')
            (self.dir / 'calls').unlink()
            with self.subTest(failing=failing), self.assertRaisesRegex(
                    PC.CacheError, '^docker %s failed: broken$' % failing):
                self.registry.adopt('reg/p:old', 'reg/p:new', self.context, ['meta.json', 'p.tar'], {})
            self.assertEqual([call.split()[0] for call in self.calls()], expected)
            (self.dir / ('script-' + failing)).unlink()
        self.script('rm', 'exit 1')
        self.script('rmi', 'exit 1')
        self.registry.adopt('reg/p:old', 'reg/p:new', self.context, [], {})  # cleaning up may fail
        self.assertEqual(self.calls()[-4:], ['commit  reg/p:new', 'rm -f ', 'push -q reg/p:new', 'rmi reg/p:new'])

    def test_fetch_cleans_up_whatever_fails(self):
        for failing, expected in (('image', ['pull', 'image', 'rmi']),
                                  ('create', ['pull', 'image', 'image', 'create', 'rmi']),
                                  ('cp', ['pull', 'image', 'image', 'create', 'cp', 'rm', 'rmi'])):
            self.script(failing, 'echo broken >&2; exit 1')
            (self.dir / 'calls').unlink(missing_ok=True)
            with self.subTest(failing=failing), self.assertRaisesRegex(
                    PC.CacheError, 'docker %s failed: broken' % failing):
                self.registry.fetch('reg/p:tag', self.dir, ['p.tar'])
            self.assertEqual([call.split()[0] for call in self.calls()], expected)
            (self.dir / ('script-' + failing)).unlink()
        # Cleaning up may fail; the result still counts.
        self.script('rm', 'exit 1')
        self.script('rmi', 'exit 1')
        self.assertEqual(self.registry.fetch('reg/p:tag', self.dir, []), ({}, None))
        self.registry.publish('reg/p:tag', self.context, ['p.tar'], {})

    def test_publish_writes_one_flat_layer_with_quoted_labels(self):
        (self.context / 'sub').mkdir()
        (self.context / 'sub/inner').write_text('not listed')
        (self.context / 'meta.json').write_text('{}')
        self.script('import', 'cat > "$dir/layer.tar"')
        self.registry.publish('reg/p:tag', self.context, ['meta.json', 'p.tar'],
                              {'z.label': 'a "quoted" value', 'a.label': 'new\nline'})
        self.assertEqual(self.calls()[0], 'import --change LABEL "a.label"="new\\nline" '
                                          '--change LABEL "z.label"="a \\"quoted\\" value" - reg/p:tag')
        with tarfile.open(self.dir / 'layer.tar') as layer:
            self.assertEqual([(member.name, member.isreg(), member.size) for member in layer],
                             [('meta.json', True, 2), ('p.tar', True, 7)])
        # A directory is stored as itself, never with what is below it.
        self.registry.publish('reg/p:tag', self.context, ['sub'], {})
        with tarfile.open(self.dir / 'layer.tar') as layer:
            self.assertEqual(layer.getnames(), ['sub'])

    def test_command_ended_by_a_signal_has_failed_whatever_it_printed(self):
        self.script('pull', 'kill -KILL $$')
        with self.assertRaisesRegex(PC.CacheError, '^docker pull failed: $'):
            self.registry.fetch('reg/p:tag', self.dir, [])
        self.script('image', 'echo sha256:abc; kill -KILL $$')
        self.assertIsNone(self.registry.image_id('some:image'))
        self.script('manifest', 'echo "{}"; kill -KILL $$')
        self.assertEqual(self.registry.probe('reg/p:tag'), 'error')
        self.script('import', 'cat > /dev/null; kill -KILL $$')
        with self.assertRaisesRegex(PC.CacheError, r'^docker import failed \(-9\): $'):
            self.registry.publish('reg/p:tag', self.context, ['p.tar'], {})
        self.assertNotIn('push', [call.split()[0] for call in self.calls()])
        # A copy out of the pulled image that was killed has not copied the file.
        for name in ('pull', 'image', 'manifest'):
            (self.dir / ('script-' + name)).unlink()
        self.script('image', 'echo "{}"')
        self.script('cp', 'echo partly > "$3"; kill -KILL $$')
        with self.assertRaisesRegex(PC.CacheError, '^docker cp failed: $'):
            self.registry.fetch('reg/p:tag', self.dir, ['p.tar'])
        self.assertEqual(self.calls()[-2:], ['rm -f ', 'rmi reg/p:tag'])


class MoreDetails(ServiceCase):
    def test_tree_with_many_entries_round_trips(self):
        top = self.make('base')
        for number in range(300):
            (top / ('file-%03d' % number)).write_text(str(number))
        good = snapshot(top)
        self.assertEqual(len(good), 309)
        self.service.push('base')
        self.set_aside('base')
        self.service.restore('base')
        self.assertEqual(snapshot(top), good)
        many = [{'path': 'f%d' % number, 'type': 'dir', 'mode': '0755'} for number in range(300)]
        self.assertEqual(PC.compare_entries(many, copy.deepcopy(many)), [])
        twice = ['a file manifest lists a path twice']
        self.assertEqual(PC.compare_entries(many + many[:1], copy.deepcopy(many)), twice)
        self.assertEqual(PC.compare_entries(many, copy.deepcopy(many) + many[-1:]), twice)

    def test_decisions_fail_closed_on_unknown_registry_answers(self):
        for remote in ('error', 'ERROR', '', 'Zzz', 'zzz', 'absent ', 'Absent', 'presently', None, 0):
            with self.subTest(remote=remote):
                self.assertEqual(PC.decide_restore(True, False, True, remote),
                                 ('refuse', 'the registry could not be queried'))
                self.assertEqual(PC.decide_push(True, 'ok', True, remote),
                                 ('refuse', 'the registry could not be queried'))
        self.assertEqual(PC.decide_restore(True, False, True, 'absent'), ('skip', 'nothing cached under this key'))
        self.assertEqual(PC.decide_restore(True, False, True, 'present'), ('restore', 'cached under this key'))
        self.assertEqual(PC.decide_restore(True, False, True, 'unauthorized'),
                         ('skip', 'docker is not logged in to the cache registry'))
        self.assertEqual(PC.decide_restore(False, True, False, 'error'), ('skip', 'cache is off'))
        self.assertEqual(PC.decide_restore(True, True, False, 'present'),
                         ('refuse', 'the provider directory already exists and is never overwritten'))
        self.assertEqual(PC.decide_restore(True, False, False, 'error'),
                         ('skip', 'a prerequisite provider is not ok here'))
        self.assertEqual(PC.decide_push(False, 'failed', False, 'error'), ('skip', 'cache is off'))
        self.assertEqual(PC.decide_push(True, 'failed', False, 'error'), ('skip', 'status is failed, not ok'))
        self.assertEqual(PC.decide_push(True, 'ok', False, 'present'),
                         ('refuse', 'status is ok but the completion marker is missing'))
        self.assertEqual(PC.decide_push(True, 'ok', True, 'present'), ('present', 'already cached under this key'))
        self.assertEqual(PC.decide_push(True, 'ok', True, 'unauthorized'),
                         ('skip', 'docker is not logged in to the cache registry'))
        self.assertEqual(PC.decide_push(True, 'ok', True, 'absent'), ('push', 'not cached yet'))
        for status in ('OK', 'ok ', 'o', 'okay', 'a', 'zz', '', None, True):
            self.assertEqual(PC.decide_push(True, status, True, 'absent')[0], 'skip', status)

    def test_decisions_for_every_combination(self):
        remotes = ('present', 'absent', 'unauthorized', 'error', None)
        for enabled in (True, False):
            for flag in (True, False):
                for other in (True, False):
                    for remote in remotes:
                        # push: enabled, status ok?, marker present?
                        answers = {'present': 'present', 'absent': 'push', 'unauthorized': 'skip'}
                        expected = ('skip' if not enabled or not flag else 'refuse' if not other else
                                    answers.get(remote, 'refuse'))
                        got = PC.decide_push(enabled, 'ok' if flag else 'failed', other, remote)
                        self.assertEqual(got[0], expected, (enabled, flag, other, remote))
                        self.assertTrue(got[1])
                        # restore: enabled, directory exists?, prerequisites ok?
                        answers = {'present': 'restore', 'absent': 'skip', 'unauthorized': 'skip'}
                        expected = ('skip' if not enabled else 'refuse' if flag else 'skip' if not other else
                                    answers.get(remote, 'refuse'))
                        got = PC.decide_restore(enabled, flag, other, remote)
                        self.assertEqual(got[0], expected, (enabled, flag, other, remote))
                        self.assertTrue(got[1])
        # Whatever the decision, only 'push' publishes and only 'restore' fetches.
        self.make('base')
        for remote in ('unauthorized', 'error', 'present'):
            self.registry.state = remote
            try:
                self.service.push('base')
            except (PC.Skip, PC.CacheError):
                pass
            self.assertEqual(self.registry.published, 0, remote)
        self.set_aside('base')
        for remote in ('unauthorized', 'error', 'absent'):
            self.registry.state = remote
            with self.assertRaises((PC.Skip, PC.CacheError)):
                self.service.restore('base')
            self.assertEqual(self.registry.fetched, [], remote)

    def test_bundle_identity_is_exact_in_both_directions(self):
        key, tar, files = 'b' * 64, 'c' * 64, 'd' * 64
        good = dict(name='mm', key=key, root='/r',
                    meta={'schema': 2, 'provider': 'mm', 'key': key, 'root': '/r', 'tar_sha256': tar,
                          'files_sha256': files, 'prerequisite_markers': {}},
                    status={'stage': 'mm', 'status': 'ok'},
                    labels={'dev.uds.takx.provider': 'mm', 'dev.uds.takx.key': key, 'dev.uds.takx.tar-sha256': tar},
                    tar_sha256=tar, recorded_tar_sha256=tar, files_sha256=files, local_markers={})
        PC.check_bundle(**good)

        def with_meta(**fields):
            return dict(meta=dict(good['meta'], **fields))

        def with_label(label, value):
            return dict(labels=dict(good['labels'], **{'dev.uds.takx.' + label: value}))
        cases = []
        for other in ('a' * 64, 'e' * 64):  # one that sorts before every digest used here, one after
            cases += [
                (dict(tar_sha256=other), 'mm: archive digest differs from the recorded one'),
                (dict(recorded_tar_sha256=other), 'mm: archive digest differs from the recorded one'),
                (with_label('tar-sha256', other), 'mm: archive digest differs from the image label'),
                (with_label('key', other), 'mm: image is labelled with another key'),
                (with_meta(key=other), 'mm: bundle is for another provider or key'),
                (with_meta(tar_sha256=other), 'mm: bundle metadata names another archive'),
                (with_meta(files_sha256=other), 'mm: file manifest differs from the recorded one'),
                (dict(files_sha256=other), 'mm: file manifest differs from the recorded one'),
                (dict(local_markers={'dep': other}), 'mm: it was built against other prerequisite outputs than '
                                                     'the ones present here'),
            ]
        for other in ('aa', 'zz', 'm', 'mmm', ''):
            cases += [
                (with_label('provider', other), 'mm: image is labelled for another provider'),
                (with_meta(provider=other), 'mm: bundle is for another provider or key'),
                (dict(status={'stage': other, 'status': 'ok'}),
                 'mm: bundled status is not an ok status of this provider'),
            ]
        for other in ('a', 'zz', 'o', 'okay', 'OK', ''):
            cases.append((dict(status={'stage': 'mm', 'status': other}),
                          'mm: bundled status is not an ok status of this provider'))
        for other in ('/', '/q', '/s', '/r/', '/r2', ''):
            cases.append((with_meta(root=other), 'mm: bundle was built under another work root'))
        for other in (0, 1, 3, '2', None, 2.5):
            cases.append((with_meta(schema=other), 'mm: unsupported bundle schema'))
        for other in ('', None, 'x' * 64, tar.upper(), tar + '\n', tar[:-1]):
            cases.append((dict(recorded_tar_sha256=other, tar_sha256=other, labels=dict(
                good['labels'], **{'dev.uds.takx.tar-sha256': other}), meta=dict(good['meta'], tar_sha256=other)),
                'mm: the image records no archive digest'))
        cases += [(dict(meta=None), 'mm: unsupported bundle schema'),
                  (dict(meta='{}'), 'mm: unsupported bundle schema'),
                  (dict(status=None), 'mm: bundled status is not an ok status of this provider'),
                  (dict(status='ok'), 'mm: bundled status is not an ok status of this provider'),
                  (dict(labels={}), 'mm: archive digest differs from the image label')]
        for change, message in cases:
            with self.subTest(change=change), self.assertRaises(PC.CacheError) as caught:
                PC.check_bundle(**dict(good, **change))
            self.assertEqual(str(caught.exception), message)

    def test_status_that_cannot_be_written_takes_the_directory_back_out(self):
        self.make('base')
        self.service.push('base')
        self.set_aside('base')
        before = settled(self.tmp.name)
        with mock.patch.object(PC.Path, 'write_text', side_effect=OSError(28, 'No space left on device')), \
                self.assertRaises(OSError) as caught:
            self.service.restore('base')
        self.assertEqual(caught.exception.errno, 28)
        self.assertEqual(settled(self.tmp.name), before)

    def test_scratch_that_cannot_be_removed_does_not_undo_the_result(self):
        self.make('base')
        self.service.push('base')
        self.set_aside('base')
        rmtree = shutil.rmtree

        def strict(path, ignore_errors=False, **options):
            if not ignore_errors:
                raise OSError(16, 'Device or resource busy')
            rmtree(path, ignore_errors=True)
        with mock.patch.object(PC.shutil, 'rmtree', strict):
            self.service.restore('base')
            self.set_aside('base')
            self.registry.images.clear()
            self.make('side')
            self.service.push('side')
        self.assertEqual(self.leftovers(), [])

    def test_appearing_directory_is_reported_by_name(self):
        self.make('base')
        self.service.push('base')
        self.set_aside('base')
        rename = PC.rename_noreplace

        def racing(source, target):
            Path(target).mkdir()
            rename(source, target)
        with mock.patch.object(PC, 'rename_noreplace', racing), self.assertRaises(PC.CacheError) as caught:
            self.service.restore('base')
        self.assertEqual(str(caught.exception), 'base: the provider directory appeared meanwhile; not overwritten')

class Refusals(ServiceCase):
    """Inputs just beside the accepted ones, on either side."""

    def test_manifest_must_be_complete_and_typed(self):
        cases = (
            (lambda m: m.update(schema=0), '^unsupported manifest schema 0$'),
            (lambda m: m.update(schema=1), '^unsupported manifest schema 1$'),
            (lambda m: m.update(schema=3), '^unsupported manifest schema 3$'),
            (lambda m: m.pop('schema'), '^unsupported manifest schema None$'),
            (lambda m: m.update(schema='2'), "^unsupported manifest schema '2'$"),
            (lambda m: m.pop('toolchain'), '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m.update(toolchain=[]), '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].pop('base'), '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].update(base=7),
             '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].update(base='debian:trixie'),
             '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].update(base='debian@sha256:' + 'd' * 63),
             '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].update(base='debian@sha256:' + 'd' * 64 + '\n'),
             '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].update(base='@sha256:' + 'd' * 64),
             '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].update(base='a b@sha256:' + 'd' * 64),
             '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].update(base='debian@sha256:' + 'D' * 64),
             '^manifest needs a toolchain with a base image pinned by digest$'),
            (lambda m: m['toolchain'].pop('environment'),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain'].update(environment=[]),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain']['environment'].update({'1X': 'v'}),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain']['environment'].update({'A B': 'v'}),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain']['environment'].update({'A\n': 'v'}),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain']['environment'].update({'': 'v'}),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain']['environment'].update(TZ=7),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain']['environment'].update(TZ='a\nb'),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain']['environment'].update(TZ='a\tb'),
             '^toolchain environment must map variable names to one-line values$'),
            (lambda m: m['toolchain'].pop('gated'), '^toolchain needs its gated tools$'),
            (lambda m: m['toolchain'].update(gated=[]), '^toolchain needs its gated tools$'),
            (lambda m: m['toolchain'].update(gated={}), '^toolchain needs its gated tools$'),
            (lambda m: m['toolchain'].update(gated={'gcc': {}}), '^toolchain needs its gated tools$'),
            (lambda m: m['toolchain']['gated'].append('gcc'), "^malformed gated tool 'gcc'$"),
            (lambda m: m['toolchain']['gated'][1].pop('expect'), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(extra=1), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(name=7), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(name='a b'), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(name='x;y'), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(name='-x'), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(name='x\n'), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(name=''), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(argv=[]), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(argv='/usr/bin/g++'), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(argv=['/usr/bin/g++', 7]), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(expect=''), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(expect='  '), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][1].update(expect=14), '^malformed gated tool '),
            (lambda m: m['toolchain']['gated'][2].update(name='gcc'), '^gated tool gcc is listed twice$'),
            (lambda m: m.pop('wrapper'), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m.update(wrapper=[]), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].pop('env'), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].pop('mounts'), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].pop('workdir'), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].update(extra=1), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].update(env='HOME=/tmp'), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].update(env=[7]), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].update(mounts={}), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].update(mounts=[None]), '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['wrapper'].update(workdir=['{root}']),
             '^manifest needs a wrapper with env, mounts and workdir$'),
            (lambda m: m['providers']['top'].update(verify=[]), '^top: verify needs argv and env$'),
            (lambda m: m['providers']['top'].update(verify={'argv': ['x']}), '^top: verify needs argv and env$'),
            (lambda m: m['providers']['top'].update(verify={'env': []}), '^top: verify needs argv and env$'),
            (lambda m: m['providers']['top'].update(verify={'argv': [], 'env': []}),
             '^top: verify needs argv and env$'),
            (lambda m: m['providers']['top'].update(verify={'argv': 'x', 'env': []}),
             '^top: verify needs argv and env$'),
            (lambda m: m['providers']['top'].update(verify={'argv': ['x', 1], 'env': []}),
             '^top: verify needs argv and env$'),
            (lambda m: m['providers']['top'].update(verify={'argv': ['x'], 'env': 'A=1'}),
             '^top: verify needs argv and env$'),
            (lambda m: m['providers']['top'].update(verify={'argv': ['x'], 'env': [1]}),
             '^top: verify needs argv and env$'),
            (lambda m: m['providers']['top'].update(verify={'argv': ['x', '{nope}/y'], 'env': []}),
             r"^top: verify names an unknown variable in '\{nope\}/y'$"),
            (lambda m: m['providers']['top'].update(verify={'argv': ['x'], 'env': ['A={timestamp}']}),
             r"^top: verify names an unknown variable in 'A=\{timestamp\}'$"),
            (lambda m: m.update(recipes=[]), '^manifest needs recipes and providers$'),
            (lambda m: m.pop('recipes'), '^manifest needs recipes and providers$'),
            (lambda m: m.update(providers=[]), '^manifest needs recipes and providers$'),
            (lambda m: m.update(providers={}), '^manifest needs recipes and providers$'),
            (lambda m: m.update(providers=['base']), '^manifest needs recipes and providers$'),
            (lambda m: m.pop('providers'), '^manifest needs recipes and providers$'),
            (lambda m: m.update(variables=[]), '^manifest needs variables and recipe_exclude$'),
            (lambda m: m.pop('variables'), '^manifest needs variables and recipe_exclude$'),
            (lambda m: m.update(recipe_exclude={}), '^manifest needs variables and recipe_exclude$'),
            (lambda m: m.pop('recipe_exclude'), '^manifest needs variables and recipe_exclude$'),
            (lambda m: m['recipes'].update({'/abs': {}}), "^not a plain relative path: '/abs'$"),
            (lambda m: m['recipes'].update({'a//b': {}}), "^not a plain relative path: 'a//b'$"),
            (lambda m: m['recipes'].update({'': {}}), "^not a plain relative path: ''$"),
            (lambda m: m['recipes'].update({'a/./b': {}}), "^not a plain relative path: 'a/./b'$"),
            (lambda m: m['recipes']['takx/one'].update(uses=[7]), '^not a plain relative path: 7$'),
            (lambda m: m['providers'].update({'Base': m['providers']['base']}), "^bad provider name 'Base'$"),
            (lambda m: m['providers'].update({'-x': m['providers']['base']}), "^bad provider name '-x'$"),
            (lambda m: m['providers'].update({'x\n': m['providers']['base']}), r"^bad provider name 'x\\n'$"),
            (lambda m: m['providers'].update({'': m['providers']['base']}), "^bad provider name ''$"),
            (lambda m: m['providers']['top'].update(lane=1), '^top: missing or malformed lane$'),
            (lambda m: m['providers']['top'].pop('recipe'), '^top: missing or malformed recipe$'),
            (lambda m: m['providers']['top'].update(prerequisites='base'), '^top: missing or malformed prerequisites$'),
            (lambda m: m['providers']['top'].update(locks='x'), '^top: missing or malformed locks$'),
            (lambda m: m['providers']['top'].update(fetched={}), '^top: missing or malformed fetched$'),
            (lambda m: m['providers']['top'].update(marker=None), '^top: missing or malformed marker$'),
            (lambda m: m['providers']['top'].update(invocation=[]), '^top: missing or malformed invocation$'),
            (lambda m: m['providers']['top'].update(recipe='takx/ghost'), '^top: unknown recipe takx/ghost$'),
            (lambda m: m['providers']['top'].update(marker='../x'), r"^not a plain relative path: '\.\./x'$"),
            (lambda m: m['providers']['top'].update(locks=['ok', '/x']), "^not a plain relative path: '/x'$"),
            (lambda m: m['providers']['top'].update(prerequisites=['ghost']), '^top: unknown prerequisite ghost$'),
            (lambda m: m['providers']['top']['invocation'].update(argv=[]), '^top: invocation needs argv and env$'),
            (lambda m: m['providers']['top']['invocation'].update(argv='x'), '^top: invocation needs argv and env$'),
            (lambda m: m['providers']['top']['invocation'].pop('argv'), '^top: invocation needs argv and env$'),
            (lambda m: m['providers']['top']['invocation'].update(env=None), '^top: invocation needs argv and env$'),
            (lambda m: m['providers']['top']['invocation'].pop('env'), '^top: invocation needs argv and env$'),
            (lambda m: m['providers']['top'].update(fetched=[{}]), r'^top: bad fetched selector \{\}$'),
            (lambda m: m['providers']['top'].update(fetched=[['consumer']]),
             r"^top: bad fetched selector \['consumer'\]$"),
            (lambda m: m['providers']['top'].update(fetched=['kind']), "^top: bad fetched selector 'kind'$"),
            (lambda m: m['providers']['top'].update(fetched=[{'consumer': 'a', 'kind': 'b'}]),
             '^top: bad fetched selector '),
            (lambda m: m['providers']['top'].update(fetched=[{'glob': '*'}]), '^top: bad fetched selector '),
            (lambda m: m['providers']['base'].update(prerequisites=['top']), '^prerequisite cycle at '),
        )
        for index, (edit, message) in enumerate(cases):
            document = manifest()
            edit(document)
            with self.subTest(index=index, message=message), self.assertRaisesRegex(PC.CacheError, message):
                PC.validate_manifest(document)
        document = manifest()
        self.assertIs(PC.validate_manifest(document), document)
        # Accepted: the names real tools have, an empty environment, a check that uses every kind of variable.
        document['toolchain']['gated'] += [{'name': name, 'argv': ['x'], 'expect': 'v'}
                                           for name in ('cmake-venv', 'c++', 'python3.13', 'a_b', 'X', '7z')]
        document['toolchain']['environment'] = {'_A1': '', 'b': 'two words'}
        document['wrapper'].update(env=[], mounts=[], workdir='')
        document['providers']['top']['verify'] = {'argv': ['{jdk}/bin/java', '{root}/P/top', '{repo}/x'], 'env': []}
        document['providers']['base']['verify'] = {'argv': ['x'], 'env': ['A={root}', 'B=1']}
        self.assertIs(PC.validate_manifest(document), document)
        # {root} and {repo} are known whatever the manifest's own variables are called.
        document['variables'].update(root='/shadowed', repo='/shadowed')
        self.assertIs(PC.validate_manifest(document), document)
        # One token may use every known name.
        document = manifest()
        document['providers']['base']['verify'] = {'argv': ['{jdk}:{root}:{repo}'], 'env': []}
        self.assertIs(PC.validate_manifest(document), document)
        for selector in ({'consumer': 'x'}, {'kind': 'x'}, {'path': 'x'}):
            document['providers']['top']['fetched'] = [selector]
            PC.validate_manifest(document)
        with self.assertRaises(PC.CacheError):
            PC.ProviderCache(dict(manifest(), schema=1), None, None, 'reg')

    def test_fetched_inputs_are_selected_exactly(self):
        records = [
            {'kind': 'lock-entry', 'name': 'archive', 'verified': True, 'expected_sha256': '1' * 64,
             'consumers': [{'recipe': 'one'}]},
            {'kind': 'git-source', 'name': 'tree', 'verified': True, 'expected_revision': '2' * 40,
             'path': '/root/inputs/tree'},
            {'kind': 'toolchain', 'name': 'jdk', 'verified': True, 'expected_sha256': '3' * 64, 'actual_sha256': None,
             'path': '/root/inputs/jdk', 'other_field': 'x', 'expected': 'not a digest field'},
            {'kind': 'a-kind', 'name': 'first', 'verified': True, 'actual_sha512': '4' * 128},
        ]

        def names(selector, root='/root'):
            return [item['name'] for item in PC.select_fetched(records, [selector], root)]
        self.assertEqual(names({'kind': 'git-source'}), ['tree'])
        self.assertEqual(names({'kind': 'lock-entry'}), ['archive'])
        self.assertEqual(names({'kind': 'toolchain'}), ['jdk'])
        self.assertEqual(names({'consumer': 'one'}), ['archive'])
        self.assertEqual(names({'path': 'inputs/tree'}), ['tree'])
        self.assertEqual(names({'path': 'inputs/jdk'}, '/root/'), ['jdk'])
        self.assertEqual(PC.select_fetched(records, [{'kind': 'toolchain'}, {'path': 'inputs/jdk'}], '/root'),
                         [{'kind': 'toolchain', 'name': 'jdk', 'digests': {'expected_sha256': '3' * 64}}])
        self.assertEqual(PC.select_fetched(records, [{'kind': 'a-kind'}], '/root'),
                         [{'kind': 'a-kind', 'name': 'first', 'digests': {'actual_sha512': '4' * 128}}])
        self.assertEqual(PC.select_fetched(records, [], '/root'), [])
        for selector in ({'path': 'inputs'}, {'path': 'inputs/tree/'}, {'path': '/root/inputs/tree'},
                         {'consumer': 'on'}, {'kind': 'lock'}, {'kind': 'zz'}):
            with self.subTest(selector=selector), self.assertRaisesRegex(PC.CacheError, 'no fetched input matches'):
                names(selector)
        with self.assertRaises(PC.CacheError):
            names({'path': 'inputs/tree'}, '/other')
        # A selector of a kind this module does not know selects nothing, whatever it is called.
        for unknown in ({'a': 'one'}, {'a': 'lock'}, {'d': 'lock-entry'}, {'glob': 'git-source'}, {'zz': 'archive'},
                        {'Consumer': 'one'}, {'KIND': 'toolchain'}):
            with self.subTest(unknown=unknown), self.assertRaisesRegex(PC.CacheError, '^no fetched input matches '):
                names(unknown)
        for verified in (1, 2, 0, None, 'true', 'True', [True]):
            records[1]['verified'] = verified
            with self.subTest(verified=verified), self.assertRaisesRegex(
                    PC.CacheError, '^fetched input tree is not verified$'):
                names({'kind': 'git-source'})

    def test_members_just_outside_the_rules(self):
        top = [('base', 'dir', '')]
        self.assertEqual(PC.check_members('base', top + [('base/up', 'symlink', '.'), ('base/d', 'dir', ''),
                                                         ('base/d/up', 'symlink', '..'),
                                                         ('base/d/self', 'symlink', '../d')]), 5)
        cases = (
            ([('base/f/', 'file', '')], "^archive member has an empty, \".\" or \"..\" component: 'base/f/'$"),
            ([('base/l/', 'symlink', 'x')], '^archive member has an empty'),
            ([('base/x/', 'block', '')], '^archive member has an empty'),
            ([('base/x', 'block', '')], "^archive member 'base/x' is a device or other special file$"),
            ([('base/x', 'char', '')], "^archive member 'base/x' is a device or other special file$"),
            ([('base/x', 'zzz', '')], "^archive member 'base/x' is a device or other special file$"),
            ([('base/x', '', '')], "^archive member 'base/x' is a device or other special file$"),
            ([('base/l', 'symlink', '-a/..')], '^symlink \'base/l\' has ".." after a name in its target$'),
            ([('base/l', 'symlink', '!/../..')], '^symlink \'base/l\' has ".." after a name in its target$'),
            ([('base/l', 'symlink', './..')], '^symlink \'base/l\' has ".." after a name in its target$'),
            ([('base/l', 'symlink', '../base/..')], '^symlink \'base/l\' has ".." after a name in its target$'),
            ([('base/l', 'symlink', '../base')], None),
        )
        for extra, message in cases:
            with self.subTest(extra=extra):
                if message is None:
                    self.assertEqual(PC.check_members('base', top + extra), 2)
                else:
                    with self.assertRaisesRegex(PC.CacheError, message):
                        PC.check_members('base', top + extra)

    def test_recipe_and_record_messages(self):
        tree = PC.WorkTree(self.root, self.repo)
        with self.assertRaisesRegex(PC.CacheError, '^recipe path takx/missing does not exist$'):
            tree.recipe_files(['takx/missing'], [])
        with self.assertRaisesRegex(PC.CacheError, r'^no recipe file matches takx/\*\.nothing$'):
            tree.recipe_files(['takx/*.nothing'], [])
        (self.repo / 'takx/empty').mkdir()
        with self.assertRaisesRegex(PC.CacheError, '^recipe directory takx/empty is empty$'):
            tree.recipe_files(['takx/empty'], [])
        for status in ('okay', 'zz', 'OK', 'failed', '', None):
            self.write_json('status/images.json', {'status': status, 'image_ids': {PC.TOOLCHAIN: TOOLCHAIN_ID}})
            with self.subTest(status=status), self.assertRaisesRegex(
                    PC.CacheError, r'^status/images\.json records no toolchain image id$'):
                tree.legacy_toolchain()
        for status in ('okay', 'zz', 'OK', 'failed', '', None):
            self.write_json('status/fetch.json', {'status': status, 'inputs': []})
            with self.subTest(status=status), self.assertRaisesRegex(
                    PC.CacheError, r'^status/fetch\.json is not an ok fetch record$'):
                tree.fetched_records()
        self.write_json('status/fetch.json', {'status': 'ok', 'inputs': []})
        self.assertEqual(tree.fetched_records(), [])

    def test_marker_counts_only_as_a_regular_file_in_a_real_directory(self):
        tree = PC.WorkTree(self.root, self.repo)
        top = self.make('base')
        digest = hashlib.sha256((top / 'complete.json').read_bytes()).hexdigest()
        self.assertEqual(tree.marker_sha256('base', 'complete.json'), digest)
        self.assertIsNone(tree.marker_sha256('base', 'other.json'))
        self.assertIsNone(tree.marker_sha256('base', 'install'))
        self.assertIsNone(tree.marker_sha256('base', 'install/tool-1'))
        self.assertIsNone(tree.marker_sha256('side', 'complete.json'))
        os.symlink('base', self.root / 'P/side')
        self.assertIsNone(tree.marker_sha256('side', 'complete.json'))
        os.unlink(self.root / 'P/side')
        (self.root / 'P/side').write_text('a file')
        self.assertIsNone(tree.marker_sha256('side', 'complete.json'))
        self.assertTrue(tree.has_directory('side'))
        self.assertEqual((tree.status_of('base'), tree.status_of('side')), ('ok', 'missing'))
        self.assertEqual(tree.provider_dir('base'), top)

    def test_linked_provider_directory_is_neither_ok_nor_replaced(self):
        self.make('base')
        self.make('top')
        self.service.push('top')
        self.set_aside('top')
        os.rename(self.root / 'P/base', self.root / 'P/real')
        os.symlink('real', self.root / 'P/base')
        before = settled(self.tmp.name)
        with self.assertRaisesRegex(PC.CacheError, '^base: the provider directory already exists'):
            self.service.restore('base')
        with self.assertRaisesRegex(PC.Skip, '^a prerequisite provider is not ok here$'):
            self.service.restore('top')
        with self.assertRaisesRegex(PC.CacheError, '^base: status is ok but the completion marker is missing$'):
            self.service.push('base')
        self.assertEqual(settled(self.tmp.name), before)

    def test_complete_directory_with_another_status_is_kept_and_not_called_ok(self):
        top = self.make('base')
        self.service.push('base')
        for status in ('running', 'zz', 'okay', 'failed', 'OK'):
            self.write_json('status/base.json', {'stage': 'base', 'status': status})
            before = settled(self.tmp.name)
            with self.subTest(status=status), self.assertRaisesRegex(
                    PC.CacheError, '^base: the provider directory already exists and is never overwritten$'):
                self.service.restore('base')
            self.assertEqual(settled(self.tmp.name), before)
        self.assertTrue((top / 'complete.json').is_file())

    def test_prerequisite_with_another_status_is_not_ok(self):
        self.make('top')
        self.make('base')
        self.service.push('top')
        self.set_aside('top')
        for status in ('running', 'zz', 'okay', 'failed', 'OK'):
            self.write_json('status/base.json', {'stage': 'base', 'status': status})
            with self.subTest(status=status), self.assertRaisesRegex(PC.Skip, 'a prerequisite provider is not ok here'):
                self.service.restore('top')
        self.assertEqual(self.registry.fetched, [])
        self.make('top')
        self.registry.images.clear()
        with self.assertRaisesRegex(PC.CacheError, '^top: a prerequisite provider is not ok here, so its outputs '
                                                   'cannot be bound$'):
            self.service.push('top')
        self.assertEqual(self.registry.images, {})

    def test_at_most_five_differences_are_listed(self):
        top = self.make('base')
        for number in range(7):
            (top / ('f%d' % number)).write_text('before')
        write_tar = PC.WorkTree.write_tar

        def racing(source, name, entries, target):
            for number in range(7):
                (top / ('f%d' % number)).write_text('after!')
            write_tar(source, name, entries, target)
        self.service.tree.write_tar = racing
        with self.assertRaises(PC.CacheError) as caught:
            self.service.push('base')
        self.assertEqual(str(caught.exception), 'base changed while it was archived: changed f0, changed f1, '
                                                'changed f2, changed f3, changed f4')
        self.assertEqual(self.registry.published, 0)

    def test_scratch_directory_is_made_inside_the_work_root(self):
        root = Path(self.tmp.name) / 'not/yet/there'
        scratch = PC.WorkTree(root, self.repo).scratch('.restore-x.')
        self.assertEqual(scratch.parent, root)
        self.assertRegex(scratch.name, r'^\.restore-x\..+')
        self.assertTrue(scratch.is_dir())
        self.assertEqual(stat.S_IMODE(os.stat(scratch).st_mode), 0o700)

    def test_timeout_comes_from_the_environment(self):
        self.assertEqual(PC.build_service({}).registry.timeout, 3600)
        self.assertEqual(PC.build_service({'TAKX_PROTO_CACHE_TIMEOUT': ''}).registry.timeout, 3600)
        self.assertEqual(PC.build_service({'TAKX_PROTO_CACHE_TIMEOUT': '7'}).registry.timeout, 7)
        self.assertEqual(PC.build_service({}).registry.docker, 'docker')
        service = PC.build_service({'TAKX_PROTO_ROOT': 'relative/root', 'TAKX_PROTO_CACHE': 'reg.example/x'})
        self.assertEqual((service.tree.root, service.prefix, service.legacy_id),
                         (Path(os.path.abspath('relative/root')), 'reg.example/x', ''))
        self.assertEqual(set(service.manifest['providers']), EXPECTED)

    def test_importing_the_module_under_any_name_does_not_run_it(self):
        for name in ('A', 'Provider_cache', '0cache', '__ma', '__main', '__main__x', 'zz'):
            module_spec = importlib.util.spec_from_file_location(name, HERE / 'provider_cache.py')
            module = importlib.util.module_from_spec(module_spec)
            with self.subTest(name=name), mock.patch.object(sys, 'argv', ['provider_cache.py', 'frobnicate']):
                module_spec.loader.exec_module(module)  # running main() would exit 2 here
                self.assertEqual(module.SCHEMA, PC.SCHEMA)

    def test_help_describes_the_tool(self):
        out = io.StringIO()
        with mock.patch.object(PC.sys, 'stdout', out), self.assertRaises(SystemExit) as caught:
            PC.main(['--help'], service=self.service)
        self.assertEqual(caught.exception.code, 0)
        self.assertIn('Input-keyed cache of the desktop-prototype provider outputs.', out.getvalue())
        self.assertIn('usage: provider_cache.py ', out.getvalue())
        self.assertIn('{key,restore,push,migrate,status,toolchain}', out.getvalue())

class Archiving(ServiceCase):
    def test_archive_listing_names_every_kind_of_member(self):
        tar = Path(self.tmp.name) / 'p.tar'
        with tarfile.open(tar, 'w', format=tarfile.PAX_FORMAT) as archive:
            def add(name, kind=tarfile.REGTYPE, data=None, link='', mode=0o644):
                info = tarfile.TarInfo(name)
                info.type, info.linkname, info.mode = kind, link, mode
                info.size = len(data) if data is not None else 0
                archive.addfile(info, io.BytesIO(data) if data is not None else None)
            add('p', tarfile.DIRTYPE, mode=0o755)
            add('p/dir', tarfile.DIRTYPE, mode=0o2750)
            add('p/dir/file', data=b'content', mode=0o4755)
            add('p/empty', data=b'')
            add('p/link', tarfile.SYMTYPE, link='dir/file')
            add('p/hard', tarfile.LNKTYPE, link='p/dir/file')
            add('p/pipe', tarfile.FIFOTYPE, mode=0o600)
            add('p/device', tarfile.CHRTYPE, mode=0o660)
        self.assertEqual(PC.WorkTree.tar_entries(tar, 'p'), [
            {'path': 'dir', 'type': 'dir', 'mode': '2750'},
            {'path': 'dir/file', 'type': 'file', 'mode': '4755', 'size': 7,
             'sha256': hashlib.sha256(b'content').hexdigest()},
            {'path': 'empty', 'type': 'file', 'mode': '0644', 'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()},
            {'path': 'link', 'type': 'link', 'target': 'dir/file'},
            {'path': 'hard', 'type': 'special', 'mode': '0644'},
            {'path': 'pipe', 'type': 'special', 'mode': '0600'},
            {'path': 'device', 'type': 'special', 'mode': '0660'}])
        self.assertEqual(PC.WorkTree.tar_members(tar), [
            ('p', 'dir', ''), ('p/dir', 'dir', ''), ('p/dir/file', 'file', ''), ('p/empty', 'file', ''),
            ('p/link', 'symlink', 'dir/file'), ('p/hard', 'hardlink', 'p/dir/file'), ('p/pipe', 'other', ''),
            ('p/device', 'other', '')])

    def test_tree_listing_names_every_kind_of_entry(self):
        top = Path(self.tmp.name) / 'tree'
        (top / 'dir').mkdir(parents=True)
        os.chmod(top / 'dir', 0o2750)
        (top / 'dir/file').write_bytes(b'content')
        os.chmod(top / 'dir/file', 0o4755)
        os.symlink('dir/file', top / 'link')
        os.symlink('dir', top / 'dirlink')
        os.mkfifo(top / 'pipe', 0o600)
        os.chmod(top / 'pipe', 0o600)
        self.assertEqual(PC.WorkTree.entries(top), [
            {'path': 'dir', 'type': 'dir', 'mode': '2750'},
            {'path': 'dir/file', 'type': 'file', 'mode': '4755', 'size': 7,
             'sha256': hashlib.sha256(b'content').hexdigest()},
            {'path': 'dirlink', 'type': 'link', 'target': 'dir'},
            {'path': 'link', 'type': 'link', 'target': 'dir/file'},
            {'path': 'pipe', 'type': 'special', 'mode': '0600'}])
        self.assertEqual(PC.WorkTree.entries(top / 'dir' / '..' / 'dir'), [
            {'path': 'file', 'type': 'file', 'mode': '4755', 'size': 7,
             'sha256': hashlib.sha256(b'content').hexdigest()}])

    def test_unsafe_tree_is_refused_before_anything_is_archived(self):
        for what, plant, message in (
                ('absolute link', lambda top: os.symlink('/etc', top / 'install/etc'), 'absolute or empty target'),
                ('escaping link', lambda top: os.symlink('../../side', top / 'install/out'),
                 'leaves the provider directory'),
                ('fifo', lambda top: os.mkfifo(top / 'pipe'), 'device or other special file')):
            self.doCleanups()
            self.setUp()
            plant(self.make('base'))
            self.service.tree.write_tar = lambda *args: self.fail('archived an unsafe tree')
            with self.subTest(what=what), self.assertRaisesRegex(PC.CacheError, message):
                self.service.push('base')
            self.assertEqual((self.registry.published, self.leftovers()), (0, []))

    def test_file_that_turns_unsafe_while_archiving_is_refused_as_unsafe(self):
        write_tar = PC.WorkTree.write_tar
        for what, swap, message in (
                ('fifo', lambda path: os.mkfifo(path), "^archive member 'base/install/tool' is a device or other "
                                                       "special file$"),
                ('escaping link', lambda path: os.symlink('../../../etc/passwd', path),
                 "^symlink 'base/install/tool' leaves the provider directory$"),
                ('absolute link', lambda path: os.symlink('/etc/passwd', path),
                 "^symlink 'base/install/tool' has an absolute or empty target$")):
            self.doCleanups()
            self.setUp()
            top = self.make('base')

            def racing(source, name, entries, target):
                os.unlink(top / 'install/tool')
                swap(top / 'install/tool')
                write_tar(source, name, entries, target)
            self.service.tree.write_tar = racing
            with self.subTest(what=what), self.assertRaisesRegex(PC.CacheError, message):
                self.service.push('base')
            self.assertEqual((self.registry.published, self.leftovers()), (0, []))

    def test_files_larger_than_one_read_are_hashed_whole(self):
        top = self.make('base')
        data = hashlib.sha256(b'seed').digest() * 81920 + b'tail'  # 2.5 MiB and four bytes
        (top / 'install/big.bin').write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self.assertEqual(PC.sha256_file(top / 'install/big.bin'), digest)
        listed = {entry['path']: entry for entry in PC.WorkTree.entries(top)}
        self.assertEqual((listed['install/big.bin']['size'], listed['install/big.bin']['sha256']), (len(data), digest))
        ref = self.service.push('base')
        tar = Path(self.tmp.name) / 'base.tar'
        tar.write_bytes(self.registry.images[ref][1]['base.tar'])
        archived = {entry['path']: entry for entry in PC.WorkTree.tar_entries(tar, 'base')}
        self.assertEqual(archived, listed)
        self.assertEqual(self.registry.images[ref][0]['dev.uds.takx.tar-sha256'],
                         hashlib.sha256(tar.read_bytes()).hexdigest())
        good = snapshot(top)
        self.set_aside('base')
        self.service.restore('base')
        self.assertEqual(snapshot(top), good)
        empty = Path(self.tmp.name) / 'empty'
        empty.write_bytes(b'')
        self.assertEqual(PC.sha256_file(empty), hashlib.sha256(b'').hexdigest())

    def test_push_record_is_written_under_a_lock(self):
        tree = PC.WorkTree(self.root, self.repo)
        tree.record_push(('base', 'k1', 'r1', 't1'))
        with open(self.root / 'cache-state/pushed.tsv') as holder:
            fcntl.flock(holder, fcntl.LOCK_EX)
            writer = threading.Thread(target=tree.record_push, args=(('side', 'k2', 'r2', 't2'),))
            writer.start()
            writer.join(0.3)
            waiting = writer.is_alive()
            written = (self.root / 'cache-state/pushed.tsv').read_text()
            fcntl.flock(holder, fcntl.LOCK_UN)
            writer.join(30)
        self.assertTrue(waiting, 'the record was written without waiting for the lock')
        self.assertEqual(written, 'base\tk1\tr1\tt1\n')
        self.assertFalse(writer.is_alive())
        self.assertEqual((self.root / 'cache-state/pushed.tsv').read_text(), 'base\tk1\tr1\tt1\nside\tk2\tr2\tt2\n')
        tree.record_push(('base', 'k1', 'r1', 't1'))
        self.assertEqual(tree.pushed(), {('base', 'k1'): 't1', ('side', 'k2'): 't2'})
        self.assertEqual(len((self.root / 'cache-state/pushed.tsv').read_text().splitlines()), 2)

    def test_provider_without_a_key_is_listed_and_the_reason_logged(self):
        self.write_json('status/fetch.json', {'status': 'ok', 'inputs': [
            {'kind': 'lock-entry', 'name': 'one', 'verified': True, 'expected_sha256': '5' * 64,
             'consumers': [{'recipe': 'one'}]}]})
        service = self.new_service()
        rows = {row['name']: row for row in service.rows()}
        self.assertEqual(rows['top'], {'name': 'top', 'key': '-', 'ref': '-', 'local': 'absent', 'remote': 'no-key',
                                       'tar_sha256': '-'})
        self.assertEqual(rows['base'], {'name': 'base', 'key': service.key('base'), 'ref': service.ref('base'),
                                        'local': 'absent', 'remote': 'unknown', 'tar_sha256': '-'})
        self.assertEqual(self.messages, ['provider-cache: top has no key: no fetched input matches consumer=two'])


class ToolchainCore(unittest.TestCase):
    """The toolchain fingerprint as a pure function of the manifest block and the probe."""

    def observed(self, **changes):
        return PC.parse_probe(probe_text(**changes))

    def test_probe_is_this_exact_program(self):
        self.assertEqual(PC.probe_script(toolchain_block()), '\n'.join([
            "set -u",
            """printf 'arch\\t%s\\n' "$(dpkg --print-architecture)\"""",
            """printf 'env\\t%s\\t%s\\n' JAVA_HOME "${JAVA_HOME-}\"""",
            """printf 'env\\t%s\\t%s\\n' TZ "${TZ-}\"""",
            """printf 'tool\\t%s\\t%s\\n' gcc "$(/usr/bin/gcc --version 2>&1 | head -n 3 | tr '\\n\\t' '  ')\"""",
            """printf 'tool\\t%s\\t%s\\n' g++ "$(/usr/bin/g++ --version 2>&1 | head -n 3 | tr '\\n\\t' '  ')\"""",
            """printf 'tool\\t%s\\t%s\\n' java "$(/jdk/bin/java -version 2>&1 | head -n 3 | tr '\\n\\t' '  ')\"""",
            """printf 'packages\\t%s\\n' "$(dpkg-query -W -f '${Package} ${Version} ${Architecture}\\n'"""
            """ | sha256sum | cut -d' ' -f1)\"""",
            """dpkg-query -W -f 'pkg\\t${db:Status-Abbrev}\\t${Package}\\t${Version}\\t${Architecture}"""
            """\\t${Section}\\n'""", '']))

    def test_probe_reports_what_the_commands_print(self):
        # The generated program, run by a real bash over stand-ins for dpkg and the gated tools.
        with tempfile.TemporaryDirectory() as temporary:
            tools = Path(temporary) / 'my tools'
            tools.mkdir()
            scripts = {
                'dpkg': 'echo arm64',
                'dpkg-query': r"""if [ "$*" = '-W -f ${Package} ${Version} ${Architecture}\n' ]; then """
                              r"""printf 'a 1 all\nb 2 arm64\n'; elif [ "$*" = '-W -f pkg\t${db:Status-Abbrev}"""
                              r"""\t${Package}\t${Version}\t${Architecture}\t${Section}\n' ]; then """
                              r"""printf 'pkg\tii \ta\t1\tall\tdevel\npkg\trc \tb\t2\tarm64\t\n'; """
                              r"""else exit 9; fi""",
                'plain': 'echo "plain 1.2"',
                'stderr': 'echo "to stderr 3.4" >&2',
                'long': r"""printf 'one\ntwo\tx\nthree\nfour\n'""",
                'args': r"""printf '<%s>' "$@"; echo""",
                'fails': 'echo "broken"; exit 3',
            }
            for name, text in scripts.items():
                (tools / name).write_text('#!/bin/bash\n' + text + '\n')
                os.chmod(tools / name, 0o755)
            block = {'base': toolchain_block()['base'], 'environment': {'SET': 'x', 'UNSET': '', 'EMPTY': ''},
                     'gated': [{'name': name, 'argv': argv, 'expect': 'x'} for name, argv in (
                         ('plain', [str(tools / 'plain')]), ('c++', [str(tools / 'stderr')]),
                         ('long', [str(tools / 'long')]), ('fails', [str(tools / 'fails')]),
                         ('args', [str(tools / 'args'), 'two words', "it's", '$HOME', '-v']),
                         ('missing', [str(tools / 'no such tool')]))]}
            done = subprocess.run(['/bin/bash', '-c', PC.probe_script(block)], stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=60, stdin=subprocess.DEVNULL,
                                  env={'PATH': '%s:/usr/bin:/bin' % tools, 'SET': 'the value', 'EMPTY': ''})
            self.assertEqual((done.returncode, done.stderr), (0, b''))
            facts = PC.parse_probe(done.stdout.decode())
            self.assertEqual(facts['arch'], 'arm64')
            self.assertEqual(facts['environment'], {'SET': 'the value', 'UNSET': '', 'EMPTY': ''})
            self.assertEqual(facts['packages'], hashlib.sha256(b'a 1 all\nb 2 arm64\n').hexdigest())
            self.assertEqual(facts['package_list'], [('ii ', 'a', '1', 'all', 'devel'), ('rc ', 'b', '2', 'arm64', '')])
            missing = facts['tools'].pop('missing')
            self.assertIn('No such file or directory', missing)
            self.assertEqual(facts['tools'], {
                'plain': 'plain 1.2 ', 'c++': 'to stderr 3.4 ', 'long': 'one two x three ', 'fails': 'broken ',
                'args': "<two words><it's><$HOME><-v> "})

    def test_probe_output_is_parsed_strictly(self):
        self.assertEqual(self.observed(), {
            'arch': 'amd64', 'environment': {'JAVA_HOME': '/jdk', 'TZ': 'UTC'}, 'packages': 'e' * 64,
            'tools': {'gcc': 'gcc (Debian 14.2.0-19) 14.2.0 Copyright (C) 2024 ',
                      'g++': 'g++ (Debian 14.2.0-19) 14.2.0 Copyright (C) 2024 ',
                      'java': 'openjdk version "17.0.21" OpenJDK Runtime Environment Temurin-17.0.21+9 '},
            'package_list': [('ii ', 'gcc-14', '14.2.0-19', 'amd64', 'devel'),
                             ('ii ', 'libc6', '2.41-12+deb13u4', 'amd64', 'libs'),
                             ('ii ', 'libc6-dev', '2.41-12+deb13u4', 'amd64', 'libdevel'),
                             ('ii ', 'libegl-dev', '1.7.0-1+b2', 'amd64', 'libs'),
                             ('ii ', 'linux-libc-dev', '6.12.117-1', 'all', 'devel'),
                             ('ii ', 'openssl', '3.5.7-1~deb13u3', 'amd64', 'utils'),
                             ('rc ', 'libold-dev', '1.0', 'amd64', 'libdevel')]})
        self.assertEqual(PC.parse_probe(''), {'arch': None, 'environment': {}, 'tools': {}, 'packages': None,
                                              'package_list': []})
        self.assertEqual(PC.parse_probe('\n\narch\tarm64\n\n')['arch'], 'arm64')
        self.assertEqual(PC.parse_probe('env\tA\t\nenv\tB\t b \ntool\tx\t\npkg\tii \ta\t1\tall\t\n'),
                         {'arch': None, 'environment': {'A': '', 'B': ' b '}, 'tools': {'x': ''}, 'packages': None,
                          'package_list': [('ii ', 'a', '1', 'all', '')]})
        for line in ('arch', 'arch\tamd64\textra', 'env\tA', 'env\tA\tb\tc', 'tool\tgcc', 'tool\tgcc\ta\tb',
                     'packages', 'packages\ta\tb', 'warning: something', ' arch\tamd64', 'ARCH\tamd64', '\t',
                     'tools\tgcc\t14', 'zzz\ta\tb', 'envy\tA\tb', 'archive\tx', 'packagess\tx', 'x' * 200,
                     'pkg', 'pkg\tii \ta\t1\tall', 'pkg\tii \ta\t1\tall\tdevel\textra', 'pkgs\tii \ta\t1\tall\t',
                     'package\tii \ta\t1\tall\tdevel'):
            with self.subTest(line=line), self.assertRaises(PC.CacheError) as caught:
                PC.parse_probe('arch\tamd64\n' + line + '\n')
            self.assertEqual(str(caught.exception),
                             'unexpected line in the toolchain probe output: %r' % (line[:80],))

    def test_fingerprint_document_names_what_the_toolchain_is(self):
        document = PC.toolchain_document(toolchain_block(), self.observed(), BASE_LAYER)
        self.assertEqual(document, {
            'schema': 1, 'arch': 'amd64', 'base': 'sha256:' + 'd' * 64, 'base_layer': BASE_LAYER,
            'environment': {'JAVA_HOME': '/jdk', 'TZ': 'UTC'},
            'gated': [{'name': 'gcc', 'argv': ['/usr/bin/gcc', '--version'], 'expect': ' 14.2.0'},
                      {'name': 'g++', 'argv': ['/usr/bin/g++', '--version'], 'expect': ' 14.2.0'},
                      {'name': 'java', 'argv': ['/jdk/bin/java', '-version'], 'expect': 'Temurin-17.0.21+9'}]})
        self.assertEqual(document, PC.expected_toolchain(toolchain_block(), 'amd64', BASE_LAYER))
        self.assertEqual(PC.derive_key(document), FINGERPRINT)
        # The base is named by the digest alone: what follows the last '@'.
        odd = dict(toolchain_block(), base='user@registry.example/debian@sha256:' + 'd' * 64)
        self.assertEqual(PC.expected_toolchain(odd, 'amd64', BASE_LAYER), document)
        block = toolchain_block()
        document['environment']['X'] = document['gated'][0]['argv'][0] = 'changed'
        document['gated'][1]['name'] = 'changed'
        self.assertEqual(block, toolchain_block())  # the document is a copy
        # What the probe saw beside the gates is not in it: a rebuilt image is the same toolchain.
        rebuilt = self.observed(gcc='tool\tgcc\tgcc (Debian 14.2.0-23+b1) 14.2.0 Copyright (C) 2025 ',
                                packages='packages\t' + 'f' * 64)
        self.assertEqual(PC.derive_key(PC.toolchain_document(toolchain_block(), rebuilt, BASE_LAYER)), FINGERPRINT)

    def test_everything_the_toolchain_is_changes_the_fingerprint(self):
        def edited(edit):
            block = toolchain_block()
            edit(block)
            return fingerprint(block=block)
        seen = {FINGERPRINT}
        for what, value in (
                ('architecture', fingerprint('arm64')),
                ('first layer', fingerprint(layer=BASE_LAYER[:-1] + 'c')),
                ('base digest', edited(lambda b: b.update(base=b['base'][:-1] + 'e'))),
                ('environment value', edited(lambda b: b['environment'].update(TZ='Europe/Berlin'))),
                ('environment variable added', edited(lambda b: b['environment'].update(LANG='C.UTF-8'))),
                ('environment variable removed', edited(lambda b: b['environment'].pop('TZ'))),
                ('gated version', edited(lambda b: b['gated'][0].update(expect=' 14.3.0'))),
                ('gated command', edited(lambda b: b['gated'][0].update(argv=['/usr/local/bin/gcc', '--version']))),
                ('gated name', edited(lambda b: b['gated'][0].update(name='cc'))),
                ('gated tool added', edited(lambda b: b['gated'].append(
                    {'name': 'make', 'argv': ['/usr/bin/make', '--version'], 'expect': 'GNU Make 4.4.1'}))),
                ('gated tool removed', edited(lambda b: b['gated'].pop())),
                ('gated order', edited(lambda b: b['gated'].reverse()))):
            with self.subTest(what=what):
                self.assertRegex(value, '^[0-9a-f]{64}$')
                self.assertNotIn(value, seen)
                seen.add(value)
        # The registry host the base is pulled through is not what the base is.
        self.assertEqual(edited(lambda b: b.update(base='mirror.example/library/debian@sha256:' + 'd' * 64)),
                         FINGERPRINT)

    def test_image_that_is_not_the_toolchain_has_no_fingerprint(self):
        cases = (
            (dict(arch='s390x'), BASE_LAYER,
             "^the toolchain image reports architecture 's390x' \\(amd64 and arm64 only\\)$"),
            (dict(arch='x86_64'), BASE_LAYER, "^the toolchain image reports architecture 'x86_64' "),
            (dict(arch=''), BASE_LAYER, "^the toolchain image reports architecture '' "),
            (dict(arch=None), BASE_LAYER, '^the toolchain image reports architecture None '),
            ({}, None, '^the toolchain image names no first layer$'),
            ({}, '', '^the toolchain image names no first layer$'),
            ({}, 'b' * 64, '^the toolchain image names no first layer$'),
            ({}, BASE_LAYER + '\n', '^the toolchain image names no first layer$'),
            ({}, 7, '^the toolchain image names no first layer$'),
            (dict(TZ='env\tTZ\tEurope/Berlin'), BASE_LAYER,
             "^the toolchain image sets TZ to 'Europe/Berlin', not to 'UTC'$"),
            (dict(TZ='env\tTZ\t'), BASE_LAYER, "^the toolchain image sets TZ to '', not to 'UTC'$"),
            (dict(JAVA_HOME=None), BASE_LAYER, "^the toolchain image sets JAVA_HOME to None, not to '/jdk'$"),
            (dict(gcc='tool\tgcc\tgcc (Debian 15.1.0-2) 15.1.0 '), BASE_LAYER,
             "^gated tool gcc: wanted ' 14.2.0' in 'gcc \\(Debian 15.1.0-2\\) 15.1.0 '$"),
            (dict(gcc='tool\tgcc\tgcc-14.2.0'), BASE_LAYER, "^gated tool gcc: wanted ' 14.2.0' in 'gcc-14.2.0'$"),
            (dict(java='tool\tjava\t/bin/bash: line 7: /jdk/bin/java: No such file or directory '), BASE_LAYER,
             "^gated tool java: wanted 'Temurin-17.0.21\\+9' in "),
            (dict(java='tool\tjava\t'), BASE_LAYER, "^gated tool java: wanted 'Temurin-17.0.21\\+9' in ''$"),
            (dict(**{'g++': None}), BASE_LAYER, "^gated tool g\\+\\+: wanted ' 14.2.0' in None$"),
        )
        for changes, layer, message in cases:
            with self.subTest(changes=changes, layer=layer), self.assertRaisesRegex(PC.CacheError, message):
                PC.toolchain_document(toolchain_block(), self.observed(**changes), layer)
        for arch in ('amd64', 'arm64'):
            self.assertEqual(PC.toolchain_document(toolchain_block(), self.observed(arch=arch), BASE_LAYER)['arch'],
                             arch)
        # The first failing gate is the one named.
        both = self.observed(gcc='tool\tgcc\tgcc 13', java='tool\tjava\tjava 21')
        with self.assertRaisesRegex(PC.CacheError, '^gated tool gcc: '):
            PC.toolchain_document(toolchain_block(), both, BASE_LAYER)

    def test_record_counts_only_while_it_describes_this_manifest(self):
        good = toolchain_record()
        self.assertEqual(PC.recorded_toolchain(toolchain_block(), good), (FINGERPRINT, 'amd64', TOOLCHAIN_ID))
        self.assertEqual(PC.recorded_toolchain(toolchain_block(), toolchain_record('sha256:x', 'arm64')),
                         (fingerprint('arm64'), 'arm64', 'sha256:x'))
        # Fields that are no part of the fingerprint may be anything.
        loose = toolchain_record(observed=None, packages_sha256=None, outputs_before=7, notes=None, extra=1)
        self.assertEqual(PC.recorded_toolchain(toolchain_block(), loose), (FINGERPRINT, 'amd64', TOOLCHAIN_ID))

        def document(**fields):
            value = dict(good['document'], **fields)
            return dict(good, document=value, fingerprint=PC.derive_key(value))
        other = toolchain_block()
        other['gated'][0]['expect'] = ' 14.3.0'
        bad = {
            'not a record': [good], 'no record': None, 'a string': json.dumps(good),
            'status failed': dict(good, status='failed'), 'no status': {k: v for k, v in good.items() if k != 'status'},
            'status OK': dict(good, status='OK'),
            'no document': {k: v for k, v in good.items() if k != 'document'},
            'document is a list': dict(good, document=[]),
            'no image id': {k: v for k, v in good.items() if k != 'image_id'},
            'empty image id': dict(good, image_id=''), 'image id is a number': dict(good, image_id=7),
            'fingerprint of something else': dict(good, fingerprint=fingerprint('arm64')),
            'no fingerprint': {k: v for k, v in good.items() if k != 'fingerprint'},
            'fingerprint with a newline': dict(good, fingerprint=FINGERPRINT + '\n'),
            'unknown architecture': document(arch='s390x'), 'no architecture': document(arch=None),
            'layer that is no digest': document(base_layer='b' * 64), 'no layer': document(base_layer=None),
            'layer is a number': document(base_layer=7),
            'another base': document(base='sha256:' + 'e' * 64),
            'another environment': document(environment={'JAVA_HOME': '/jdk', 'TZ': 'CET'}),
            'another gate': document(gated=other['gated']),
            'one gate fewer': document(gated=good['document']['gated'][:-1]),
            'another document schema': document(schema=2),
            'an extra field': document(note='x'),
        }
        for what, record in bad.items():
            with self.subTest(what=what):
                self.assertIsNone(PC.recorded_toolchain(toolchain_block(), record))
        # The record of another manifest's toolchain is no record of this one, and the other way round.
        self.assertIsNone(PC.recorded_toolchain(other, good))
        self.assertEqual(PC.recorded_toolchain(other, toolchain_record(block=other))[0], fingerprint(block=other))

    def test_templates_are_rendered_from_known_names_only(self):
        values = {'root': '/r', 'repo': '/src', 'jdk': '/jdk'}
        self.assertEqual(PC.render('{root}/P/x', values), '/r/P/x')
        self.assertEqual(PC.render('A={repo}:{root}:{repo}', values), 'A=/src:/r:/src')
        self.assertEqual(PC.render('plain', values), 'plain')
        self.assertEqual(PC.render('', values), '')
        self.assertEqual(PC.render('{Root} {a1} {} ${HOME}', values), '{Root} {a1} {} ${HOME}')  # no placeholders
        for token, name in (('{nope}', 'nope'), ('{root}/{timestamp}', 'timestamp')):
            with self.subTest(token=token), self.assertRaises(PC.CacheError) as caught:
                PC.render(token, values)
            self.assertEqual(str(caught.exception), 'unknown variable {%s} in %r' % (name, token))


class ReplaceDecision(unittest.TestCase):
    """decide_replace: the one case in which a cached entry may be overwritten."""

    KEY, DIGEST, OTHER = 'a' * 64, 'sha256:' + '1' * 64, 'sha256:' + '2' * 64
    PRESENT = 'already cached under this key'

    def note(self, **changes):
        value = {'name': 'top', 'key': self.KEY, 'digest': self.DIGEST, 'kind': 'bundle', 'markers': {'base': 'm' * 64},
                 'reason': 'top: archive digest differs from the recorded one', 'at': 1}
        value.update(changes)
        return {key: item for key, item in value.items() if item is not ...}

    def decide(self, note, remote=..., markers=..., cached=True, key=None):
        return PC.decide_replace(note, key or self.KEY, self.DIGEST if remote is ... else remote,
                                 {'base': 'm' * 64} if markers is ... else markers, cached)

    def test_entry_refused_for_what_it_is_is_replaced_while_the_tag_still_names_it(self):
        self.assertEqual(self.decide(self.note()), (
            'replace', 'restore refused the cached entry (top: archive digest differs from the recorded one)'))
        # Its prerequisites play no part.
        for markers, cached in (({}, False), (None, False), ({'base': 'x' * 64}, True)):
            self.assertEqual(self.decide(self.note(), markers=markers, cached=cached)[0], 'replace')

    def test_without_a_usable_note_the_entry_stays(self):
        for what, note in (('no note', None), ('a list', [self.note()]), ('a string', 'bundle'),
                           ('note for another key', self.note(key='b' * 64)), ('no key', self.note(key=...)),
                           ('no digest', self.note(digest=...)), ('digest is null', self.note(digest=None)),
                           ('digest is a number', self.note(digest=7)), ('short digest', self.note(digest='sha256:1')),
                           ('bare hex', self.note(digest='1' * 64)),
                           ('digest with a newline', self.note(digest=self.DIGEST + '\n')),
                           ('unknown kind', self.note(kind='other')), ('no kind', self.note(kind=...))):
            with self.subTest(what=what):
                usable = isinstance(note, dict) and isinstance(note.get('digest'), str)
                remote = note['digest'] if usable else self.DIGEST
                self.assertEqual(self.decide(note, remote=remote), ('keep', self.PRESENT))
        self.assertEqual(self.decide(self.note(), key='b' * 64), ('keep', self.PRESENT))

    def test_entry_that_is_not_the_refused_one_stays_and_the_note_goes(self):
        self.assertEqual(self.decide(self.note(), remote=self.OTHER),
                         ('forget', self.PRESENT + '; it is not the entry that was refused here'))
        self.assertEqual(self.decide(self.note(), remote='sha256:' + '0' * 64),
                         ('forget', self.PRESENT + '; it is not the entry that was refused here'))
        self.assertEqual(self.decide(self.note(digest=self.OTHER), remote=self.DIGEST)[0], 'forget')
        self.assertEqual(self.decide(self.note(kind='prerequisites'), remote=self.OTHER)[0], 'forget')
        # Not knowing what the tag names now is no licence to overwrite; the note stays for the next push.
        for remote in (None, '', self.DIGEST[:-1], self.DIGEST + '\n', 7, self.DIGEST.upper()):
            with self.subTest(remote=remote):
                self.assertEqual(self.decide(self.note(), remote=remote), (
                    'keep', self.PRESENT + '; the registry did not name the entry, so the one refused here is not '
                                           'replaced'))

    def test_entry_bound_to_other_prerequisites_is_replaced_only_when_the_local_ones_are_the_cached_ones(self):
        note = self.note(kind='prerequisites')
        self.assertEqual(self.decide(note), (
            'replace', 'the cached entry is bound to other prerequisite outputs than the cached ones'))
        self.assertEqual(self.decide(note, cached=False), (
            'keep', self.PRESENT + '; it is bound to other prerequisite outputs, and the ones here are not the '
                                   'cached ones, so it may be usable elsewhere and is not replaced'))
        for markers in ({'base': 'x' * 64}, {}, None, {'base': 'm' * 64, 'more': 'm' * 64}):
            with self.subTest(markers=markers):
                self.assertEqual(self.decide(note, markers=markers), (
                    'forget', self.PRESENT + '; the prerequisites here changed since it was refused'))
                self.assertEqual(self.decide(note, markers=markers, cached=False)[0], 'forget')


class Toolchain(ServiceCase):
    """The service side of the fingerprint: probing, the record, the local check."""

    def record(self):
        return json.loads((self.root / 'status/toolchain.json').read_text())

    def test_first_use_probes_the_local_image_and_records_what_it_is(self):
        os.unlink(self.root / 'status/toolchain.json')
        with mock.patch.object(PC.time, 'time', return_value=1000.5):
            self.assertEqual(self.service.toolchain(), (FINGERPRINT, 'amd64'))
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)])
        self.assertEqual(self.registry.script, PC.probe_script(toolchain_block()))
        self.assertEqual(self.record(), {
            'stage': 'toolchain', 'status': 'ok', 'outputs': [PC.TOOLCHAIN], 'seconds': 0,
            'notes': 'toolchain fingerprint of the provider cache keys (provider_cache.py toolchain); observed '
                     'is what the probe saw and is not part of it; build_packages are the installed packages '
                     'build_selection chose, in the keys only with TAKX_PROTO_CACHE_PACKAGES=key',
            'image': PC.TOOLCHAIN, 'image_id': TOOLCHAIN_ID, 'fingerprint': FINGERPRINT,
            'document': PC.expected_toolchain(toolchain_block(), 'amd64', BASE_LAYER),
            'observed': PC.parse_probe(probe_text())['tools'], 'packages_sha256': 'e' * 64, 'outputs_before': None,
            'build_packages': BUILD_PACKAGES, 'build_packages_sha256': BUILD_DIGEST,
            'build_selection': {'sections': ['devel', 'libdevel'], 'names': ['libc6', '*-dev']}})
        self.assertEqual(self.messages, ['provider-cache: toolchain fingerprint %s (amd64, image %s, build packages '
                                         '%s)' % (FINGERPRINT, TOOLCHAIN_ID, BUILD_DIGEST)])
        self.assertEqual(sorted(os.listdir(self.root / 'status')), ['fetch.json', 'images.json', 'toolchain.json'])
        # From then on a key asks docker nothing, in this process or another.
        del self.registry.calls[:]
        self.assertEqual(self.service.key('base'), self.new_service().key('base'))
        self.assertEqual(self.new_service().toolchain(), (FINGERPRINT, 'amd64'))
        self.assertEqual(self.registry.calls, [])
        self.assertEqual(PC.recorded_toolchain(toolchain_block(), self.record()), (FINGERPRINT, 'amd64', TOOLCHAIN_ID))

    def test_the_architecture_of_the_image_is_the_architecture_of_the_keys(self):
        amd64 = self.service.explain('base')
        os.unlink(self.root / 'status/toolchain.json')
        self.registry.probe_output = probe_text('arm64')
        service = self.new_service()
        self.assertEqual(service.toolchain(), (fingerprint('arm64'), 'arm64'))
        arm64 = service.explain('base')
        self.assertEqual((arm64['arch'], arm64['toolchain']), ('arm64', fingerprint('arm64')))
        self.assertEqual({field for field in amd64 if amd64[field] != arm64[field]}, {'arch', 'toolchain'})
        self.assertNotEqual(service.key('base'), self.service.key('base'))

    def test_record_that_does_not_describe_the_manifest_is_not_used(self):
        other = toolchain_block()
        other['gated'][0]['expect'] = ' 14.3.0'
        stale = toolchain_record(block=other)
        cases = {'record of another manifest': stale, 'failed record': toolchain_record(status='failed'),
                 'record without image id': toolchain_record(image_id='')}
        for what, record in cases.items():
            self.write_json('status/toolchain.json', record)
            self.registry.docker = False
            del self.registry.calls[:]
            with self.subTest(what=what, docker=False), self.assertRaisesRegex(
                    PC.CacheError, r'^no toolchain fingerprint is recorded for this manifest \(status/toolchain\.json\)'
                                   r' '
                                   r'and docker is not available to probe takx-desktop-proto:toolchain$'):
                self.new_service().key('base')
            self.assertEqual(self.registry.calls, [])
            self.registry.docker = True
        # With docker the image is probed. A record that named another fingerprint is not replaced silently.
        self.write_json('status/toolchain.json', stale)
        with self.assertRaisesRegex(PC.CacheError, 'has toolchain fingerprint %s, not the recorded %s; '
                                    % (FINGERPRINT, stale['fingerprint'])):
            self.new_service().key('base')
        self.assertEqual(self.record(), stale)
        # One that named this fingerprint (or none) is brought up to date.
        for what, record in (('failed record', cases['failed record']), ('no fingerprint', {'status': 'ok'}),
                             ('record without image id', cases['record without image id'])):
            self.write_json('status/toolchain.json', record)
            with self.subTest(what=what):
                self.assertEqual(self.new_service().toolchain(), (FINGERPRINT, 'amd64'))
                self.assertEqual(self.record()['image_id'], TOOLCHAIN_ID)
                self.assertEqual(self.record()['status'], 'ok')
        for text in ('{not json', '[]', '"text"', ''):
            (self.root / 'status/toolchain.json').write_text(text)
            with self.subTest(text=text):
                self.assertEqual(self.new_service().toolchain(), (FINGERPRINT, 'amd64'))
                self.assertEqual(self.record()['fingerprint'], FINGERPRINT)

    def test_image_that_fails_a_gate_is_never_recorded(self):
        os.unlink(self.root / 'status/toolchain.json')
        for what, change, message in (
                ('gate', lambda: setattr(self.registry, 'probe_output', probe_text(gcc='tool\tgcc\tgcc 15.1.0 ')),
                 "^gated tool gcc: wanted ' 14.2.0' in 'gcc 15.1.0 '$"),
                ('environment', lambda: setattr(self.registry, 'probe_output', probe_text(TZ='env\tTZ\tCET')),
                 "^the toolchain image sets TZ to 'CET', not to 'UTC'$"),
                ('architecture', lambda: setattr(self.registry, 'probe_output', probe_text('riscv64')),
                 "^the toolchain image reports architecture 'riscv64'"),
                ('probe output', lambda: setattr(self.registry, 'probe_output', 'bash: dpkg: command not found\n'),
                 '^unexpected line in the toolchain probe output'),
                ('layer', lambda: setattr(self.registry, 'layer', None), '^the toolchain image names no first layer$'),
                ('no image', lambda: setattr(self.registry, 'toolchain', None),
                 '^there is no local takx-desktop-proto:toolchain image to fingerprint$')):
            self.registry.probe_output, self.registry.layer, self.registry.toolchain = (
                probe_text(), BASE_LAYER, TOOLCHAIN_ID)
            change()
            with self.subTest(what=what), self.assertRaisesRegex(PC.CacheError, message):
                self.new_service().key('base')
            self.assertFalse((self.root / 'status/toolchain.json').exists())
        self.assertEqual(self.messages, [])

    def test_record_command_accepts_another_toolchain_and_holds_back_what_was_built_before(self):
        self.make('base')
        old_key = self.service.key('base')
        self.registry.toolchain, self.registry.layer = 'sha256:' + '9' * 64, 'sha256:' + 'c' * 64
        new = fingerprint(layer='sha256:' + 'c' * 64)
        out = io.StringIO()
        service = self.new_service()
        with mock.patch.object(PC.time, 'time', return_value=5000.9):
            self.assertEqual(PC.main(['toolchain', '--record'], service=service, out=out), 0)
        self.assertEqual(out.getvalue(), new + '\tamd64\n')
        record = self.record()
        self.assertEqual((record['fingerprint'], record['image_id'], record['outputs_before']),
                         (new, 'sha256:' + '9' * 64, 5001))
        new_key = self.new_service().key('base')
        self.assertNotEqual(new_key, old_key)
        status = self.root / 'status/base.json'
        for written in (5000.999, 4000, 0):  # ok before the change: not an output of the toolchain the key names
            os.utime(status, (written, written))
            with self.subTest(written=written), self.assertRaisesRegex(
                    PC.CacheError, '^base: it was ok before the toolchain record was replaced, so it is not an '
                                   'output of the toolchain its key would name; rebuild it or restore it$'):
                self.new_service().push('base')
        self.assertEqual((self.registry.published, self.leftovers()), (0, []))
        for written in (5001, 5001.5, 9000):  # built or restored since
            os.utime(status, (written, written))
            self.registry.images.clear()
            with self.subTest(written=written):
                self.assertEqual(self.new_service().push('base'), 'reg.example/cache/base:' + new_key)
        # Recording the same toolchain again, also as another image, changes neither the keys nor that time.
        self.registry.toolchain = 'sha256:' + '8' * 64
        for argv in (['toolchain', '--record'], ['toolchain']):
            out = io.StringIO()
            with mock.patch.object(PC.time, 'time', return_value=7000.0):
                self.assertEqual(PC.main(argv, service=self.new_service(), out=out), 0)
            self.assertEqual(out.getvalue(), new + '\tamd64\n')
        self.assertEqual((self.record()['outputs_before'], self.record()['image_id']), (5001, 'sha256:' + '8' * 64))
        self.new_service().push('base')
        self.assertEqual((self.record()['outputs_before'], self.record()['image_id']), (5001, 'sha256:' + '8' * 64))
        # Without --record the command only reads; it never accepts another toolchain.
        self.write_json('status/toolchain.json', toolchain_record())
        del self.registry.calls[:]
        out = io.StringIO()
        self.assertEqual(PC.main(['toolchain'], service=self.new_service(), out=out), 0)
        self.assertEqual((out.getvalue(), self.registry.calls), (FINGERPRINT + '\tamd64\n', []))
        self.assertEqual(self.record(), toolchain_record())

    def test_outputs_are_held_back_from_the_second_after_the_record_was_replaced(self):
        self.registry.toolchain, self.registry.layer = 'sha256:' + '9' * 64, 'sha256:' + 'c' * 64
        for now, expected in ((5001.2, 5002), (5002.0, 5003), (6000.999, 6001)):   # odd and even seconds
            self.write_json('status/toolchain.json', toolchain_record())
            service = self.new_service()
            service.rerecord = True
            with self.subTest(now=now), mock.patch.object(PC.time, 'time', return_value=now):
                service.toolchain(local=True)
                self.assertEqual(self.record()['outputs_before'], expected)

    def test_record_is_written_below_a_work_root_that_does_not_exist_yet(self):
        root = Path(self.tmp.name) / 'new' / 'work-root'
        tree = PC.WorkTree(root, self.repo)
        tree.record_toolchain(toolchain_record())
        self.assertEqual(json.loads((root / 'status/toolchain.json').read_text()), toolchain_record())
        self.assertEqual(os.listdir(root / 'status'), ['toolchain.json'])
        self.assertEqual(tree.toolchain_record(), toolchain_record())

    def test_keys_follow_the_record_the_local_check_accepted_whichever_way_it_changed(self):
        layers = ['sha256:' + '%x' % digit * 64 for digit in range(16)]
        lower = [layer for layer in layers if fingerprint(layer=layer) < FINGERPRINT]
        higher = [layer for layer in layers if fingerprint(layer=layer) > FINGERPRINT]
        self.assertTrue(lower and higher)
        for layer in (lower[0], higher[0]):
            service = self.new_service()
            before = service.key('base')                       # derived from the record of setUp
            self.write_json('status/toolchain.json', toolchain_record(layer=layer))   # same image id: accepted
            with self.subTest(layer=layer):
                self.assertEqual(service._check_toolchain(), (fingerprint(layer=layer), 'amd64'))
                self.assertNotEqual(service.key('base'), before)
                self.assertEqual(service.key('base'), self.new_service().key('base'))
                self.assertEqual(service.explain('base')['toolchain'], fingerprint(layer=layer))
            self.write_json('status/toolchain.json', toolchain_record())

    def test_record_command_always_probes_and_reports_failures(self):
        out = io.StringIO()
        self.assertEqual(PC.main(['toolchain', '--record'], service=self.service, out=out), 0)
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)])
        self.assertEqual(self.record()['outputs_before'], None)  # the same toolchain: nothing is held back
        self.registry.docker = False
        with mock.patch.object(PC.sys, 'stderr', io.StringIO()) as errors:
            self.assertEqual(PC.main(['toolchain', '--record'], service=self.new_service(), out=out), 1)
        self.assertIn('docker is not available', errors.getvalue())
        out = io.StringIO()
        self.assertEqual(PC.main(['toolchain', '--probe-script'], service=self.new_service(), out=out), 0)
        self.assertEqual(out.getvalue(), PC.probe_script(toolchain_block()))

    def test_recorded_image_id_is_accepted_only_when_it_is_exactly_the_local_one(self):
        self.assertEqual(self.service.toolchain(local=True), (FINGERPRINT, 'amd64'))
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN)])
        self.assertEqual(self.service._check_toolchain(), (FINGERPRINT, 'amd64'))
        self.registry.layer = 'sha256:' + 'c' * 64   # what a probe would find: another toolchain
        for recorded in ('sha256:' + '0' * 64, 'sha256:' + 'f' * 64, TOOLCHAIN_ID + '0', TOOLCHAIN_ID[:-1]):
            self.write_json('status/toolchain.json', toolchain_record(image_id=recorded))
            del self.registry.calls[:]
            with self.subTest(recorded=recorded), self.assertRaisesRegex(PC.CacheError, 'has toolchain fingerprint'):
                self.new_service().toolchain(local=True)
            self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)])

    def test_keys_derived_from_another_record_are_dropped_by_the_local_check(self):
        self.make('base')
        amd64 = self.service.key('base')
        # Another process replaced the record while this one already held keys.
        self.write_json('status/toolchain.json', toolchain_record(arch='arm64'))
        ref = self.service.push('base')
        arm64 = self.new_service().key('base')
        self.assertNotEqual(arm64, amd64)
        self.assertEqual(ref, 'reg.example/cache/base:' + arm64)
        self.assertEqual(json.loads(self.registry.images[ref][1]['meta.json'])['key_inputs']['arch'], 'arm64')
        self.assertEqual(self.service.key('base'), arm64)

    def test_work_tree_reads_and_writes_the_record(self):
        tree = self.service.tree
        self.assertEqual(tree.toolchain_record(), toolchain_record())
        tree.record_toolchain({'b': 1, 'a': [2]})
        self.assertEqual((self.root / 'status/toolchain.json').read_text(), '{\n "a": [\n  2\n ],\n "b": 1\n}\n')
        self.assertEqual(sorted(os.listdir(self.root / 'status')), ['fetch.json', 'images.json', 'toolchain.json'])
        for text in ('[1]', '"x"', '7', 'null', '{', ''):
            (self.root / 'status/toolchain.json').write_text(text)
            self.assertIsNone(tree.toolchain_record(), text)
        os.unlink(self.root / 'status/toolchain.json')
        self.assertIsNone(tree.toolchain_record())
        shutil.rmtree(self.root / 'status')
        tree.record_toolchain({'a': 1})  # the directory is made when needed
        self.assertEqual(tree.toolchain_record(), {'a': 1})
        with mock.patch.object(PC.os, 'replace', side_effect=OSError(28, 'No space left on device')), \
                self.assertRaises(OSError):
            tree.record_toolchain({'a': 2})
        self.assertEqual(os.listdir(self.root / 'status'), ['toolchain.json'])  # no temporary file stays
        self.assertEqual(tree.toolchain_record(), {'a': 1})

    def test_work_tree_reads_the_status_of_a_provider(self):
        tree = self.service.tree
        self.assertEqual((tree.status_json('base'), tree.status_time('base')), ({}, None))
        self.make('base')
        self.assertEqual(tree.status_json('base')['status'], 'ok')
        os.utime(self.root / 'status/base.json', (10, 1234.5))
        self.assertEqual(tree.status_time('base'), 1234.5)
        for text in ('[1]', '"ok"', '{', ''):
            (self.root / 'status/base.json').write_text(text)
            self.assertEqual(tree.status_json('base'), {}, text)


class Replace(ServiceCase):
    """An entry restore had to refuse is replaced by the push after the rebuild; a healthy one never is."""

    def setUp(self):
        super().setUp()
        self.make('base')
        self.ref = self.service.push('base')
        self.digest = self.registry.digests[self.ref]
        self.set_aside('base')
        del self.registry.calls[:]
        del self.messages[:]

    def notes(self):
        path = self.root / 'cache-state/refused.json'
        return json.loads(path.read_text()) if path.exists() else None

    def corrupt(self, ref=None, name='base'):
        """One changed byte in the stored archive; the registry digest stays, as for a bad upload."""
        content = self.registry.images[ref or self.ref][1]
        data = bytearray(content[name + '.tar'])
        data[len(data) // 2] ^= 1
        content[name + '.tar'] = bytes(data)

    def refuse(self, name='base', error=PC.CacheError):
        with self.assertRaises(error) as caught:
            self.service.restore(name)
        self.assertFalse(os.path.lexists(self.root / 'P' / name))
        self.assertEqual(self.leftovers(), [])
        return str(caught.exception)

    def test_refused_entry_is_replaced_by_the_push_after_the_rebuild(self):
        self.corrupt()
        with mock.patch.object(PC.time, 'time', return_value=1234.9):
            message = self.refuse()
        self.assertEqual(message, 'base: archive digest differs from the recorded one')
        key = self.service.key('base')
        self.assertEqual(self.notes(), {self.ref: {'name': 'base', 'key': key, 'digest': self.digest, 'kind': 'bundle',
                                                  'markers': {}, 'reason': message, 'at': 1234}})
        self.assertEqual(self.registry.published, 1)
        rebuilt = self.make('base')
        good = snapshot(rebuilt)
        del self.registry.calls[:]
        self.assertEqual(self.service.push('base'), self.ref)
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe', self.ref), ('digest', self.ref),
                                               ('publish', self.ref), ('probe', self.ref), ('digest', self.ref)])
        self.assertEqual(self.registry.published, 2)
        self.assertNotEqual(self.registry.digests[self.ref], self.digest)
        self.assertIsNone(self.notes() or None)   # the note is used up
        tar_sha = hashlib.sha256(self.registry.images[self.ref][1]['base.tar']).hexdigest()
        self.assertEqual(self.messages, [
            'provider-cache: base: restore refused the cached entry (%s); replacing %s@%s'
            % (message, self.ref, self.digest),
            'provider-cache: replaced %s (9 entries, tar sha256 %s)' % (self.ref, tar_sha)])
        self.assertEqual(self.service.tree.pushed()[('base', key)], tar_sha)
        # The next machine gets the rebuilt output, and a push there leaves it alone.
        self.set_aside('base')
        self.service.restore('base')
        self.assertEqual(snapshot(rebuilt), good)
        del self.registry.calls[:]
        self.service.push('base')
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe', self.ref)])
        self.assertEqual(self.registry.published, 2)

    def test_every_judgement_of_the_bundle_is_noted_with_the_digest_that_was_pulled(self):
        labels, content = self.registry.images[self.ref]
        stored = dict(labels), dict(content)

        def meta(**fields):
            content['meta.json'] = json.dumps(dict(json.loads(stored[1]['meta.json']), **fields)).encode()
        cases = (
            ('archive bytes', self.corrupt, 'archive digest differs from the recorded one'),
            ('label', lambda: labels.update({'dev.uds.takx.key': '0' * 64}), 'image is labelled with another key'),
            ('metadata', lambda: meta(root='/elsewhere'), 'bundle was built under another work root'),
            ('schema of the past', lambda: meta(schema=1), 'unsupported bundle schema'),
            ('marker digest', lambda: meta(marker_sha256='0' * 64), 'completion marker differs'),
            ('metadata that is no JSON', lambda: content.update({'meta.json': b'{'}), 'not JSON'),
            ('file manifest', lambda: content.update({'files.json': b'{}'}), 'file manifest differs'),
            ('archive that is no archive', lambda: (content.update({'base.tar': b'x' * 2048}), labels.update(
                {'dev.uds.takx.tar-sha256': hashlib.sha256(b'x' * 2048).hexdigest()}), content.update(
                {'tar.sha256': hashlib.sha256(b'x' * 2048).hexdigest().encode()}), meta(
                tar_sha256=hashlib.sha256(b'x' * 2048).hexdigest())), ''),
            ('a file the image lacks', lambda: content.pop('meta.json'), 'the image holds no /meta.json'),
        )
        for what, change, message in cases:
            labels.clear()
            labels.update(stored[0])
            content.clear()
            content.update(stored[1])
            (self.root / 'cache-state/refused.json').unlink(missing_ok=True)
            change()
            with self.subTest(what=what):
                self.assertIn(message, self.refuse(error=(PC.CacheError, tarfile.TarError)))
                note = self.notes()[self.ref]
                self.assertEqual((note['kind'], note['digest'], note['key'], note['name'], note['markers']),
                                 ('bundle', self.digest, self.service.key('base'), 'base', {}))
                self.assertIn(message, note['reason'])
                self.assertLessEqual(len(note['reason']), 300)

    def test_what_is_no_judgement_of_the_entry_is_not_noted(self):
        def raising(error):
            def action(*arguments, **options):
                raise error
            return action
        cases = (
            ('registry error on the pull', mock.patch.object(self.registry, 'fetch', raising(PC.CacheError(
                'docker pull failed: net/http: TLS handshake timeout'))), PC.CacheError),
            ('copy that failed here', mock.patch.object(self.registry, 'fetch', raising(PC.CacheError(
                'docker cp failed: no space left on device'))), PC.CacheError),
            ('full disk while unpacking', mock.patch.object(self.service.tree, 'unpack', raising(
                OSError(28, 'No space left on device'))), OSError),
            ('unreadable scratch file', mock.patch.object(PC, 'sha256_file', raising(
                OSError(5, 'Input/output error'))), OSError),
            ('directory that appeared meanwhile', mock.patch.object(self.service.tree, 'install', raising(
                FileExistsError('x'))), PC.CacheError),
            ('status that cannot be written', mock.patch.object(self.service.tree, 'install', raising(
                OSError(28, 'No space left on device'))), OSError),
        )
        for what, patch, error in cases:
            with self.subTest(what=what), patch:
                self.refuse(error=error)
            self.assertIsNone(self.notes(), what)
        # A prerequisite that changes during the restore is a local race, too.
        self.service.restore('base')
        self.make('top')
        top = self.service.push('top')
        self.set_aside('top')
        real = self.service._markers
        answers = iter([real('top'), {'base': '0' * 64}])
        with mock.patch.object(self.service, '_markers', lambda name: next(answers)):
            self.assertEqual(self.refuse('top'), 'top: a prerequisite changed during the restore')
        self.assertIsNone(self.notes())
        self.assertIn(top, self.registry.images)
        # So the entry stays whatever is built and pushed afterwards.
        self.set_aside('base')
        self.make('base')
        self.service.push('base')
        self.assertEqual((self.registry.published, self.registry.digests[self.ref]), (2, self.digest))

    def test_long_reason_is_cut_in_the_note(self):
        def failing(*arguments, **options):
            raise PC.CacheError('base: ' + 'x' * 500)
        with mock.patch.object(self.service, '_examine', failing):
            self.refuse()
        self.assertEqual(self.notes()[self.ref]['reason'], 'base: ' + 'x' * 294)

    def test_healthy_entry_is_never_overwritten(self):
        key = self.service.key('base')
        self.make('base')
        stored = copy.deepcopy(self.registry.images[self.ref])
        notes = {
            'no note for this reference': {self.ref + 'x': {'key': key, 'digest': self.digest, 'kind': 'bundle'}},
            'note of another key': {self.ref: {'key': '0' * 64, 'digest': self.digest, 'kind': 'bundle'}},
            'note without a digest': {self.ref: {'key': key, 'kind': 'bundle'}},
            'note of an unknown kind': {self.ref: {'key': key, 'digest': self.digest, 'kind': 'whim'}},
            'note that is no object': {self.ref: 'bundle'},
        }
        for what, value in notes.items():
            self.write_json('cache-state/refused.json', value)
            del self.messages[:]
            with self.subTest(what=what):
                self.assertEqual(self.service.push('base'), self.ref)
                self.assertEqual(self.messages, ['provider-cache: base is already cached under this key: ' + self.ref])
                self.assertEqual(self.notes(), value)
        self.assertEqual((self.registry.published, self.registry.images[self.ref]), (1, stored))
        self.assertEqual(self.registry.digests[self.ref], self.digest)

    def test_entry_someone_else_pushed_meanwhile_is_kept_and_the_note_dropped(self):
        self.corrupt()
        self.refuse()
        other = self.ref.replace('/base:', '/side:')
        self.service.tree.note_refusal(other, {'key': 'k', 'digest': self.digest, 'kind': 'bundle'})
        labels, content = copy.deepcopy(self.registry.images[self.ref])
        self.registry.store(self.ref, labels, content)   # another machine replaced it first
        theirs = self.registry.digests[self.ref]
        self.assertNotEqual(theirs, self.digest)
        self.make('base')
        del self.messages[:]
        self.assertEqual(self.service.push('base'), self.ref)
        self.assertEqual(self.messages, ['provider-cache: base is already cached under this key; it is not the entry '
                                         'that was refused here: ' + self.ref])
        self.assertEqual((self.registry.published, self.registry.digests[self.ref]), (2, theirs))
        self.assertEqual(self.notes(), {other: {'key': 'k', 'digest': self.digest, 'kind': 'bundle'}})

    def test_without_a_registry_digest_nothing_is_replaced(self):
        # docker names no digest for the pulled image: the refusal cannot be tied to an entry.
        self.corrupt()
        del self.registry.digests[self.ref]
        self.refuse()
        self.assertIsNone(self.notes())
        self.assertEqual(self.messages, [
            'provider-cache: base: docker named no registry digest for %s, so the refusal is not noted and a later '
            'push will not replace the entry' % self.ref])
        # The registry does not say what the tag names at push time: the note stays, the entry too.
        self.registry.digests[self.ref] = self.digest
        self.refuse()
        note = self.notes()
        self.assertEqual(note[self.ref]['digest'], self.digest)
        del self.registry.digests[self.ref]
        self.make('base')
        del self.messages[:]
        self.service.push('base')
        self.assertEqual(self.messages, ['provider-cache: base is already cached under this key; the registry did not '
                                         'name the entry, so the one refused here is not replaced: ' + self.ref])
        self.assertEqual((self.registry.published, self.notes()), (1, note))

    def test_replacing_entry_may_have_any_digest_but_the_refused_one(self):
        store = self.registry.store
        for number, digest in enumerate(('sha256:' + '0' * 64, 'sha256:' + 'f' * 64, None)):
            self.registry.digests[self.ref] = self.digest = 'sha256:' + '7' * 64
            self.corrupt()
            self.refuse()
            self.make('base')

            def stored(ref, labels, content, digest=digest):
                store(ref, labels, content)
                self.registry.digests[ref] = digest      # lower, higher, or not named by the registry
            del self.messages[:]
            with self.subTest(digest=digest), mock.patch.object(self.registry, 'store', stored):
                self.assertEqual(self.service.push('base'), self.ref)
                self.assertEqual(self.registry.published, 2 + number)
                self.assertEqual(self.notes(), {})
                self.assertIn('provider-cache: replaced ' + self.ref, self.messages[-1])
            self.set_aside('base')

    def test_push_that_leaves_the_refused_entry_under_the_tag_is_an_error(self):
        self.corrupt()
        self.refuse()
        note = self.notes()
        self.make('base')
        with mock.patch.object(self.registry, 'store', lambda ref, labels, content: None), self.assertRaisesRegex(
                PC.CacheError, '^base: pushed, but %s still names the refused entry %s$'
                % (re.escape(self.ref), self.digest)):
            self.service.push('base')
        self.assertEqual(self.notes(), note)
        self.assertEqual(self.leftovers(), [])

    def chain(self):
        """base and top cached as a consistent pair, nothing local."""
        self.service.restore('base')
        self.make('top')
        top = self.service.push('top')
        self.set_aside('top')
        self.set_aside('base')
        del self.messages[:]
        return top

    def test_entry_bound_to_a_local_only_prerequisite_is_healthy_for_others_and_stays(self):
        top = self.chain()
        digest = self.registry.digests[top]
        self.make('base')                       # built here although the cache has one: another marker
        self.service.push('base')               # "already cached": the cache keeps serving the first one
        self.assertEqual(self.refuse('top', PC.OtherPrerequisites),
                         'top: it was built against other prerequisite outputs than the ones present here')
        note = self.notes()[top]
        marker = hashlib.sha256((self.root / 'P/base/complete.json').read_bytes()).hexdigest()
        self.assertEqual((note['kind'], note['digest'], note['markers']), ('prerequisites', digest, {'base': marker}))
        self.make('top')
        del self.messages[:]
        self.assertEqual(self.service.push('top'), top)
        self.assertEqual(self.messages, [
            'provider-cache: top is already cached under this key; it is bound to other prerequisite outputs, and '
            'the ones here are not the cached ones, so it may be usable elsewhere and is not replaced: ' + top])
        self.assertEqual((self.registry.published, self.registry.digests[top]), (2, digest))
        self.assertEqual(self.notes()[top], note)
        # A fresh machine still restores the pair that is cached.
        self.set_aside('top')
        self.set_aside('base')
        self.service.restore('base')
        self.service.restore('top')

    def test_entry_orphaned_by_a_replaced_prerequisite_is_replaced_too(self):
        top = self.chain()
        self.corrupt()
        self.refuse('base')
        self.make('base')
        self.service.push('base')               # replaces base: top in the cache is bound to the old one
        self.assertEqual(self.registry.published, 3)
        self.refuse('top', PC.OtherPrerequisites)
        self.make('top')
        del self.messages[:]
        self.assertEqual(self.service.push('top'), top)
        self.assertEqual(self.messages[0], 'provider-cache: top: the cached entry is bound to other prerequisite '
                         'outputs than the cached ones; replacing %s@sha256:%064x' % (top, 2))
        self.assertEqual(self.registry.published, 4)
        self.assertEqual(self.notes(), {})
        good = {name: snapshot(self.root / 'P' / name) for name in ('base', 'top')}
        self.set_aside('top')
        self.set_aside('base')
        self.service.restore('base')
        self.service.restore('top')
        self.assertEqual({name: snapshot(self.root / 'P' / name) for name in ('base', 'top')}, good)

    def test_entry_is_replaced_when_the_prerequisite_was_restored_from_the_cache(self):
        top = self.chain()
        # Another machine replaced base; top in the cache is still bound to the first one.
        self.service.restore('base')
        self.set_aside('base')
        self.make('base')
        labels, content = self.registry.images[self.ref]
        scratch = PC.ProviderCache(manifest(), self.service.tree, FakeRegistry(), 'reg.example/cache',
                                   log=self.messages.append)
        scratch.push('base')
        self.registry.store(self.ref, *scratch.registry.images[self.ref])
        self.set_aside('base')
        os.unlink(self.root / 'cache-state/pushed.tsv')   # nothing was pushed from this work root
        self.service.restore('base')
        self.refuse('top', PC.OtherPrerequisites)
        self.make('top')
        self.assertEqual(self.service.push('top'), top)
        self.assertEqual(self.registry.digests[top], 'sha256:%064x' % 4)
        self.assertEqual(self.notes(), {})
        # Restored under another key or from another reference does not count as the cached one.
        for field, value in (('cache_key', '0' * 64), ('cache_key', 'f' * 64), ('cache_key', None),
                             ('restored_from', self.ref + 'x'), ('restored_from', self.ref[:-1]),
                             ('restored_from', None)):
            status = json.loads((self.root / 'status/base.json').read_text())
            self.write_json('status/base.json', dict(status, **{field: value}))
            with self.subTest(field=field):
                self.assertFalse(self.service._prerequisites_cached('top'))
            self.write_json('status/base.json', status)
        self.assertTrue(self.service._prerequisites_cached('top'))
        self.assertTrue(self.service._prerequisites_cached('base'))   # nothing to be bound to

    def test_note_about_prerequisites_that_changed_again_is_dropped(self):
        top = self.chain()
        self.make('base')
        self.refuse('top', PC.OtherPrerequisites)
        self.set_aside('base')
        self.make('base')                       # a third base
        self.make('top')
        del self.messages[:]
        self.service.push('top')
        self.assertEqual(self.messages, ['provider-cache: top is already cached under this key; the prerequisites '
                                         'here changed since it was refused: ' + top])
        self.assertEqual((self.registry.published, self.notes()), (2, {}))

    def test_notes_are_kept_per_reference(self):
        tree = self.service.tree
        self.assertEqual(tree.refusals(), {})
        tree.note_refusal('r/a:1', None)  # forgetting what was never noted writes nothing
        self.assertFalse((self.root / 'cache-state/refused.json').exists())
        tree.note_refusal('r/a:1', {'key': 'k1'})
        tree.note_refusal('r/b:2', {'key': 'k2'})
        tree.note_refusal('r/a:1', {'key': 'k3'})
        self.assertEqual(tree.refusals(), {'r/a:1': {'key': 'k3'}, 'r/b:2': {'key': 'k2'}})
        tree.note_refusal('r/a:1', None)
        tree.note_refusal('r/c:3', None)
        self.assertEqual(tree.refusals(), {'r/b:2': {'key': 'k2'}})
        tree.note_refusal('r/b:2', None)
        self.assertEqual((tree.refusals(), self.notes()), ({}, {}))
        self.assertEqual(sorted(os.listdir(self.root / 'cache-state')), ['pushed.tsv', 'refused.json'])
        for text in ('[1]', '"x"', '{', ''):
            (self.root / 'cache-state/refused.json').write_text(text)
            self.assertEqual(tree.refusals(), {}, text)
        shutil.rmtree(self.root / 'cache-state')
        self.assertEqual((tree.refusals(), tree.pushed_markers()), ({}, {}))
        tree.note_refusal('r/a:1', {'key': 'k1'})  # the directory is made when needed
        self.assertEqual(tree.refusals(), {'r/a:1': {'key': 'k1'}})
        tree.record_push(('a', 'k', 'r/a:1', 't', 'm1'))
        tree.record_push(('b', 'k', 'r/b:2', 't', 'm2'))
        tree.record_push(('a', 'k', 'r/a:1', 't', 'm3'))   # pushed again: the later output is the served one
        tree.record_push(('c', 'k', 'r/c:3', 't'))         # a row written by schema 1 names no output
        (self.root / 'cache-state/pushed.tsv').write_text(
            (self.root / 'cache-state/pushed.tsv').read_text() + 'short\trow\na\tk\tr/d:4\tt\tm\tsix\n')
        self.assertEqual(tree.pushed_markers(), {'r/a:1': 'm3', 'r/b:2': 'm2'})
        self.assertEqual(tree.pushed(), {('a', 'k'): 't', ('b', 'k'): 't', ('c', 'k'): 't'})

    def test_two_writers_do_not_lose_a_note(self):
        tree = self.service.tree
        started, release = threading.Event(), threading.Event()
        real = PC.WorkTree._write_json

        def slow(path, value):
            started.set()
            release.wait(5)
            real(path, value)
        with mock.patch.object(PC.WorkTree, '_write_json', staticmethod(slow)):
            first = threading.Thread(target=tree.note_refusal, args=('r/a:1', {'key': 'k1'}))
            first.start()
            self.assertTrue(started.wait(5))
        second = threading.Thread(target=tree.note_refusal, args=('r/b:2', {'key': 'k2'}))
        second.start()
        second.join(0.3)
        self.assertTrue(second.is_alive())  # it waits for the first writer
        release.set()
        first.join(5)
        second.join(5)
        self.assertEqual(tree.refusals(), {'r/a:1': {'key': 'k1'}, 'r/b:2': {'key': 'k2'}})


class FakeRunner:
    """Runner port: records the call, looks at the work root, answers with a code."""

    def __init__(self, root, code=0):
        self.root, self.code, self.calls, self.seen = Path(root), code, [], []

    def run(self, name, env, argv, log):
        self.calls.append((name, list(env), list(argv), str(log)))
        self.seen.append({'tree': sorted(os.listdir(self.root / 'P' / name)),
                          'status': (self.root / 'status' / (name + '.json')).exists(),
                          'logs': (self.root / 'logs').is_dir()})
        if isinstance(self.code, Exception):
            raise self.code
        return self.code


class PostRestoreCheck(ServiceCase):
    """The recipe's own re-check of a restored tree (providers.json 'verify')."""

    def document(self):
        value = manifest()
        value['providers']['base']['verify'] = {
            'env': ['PROOF={root}/P/base', 'JDK={jdk}'],
            'argv': ['python3', '-B', '-m', 'unittest', 'discover', '-s', '{repo}/takx/one', '-p', 'test_build.py']}
        return value

    def setUp(self):
        super().setUp()
        self.runner = FakeRunner(self.root)
        self.service = self.checked()
        self.top = self.make('base')
        self.good = snapshot(self.top)
        self.ref = self.service.push('base')
        self.digest = self.registry.digests[self.ref]
        self.set_aside('base')
        del self.messages[:]

    def checked(self, **options):
        options.setdefault('runner', self.runner)
        return PC.ProviderCache(self.document(), PC.WorkTree(self.root, self.repo), self.registry,
                                'reg.example/cache', '', self.messages.append, **options)

    def notes(self):
        path = self.root / 'cache-state/refused.json'
        return json.loads(path.read_text()) if path.exists() else None

    def test_check_does_not_change_the_key(self):
        self.assertEqual(self.service.key('base'), self.new_service().key('base'))

    def test_check_runs_on_the_tree_in_place_before_the_status_says_ok(self):
        self.assertEqual(self.service.restore('base'), self.ref)
        self.assertEqual(self.runner.calls, [(
            'base', ['PROOF=%s/P/base' % self.root, 'JDK=/jdk'],
            ['python3', '-B', '-m', 'unittest', 'discover', '-s', '%s/takx/one' % self.repo, '-p', 'test_build.py'],
            '%s/logs/base-verify.log' % self.root)])
        self.assertEqual(self.runner.seen, [{'tree': sorted(os.listdir(self.top)), 'status': False, 'logs': True}])
        self.assertEqual(snapshot(self.top), self.good)
        self.assertEqual(self.service.tree.status_of('base'), 'ok')
        self.assertEqual(self.messages, ["provider-cache: restored base from %s (9 entries verified, the recipe's "
                                         "own check passed)" % self.ref])
        self.assertIsNone(self.notes())

    def test_rejected_tree_is_taken_out_again_and_the_entry_is_replaced_after_the_rebuild(self):
        (self.root / 'status/base.json').write_text('{"stage": "base", "status": "failed"}\n')
        before = settled(self.tmp.name)
        self.runner.code = 1
        with self.assertRaises(PC.Rejected) as caught:
            self.service.restore('base')
        self.assertEqual(str(caught.exception), "base: the recipe's own check rejects the restored output; see "
                                                "logs/base-verify.log")
        self.assertEqual(self.runner.seen, [{'tree': sorted(os.listdir(Path(self.tmp.name) / os.listdir(
            self.tmp.name)[0])) if False else self.runner.seen[0]['tree'], 'status': True, 'logs': True}])
        after = settled(self.tmp.name)
        after.pop('root/logs')
        self.assertEqual(after, before)           # no tree, the earlier status untouched, no scratch
        self.assertFalse(os.path.lexists(self.root / 'P/base'))
        self.assertEqual((self.root / 'status/base.json').read_text(), '{"stage": "base", "status": "failed"}\n')
        note = self.notes()[self.ref]
        self.assertEqual((note['kind'], note['digest'], note['reason']), ('check', self.digest, str(caught.exception)))
        files = hashlib.sha256(self.bundle('base')[1]['files.json']).hexdigest()
        self.assertEqual((note['files_sha256'], note['check']), (files, PC.check_identity('recipe', [
            'PROOF=%s/P/base' % self.root, 'JDK=/jdk', '--', 'python3', '-B', '-m', 'unittest', 'discover', '-s',
            '%s/takx/one' % self.repo, '-p', 'test_build.py'])))
        # The lane builds; its push replaces what the check rejected, because the
        # same check accepts the rebuilt output and that output is another one.
        os.unlink(self.root / 'status/base.json')
        self.make('base')
        self.runner.code = 0
        del self.runner.calls[:]
        self.service.push('base')
        self.assertEqual(self.registry.published, 2)
        self.assertNotEqual(self.registry.digests[self.ref], self.digest)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(self.notes(), {})

    def test_check_that_could_not_run_is_no_judgement_of_the_entry(self):
        before = settled(self.tmp.name)
        for code in (2, 125, 126, 127, 137, -9, CacheErrorFromRunner):
            self.runner.code = code() if code is CacheErrorFromRunner else code
            with self.subTest(code=code), self.assertRaises(PC.CacheError) as caught:
                self.service.restore('base')
            self.assertNotIsInstance(caught.exception, PC.Rejected)
            if code is not CacheErrorFromRunner:
                self.assertEqual(str(caught.exception), "base: the recipe's own check could not be run here (exit %s); "
                                                        "see logs/base-verify.log" % code)
            after = settled(self.tmp.name)
            after.pop('root/logs')
            self.assertEqual(after, before)
            self.assertIsNone(self.notes())
        self.make('base')
        self.service.push('base')
        self.assertEqual((self.registry.published, self.registry.digests[self.ref]), (1, self.digest))

    def test_check_is_not_run_when_switched_off_or_not_declared(self):
        for what, service, name in (('switched off', self.checked(verify=False), 'base'),
                                    ('no runner', self.checked(runner=None), 'base'),
                                    ('not declared', self.service, 'side')):
            if name == 'side':
                self.make('side')
                self.service.push('side')
                self.set_aside('side')
            del self.messages[:]
            with self.subTest(what=what):
                ref = service.restore(name)
                self.assertEqual(self.runner.calls, [])
                self.assertEqual(self.messages, ['provider-cache: restored %s from %s (9 entries verified)'
                                                 % (name, ref)])
            if name == 'base':
                self.set_aside('base')

    def test_command_line_and_environment_switch_the_check_off(self):
        with mock.patch.object(PC.sys, 'stderr', io.StringIO()):
            self.assertEqual(PC.main(['restore', '--no-verify', 'base'], service=self.service), 0)
        self.assertEqual(self.runner.calls, [])
        self.set_aside('base')
        fresh = self.checked()
        with mock.patch.object(PC.sys, 'stderr', io.StringIO()):
            self.assertEqual(PC.main(['restore', 'base'], service=fresh), 0)
        self.assertEqual(len(self.runner.calls), 1)
        self.runner.code = 1
        self.set_aside('base')
        errors = io.StringIO()
        with mock.patch.object(PC.sys, 'stderr', errors):
            self.assertEqual(PC.main(['restore', 'base'], service=self.checked()), 1)
        self.assertEqual(errors.getvalue(), "provider-cache: restore base failed: base: the recipe's own check "
                                            "rejects the restored output; see logs/base-verify.log\n")
        for environment, verify, timeout in (({}, True, 900), ({'TAKX_PROTO_CACHE_VERIFY': 'off'}, False, 900),
                                             ({'TAKX_PROTO_CACHE_VERIFY': ''}, True, 900),
                                             ({'TAKX_PROTO_CACHE_VERIFY': 'OFF'}, True, 900),
                                             ({'TAKX_PROTO_CACHE_VERIFY_TIMEOUT': '30'}, True, 30),
                                             ({'TAKX_PROTO_CACHE_VERIFY_TIMEOUT': ''}, True, 900)):
            service = PC.build_service(environment)
            with self.subTest(environment=environment):
                self.assertEqual((service.verify, service.runner.timeout), (verify, timeout))
                self.assertEqual((service.runner.wrapper, service.runner.docker),
                                 (str(HERE / 'run-in-toolchain.sh'), 'docker'))
        with mock.patch.object(PC.sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            PC.main(['push', '--no-verify', 'base'], service=self.service)

    def test_install_runs_the_check_between_the_rename_and_the_status(self):
        tree = self.service.tree
        source = Path(self.tmp.name) / 'unpacked'
        shutil.copytree(self.set_aside_copy(), source, symlinks=True)
        events = []

        def check():
            events.append((os.path.lexists(self.root / 'P/base'), os.path.lexists(source),
                           (self.root / 'status/base.json').exists()))
        tree.install(source, 'base', '{"status": "ok"}\n', check)
        self.assertEqual(events, [(True, False, False)])
        self.assertEqual((self.root / 'status/base.json').read_text(), '{"status": "ok"}\n')
        # A check that raises: the tree goes back where it came from, whatever was raised.
        for error in (PC.Rejected('no'), PC.CacheError('could not run'), OSError(5, 'io'), KeyboardInterrupt()):
            os.rename(self.root / 'P/base', source)
            os.unlink(self.root / 'status/base.json')

            def failing():
                raise error
            with self.subTest(error=error), self.assertRaises(type(error)):
                tree.install(source, 'base', '{"status": "ok"}\n', failing)
            self.assertTrue(source.is_dir())
            self.assertFalse(os.path.lexists(self.root / 'P/base'))
            self.assertEqual(sorted(os.listdir(self.root / 'status')), ['fetch.json', 'images.json', 'toolchain.json'])
            tree.install(source, 'base', '{"status": "ok"}\n')

    def set_aside_copy(self):
        """A tree like the one that was pushed (set aside in setUp)."""
        return next(path for path in Path(self.tmp.name).iterdir() if path.name.startswith('aside-base-'))


class CacheErrorFromRunner(PC.CacheError):
    def __init__(self):
        super().__init__('the check of base did not finish in 900 s')


RUNNER_WRAPPER = r"""#!/bin/bash
# Stands in for run-in-toolchain.sh: records its arguments, then does what the file next to it says.
dir="$(dirname "$0")"
{ printf 'CALL\n'; printf 'ARG %s\n' "$@"; } >> "$dir/wrapper-calls"
echo "to stdout"; echo "to stderr" >&2
if [[ -f "$dir/wrapper-behaviour" ]]; then source "$dir/wrapper-behaviour"; fi
exit 0
"""


class Runner(unittest.TestCase):
    """ToolchainRunner against a stand-in for the wrapper; no docker involved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / 'wrapper').write_text(RUNNER_WRAPPER)
        (self.dir / 'docker').write_text('#!/bin/bash\nprintf \'%s\\n\' "$*" >> "$(dirname "$0")/docker-calls"\n')
        for name in ('wrapper', 'docker'):
            os.chmod(self.dir / name, 0o755)
        self.log = self.dir / 'check.log'
        self.runner = PC.ToolchainRunner(self.dir / 'wrapper', 30, str(self.dir / 'docker'))

    def calls(self):
        text = (self.dir / 'wrapper-calls').read_text()
        return [[line[4:] for line in block.split('\n') if line.startswith('ARG ')]
                for block in text.split('CALL\n')[1:]]

    def test_defaults(self):
        runner = PC.ToolchainRunner('/x/run-in-toolchain.sh')
        self.assertEqual((runner.wrapper, runner.timeout, runner.docker), ('/x/run-in-toolchain.sh', 900, 'docker'))

    def test_command_goes_through_the_wrapper_and_its_output_into_the_log(self):
        self.log.write_text('earlier\n')
        code = self.runner.run('proj', ['A=1', 'B=two words'], ['python3', '-m', 'unittest', 'x y'], self.log)
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [['--name', 'takx-verify-proj-%d' % os.getpid(), '-e', 'A=1', '-e',
                                         'B=two words', '--', 'python3', '-m', 'unittest', 'x y']])
        self.assertEqual(self.log.read_text(), 'earlier\n\n===== provider-cache restore proj: python3 -m unittest '
                                               'x y\nto stdout\nto stderr\n')
        self.assertFalse((self.dir / 'docker-calls').exists())
        self.runner.run('proj', [], ['true'], self.log)
        self.assertEqual(self.calls()[1], ['--name', 'takx-verify-proj-%d' % os.getpid(), '--', 'true'])

    def test_exit_status_is_passed_on(self):
        for code in (1, 2, 125):
            (self.dir / 'wrapper-behaviour').write_text('exit %d\n' % code)
            self.assertEqual(self.runner.run('proj', [], ['x'], self.log), code)
        (self.dir / 'wrapper-behaviour').write_text('kill -9 $$\n')
        self.assertEqual(self.runner.run('proj', [], ['x'], self.log), -9)

    def test_standard_input_is_not_inherited(self):
        (self.dir / 'wrapper-behaviour').write_text('cat; echo "end of input"\n')
        self.assertEqual(self.runner.run('proj', [], ['x'], self.log), 0)
        self.assertTrue(self.log.read_text().endswith('to stderr\nend of input\n'))

    def test_check_that_does_not_finish_is_stopped_with_everything_it_started(self):
        # The wrapper and what it starts ignore every signal that can be ignored.
        (self.dir / 'wrapper-behaviour').write_text(
            'trap "" HUP INT QUIT USR1 USR2 TERM FPE ALRM PIPE\nsleep 300 & echo $! > "$dir/child"; wait\n')
        runner = PC.ToolchainRunner(self.dir / 'wrapper', 1, str(self.dir / 'docker'))
        outcome = []

        def run():
            try:
                runner.run('proj', [], ['x'], self.log)
            except PC.CacheError as error:
                outcome.append(str(error))
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(15)
        child = int((self.dir / 'child').read_text())
        if thread.is_alive():  # not stopped: do it here so that nothing outlives the test
            os.killpg(os.getpgid(child), 9)
            thread.join(15)
            self.fail('the check was not stopped')
        self.assertEqual(outcome, ['the check of proj did not finish in 1 s'])
        for _ in range(50):  # the kill is asynchronous
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            threading.Event().wait(0.1)
        else:
            self.fail('the process the wrapper started is still running')
        self.assertEqual((self.dir / 'docker-calls').read_text(), 'rm -f takx-verify-proj-%d\n' % os.getpid())

    def test_stopping_a_check_reports_the_timeout_whatever_the_clean_up_meets(self):
        (self.dir / 'wrapper-behaviour').write_text('sleep 300\n')
        (self.dir / 'docker').write_text('#!/bin/bash\nprintf \'%s\\n\' "$*" >> "$(dirname "$0")/docker-calls"\n'
                                         'echo "No such container" >&2\nexit 1\n')
        runner = PC.ToolchainRunner(self.dir / 'wrapper', 1, str(self.dir / 'docker'))
        with self.assertRaises(PC.CacheError) as caught:    # removing the container fails: still the timeout
            runner.run('proj', [], ['x'], self.log)
        self.assertEqual(str(caught.exception), 'the check of proj did not finish in 1 s')
        self.assertEqual((self.dir / 'docker-calls').read_text(), 'rm -f takx-verify-proj-%d\n' % os.getpid())
        # The process group is gone by the time it is killed: still the timeout.
        killpg = os.killpg

        def gone(group, signal):
            killpg(group, signal)
            raise ProcessLookupError(3, 'No such process')
        with mock.patch.object(PC.os, 'killpg', gone), self.assertRaises(PC.CacheError) as caught:
            runner.run('proj', [], ['x'], self.log)
        self.assertEqual(str(caught.exception), 'the check of proj did not finish in 1 s')

    def test_wrapper_or_log_that_cannot_be_used_is_a_refusal(self):
        missing = PC.ToolchainRunner(self.dir / 'no-wrapper', 30)
        with self.assertRaisesRegex(PC.CacheError, '^the check of proj could not be started: .*No such file'):
            missing.run('proj', [], ['x'], self.log)
        with self.assertRaisesRegex(PC.CacheError, '^the check of proj could not be started: '):
            self.runner.run('proj', [], ['x'], self.dir / 'no-directory' / 'check.log')


class FakeLaneCheck:
    """What `restore --verify-with` sets on the service: records the call, answers with a code."""

    def __init__(self, root, code=0, events=None, command=('lane-check', 'side')):
        self.root, self.code, self.calls, self.command = Path(root), code, [], list(command)
        self.events = [] if events is None else events

    def run(self, name, log):
        self.calls.append((name, str(log)))
        self.events.append(('lane', os.path.isdir(self.root / 'P' / name),
                            (self.root / 'status' / (name + '.json')).exists(), (self.root / 'logs').is_dir()))
        if isinstance(self.code, Exception):
            raise self.code
        return self.code


class OrderedRunner(FakeRunner):
    def __init__(self, root, events, code=0):
        super().__init__(root, code)
        self.events = events

    def run(self, name, env, argv, log):
        self.events.append(('recipe',))
        return super().run(name, env, argv, log)


class LaneSuppliedCheck(ServiceCase):
    """A check the lane brings itself: `restore NAME --verify-with COMMAND...`."""

    def document(self):
        value = manifest()
        value['providers']['base']['verify'] = {'env': [], 'argv': ['python3', '{repo}/takx/one/check.py']}
        return value

    def setUp(self):
        super().setUp()
        self.events = []
        self.runner = OrderedRunner(self.root, self.events)
        self.lane = FakeLaneCheck(self.root, events=self.events)
        self.service = self.checked()
        for name in ('base', 'side'):
            self.make(name)
            self.service.push(name)
            self.set_aside(name)
        self.ref, self.side = self.service.ref('base'), self.service.ref('side')
        self.digest = self.registry.digests[self.ref]
        del self.messages[:]

    def checked(self, lane=True, **options):
        options.setdefault('runner', self.runner)
        service = PC.ProviderCache(self.document(), PC.WorkTree(self.root, self.repo), self.registry,
                                   'reg.example/cache', '', self.messages.append, **options)
        self.assertIsNone(service.lane_check)   # nothing runs a lane's command unless the lane gave one
        service.lane_check = self.lane if lane else None
        return service

    def notes(self):
        path = self.root / 'cache-state/refused.json'
        return json.loads(path.read_text()) if path.exists() else None

    def test_lane_check_runs_after_the_recipes_on_the_tree_in_place_before_the_status_says_ok(self):
        self.assertEqual(self.service.restore('base'), self.ref)
        self.assertEqual(self.events, [('recipe',), ('lane', True, False, True)])
        self.assertEqual(self.lane.calls, [('base', '%s/logs/base-verify.log' % self.root)])
        self.assertEqual(self.runner.calls[0][3], '%s/logs/base-verify.log' % self.root)
        self.assertEqual(self.service.tree.status_of('base'), 'ok')
        self.assertEqual(self.messages, ["provider-cache: restored base from %s (9 entries verified, the recipe's "
                                         "own check passed, the lane's own check passed)" % self.ref])
        self.assertIsNone(self.notes())

    def test_lane_check_is_the_only_check_of_a_provider_that_declares_none(self):
        self.assertEqual(self.service.restore('side'), self.side)
        self.assertEqual(self.events, [('lane', True, False, True)])
        self.assertEqual(self.lane.calls, [('side', '%s/logs/side-verify.log' % self.root)])
        self.assertEqual(self.messages, ["provider-cache: restored side from %s (9 entries verified, the lane's "
                                         "own check passed)" % self.side])

    def test_tree_the_lane_rejects_is_taken_out_and_the_entry_is_replaced_after_the_rebuild(self):
        before = settled(self.tmp.name)
        self.lane.code = 1
        with self.assertRaises(PC.Rejected) as caught:
            self.service.restore('base')
        self.assertEqual(str(caught.exception), "base: the lane's own check rejects the restored output; see "
                                                "logs/base-verify.log")
        self.assertEqual(self.events, [('recipe',), ('lane', True, False, True)])
        after = settled(self.tmp.name)
        after.pop('root/logs')
        self.assertEqual(after, before)           # no tree, no status, no scratch
        note = self.notes()[self.ref]
        self.assertEqual((note['kind'], note['digest'], note['reason']), ('check', self.digest, str(caught.exception)))
        self.assertEqual(note['check'], PC.check_identity('lane', self.lane.command))
        published = self.registry.published
        self.make('base')
        self.lane.code = 0
        self.service.push('base')
        self.assertEqual(self.registry.published, published + 1)
        self.assertNotEqual(self.registry.digests[self.ref], self.digest)
        self.assertEqual(self.notes(), {})

    def test_lane_check_is_not_asked_about_a_tree_the_recipes_check_rejects(self):
        self.runner.code = 1
        with self.assertRaises(PC.Rejected) as caught:
            self.service.restore('base')
        self.assertEqual(str(caught.exception), "base: the recipe's own check rejects the restored output; see "
                                                "logs/base-verify.log")
        self.assertEqual(self.events, [('recipe',)])
        self.assertEqual(self.lane.calls, [])

    def test_lane_check_that_could_not_run_is_no_judgement_of_the_entry(self):
        before = settled(self.tmp.name)
        for code in (2, 125, 127, -9, CacheErrorFromRunner):
            self.lane.code = code() if code is CacheErrorFromRunner else code
            with self.subTest(code=code), self.assertRaises(PC.CacheError) as caught:
                self.service.restore('side')
            self.assertNotIsInstance(caught.exception, PC.Rejected)
            if code is not CacheErrorFromRunner:
                self.assertEqual(str(caught.exception), "side: the lane's own check could not be run here (exit %s); "
                                                        "see logs/side-verify.log" % code)
            after = settled(self.tmp.name)
            after.pop('root/logs')
            self.assertEqual(after, before)
            self.assertIsNone(self.notes())

    def test_switching_the_checks_off_switches_the_lanes_off_too(self):
        for what, service, expected in (('off', self.checked(verify=False), []),
                                        ('no lane command', self.checked(lane=False), [('recipe',)]),
                                        ('no runner', self.checked(runner=None), [('lane', True, False, True)])):
            del self.events[:], self.messages[:]
            with self.subTest(what=what):
                service.restore('base')
                self.assertEqual(self.events, expected)
            self.set_aside('base')

    # -- the command line ----------------------------------------------------
    def cli(self, *argv, service=None):
        errors = io.StringIO()
        service = service or self.checked(lane=False)
        with mock.patch.object(PC.sys, 'stderr', errors):
            code = PC.main(list(argv), service=service, out=io.StringIO())
        return code, errors.getvalue(), service

    def test_everything_after_verify_with_is_the_command_word_for_word(self):
        words = ['/bin/sh', '-c', 'exit 0', '-e', 'A=1', '--', 'restore', '--no-verify', '--verify-with', '', 'x y']
        code, text, service = self.cli('restore', 'side', '--verify-with', *words)
        self.assertEqual(code, 0, text)
        self.assertIsInstance(service.lane_check, PC.LaneCheck)
        self.assertEqual((service.lane_check.command, service.lane_check.timeout), (words, 900))
        self.assertTrue(service.verify)
        self.assertEqual(self.service.tree.status_of('side'), 'ok')
        self.assertIn("the lane's own check passed", self.messages[-1])
        # The time allowed is the one of the recipe's check.
        self.runner.timeout = 41
        self.set_aside('side')
        code, text, service = self.cli('restore', 'side', '--verify-with', 'true')
        self.assertEqual((code, service.lane_check.timeout), (0, 41))
        self.set_aside('side')
        code, text, service = self.cli('restore', 'side', '--verify-with', 'true', service=self.checked(
            lane=False, runner=None))
        self.assertEqual((code, service.lane_check.timeout), (0, 900))

    def test_command_sees_the_tree_in_place_and_decides(self):
        program = 'test -s "$0/P/side/complete.json" && test ! -e "$0/status/side.json" && exit "$1"'
        code, text, service = self.cli('restore', 'side', '--verify-with', '/bin/sh', '-c', program,
                                       str(self.root), '0')
        self.assertEqual((code, text), (0, ''))
        self.assertEqual(self.service.tree.status_of('side'), 'ok')
        self.assertIn("\n===== provider-cache restore side: the lane's own check (/bin/sh, 5 words)\n",
                      (self.root / 'logs/side-verify.log').read_text())
        self.set_aside('side')
        code, text, service = self.cli('restore', 'side', '--verify-with', '/bin/sh', '-c', program,
                                       str(self.root), '1')
        self.assertEqual((code, text), (1, "provider-cache: restore side failed: side: the lane's own check rejects "
                                           "the restored output; see logs/side-verify.log\n"))
        self.assertFalse(os.path.lexists(self.root / 'P/side'))
        self.assertEqual(self.notes()[self.side]['kind'], 'check')
        code, text, service = self.cli('restore', 'side', '--verify-with', '/bin/sh', '-c', program,
                                       str(self.root), '7')
        self.assertEqual((code, text), (1, "provider-cache: restore side failed: side: the lane's own check could "
                                           "not be run here (exit 7); see logs/side-verify.log\n"))
        code, text, service = self.cli('restore', 'side', '--verify-with', str(self.root / 'no-such-command'))
        self.assertEqual(code, 1)
        self.assertIn('restore side failed: the check of side could not be started: ', text)
        self.assertFalse(os.path.lexists(self.root / 'P/side'))

    def test_no_verify_wins_over_a_command(self):
        code, text, service = self.cli('restore', '--no-verify', 'side', '--verify-with', 'false')
        self.assertEqual((code, text), (0, ''))
        self.assertEqual((service.lane_check.command, service.verify), (['false'], False))
        self.assertEqual(self.service.tree.status_of('side'), 'ok')
        self.assertFalse((self.root / 'logs').exists())

    def test_verify_with_needs_restore_or_push_one_name_and_a_command(self):
        for argv in (['push', '--verify-with', 'true'], ['restore', '--verify-with', 'true'],
                     ['push', 'base', 'side', '--verify-with', 'true'], ['push', 'side', '--verify-with'],
                     ['restore', 'base', 'side', '--verify-with', 'true'], ['restore', 'side', '--verify-with'],
                     ['key', 'side', '--verify-with', 'true'], ['migrate', 'side', '--verify-with', 'true'],
                     ['status', '--verify-with', 'true'], ['toolchain', '--verify-with', 'true'],
                     ['status', '--remote', '--verify-with', 'true']):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as caught:
                self.cli(*argv)
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(os.path.lexists(self.root / 'P/side'))
            self.assertEqual(self.lane.calls, [])


class LaneCheckAdapter(unittest.TestCase):
    """LaneCheck: the lane's command on the host, word for word."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / 'command').write_text(RUNNER_WRAPPER)
        os.chmod(self.dir / 'command', 0o755)
        self.log = self.dir / 'check.log'
        self.command = str(self.dir / 'command')

    def calls(self):
        text = (self.dir / 'wrapper-calls').read_text()
        return [[line[4:] for line in block.split('\n') if line.startswith('ARG ')]
                for block in text.split('CALL\n')[1:]]

    def test_defaults_and_words(self):
        check = PC.LaneCheck((Path('/x/run'), '-e', 'A=1'))
        self.assertEqual((check.command, check.timeout), (['/x/run', '-e', 'A=1'], 900))

    def test_command_runs_as_given_and_its_output_goes_into_the_log(self):
        self.log.write_text('earlier\n')
        words = [self.command, '-e', 'A=two words', '--', 'python3', '-c', 'pass', '', 'sqlcipher']
        self.assertEqual(PC.LaneCheck(words, 30).run('sqlcipher', self.log), 0)
        self.assertEqual(self.calls(), [words[1:]])
        self.assertEqual(self.log.read_text(), "earlier\n\n===== provider-cache restore sqlcipher: the lane's own "
                                               "check (%s, 9 words)\nto stdout\nto stderr\n" % self.command)

    def test_exit_status_is_passed_on_and_input_is_not_inherited(self):
        check = PC.LaneCheck([self.command], 30)
        for code in (1, 2, 125):
            (self.dir / 'wrapper-behaviour').write_text('exit %d\n' % code)
            self.assertEqual(check.run('kml', self.log), code)
        (self.dir / 'wrapper-behaviour').write_text('kill -9 $$\n')
        self.assertEqual(check.run('kml', self.log), -9)
        (self.dir / 'wrapper-behaviour').write_text('cat; echo "end of input"\n')
        self.assertEqual(check.run('kml', self.log), 0)
        self.assertTrue(self.log.read_text().endswith('to stderr\nend of input\n'))

    def test_command_that_does_not_finish_is_stopped_with_everything_it_started(self):
        (self.dir / 'wrapper-behaviour').write_text(
            'trap "" HUP INT QUIT USR1 USR2 TERM FPE ALRM PIPE\nsleep 300 & echo $! > "$dir/child"; wait\n')
        check = PC.LaneCheck([self.command], 1)
        outcome = []

        def run():
            try:
                check.run('kml', self.log)
            except PC.CacheError as error:
                outcome.append(str(error))
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(15)
        child = int((self.dir / 'child').read_text())
        if thread.is_alive():  # not stopped: do it here so that nothing outlives the test
            os.killpg(os.getpgid(child), 9)
            thread.join(15)
            self.fail('the check was not stopped')
        self.assertEqual(outcome, ['the check of kml did not finish in 1 s'])
        for _ in range(50):  # the kill is asynchronous
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            threading.Event().wait(0.1)
        else:
            self.fail('the process the command started is still running')

    def test_command_or_log_that_cannot_be_used_is_a_refusal(self):
        with self.assertRaisesRegex(PC.CacheError, '^the check of kml could not be started: .*No such file'):
            PC.LaneCheck([str(self.dir / 'no-command')], 30).run('kml', self.log)
        with self.assertRaisesRegex(PC.CacheError, '^the check of kml could not be started: '):
            PC.LaneCheck([self.command], 30).run('kml', self.dir / 'no-directory' / 'check.log')


class Migrate(ServiceCase):
    """Bundles saved under schema-1 keys made addressable under the current keys."""

    def setUp(self):
        super().setUp()
        self.top = self.make('base')
        self.good = snapshot(self.top)
        self.old_ref = self.legacy_push('base')
        del self.registry.calls[:]
        del self.messages[:]

    def legacy_push(self, name, service=None):
        """Store what schema 1 pushed: the bundle under the key that names the toolchain image."""
        service = service or self.service
        entry = manifest()['providers'][name]
        old_key, old_document = service.legacy_keys([name])[name]
        context = Path(tempfile.mkdtemp(dir=self.tmp.name))
        service._local, kept = (ATTRIBUTE, TOOLCHAIN_ID), service._local
        entries, tar_sha = service._bundle(name, old_key, entry, context, service._markers(name),
                                           service.tree.status_bytes(name),
                                           service.tree.marker_sha256(name, entry['marker']))
        service._local = kept
        meta = json.loads((context / 'meta.json').read_text())
        del meta['toolchain']  # schema 1 recorded nothing about the toolchain beside the key
        meta.update(schema=1, key_inputs=old_document)
        (context / 'meta.json').write_text(json.dumps(meta, sort_keys=True, indent=1) + '\n')
        files = [name + '.tar', *PC.IMAGE_FILES]
        ref = PC.image_ref('reg.example/cache', name, old_key)
        self.registry.store(ref, service._labels(name, old_key, tar_sha),
                            {item: (context / item).read_bytes() for item in files})
        return ref

    def test_schema_1_key_is_the_pinned_derivation_from_the_local_inputs(self):
        keys = self.service.legacy_keys()
        self.assertEqual(sorted(keys), ['base', 'side', 'top'])
        document = keys['top'][1]
        self.assertEqual((document['schema'], document['toolchain'], document['prerequisites']),
                         (1, TOOLCHAIN_ID, {'base': keys['base'][0]}))
        self.assertNotIn('arch', document)
        current = self.service.explain('top')
        self.assertEqual({field for field in document if document[field] != current.get(field)},
                         {'schema', 'toolchain', 'invocation_sha256', 'prerequisites'})
        self.assertEqual(self.old_ref, 'reg.example/cache/base:' + keys['base'][0])
        # The image id comes from the image record unless it is given.
        given = self.new_service(legacy='sha256:' + '3' * 64).legacy_keys(['base'])['base']
        self.assertEqual(given[1]['toolchain'], 'sha256:' + '3' * 64)
        self.assertNotEqual(given[0], keys['base'][0])
        os.unlink(self.root / 'status/images.json')
        with self.assertRaisesRegex(PC.CacheError, r'^status/images\.json cannot be read'):
            self.new_service().legacy_keys(['base'])
        self.assertEqual(self.new_service(legacy=TOOLCHAIN_ID).legacy_keys(['base'])['base'], keys['base'])

    def test_bundle_becomes_addressable_under_the_current_key_without_its_archive_again(self):
        old_labels, old_content = copy.deepcopy(self.registry.images[self.old_ref])
        old_digest = self.registry.digests[self.old_ref]
        key = self.service.key('base')
        ref = self.service.migrate('base')
        self.assertEqual(ref, 'reg.example/cache/base:' + key)
        self.assertEqual(self.registry.calls, [
            ('image_id', PC.TOOLCHAIN), ('probe', ref), ('probe', self.old_ref), ('fetch', self.old_ref),
            ('adopt', self.old_ref, ref), ('forget', self.old_ref), ('probe', ref)])
        self.assertEqual(self.registry.kept, [])
        self.assertEqual(self.registry.images[self.old_ref], (old_labels, old_content))   # the old tag is not touched
        labels, content = self.registry.images[ref]
        self.assertEqual(labels, dict(old_labels, **{'dev.uds.takx.key': key}))
        self.assertEqual({name for name in content if content[name] != old_content[name]}, {'meta.json'})
        self.assertEqual(sorted(content), sorted(old_content))
        meta, old_meta = json.loads(content['meta.json']), json.loads(old_content['meta.json'])
        self.assertEqual(meta, dict(old_meta, schema=2, key=key, key_inputs=self.service.explain('base'), adopted_from={
            'schema': 1, 'key': old_meta['key'], 'ref': self.old_ref, 'digest': old_digest,
            'toolchain_image_id': TOOLCHAIN_ID}, toolchain=dict(ATTRIBUTE, image_id=TOOLCHAIN_ID)))
        # Written like the meta.json of a pushed bundle: sorted, one space of indent, a final newline.
        self.assertEqual(content['meta.json'], (json.dumps(meta, sort_keys=True, indent=1) + '\n').encode())
        tar_sha = hashlib.sha256(content['base.tar']).hexdigest()
        self.assertEqual(self.messages, ['provider-cache: base is now also %s (was %s under the schema-1 key, 9 '
                                         'entries verified, tar sha256 %s; records no package list, but was built '
                                         'by the toolchain image that is here)' % (ref, self.old_ref, tar_sha)])
        self.assertEqual(self.service.tree.pushed(), {('base', key): tar_sha})
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(snapshot(self.top), self.good)
        # It restores like any bundle pushed under the current key.
        self.set_aside('base')
        self.assertEqual(self.service.restore('base'), ref)
        self.assertEqual(snapshot(self.top), self.good)
        status = json.loads((self.root / 'status/base.json').read_text())
        self.assertEqual((status['restored_from'], status['cache_key']), (ref, key))
        # Once is enough.
        with self.assertRaisesRegex(PC.Skip, r'^already cached under the current key \(%s\)$' % re.escape(ref)):
            self.service.migrate('base')

    def test_downstream_bundle_is_found_under_the_key_its_old_prerequisite_key_gave(self):
        self.make('top')
        old_top = self.legacy_push('top')
        self.assertEqual(json.loads(self.registry.images[old_top][1]['meta.json'])['key_inputs']['prerequisites'],
                         {'base': self.old_ref.rsplit(':', 1)[1]})
        self.service.migrate('base')
        ref = self.service.migrate('top')
        meta = json.loads(self.registry.images[ref][1]['meta.json'])
        self.assertEqual(meta['key_inputs']['prerequisites'], {'base': self.service.key('base')})
        good = snapshot(self.root / 'P/top')
        self.set_aside('top')
        self.service.restore('top')
        self.assertEqual(snapshot(self.root / 'P/top'), good)

    def test_nothing_to_do_is_a_clean_skip(self):
        other = self.new_service(legacy='sha256:' + '4' * 64)
        absent = PC.image_ref('reg.example/cache', 'base', other.legacy_keys(['base'])['base'][0])
        cases = (
            ('nothing under the old key', other, r'^nothing cached under the schema-1 key \(%s\)$' % re.escape(absent)),
            ('cache off', self.new_service(prefix='off'), '^cache is off$'),
        )
        for what, service, message in cases:
            with self.subTest(what=what), self.assertRaisesRegex(PC.Skip, message):
                service.migrate('base')
        self.registry.docker = False
        with self.assertRaisesRegex(PC.Skip, '^docker is not available$'):
            self.service.migrate('base')
        self.registry.docker, self.registry.logged_in = True, False
        with self.assertRaisesRegex(PC.Skip, '^docker is not logged in to the cache registry$'):
            self.service.migrate('base')
        self.registry.logged_in = True
        self.make('top')
        self.legacy_push('top')
        self.set_aside('base')
        with self.assertRaisesRegex(PC.Skip, '^a prerequisite provider is not ok here$'):
            self.service.migrate('top')
        self.assertEqual((self.registry.published, self.registry.fetched), (2, []))
        with self.assertRaisesRegex(PC.CacheError, '^unknown provider ghost$'):
            self.service.migrate('ghost')

    def test_registry_that_cannot_be_asked_is_a_refusal(self):
        self.registry.state = 'error'
        with self.assertRaisesRegex(PC.CacheError, '^base: the registry could not be queried$'):
            self.service.migrate('base')
        self.registry.state = None
        answers = iter(['absent', 'error'])
        with mock.patch.object(self.registry, 'probe', lambda ref: next(answers)), self.assertRaisesRegex(
                PC.CacheError, '^base: the registry could not be queried for %s$' % re.escape(self.old_ref)):
            self.service.migrate('base')
        answers = iter(['absent', 'unauthorized'])
        with mock.patch.object(self.registry, 'probe', lambda ref: next(answers)), self.assertRaises(PC.CacheError):
            self.service.migrate('base')
        for last in ('absent', 'unauthorized', 'error'):
            answers = iter(['absent', 'present', last])
            self.registry.images.pop(self.service.ref('base'), None)
            with self.subTest(last=last), mock.patch.object(self.registry, 'probe', lambda ref: next(answers)), \
                    self.assertRaisesRegex(PC.CacheError, '^base: adopted, but the registry does not show '):
                self.service.migrate('base')
        self.assertEqual(self.service.tree.pushed(), {})
        self.assertEqual((self.registry.kept, self.leftovers()), ([], []))

    def test_scratch_directory_is_removed_whatever_happens_to_it(self):
        real, calls = shutil.rmtree, []

        def recording(path, ignore_errors=False):
            calls.append((Path(path).name.split('.')[1], ignore_errors))
            real(path, ignore_errors=True)
        with mock.patch.object(PC.shutil, 'rmtree', recording):
            self.service.migrate('base')
            self.set_aside('base')
            self.service.restore('base')
            self.registry.images.clear()
            self.service.push('base')
        self.assertEqual(calls, [('migrate-base', True), ('restore-base', True), ('push-base', True)])

    def test_bundle_that_does_not_verify_is_not_adopted(self):
        labels, content = self.registry.images[self.old_ref]
        stored = dict(labels), dict(content)

        def meta(**fields):
            content['meta.json'] = json.dumps(dict(json.loads(stored[1]['meta.json']), **fields)).encode()

        def flip():
            data = bytearray(content['base.tar'])
            data[len(data) // 2] ^= 1
            content['base.tar'] = bytes(data)
        inputs = json.loads(stored[1]['meta.json'])['key_inputs']
        cases = (
            ('archive bytes', flip, '^base: archive digest differs from the recorded one$'),
            ('label of the current key', lambda: labels.update({'dev.uds.takx.key': self.service.key('base')}),
             '^base: image is labelled with another key$'),
            ('bundle of the current schema', lambda: meta(schema=2), '^base: unsupported bundle schema$'),
            ('other recorded inputs', lambda: meta(key_inputs=dict(inputs, repo='/elsewhere')),
             '^base: the bundle under %s records other key inputs than the ones derived here$'
             % re.escape(self.old_ref)),
            ('no recorded inputs', lambda: meta(key_inputs=None), 'records other key inputs'),
            ('inputs of the current schema', lambda: meta(key_inputs=self.service.explain('base')),
             'records other key inputs'),
            ('other prerequisites', lambda: meta(prerequisite_markers={'side': '0' * 64}),
             'other prerequisite outputs'),
            ('marker digest', lambda: meta(marker_sha256='0' * 64), 'completion marker differs'),
            ('a file the image lacks', lambda: content.pop('files.json'), '^the image holds no /files.json$'),
        )
        before = settled(self.tmp.name)
        for what, change, message in cases:
            labels.clear()
            labels.update(stored[0])
            content.clear()
            content.update(stored[1])
            change()
            with self.subTest(what=what), self.assertRaisesRegex(PC.CacheError, message):
                self.service.migrate('base')
            self.assertEqual(self.registry.kept, [], what)        # the pulled image does not stay
            self.assertEqual(settled(self.tmp.name), before, what)
        self.assertEqual((self.registry.published, sorted(self.registry.images)), (1, [self.old_ref]))
        self.assertFalse((self.root / 'cache-state').exists())  # migrate judges nothing for a later push

    def test_failure_while_adopting_leaves_nothing_behind(self):
        def failing(*arguments):
            raise PC.CacheError('docker push failed: denied')
        before = settled(self.tmp.name)
        with mock.patch.object(self.registry, 'adopt', failing), self.assertRaisesRegex(
                PC.CacheError, '^docker push failed: denied$'):
            self.service.migrate('base')
        self.assertEqual((self.registry.kept, sorted(self.registry.images)), ([], [self.old_ref]))
        self.assertEqual(settled(self.tmp.name), before)
        # A prerequisite that changes while the old bundle is checked stops it, too.
        self.make('top')
        self.legacy_push('top')
        real = self.service._markers
        answers = iter([real('top'), {'base': '0' * 64}])
        with mock.patch.object(self.service, '_markers', lambda name: next(answers)), self.assertRaisesRegex(
                PC.CacheError, '^top: a prerequisite changed during the migration$'):
            self.service.migrate('top')
        self.assertEqual((self.registry.kept, self.leftovers()), ([], []))

    def test_toolchain_must_be_the_recorded_one(self):
        self.registry.layer = 'sha256:' + 'c' * 64
        self.registry.toolchain = 'sha256:' + '7' * 64
        with self.assertRaisesRegex(PC.CacheError, 'has toolchain fingerprint'):
            self.service.migrate('base')
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)])

    def test_command_line(self):
        errors = io.StringIO()
        with mock.patch.object(PC.sys, 'stderr', errors):
            self.assertEqual(PC.main(['migrate', 'base', 'side'], service=self.service), 3)
        self.assertRegex(errors.getvalue(), r'^provider-cache: migrate side skipped: nothing cached under the '
                                            r'schema-1 key \(reg\.example/cache/side:[0-9a-f]{64}\)\n$')
        self.assertEqual(self.registry.published, 2)
        errors = io.StringIO()
        with mock.patch.object(PC.sys, 'stderr', errors):
            self.assertEqual(PC.main(['migrate'], service=self.new_service()), 3)
        self.assertEqual(len(errors.getvalue().splitlines()), 3)
        self.assertIn('migrate base skipped: already cached under the current key', errors.getvalue())
        # --from-toolchain-id names the image the old keys were derived from.
        self.registry.images.clear()
        self.write_json('status/images.json', {'status': 'ok', 'image_ids': {PC.TOOLCHAIN: 'sha256:' + '5' * 64}})
        self.legacy_push('base', self.new_service(legacy=TOOLCHAIN_ID))
        with mock.patch.object(PC.sys, 'stderr', io.StringIO()):
            self.assertEqual(PC.main(['migrate', 'base'], service=self.new_service()), 3)
            self.assertEqual(PC.main(['migrate', '--from-toolchain-id', TOOLCHAIN_ID, 'base'],
                                     service=self.new_service()), 0)
        self.registry.images[self.old_ref][1]['tar.sha256'] = b'0' * 64
        del self.registry.images[self.service.ref('base')]
        errors = io.StringIO()
        with mock.patch.object(PC.sys, 'stderr', errors):
            self.assertEqual(PC.main(['migrate', '--from-toolchain-id', TOOLCHAIN_ID, 'base'],
                                     service=self.new_service()), 1)
        self.assertEqual(errors.getvalue(), 'provider-cache: migrate base failed: base: archive digest differs from '
                                            'the recorded one\n')
        service = PC.build_service({'TAKX_PROTO_TOOLCHAIN_ID': 'sha256:' + '6' * 64})
        self.assertEqual(service.legacy_id, 'sha256:' + '6' * 64)


RUN_STUB = r"""#!/bin/bash
# Stands in for run-in-toolchain.sh: records the call, "builds" by creating the outputs.
printf '%s\n' "$*" >> "$STUB_LOG.run"
out="" prev="" detached=0
for arg in "$@"; do
  [[ "$prev" == --output ]] && out="$arg"
  [[ "$arg" == -d ]] && detached=1
  prev="$arg"
done
[[ -n "$out" ]] || out="${STUB_OUTPUT_DIR:-}"
case "$*" in
  *unittest*) exit 0;;
  *" -c "*) echo "check passed"; exit 0;;
esac
rc=1
if [[ "${STUB_BUILD:-ok}" == ok ]]; then
  rc=0
  for item in $STUB_OUTPUTS; do mkdir -p "$(dirname "$out/$item")"; echo built > "$out/$item"; done
fi
echo "$rc" > "$STUB_LOG.rc"
((detached)) && exit 0
exit "$rc"
"""
DOCKER_STUB = r"""#!/bin/bash
if [[ "$1" == wait ]]; then cat "$STUB_LOG.rc"; fi
exit 0
"""
CACHE_STUB = r"""
import json, os, sys
command, name = sys.argv[1:3]
with open(os.environ['STUB_LOG'] + '.cache', 'a') as stream:
    stream.write(command + ' ' + name + '\n')
root = os.environ['TAKX_PROTO_ROOT']
if command == 'restore':
    if os.environ.get('STUB_RESTORE') != 'hit':
        sys.exit(3)
    os.makedirs(os.path.join(root, 'P', name))
    with open(os.path.join(root, 'P', name, os.environ['STUB_MARKER']), 'w') as stream:
        stream.write('restored\n')
    if '--verify-with' in sys.argv and os.environ.get('STUB_VERIFY') == 'run':
        import subprocess
        words = sys.argv[sys.argv.index('--verify-with') + 1:]
        with open(os.environ['STUB_LOG'] + '.verify', 'a') as stream:
            stream.write(json.dumps(words) + '\n')
        if subprocess.call(words, stdout=subprocess.DEVNULL) != 0:
            sys.exit(1)
    with open(os.path.join(root, 'status', name + '.json'), 'w') as stream:
        json.dump({'stage': name, 'status': 'ok', 'restored_from': 'ref'}, stream)
    sys.exit(0)
sys.exit(int(os.environ.get('STUB_PUSH', '0')))
"""


class LaneHooks(unittest.TestCase):
    """The real lane scripts, run in a copy of the tree with stubbed tools."""

    CASES = {  # lane -> (step, provider, marker, outputs the stub build creates)
        'lane1.sh': ('sqlcipher', 'sqlcipher', 'complete.json', 'complete.json'),
        'lane2.sh': ('proj', 'proj', 'complete.json', 'complete.json install/lib/libproj.a'),
        'lane3.sh': ('las', 'las', 'complete.json', 'complete.json'),
        'lane4.sh': ('consumers57', 'consumers57', 'staging-receipt.json',
                     'staging-receipt.json Platform/x Client/x'),
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name).resolve()
        self.root, self.log = base / 'root', base / 'log'
        proto = base / 'repo/takx/desktop-prototype'
        (proto / 'stages').mkdir(parents=True)
        self.bin = base / 'bin'
        self.bin.mkdir()
        for lane in self.CASES:
            (proto / 'stages' / lane).write_text((HERE / 'stages' / lane).read_text())
            os.chmod(proto / 'stages' / lane, 0o755)
        for path, text in ((proto / 'run-in-toolchain.sh', RUN_STUB), (self.bin / 'docker', DOCKER_STUB)):
            path.write_text(text)
            os.chmod(path, 0o755)
        (proto / 'provider_cache.py').write_text(CACHE_STUB)
        for recipe in ('native-proj', 'native-las'):
            (base / 'repo/takx' / recipe).mkdir()
            (base / 'repo/takx' / recipe / 'build.py').write_text('')
            (base / 'repo/takx' / recipe / 'inputs.lock.json').write_text('{}')
        self.stages = proto / 'stages'

    def lane(self, lane, **environment):
        step, provider, marker, outputs = self.CASES[lane]
        env = {'PATH': '%s:%s' % (self.bin, os.environ.get('PATH', '/usr/bin:/bin')),
               'TAKX_PROTO_ROOT': str(self.root), 'STUB_LOG': str(self.log), 'STUB_MARKER': marker,
               'STUB_OUTPUTS': outputs, 'STUB_OUTPUT_DIR': str(self.root / 'P' / provider), 'HOME': self.tmp.name}
        env.update(environment)
        done = subprocess.run(['/bin/bash', str(self.stages / lane), step], env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        return done.returncode, done.stdout.decode()

    def calls(self, kind):
        path = Path(str(self.log) + '.' + kind)
        return path.read_text().splitlines() if path.exists() else []

    def builds(self):
        return [line for line in self.calls('run') if 'unittest' not in line and ' -c ' not in line]

    def state(self, provider):
        try:
            return json.loads((self.root / 'status' / (provider + '.json')).read_text())
        except OSError:
            return {}

    def test_cached_provider_is_restored_and_not_built(self):
        for lane, (step, provider, marker, outputs) in self.CASES.items():
            with self.subTest(lane=lane):
                code, text = self.lane(lane, STUB_RESTORE='hit')
                self.assertEqual(code, 0, text)
                self.assertIn('restored from the provider cache', text)
                self.assertEqual([c for c in self.calls('cache') if c.endswith(' ' + provider)],
                                 ['restore ' + provider])
                self.assertEqual(self.builds(), [])
                self.assertEqual(self.state(provider).get('restored_from'), 'ref')
                # The next run finds it ok locally and does not even ask the cache.
                code, text = self.lane(lane, STUB_RESTORE='hit')
                self.assertEqual(code, 0, text)
                self.assertEqual([c for c in self.calls('cache') if c.endswith(' ' + provider)],
                                 ['restore ' + provider])

    def test_lane1_gives_the_cache_the_receipt_check_it_runs_after_a_build(self):
        code, text = self.lane('lane1.sh', STUB_RESTORE='hit', STUB_VERIFY='run')
        self.assertEqual(code, 0, text)
        self.assertIn('sqlcipher restored from the provider cache', text)
        words, = [json.loads(line) for line in self.calls('verify')]
        self.assertEqual(os.path.realpath(words[0]), str(self.stages.parent / 'run-in-toolchain.sh'))
        self.assertEqual(words[1:8], ['-e', 'PYTHONDONTWRITEBYTECODE=1', '--', '/usr/bin/python3', '-I', '-B', '-c'])
        self.assertIn("sys.exit('lane1 check ' + name + ': ' + message)", words[8])
        self.assertEqual(words[9:], ['sqlcipher', str(self.root / 'P')])
        # The cache ran those words once; a build in a new work root runs the same words as its own check.
        joined = ' '.join(words[1:]) + '\n'
        self.assertEqual(Path(str(self.log) + '.run').read_text().count(joined), 1)
        self.assertEqual(self.builds(), [line for line in self.builds() if 'takx-sqlcipher' not in line])
        os.rename(self.root, str(self.root) + '-restored')
        code, text = self.lane('lane1.sh')
        self.assertEqual(code, 0, text)
        self.assertEqual(len([b for b in self.builds() if 'takx-sqlcipher' in b]), 1)
        self.assertEqual(Path(str(self.log) + '.run').read_text().count(joined), 2)
        # Every step of lane1 restores through the same function.
        lane1 = (HERE / 'stages/lane1.sh').read_text()
        self.assertEqual(lane1.count('restore "$1" --verify-with "${check_command[@]}" "$1" "${P}"'), 1)
        self.assertEqual(lane1.count('push "$1" --verify-with "${check_command[@]}" "$1" "${P}"'), 1)
        self.assertEqual(lane1.count('python3 -I "${cache}" restore'), 1)
        self.assertIn('\ncheck() { # name\n  "${check_command[@]}" "$1" "${P}"\n}\n', lane1)

    def test_cache_miss_builds_then_pushes(self):
        for lane, (step, provider, marker, outputs) in self.CASES.items():
            with self.subTest(lane=lane):
                code, text = self.lane(lane)
                self.assertEqual(code, 0, text)
                self.assertEqual([c for c in self.calls('cache') if c.endswith(' ' + provider)],
                                 ['restore ' + provider, 'push ' + provider])
                self.assertEqual(len([b for b in self.builds() if 'takx-' + provider in b]), 1)
                self.assertEqual(self.state(provider).get('status'), 'ok')
                self.assertNotIn('restored_from', self.state(provider))

    def test_failed_build_is_not_pushed_and_failed_push_does_not_fail_the_build(self):
        for lane, (step, provider, marker, outputs) in self.CASES.items():
            with self.subTest(lane=lane):
                code, text = self.lane(lane, STUB_BUILD='fail')
                self.assertNotEqual(code, 0, text)
                self.assertEqual(self.state(provider).get('status'), 'failed')
                self.assertNotIn('push ' + provider, self.calls('cache'))
                code, text = self.lane(lane, STUB_PUSH='1')
                self.assertEqual(code, 0, text)
                self.assertIn('push ' + provider, self.calls('cache'))
                self.assertIn('not pushed to the provider cache', text)
                self.assertEqual(self.state(provider).get('status'), 'ok')

    def test_cache_off_and_non_default_invocations_never_touch_the_cache(self):
        for lane, variable in (('lane1.sh', 'TAKX_PROTO_CACHE'), ('lane2.sh', 'TAKX_PROTO_CACHE'),
                               ('lane3.sh', 'TAKX_LANE3_FETCH'), ('lane2.sh', 'TAKX_LANE2_FETCH'),
                               ('lane4.sh', 'TAKX_LANE4_FETCH'), ('lane4.sh', 'TAKX_PROTO_CACHE')):
            with self.subTest(lane=lane, variable=variable):
                if (self.root / 'P').exists():
                    os.rename(self.root, '%s-%d' % (self.root, len(os.listdir(self.tmp.name))))
                value = 'off' if variable == 'TAKX_PROTO_CACHE' else '0'
                code, text = self.lane(lane, STUB_RESTORE='hit', **{variable: value})
                self.assertEqual(code, 0, text)
                self.assertEqual(self.calls('cache'), [])
                self.assertEqual(self.state(self.CASES[lane][1]).get('status'), 'ok')


DOCKER_RECORDER = r"""#!/bin/bash
# Records every call; behaviour comes from files next to it.
dir="$(dirname "$0")"
printf '%s\n' "$*" >> "$dir/calls"
case "$1" in
  import) cat > "$dir/layer.tar";;
  create) echo container-id;;
  image) echo '{"dev.uds.takx.provider":"p"}';;
  cp) echo copied > "$3";;
esac
if [[ -f "$dir/fail-$1" ]]; then cat "$dir/fail-$1" >&2; exit 1; fi
exit 0
"""


class DockerAdapter(unittest.TestCase):
    """DockerRegistry against a recording `docker`; no daemon involved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / 'docker').write_text(DOCKER_RECORDER)
        os.chmod(self.dir / 'docker', 0o755)
        self.registry = PC.DockerRegistry(30, str(self.dir / 'docker'))

    def calls(self):
        path = self.dir / 'calls'
        return path.read_text().splitlines() if path.exists() else []

    def test_probe_classifies_the_answer_of_the_registry(self):
        ref = 'ghcr.io/owner/cache/p:' + 'a' * 64
        self.assertEqual(self.registry.probe(ref), 'present')
        for text, expected in (('manifest unknown', 'absent'), ('no such manifest: x', 'absent'),
                               ('unauthorized: authentication required', 'unauthorized'),
                               ('denied: denied', 'unauthorized'), ('no basic auth credentials', 'unauthorized'),
                               ('error pinging v2 registry: Bad Gateway', 'error'), ('', 'error')):
            (self.dir / 'fail-manifest').write_text(text + '\n')
            with self.subTest(text=text):
                self.assertEqual(self.registry.probe(ref), expected)

    def test_only_a_loopback_registry_is_queried_without_tls(self):
        for host, insecure in (('ghcr.io', False), ('registry.example:5000', False), ('localhost.example', False),
                               ('127.0.0.1:5000', True), ('localhost:5000', True), ('localhost', True)):
            self.registry.probe(host + '/cache/p:tag')
            with self.subTest(host=host):
                self.assertEqual(self.calls()[-1], 'manifest inspect %s%s/cache/p:tag'
                                 % ('--insecure ' if insecure else '', host))

    def test_publish_streams_one_layer_and_never_builds(self):
        context = self.dir / 'context'
        context.mkdir()
        (context / 'p.tar').write_bytes(b'archive')
        (context / 'meta.json').write_text('{}')
        (context / 'unlisted').write_text('stays')
        self.registry.publish('reg/p:tag', context, ['p.tar', 'meta.json'], {'b': 'two words', 'a': '1'})
        self.assertEqual(self.calls(), ['import --change LABEL "a"="1" --change LABEL "b"="two words" - reg/p:tag',
                                        'push -q reg/p:tag', 'rmi reg/p:tag'])
        with tarfile.open(self.dir / 'layer.tar') as layer:
            self.assertEqual(layer.getnames(), ['p.tar', 'meta.json'])
            self.assertEqual(layer.extractfile('p.tar').read(), b'archive')
        self.assertEqual(sorted(os.listdir(context)), ['meta.json', 'p.tar', 'unlisted'])

    def test_failed_import_or_push_is_an_error_and_leaves_no_image(self):
        context = self.dir / 'context'
        context.mkdir()
        (context / 'p.tar').write_bytes(b'archive')
        (self.dir / 'fail-push').write_text('denied\n')
        with self.assertRaises(PC.CacheError):
            self.registry.publish('reg/p:tag', context, ['p.tar'], {})
        self.assertEqual(self.calls()[-2:], ['push -q reg/p:tag', 'rmi reg/p:tag'])
        (self.dir / 'calls').unlink()
        (self.dir / 'fail-import').write_text('no space\n')
        with self.assertRaises(PC.CacheError) as caught:
            self.registry.publish('reg/p:tag', context, ['p.tar'], {})
        self.assertIn('no space', str(caught.exception))
        self.assertEqual([call.split()[0] for call in self.calls()], ['import'])

    def test_fetch_copies_the_named_files_and_removes_container_and_image(self):
        target = self.dir / 'target'
        target.mkdir()
        labels, digest = self.registry.fetch('reg/p:tag', target, ['p.tar', 'meta.json'])
        self.assertEqual(labels, {'dev.uds.takx.provider': 'p'})
        self.assertIsNone(digest)  # this docker lists no registry digest
        self.assertEqual(self.calls(), [
            'pull -q reg/p:tag', 'image inspect --format {{json .Config.Labels}} reg/p:tag',
            'image inspect --format {{json .RepoDigests}} reg/p:tag',
            'create reg/p:tag /nonexistent', 'cp container-id:/p.tar %s/p.tar' % target,
            'cp container-id:/meta.json %s/meta.json' % target, 'rm -f container-id', 'rmi reg/p:tag'])
        (self.dir / 'calls').unlink()
        (self.dir / 'fail-cp').write_text('no such file\n')
        with self.assertRaises(PC.CacheError):
            self.registry.fetch('reg/p:tag', target, ['p.tar'])
        self.assertEqual(self.calls()[-2:], ['rm -f container-id', 'rmi reg/p:tag'])
        (self.dir / 'calls').unlink()
        (self.dir / 'fail-pull').write_text('manifest unknown\n')
        with self.assertRaises(PC.CacheError):
            self.registry.fetch('reg/p:tag', target, ['p.tar'])
        self.assertEqual(self.calls(), ['pull -q reg/p:tag'])

    def test_image_id_and_availability(self):
        self.assertTrue(self.registry.available())
        self.assertFalse(PC.DockerRegistry(30, str(self.dir / 'missing')).available())
        (self.dir / 'fail-image').write_text('No such image\n')
        self.assertIsNone(self.registry.image_id(PC.TOOLCHAIN))
        with self.assertRaises(PC.CacheError):
            PC.DockerRegistry(30, str(self.dir / 'missing')).probe('reg/p:tag')

    def test_the_module_never_reads_docker_credentials(self):
        text = (HERE / 'provider_cache.py').read_text()
        for word in ('config.json', '/.docker', 'DOCKER_CONFIG', "'login'", '--password', 'credsStore'):
            self.assertNotIn(word, text)


class RepositoryManifest(unittest.TestCase):
    """providers.json against the recipes and lane scripts it describes."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = PC.validate_manifest(json.loads((HERE / 'providers.json').read_text()))

    def test_every_provider_of_the_lanes_is_described(self):
        self.assertEqual(set(self.manifest['providers']), EXPECTED)

    def test_recipes_locks_and_helpers_exist_and_exclude_tests(self):
        tree = PC.WorkTree('/nonexistent', REPO)
        for name, entry in self.manifest['providers'].items():
            with self.subTest(provider=name):
                files = tree.recipe_files(PC.recipe_sources(self.manifest, entry['recipe']),
                                          self.manifest['recipe_exclude'])
                own = [path for path in files if path.startswith(entry['recipe'] + '/')]
                self.assertTrue(own)
                for path in files:
                    base = path.rsplit('/', 1)[-1]
                    self.assertFalse(base.startswith(('test_', 'README')) or '__pycache__' in path, path)
                script = entry['invocation']['argv'][2]
                self.assertIn(script.replace('{repo}/', ''), files)
                locks = tree.locks(entry['locks'])
                self.assertTrue(all(lock['digests'] for lock in locks.values()), locks.keys())

    def test_declared_helpers_cover_the_modules_each_recipe_loads(self):
        # Every sibling recipe file a recipe names through HERE.parent must be in its closure.
        pattern = re.compile(r"HERE\.parent\s*/\s*'([a-z-]+/[A-Za-z0-9_.-]+)'|'([a-z]+-[a-z-]+/[a-z_]+\.py)'")
        tree = PC.WorkTree('/nonexistent', REPO)
        for recipe in self.manifest['recipes']:
            files = tree.recipe_files(PC.recipe_sources(self.manifest, recipe), self.manifest['recipe_exclude'])
            for source in (REPO / recipe).glob('*.py'):
                if source.name.startswith('test_'):
                    continue
                for match in pattern.finditer(source.read_text()):
                    named = 'takx/' + (match.group(1) or match.group(2))
                    if (REPO / named).is_file():
                        with self.subTest(recipe=recipe, source=source.name):
                            self.assertIn(named, files)

    def test_invocation_templates_match_the_lane_scripts(self):
        known = set(self.manifest['variables']) | {'root', 'repo', 'timestamp'}
        for name, entry in self.manifest['providers'].items():
            lane = (HERE / 'stages' / entry['lane']).read_text()
            call = entry['invocation']
            with self.subTest(provider=name):
                for token in call['argv'] + call['env']:
                    self.assertLessEqual(set(re.findall(r'\{([a-z]+)\}', token)), known, token)
                    if token.startswith('--'):
                        self.assertIn(token, lane)
                script = call['argv'][2]
                self.assertTrue(script.startswith('{repo}/' + entry['recipe'] + '/'), script)
                self.assertIn(entry['recipe'].split('/', 1)[1], lane)
                self.assertIn(entry['marker'], lane)
                self.assertIn('{root}/P/' + name, ' '.join(call['argv'] + call['env']))
                for dep in entry['prerequisites']:
                    self.assertRegex(lane, r'\b%s\b' % re.escape(dep))
        for variable in ('jdk', 'cmake', 'ninja'):
            value = self.manifest['variables'][variable]
            for lane in ('lane2.sh', 'lane3.sh'):
                self.assertIn('%s=%s\n' % (variable, value), (HERE / 'stages' / lane).read_text())
        self.assertIn(self.manifest['variables']['jdk'], self.manifest['variables']['javapath'])

    def test_fetched_selectors_name_inputs_that_fetch_inputs_records(self):
        fetcher = (HERE / 'fetch_inputs.py').read_text()
        for name, entry in self.manifest['providers'].items():
            for selector in entry['fetched']:
                (how, value), = selector.items()
                with self.subTest(provider=name, selector=selector):
                    if how == 'consumer':
                        self.assertTrue((REPO / 'takx' / value).is_dir() or value == 'commoncommo')
                    elif how == 'kind':
                        self.assertIn("'%s'" % value, fetcher)
                    else:
                        self.assertIn(value.split('/')[1], fetcher)

    def test_lanes_restore_before_building_and_push_after_success(self):
        for lane in ('lane1.sh', 'lane2.sh', 'lane3.sh', 'lane4.sh'):
            path = HERE / 'stages' / lane
            text = path.read_text()
            with self.subTest(lane=lane):
                self.assertEqual(subprocess.run(['/bin/bash', '-n', str(path)]).returncode, 0)
                self.assertIn('provider_cache.py"\n', text)
                # lane1 also hands over its receipt check; the other lanes' checks are in providers.json.
                self.assertIn('restore "$1" --verify-with "${check_command[@]}" "$1" "${P}" \\\n    >>'
                              if lane == 'lane1.sh' else 'restore "$1" >>', text)
                self.assertIn('push "$1" --verify-with "${check_command[@]}" "$1" "${P}" \\\n    >>'
                              if lane == 'lane1.sh' else 'push "$1" >>', text)
                self.assertEqual(text.count('[[ "${TAKX_PROTO_CACHE:-}" != off ]] || return'), 2)
                # cache_push is the last word of its function and cannot fail it.
                body = text[text.index('cache_push() {'):]
                self.assertIn('  return 0\n}', body[:body.index('\n}\n') + 3])
        lane1 = (HERE / 'stages/lane1.sh').read_text()
        for name in ('sqlcipher', 'cesium-acq', 'cesium', 'kml'):
            function = lane1[lane1.index('do_%s() {' % name.replace('-', '_')):]
            function = function[:function.index('\n}\n')]
            order = [function.index(part) for part in (
                'already_ok ' + name, 'set_aside ' + name, 'cache_restored ' + name, 'recipe ' + name,
                'finish ' + name, '&& cache_push ' + name)]
            self.assertEqual(order, sorted(order), name)
        for lane in ('lane2.sh', 'lane3.sh', 'lane4.sh'):
            text = (HERE / 'stages' / lane).read_text()
            function = text[text.index('\nprovider() {' if lane != 'lane4.sh' else '\nstage() {'):]
            function = function[:function.index('\n}\n')]
            order = [function.index(part) for part in (
                'already ok, skipping', 'set_aside "$name"', 'if cache_restored "$name"', '"$run" -d --name',
                'write_status "$name" ok', 'cache_push "$name"')]
            self.assertEqual(order, sorted(order), lane)

    def test_toolchain_block_is_what_the_dockerfile_pins_sets_and_asserts(self):
        # The fingerprint in every key is derived from this block; the Dockerfile is where the facts are.
        block = self.manifest['toolchain']
        text = (HERE / 'Dockerfile').read_text()
        self.assertEqual(re.findall(r'^ARG BASE=(\S+)$', text, re.M), [block['base']])
        stage = text[text.index('FROM ${BASE} AS toolchain\n'):text.index('\nFROM toolchain AS runtime\n')]
        environment = {}
        for line in re.findall(r'^ENV (.+)$', stage, re.M):
            for assignment in shlex.split(line):
                name, value = assignment.split('=', 1)
                environment[name] = value
        self.assertEqual(environment, block['environment'])
        gates = []
        for line in re.findall(r'^\s*want (.+?); \\$', stage, re.M):
            name, expect, *command = shlex.split(line.replace('$JAVA_HOME', environment['JAVA_HOME']))
            gates.append({'name': name, 'expect': expect, 'argv': command})
        self.assertEqual(gates, block['gated'])
        self.assertEqual(len(gates), 12)
        self.assertEqual(stage.count('    want '), len(gates))  # no gate is written in a way this misses
        # The probe reads the output the way the Dockerfile's `want` does.
        self.assertIn('got="$("$@" 2>&1 | head -n 3 | tr \'\\n\' \' \')"', stage)
        self.assertIn('case "$got" in *"$expect"*)', stage)
        for gate in block['gated']:
            self.assertIn('$(%s 2>&1 | head -n 3 | tr ' % ' '.join(gate['argv']), PC.probe_script(block))
        self.assertEqual(block['environment']['JAVA_HOME'], self.manifest['variables']['jdk'])
        self.assertEqual('PATH=' + block['environment']['PATH'], self.manifest['variables']['javapath'])

    def test_wrapper_block_is_what_run_in_toolchain_gives_every_command(self):
        # The real wrapper against a docker that only records. What it adds beside the
        # block is listed here: none of it is part of a key.
        outside_the_key = {'SSL_CERT_FILE', 'CURL_CA_BUNDLE', 'GIT_SSL_CAINFO', 'PIP_CERT', 'REQUESTS_CA_BUNDLE',
                           'TAKX_JAVA_TRUSTSTORE'}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            (base / 'bin').mkdir()
            (base / 'bin/docker').write_text('#!/bin/bash\nprintf \'%s\\n\' "$@" > "$STUB_ARGS"\n')
            os.chmod(base / 'bin/docker', 0o755)
            for ca in ('', str(base / 'ca.crt')):
                if ca:
                    Path(ca).write_text('not a certificate; only its presence counts here\n')
                environment = {'PATH': '%s:/usr/bin:/bin' % (base / 'bin'), 'HOME': str(base),
                               'TAKX_PROTO_ROOT': str(base / 'root'), 'STUB_ARGS': str(base / 'args')}
                if ca:
                    environment['TAKX_PROTO_CA'] = ca
                done = subprocess.run(['/bin/bash', str(HERE / 'run-in-toolchain.sh'), '-e', 'STEP=1', '--',
                                       'python3', '-B', 'x.py'], env=environment, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                self.assertEqual((done.returncode, done.stderr), (0, b''))
                arguments = (base / 'args').read_text().split('\n')[:-1]
                self.assertEqual(arguments[0], 'run')
                image = arguments.index(PC.TOOLCHAIN)
                self.assertEqual(arguments[image + 1:], ['python3', '-B', 'x.py'])
                options = arguments[1:image]
                values = {'root': str(base / 'root'), 'repo': str(REPO)}
                wrapper = self.manifest['wrapper']

                def given(flag):
                    return [options[index + 1] for index, option in enumerate(options) if option == flag]
                expected = [PC.render(token, values) for token in wrapper['env']]
                variables = given('-e')
                self.assertEqual(variables[:len(expected)], expected)
                self.assertEqual(variables[-1], 'STEP=1')
                extra = {item.split('=', 1)[0] for item in variables[len(expected):-1]}
                self.assertLessEqual(extra, outside_the_key)
                if ca:
                    self.assertIn('SSL_CERT_FILE', extra)
                mounts = given('-v')
                expected = [PC.render(token, values) for token in wrapper['mounts']]
                self.assertEqual(mounts[:len(expected)], expected)
                self.assertEqual([mount.split(':', 1)[1] for mount in mounts[len(expected):]],
                                 ['/ca.crt:ro'] * (len(mounts) - len(expected)))
                self.assertEqual(given('-w'), [PC.render(wrapper['workdir'], values)])
                self.assertNotIn('--platform', options)   # only with TAKX_PROTO_ARCH set

    def test_post_restore_checks_are_declared_for_exactly_these_providers(self):
        declared = {name: entry['verify'] for name, entry in self.manifest['providers'].items() if 'verify' in entry}
        self.assertEqual(sorted(declared), ['assimp', 'crypto', 'draco', 'gdal-jni', 'geos', 'gles', 'las', 'proj',
                                            'spatial'])
        for name, call in declared.items():
            entry = self.manifest['providers'][name]
            with self.subTest(provider=name):
                # The recipe's own tests, pointed at this provider's output by the recipe's proof variable.
                self.assertEqual(call['argv'][:6], ['python3', '-B', '-m', 'unittest', 'discover', '-s'])
                self.assertEqual(call['argv'][6], '{repo}/' + entry['recipe'])
                self.assertIn(call['env'][0].split('=', 1)[1], ['{root}/P/' + name])
                variable = call['env'][0].split('=', 1)[0]
                tests = ''.join(path.read_text() for path in (REPO / entry['recipe']).glob('test*.py'))
                self.assertIn("os.environ.get('%s')" % variable, tests)
                # Everything else a check reads is a prerequisite, which restore requires to be ok.
                for item in call['env'][1:]:
                    self.assertRegex(item, r'^[A-Z_]+=\{root\}/P/(%s)$' % '|'.join(entry['prerequisites']))

    def test_providers_without_any_post_restore_check_are_exactly_these(self):
        providers = self.manifest['providers']
        lane1 = sorted(name for name, entry in providers.items() if entry['lane'] == 'lane1.sh')
        self.assertEqual(lane1, ['cesium', 'cesium-acq', 'kml', 'sqlcipher'])
        self.assertEqual([name for name in lane1 if 'verify' in providers[name]], [])
        # lane1 hands its receipt check to restore (--verify-with); that check knows each of its providers.
        text = (HERE / 'stages/lane1.sh').read_text()
        program = text[text.index("CHECK_PY=$(cat <<'PY'\n"):text.index('\nPY\n)\n')]
        for name in lane1:
            self.assertEqual(program.count("if name == '%s':\n" % name), 1, name)
        unchecked = sorted(name for name, entry in providers.items() if 'verify' not in entry and name not in lane1)
        self.assertEqual(unchecked, ['commoncommo', 'consumers57', 'curl', 'gis-java', 'kernel-java', 'mdb', 'scene',
                                     'ui-original', 'ui-wizard-reference', 'worldwind-interop', 'worldwind-java'])

    def test_wrappers_delegate_to_the_module(self):
        for script, command in (('push-cache.sh', 'push'), ('restore-cache.sh', 'restore')):
            text = (HERE / script).read_text()
            self.assertIn('provider_cache.py', text)
            self.assertIn(command, text)
            self.assertEqual(subprocess.run(['/bin/bash', '-n', str(HERE / script)]).returncode, 0)


RUN_ARGV_STUB = r"""#!/bin/bash
# Stands in for run-in-toolchain.sh: records every argument on its own line and
# "builds" by creating the completion marker.
{ printf 'CALL\n'; printf 'ARG %s\n' "$@"; } >> "$STUB_LOG.argv"
case "$*" in
  *unittest*) exit 0;;
  *" -c "*) echo "check passed"; exit 0;;
esac
mkdir -p "$STUB_OUTPUT_DIR" && echo built > "$STUB_OUTPUT_DIR/$STUB_MARKER"
echo 0 > "$STUB_LOG.rc"
exit 0
"""


class LaneInvocations(unittest.TestCase):
    """The command each lane really runs for a provider, against providers.json.

    The key is derived from the invocation template in providers.json, not
    from the lane script. A lane edit that changes an argument, a flag or an
    environment variable without the same edit in the manifest would keep the
    old key and restore an output built by another command. Here every real
    lane step is run with stubbed tools on a cache miss and the recorded
    command must be the rendered template, argument for argument.
    """

    @classmethod
    def setUpClass(cls):
        cls.manifest = PC.validate_manifest(json.loads((HERE / 'providers.json').read_text()))
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.base = Path(cls.tmp.name).resolve()
        cls.repo = cls.base / 'repo'
        proto = cls.repo / 'takx/desktop-prototype'
        (proto / 'stages').mkdir(parents=True)
        cls.bin = cls.base / 'bin'
        cls.bin.mkdir()
        for lane in sorted({entry['lane'] for entry in cls.manifest['providers'].values()}):
            (proto / 'stages' / lane).write_text((HERE / 'stages' / lane).read_text())
            os.chmod(proto / 'stages' / lane, 0o755)
        for path, text in ((proto / 'run-in-toolchain.sh', RUN_ARGV_STUB), (cls.bin / 'docker', DOCKER_STUB)):
            path.write_text(text)
            os.chmod(path, 0o755)
        (proto / 'provider_cache.py').write_text(CACHE_STUB)
        for entry in cls.manifest['providers'].values():
            # The lanes read the entrypoint and the locks to seed their source caches.
            script = cls.repo / entry['invocation']['argv'][2].replace('{repo}/', '')
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text('')
            for lock in entry['locks']:
                (cls.repo / lock).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / lock, cls.repo / lock)

    def recorded(self, name):
        """Run the lane step of `name` on a cache miss in a new work root.

        Returns (output, calls of the toolchain wrapper, cache commands, root).
        The stub build writes the completion marker only, so a lane that also
        lists other outputs reports a failure afterwards; LaneHooks covers that
        part, here only the recorded command matters.
        """
        providers = self.manifest['providers']
        root = Path(tempfile.mkdtemp(prefix='root-%s.' % name, dir=self.base))
        log = root.with_name(root.name + '.log')
        for other, entry in providers.items():
            if other != name:  # every other provider is finished here
                (root / 'P' / other).mkdir(parents=True)
                (root / 'P' / other / entry['marker']).write_text('done\n')
                (root / 'status').mkdir(exist_ok=True)
                (root / 'status' / (other + '.json')).write_text(
                    json.dumps({'stage': other, 'status': 'ok', 'outputs': []}))
        env = {'PATH': '%s:%s' % (self.bin, os.environ.get('PATH', '/usr/bin:/bin')), 'HOME': self.tmp.name,
               'TAKX_PROTO_ROOT': str(root), 'STUB_LOG': str(log), 'STUB_MARKER': providers[name]['marker'],
               'STUB_OUTPUT_DIR': str(root / 'P' / name), 'TAKX_PREREQ_TIMEOUT': '0',
               'TAKX_LANE2_SQLCIPHER_WAIT_S': '0', 'TAKX_LANE3_WAIT_S': '0'}
        lane = self.repo / 'takx/desktop-prototype/stages' / providers[name]['lane']
        done = subprocess.run(['/bin/bash', str(lane), name], env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
        calls = []
        path = Path(str(log) + '.argv')
        for line in (path.read_text().split('\n')[:-1] if path.exists() else []):
            if line == 'CALL':
                calls.append([])
            elif line.startswith('ARG '):
                calls[-1].append(line[4:])
            else:  # an argument that holds a newline
                calls[-1][-1] += '\n' + line
        cache = Path(str(log) + '.cache')
        return done.stdout.decode(), calls, (cache.read_text().split() if cache.exists() else []), root

    def pattern(self, token, root):
        values = dict(self.manifest['variables'], root=str(root), repo=str(self.repo))
        parts = re.split(r'\{([a-z]+)\}', token)
        text = ''
        for index, part in enumerate(parts):
            if index % 2 == 0:
                text += re.escape(part)
            elif part == 'timestamp':
                text += '[0-9]+'
            else:
                text += re.escape(values[part])
        return text

    def test_every_lane_runs_exactly_the_command_the_key_describes(self):
        for name, entry in sorted(self.manifest['providers'].items()):
            with self.subTest(provider=name):
                text, calls, cache, root = self.recorded(name)
                self.assertEqual(cache[:2], ['restore', name], text)
                builds = [call for call in calls if 'unittest' not in call and '-c' not in call]
                self.assertEqual(len(builds), 1, text)
                call = builds[0]
                self.assertIn('--', call, call)
                options, command = call[:call.index('--')], call[call.index('--') + 1:]
                self.assertEqual(options[options.index('--name') + 1], 'takx-' + name)
                environment = [options[index + 1] for index, option in enumerate(options) if option in ('-e', '--env')]
                for label, actual, template in (('env', environment, entry['invocation']['env']),
                                                ('argv', command, entry['invocation']['argv'])):
                    self.assertEqual(len(actual), len(template), '%s of %s: %r' % (label, name, actual))
                    for value, token in zip(actual, template):
                        self.assertRegex(value, r'\A%s\Z' % self.pattern(token, root),
                                         '%s of %s differs from providers.json' % (label, name))

    def test_every_lane_checks_a_fresh_output_with_the_command_restore_runs(self):
        # 'verify' in providers.json is the lane's own acceptance step after a build. A lane that
        # changes that step without the manifest would let restore accept what the lane rejects.
        declared = {name: entry['verify'] for name, entry in self.manifest['providers'].items() if 'verify' in entry}
        self.assertTrue(declared)
        for name, call in sorted(declared.items()):
            with self.subTest(provider=name):
                text, calls, cache, root = self.recorded(name)
                checks = [recorded for recorded in calls if 'unittest' in recorded]
                self.assertEqual(len(checks), 1, text)
                options, command = checks[0][:checks[0].index('--')], checks[0][checks[0].index('--') + 1:]
                environment = [options[index + 1] for index, option in enumerate(options) if option in ('-e', '--env')]
                for label, actual, template in (('env', environment, call['env']), ('argv', command, call['argv'])):
                    self.assertEqual(len(actual), len(template), '%s of %s: %r' % (label, name, actual))
                    for value, token in zip(actual, template):
                        self.assertRegex(value, r'\A%s\Z' % self.pattern(token, root),
                                         'check %s of %s differs from providers.json' % (label, name))
        # The comparison is exact here, too.
        lane = self.repo / 'takx/desktop-prototype/stages/lane2.sh'
        original = lane.read_text()
        self.addCleanup(lane.write_text, original)
        self.assertIn(' -p test_build.py -v ', original)
        lane.write_text(original.replace(' -p test_build.py -v ', ' -p test_other.py -v '))
        text, calls, cache, root = self.recorded('proj')
        command = [recorded for recorded in calls if 'unittest' in recorded][0]
        command = command[command.index('--') + 1:]
        differing = [value for value, token in zip(command, declared['proj']['argv'])
                     if not re.match(r'\A%s\Z' % self.pattern(token, root), value)]
        self.assertEqual(differing, ['test_other.py'])

    def test_a_changed_lane_argument_is_noticed(self):
        # The comparison above is exact: one changed value in a lane fails it.
        lane = self.repo / 'takx/desktop-prototype/stages/lane1.sh'
        original = lane.read_text()
        self.addCleanup(lane.write_text, original)
        self.assertIn('--jobs 1\n', original)
        lane.write_text(original.replace('--jobs 1\n', '--jobs 2\n'))
        text, calls, cache, root = self.recorded('sqlcipher')
        command = [call for call in calls if 'unittest' not in call and '-c' not in call][0]
        command = command[command.index('--') + 1:]
        template = self.manifest['providers']['sqlcipher']['invocation']['argv']
        self.assertEqual(len(command), len(template))
        differing = [value for value, token in zip(command, template)
                     if not re.match(r'\A%s\Z' % self.pattern(token, root), value)]
        self.assertEqual(differing, ['2'])


class PackagesCore(unittest.TestCase):
    """The build packages of the toolchain: selection, digest, comparison, policy."""

    ROWS = [('ii ', 'gcc-14', '14.2.0-19', 'amd64', 'devel'), ('ii ', 'libc6-dev', '2.41-12+deb13u4', 'amd64', 'libdevel'),
            ('ii ', 'libc6', '2.41-12+deb13u4', 'amd64', 'libs'), ('ii ', 'openssl', '3.5.7-1~deb13u3', 'amd64', 'utils'),
            ('ii ', 'libegl-dev', '1.7.0-1+b2', 'amd64', 'libs'), ('rc ', 'libold-dev', '1.0', 'amd64', 'libdevel'),
            ('ii ', 'linux-libc-dev', '6.12.117-1', 'all', 'devel')]

    def test_selection_is_by_section_or_name_pattern_of_installed_packages(self):
        block = toolchain_block()
        self.assertEqual(PC.build_packages(block, self.ROWS), BUILD_PACKAGES)
        self.assertEqual(PC.build_packages(block, list(reversed(self.ROWS)) + self.ROWS), BUILD_PACKAGES)
        only_names = dict(block, build_packages={'sections': [], 'names': ['libc6']})
        self.assertEqual(PC.build_packages(only_names, self.ROWS), ['libc6 2.41-12+deb13u4 amd64'])
        only_sections = dict(block, build_packages={'sections': ['libdevel'], 'names': []})
        self.assertEqual(PC.build_packages(only_sections, self.ROWS), ['libc6-dev 2.41-12+deb13u4 amd64'])
        # The section is the last part of what dpkg prints; patterns match the whole name, case included.
        area = [('ii ', 'tool', '1', 'amd64', 'non-free/devel'), ('ii ', 'other', '1', 'amd64', 'devel/x'),
                ('ii ', 'xlibc6', '1', 'amd64', 'libs'), ('ii ', 'LIBC6', '1', 'amd64', 'libs'),
                ('ii ', 'libc6x', '1', 'amd64', 'libs')]
        self.assertEqual(PC.build_packages(block, area), ['tool 1 amd64'])
        # Both architectures of one name are two packages.
        both = [('ii ', 'libc6', '2', 'amd64', 'libs'), ('ii ', 'libc6', '2', 'i386', 'libs')]
        self.assertEqual(PC.build_packages(block, both), ['libc6 2 amd64', 'libc6 2 i386'])

    def test_only_fully_installed_packages_count(self):
        block = toolchain_block()
        for status in ('rc ', 'un ', 'iU ', 'pi ', 'hi ', 'i', ''):
            rows = [(status, 'libc6', '2', 'amd64', 'libs'), ('ii ', 'gcc-14', '1', 'amd64', 'devel')]
            with self.subTest(status=status):
                self.assertEqual(PC.build_packages(block, rows), ['gcc-14 1 amd64'])
        self.assertEqual(PC.build_packages(block, [('iiR', 'libc6', '2', 'amd64', 'libs')]), ['libc6 2 amd64'])

    def test_selection_refuses_what_is_no_list(self):
        block = toolchain_block()
        for rows in ([], [('ii ', 'openssl', '3', 'amd64', 'utils')], [('rc ', 'libc6', '2', 'amd64', 'libs')]):
            with self.subTest(rows=rows), self.assertRaisesRegex(
                    PC.CacheError, '^the toolchain probe names no installed build package$'):
                PC.build_packages(block, rows)
        for row in (('ii ', '', '2', 'amd64', 'libs'), ('ii ', 'libc6', '', 'amd64', 'libs'),
                    ('ii ', 'libc6', '2', '', 'libs'), ('ii ', 'lib c6', '2', 'amd64', 'libs'),
                    ('ii ', 'libc6', '2 3', 'amd64', 'libs'), ('ii ', 'libc6', '2', 'amd 64', 'libs')):
            with self.subTest(row=row), self.assertRaisesRegex(PC.CacheError, 'no plain name, version and architecture'):
                PC.build_packages(block, [row])
        # A package that is not installed is not looked at.
        self.assertEqual(PC.build_packages(block, [('rc ', 'a b', '', '', 'devel'), self.ROWS[0]]), BUILD_PACKAGES[:1])

    def test_digest_is_of_the_lines_and_pinned(self):
        self.assertEqual(PC.packages_digest(['a 1 all', 'b 2 amd64']), hashlib.sha256(b'a 1 all\nb 2 amd64\n').hexdigest())
        self.assertEqual(PC.packages_digest([]), hashlib.sha256(b'').hexdigest())
        self.assertEqual(PC.packages_digest(BUILD_PACKAGES), BUILD_DIGEST)
        self.assertEqual(BUILD_DIGEST, 'b32dd923b7f03f688f3cbd9a1535aa301ba92626159dcba6a961cdfc1d56cbdb')
        self.assertNotEqual(PC.packages_digest(['a 1 all', 'b 2 amd64']), PC.packages_digest(['b 2 amd64', 'a 1 all']))

    def test_attribute_is_both_digests_and_the_list(self):
        observed = PC.parse_probe(probe_text())
        self.assertEqual(PC.packages_attribute(toolchain_block(), observed), ATTRIBUTE)
        for bad in (None, '', 'e' * 63, 'E' * 64, 5):
            with self.subTest(bad=bad), self.assertRaisesRegex(PC.CacheError, 'no digest of the package list'):
                PC.packages_attribute(toolchain_block(), dict(observed, packages=bad))
        with self.assertRaisesRegex(PC.CacheError, 'no installed build package'):
            PC.packages_attribute(toolchain_block(), dict(observed, package_list=[]))

    def test_attribute_is_valid_only_with_the_digest_of_its_own_list(self):
        self.assertIs(PC.valid_attribute(ATTRIBUTE), ATTRIBUTE)
        more = dict(ATTRIBUTE, image_id='x')
        self.assertIs(PC.valid_attribute(more), more)
        for bad in (None, [], 'x', {}, dict(ATTRIBUTE, build_packages=[]), dict(ATTRIBUTE, build_packages=None),
                    dict(ATTRIBUTE, build_packages=BUILD_PACKAGES[:-1]), dict(ATTRIBUTE, build_packages_sha256='0' * 64),
                    dict(ATTRIBUTE, build_packages=BUILD_PACKAGES + [5]),
                    {'build_packages': [], 'build_packages_sha256': PC.packages_digest([])},
                    {'build_packages': 'ab', 'build_packages_sha256': PC.packages_digest(['a', 'b'])}):
            with self.subTest(bad=bad):
                self.assertIsNone(PC.valid_attribute(bad))

    def test_record_counts_for_packages_only_with_this_manifests_selection(self):
        block = toolchain_block()
        self.assertEqual(PC.recorded_packages(block, toolchain_record()), ATTRIBUTE)
        other = dict(block, build_packages={'sections': ['devel'], 'names': ['libc6', '*-dev']})
        self.assertIsNone(PC.recorded_packages(other, toolchain_record()))
        for change in ({'build_selection': None}, {'build_packages': BUILD_PACKAGES[1:]}, {'build_packages_sha256': None},
                       {'packages_sha256': None}, {'build_packages': None}):
            with self.subTest(change=change):
                self.assertIsNone(PC.recorded_packages(block, toolchain_record(**change)))
        for record in (None, [], 'x'):
            self.assertIsNone(PC.recorded_packages(block, record))
        old = toolchain_record()
        for field in ('build_packages', 'build_packages_sha256', 'build_selection'):
            del old[field]
        self.assertIsNone(PC.recorded_packages(block, old))     # a record of before the lists were kept
        self.assertIsNotNone(PC.recorded_toolchain(block, old))  # which still names the toolchain

    def test_changes_are_named_package_by_package(self):
        before = ['gcc-14 14.2.0-19 amd64', 'libc6-dev 2.41-12+deb13u4 amd64', 'gone 1 all', 'multi 1 amd64',
                  'multi 1 i386']
        after = ['gcc-14 14.2.0-19 amd64', 'libc6-dev 2.41-12+deb13u5 amd64', 'new 2 amd64', 'multi 1 amd64',
                 'multi 2 i386']
        self.assertEqual(PC.package_changes(before, after), [
            'gone:all 1 not here', 'libc6-dev:amd64 2.41-12+deb13u4 -> 2.41-12+deb13u5', 'multi:i386 1 -> 2',
            'new:amd64 2 only here'])
        self.assertEqual(PC.package_changes(before, before), [])
        self.assertEqual(PC.package_changes(before, list(reversed(before))), [])
        self.assertEqual(PC.package_changes([], ['a 1 all']), ['a:all 1 only here'])
        self.assertEqual(PC.package_changes(['a 1 all'], []), ['a:all 1 not here'])
        self.assertEqual(PC.package_changes(['odd'], ['odd 1']), ['odd:  -> 1'])  # malformed lines do not raise

    def test_key_fingerprint_names_the_packages_only_in_key_mode(self):
        self.assertEqual(PC.PACKAGE_MODES, ('report', 'enforce', 'key'))
        for mode in ('report', 'enforce'):
            self.assertEqual(PC.key_fingerprint(FINGERPRINT, ATTRIBUTE, mode), FINGERPRINT)
            self.assertEqual(PC.key_fingerprint(FINGERPRINT, None, mode), FINGERPRINT)
        keyed = PC.key_fingerprint(FINGERPRINT, ATTRIBUTE, 'key')
        self.assertEqual(keyed, PC.derive_key({'schema': 1, 'toolchain': FINGERPRINT,
                                               'build_packages_sha256': BUILD_DIGEST}))
        self.assertRegex(keyed, r'\A[0-9a-f]{64}\Z')
        self.assertNotEqual(keyed, FINGERPRINT)
        drifted = PC.packages_attribute(toolchain_block(), PC.parse_probe(probe_text(**DRIFTED)))
        self.assertNotEqual(PC.key_fingerprint(FINGERPRINT, drifted, 'key'), keyed)
        self.assertNotEqual(PC.key_fingerprint('0' * 64, ATTRIBUTE, 'key'), keyed)
        # The whole-list digest is not in it: only what feeds a build is.
        self.assertEqual(PC.key_fingerprint(FINGERPRINT, dict(ATTRIBUTE, packages_sha256='0' * 64), 'key'), keyed)
        for bad in (None, {}, dict(ATTRIBUTE, build_packages_sha256='0' * 64)):
            with self.subTest(bad=bad), self.assertRaisesRegex(PC.CacheError, 'no build package list'):
                PC.key_fingerprint(FINGERPRINT, bad, 'key')
        for mode in ('', 'Key', 'off', None, 'keys'):
            with self.subTest(mode=mode), self.assertRaisesRegex(PC.CacheError, 'unknown package mode'):
                PC.key_fingerprint(FINGERPRINT, ATTRIBUTE, mode)

    def test_judgement_of_a_bundles_packages(self):
        here, other_id = TOOLCHAIN_ID, 'sha256:' + '7' * 64
        drifted = PC.packages_attribute(toolchain_block(), PC.parse_probe(probe_text(**DRIFTED)))
        same = ('same', 'built with the build packages of the toolchain here')
        by_image = ('same', 'records no package list, but was built by the toolchain image that is here')
        other = ('other', 'built with other toolchain build packages than the ones here (1): '
                          'libc6-dev:amd64 2.41-12+deb13u5 -> 2.41-12+deb13u4')
        unknown = ('unknown', 'records no toolchain package list (saved before it was recorded)')
        unknown_by = ('unknown', 'records no toolchain package list (saved before it was recorded, by toolchain '
                                 'image %s)' % other_id)
        cases = [(dict(ATTRIBUTE, image_id=other_id), other_id, same),   # the list decides, not the image id
                 (ATTRIBUTE, None, same), (drifted, here, other), (dict(drifted, image_id=here), here, other),
                 (None, here, by_image), ({}, here, by_image), (dict(ATTRIBUTE, build_packages_sha256='0' * 64), here, by_image),
                 (None, None, unknown), (None, '', unknown), (None, 5, unknown), ('junk', None, unknown),
                 (None, other_id, unknown_by), ({'image_id': other_id}, other_id, unknown_by)]
        for bundle, built_by, (verdict, detail) in cases:
            for mode in PC.PACKAGE_MODES:
                with self.subTest(bundle=bundle, built_by=built_by, mode=mode):
                    self.assertEqual(PC.judge_packages(bundle, built_by, ATTRIBUTE, here, mode),
                                     (verdict, detail, mode != 'report' and verdict != 'same'))
        # More than eight changed packages are counted and cut.
        many = ['p%02d 1 all' % index for index in range(12)]
        bundle = {'build_packages': many, 'build_packages_sha256': PC.packages_digest(many)}
        detail = PC.judge_packages(bundle, None, ATTRIBUTE, here, 'report')[1]
        self.assertIn('(17): ', detail)
        self.assertTrue(detail.endswith(' ...'))
        self.assertEqual(detail.count(', '), 7)

    def test_check_identity_names_whose_check_and_its_exact_words(self):
        one = PC.check_identity('recipe', ['A=1', '--', 'python3', 'x'])
        self.assertRegex(one, r'\A[0-9a-f]{64}\Z')
        self.assertEqual(one, PC.check_identity('recipe', ('A=1', '--', 'python3', 'x')))
        self.assertEqual(one, PC.derive_key({'whose': 'recipe', 'words': ['A=1', '--', 'python3', 'x']}))
        for whose, words in (('lane', ['A=1', '--', 'python3', 'x']), ('recipe', ['A=1', '--', 'python3', 'y']),
                             ('recipe', ['--', 'A=1', 'python3', 'x']), ('recipe', ['A=1', '--', 'python3']),
                             ('recipe', ['A=1 -- python3 x'])):
            with self.subTest(whose=whose, words=words):
                self.assertNotEqual(PC.check_identity(whose, words), one)

    def test_manifest_needs_a_well_formed_selection_and_reason(self):
        for selection in (None, [], {}, {'sections': ['devel']}, {'names': ['x']}, {'sections': [], 'names': []},
                          {'sections': 'devel', 'names': []}, {'sections': ['devel'], 'names': [5]},
                          {'sections': ['devel'], 'names': [], 'more': []}, {'sections': [''], 'names': []},
                          {'sections': ['devel'], 'names': [' ']}):
            value = manifest()
            if selection is None:
                del value['toolchain']['build_packages']
            else:
                value['toolchain']['build_packages'] = selection
            with self.subTest(selection=selection), self.assertRaisesRegex(PC.CacheError, 'toolchain needs build_packages'):
                PC.validate_manifest(value)
        for selection in ({'sections': ['devel'], 'names': []}, {'sections': [], 'names': ['*-dev']}):
            value = manifest()
            value['toolchain']['build_packages'] = selection
            self.assertIs(PC.validate_manifest(value), value)
        for reason in ('', '  ', 5, [], True, False):
            value = manifest()
            value['providers']['side']['unpublished'] = reason
            with self.subTest(reason=reason), self.assertRaisesRegex(
                    PC.CacheError, '^side: unpublished must say why the provider is not kept in the cache$'):
                PC.validate_manifest(value)
        value = manifest()
        value['providers']['side']['unpublished'] = 'a reason'
        self.assertIs(PC.validate_manifest(value), value)

    def test_selection_and_reason_are_not_part_of_fingerprint_or_key(self):
        block = toolchain_block()
        block['build_packages'] = {'sections': ['x'], 'names': []}
        self.assertEqual(fingerprint(block=block), FINGERPRINT)
        plain, marked = manifest(), manifest()
        marked['providers']['side']['unpublished'] = 'a reason'
        marked['toolchain'] = block
        self.assertEqual(PC.key_document(plain, 'side', facts(), {}), PC.key_document(marked, 'side', facts(), {}))


class CheckReplaceDecision(unittest.TestCase):
    """decide_replace for an entry a post-restore check rejected (kind 'check')."""

    KEY, DIGEST, FILES, CHECK = 'a' * 64, 'sha256:' + '1' * 64, 'f' * 64, 'c' * 64
    PRESENT = 'already cached under this key'

    def note(self, **changes):
        value = {'key': self.KEY, 'digest': self.DIGEST, 'kind': 'check', 'reason': 'the reason', 'markers': {},
                 'files_sha256': self.FILES, 'check': self.CHECK}
        value.update(changes)
        return value

    def decide(self, note=None, digest=None, files=None, recheck=None, cached=True):
        return PC.decide_replace(note or self.note(), self.KEY, digest or self.DIGEST, {}, cached, files, recheck)

    def test_first_the_rebuilt_output_is_asked_for(self):
        self.assertEqual(self.decide(), ('compare', self.PRESENT + '; a check rejected it here'))
        self.assertEqual(self.decide(recheck='passed')[0], 'compare')   # never on the check alone
        self.assertEqual(self.decide(cached=False)[0], 'compare')       # the prerequisites do not matter here

    def test_same_files_as_the_entry_judges_the_machine(self):
        for recheck in (None, 'passed', 'rejected', 'exit 2'):
            with self.subTest(recheck=recheck):
                self.assertEqual(self.decide(files=self.FILES, recheck=recheck), (
                    'forget', self.PRESENT + '; the output rebuilt here is file for file the cached entry, so the '
                                             'check that rejected it judged this machine, not the entry'))

    def test_other_files_need_the_same_check_on_the_rebuilt_output(self):
        other = 'e' * 64
        self.assertEqual(self.decide(files=other), (
            'recheck', self.PRESENT + '; a check rejected it here and the rebuilt output differs from it'))
        self.assertEqual(self.decide(files=other, recheck='passed'), (
            'replace', 'the check that rejected the cached entry accepts the output rebuilt here, which differs '
                       'from it (the reason)'))
        self.assertEqual(self.decide(files=other, recheck='rejected'), (
            'forget', self.PRESENT + '; the check that rejected it rejects the output rebuilt here as well, so it '
                                     'does not judge the entry'))
        for why in ('exit 2', 'exit -9', 'the check of base did not finish in 900 s', 'Passed', 'PASSED', ''):
            with self.subTest(why=why):
                self.assertEqual(self.decide(files=other, recheck=why), (
                    'keep', self.PRESENT + '; a check rejected it here, but that check could not judge the rebuilt '
                                           'output (%s), so the entry is not replaced' % why))

    def test_note_must_name_the_check_and_the_manifest(self):
        for change in ({'files_sha256': None}, {'check': None}, {'files_sha256': 'f' * 63}, {'check': 'C' * 64},
                       {'files_sha256': 5}, {'check': ['c' * 64]}):
            for files, recheck in ((None, None), ('e' * 64, 'passed')):
                with self.subTest(change=change, files=files):
                    self.assertEqual(self.decide(self.note(**change), files=files, recheck=recheck), (
                        'keep', self.PRESENT + '; the note of its rejection names no check and no file manifest'))
        without = self.note()
        del without['files_sha256'], without['check']
        self.assertEqual(self.decide(without, files='e' * 64, recheck='passed')[0], 'keep')

    def test_the_tag_must_still_name_the_rejected_entry(self):
        other = 'sha256:' + '2' * 64
        self.assertEqual(self.decide(digest=other, files='e' * 64, recheck='passed')[0], 'forget')
        self.assertEqual(PC.decide_replace(self.note(), self.KEY, None, {}, True, 'e' * 64, 'passed')[0], 'keep')
        self.assertEqual(PC.decide_replace(self.note(key='b' * 64), self.KEY, self.DIGEST, {}, True, 'e' * 64,
                                           'passed'), ('keep', self.PRESENT))

    def test_the_other_kinds_ignore_the_rebuilt_output(self):
        for files, recheck in ((None, None), (self.FILES, 'rejected'), ('e' * 64, 'exit 2')):
            with self.subTest(files=files):
                self.assertEqual(self.decide(self.note(kind='bundle'), files=files, recheck=recheck),
                                 ('replace', 'restore refused the cached entry (the reason)'))
                self.assertEqual(self.decide(self.note(kind='prerequisites'), files=files, recheck=recheck)[0],
                                 'replace')
                self.assertEqual(self.decide(self.note(kind='other'), files='e' * 64, recheck='passed'),
                                 ('keep', self.PRESENT))


class PackagesService(ServiceCase):
    """The toolchain's build packages in the record, in bundles, on restore and in the keys."""

    OTHER_ID = 'sha256:' + '9' * 64
    CHANGE = 'libc6-dev:amd64 2.41-12+deb13u4 -> 2.41-12+deb13u5'

    def record(self):
        return json.loads((self.root / 'status/toolchain.json').read_text())

    def notes(self):
        path = self.root / 'cache-state/refused.json'
        return json.loads(path.read_text()) if path.exists() else None

    def rebuilt_image(self):
        """The local image becomes a rebuild with one newer build package."""
        self.registry.toolchain, self.registry.probe_output = self.OTHER_ID, probe_text(**DRIFTED)

    def cached(self, name='base'):
        self.make(name)
        ref = self.service.push(name)
        self.set_aside(name)
        del self.messages[:]
        return ref

    def test_bundle_records_the_packages_it_was_built_with(self):
        self.make('base')
        self.service.push('base')
        meta = json.loads(self.bundle('base')[1]['meta.json'])
        self.assertEqual(meta['toolchain'], dict(ATTRIBUTE, image_id=TOOLCHAIN_ID))
        self.assertNotIn('build_packages', json.dumps(meta['key_inputs']))
        self.assertEqual(meta['key_inputs']['toolchain'], FINGERPRINT)

    def test_same_packages_restore_silently_in_every_mode(self):
        ref = self.cached()
        for mode in PC.PACKAGE_MODES[:2]:
            with self.subTest(mode=mode):
                self.assertEqual(self.new_service(packages=mode).restore('base'), ref)
                self.assertEqual(self.messages, ['provider-cache: restored base from %s (9 entries verified)' % ref])
                self.assertNotIn('toolchain_packages', json.loads((self.root / 'status/base.json').read_text()))
                self.set_aside('base')
                del self.messages[:]

    def test_report_mode_uses_a_bundle_of_other_packages_and_says_so(self):
        ref = self.cached()
        key = self.service.key('base')
        self.rebuilt_image()
        service = self.new_service()
        with mock.patch.object(PC.time, 'time', return_value=5000.2):
            self.assertEqual(service.restore('base'), ref)
        drifted = PC.packages_digest([line.replace('u4 amd64', 'u5 amd64') if line.startswith('libc6-dev') else line
                                      for line in BUILD_PACKAGES])
        detail = ('built with other toolchain build packages than the ones here (1): '
                  'libc6-dev:amd64 2.41-12+deb13u4 -> 2.41-12+deb13u5')
        self.assertEqual(self.messages, [
            'provider-cache: the toolchain image %s has other build packages than the recorded one: %s (1); '
            'outputs that were ok before are not pushed' % (self.OTHER_ID, self.CHANGE),
            'provider-cache: toolchain fingerprint %s (amd64, image %s, build packages %s)'
            % (FINGERPRINT, self.OTHER_ID, drifted),
            'provider-cache: restored base from %s (9 entries verified; %s)' % (ref, detail)])
        status = json.loads((self.root / 'status/base.json').read_text())
        self.assertEqual(status['toolchain_packages'], detail)
        self.assertEqual((status['restored_from'], status['cache_key']), (ref, key))
        record = self.record()
        self.assertEqual((record['fingerprint'], record['image_id'], record['build_packages_sha256'],
                          record['packages_sha256'], record['outputs_before']),
                         (FINGERPRINT, self.OTHER_ID, drifted, 'f' * 64, 5001))
        self.assertEqual(service.key('base'), key)            # the keys did not move
        self.assertEqual(self.new_service().key('base'), key)
        self.assertIsNone(self.notes())

    def test_enforce_mode_does_not_use_a_bundle_of_other_packages_and_judges_nothing(self):
        ref = self.cached()
        digest = self.registry.digests[ref]
        self.rebuilt_image()
        self.service = self.new_service(packages='enforce')
        self.assertEqual(self.refused(), (
            'base: the cached entry built with other toolchain build packages than the ones here (1): %s, and '
            'TAKX_PROTO_CACHE_PACKAGES=enforce does not use such an entry' % self.CHANGE))
        self.assertIsNone(self.notes())
        # The lane builds; the healthy entry of the other toolchain stays.
        self.make('base')
        os.utime(self.root / 'status/base.json', (2 ** 31, 2 ** 31))
        del self.messages[:]
        self.assertEqual(self.service.push('base'), ref)
        self.assertEqual((self.registry.published, self.registry.digests[ref]), (1, digest))
        self.assertEqual(self.messages, ['provider-cache: base is already cached under this key: ' + ref])

    def test_bundle_without_a_package_list_is_known_only_by_the_image_that_built_it(self):
        ref = self.cached()
        labels, content = self.bundle('base')
        meta = json.loads(content['meta.json'])
        del meta['toolchain']
        cases = (({}, 'unknown', 'records no toolchain package list (saved before it was recorded)'),
                 ({'adopted_from': {'toolchain_image_id': self.OTHER_ID}}, 'unknown',
                  'records no toolchain package list (saved before it was recorded, by toolchain image %s)'
                  % self.OTHER_ID),
                 ({'adopted_from': {'toolchain_image_id': TOOLCHAIN_ID}}, 'same', None),
                 ({'toolchain': {'image_id': TOOLCHAIN_ID}, 'adopted_from': {'toolchain_image_id': self.OTHER_ID}},
                  'same', None))
        for more, verdict, detail in cases:
            content['meta.json'] = json.dumps(dict(meta, **more)).encode()
            for mode in ('report', 'enforce'):
                del self.messages[:]
                self.service = self.new_service(packages=mode)
                with self.subTest(more=more, mode=mode):
                    if mode == 'enforce' and verdict != 'same':
                        self.assertEqual(self.refused(), 'base: the cached entry %s, and TAKX_PROTO_CACHE_PACKAGES='
                                                         'enforce does not use such an entry' % detail)
                        self.assertIsNone(self.notes())
                        continue
                    self.assertEqual(self.service.restore('base'), ref)
                    status = json.loads((self.root / 'status/base.json').read_text())
                    self.assertEqual(status.get('toolchain_packages'), detail)
                    self.assertEqual(self.messages, ['provider-cache: restored base from %s (9 entries verified%s)'
                                                     % (ref, '; ' + detail if detail else '')])
                    self.set_aside('base')

    def test_outputs_from_before_the_packages_changed_are_not_pushed(self):
        self.make('base')
        status = self.root / 'status/base.json'
        os.utime(status, (4000, 4000))
        self.rebuilt_image()
        for mode in ('report', 'enforce'):
            self.write_json('status/toolchain.json', toolchain_record())
            with self.subTest(mode=mode), mock.patch.object(PC.time, 'time', return_value=5000.2):
                with self.assertRaisesRegex(PC.CacheError, '^base: it was ok before the toolchain record was replaced'):
                    self.new_service(packages=mode).push('base')
            self.assertEqual((self.registry.published, self.record()['outputs_before']), (0, 5001))
        os.utime(status, (5001, 5001))
        self.new_service().push('base')
        meta = json.loads(self.bundle('base')[1]['meta.json'])
        self.assertEqual((meta['toolchain']['image_id'], meta['toolchain']['packages_sha256']), (self.OTHER_ID, 'f' * 64))
        self.assertIn('libc6-dev 2.41-12+deb13u5 amd64', meta['toolchain']['build_packages'])
        # The same packages in yet another image move nothing.
        self.registry.toolchain = 'sha256:' + '8' * 64
        with mock.patch.object(PC.time, 'time', return_value=9000.0):
            self.new_service().toolchain(local=True)
        self.assertEqual((self.record()['outputs_before'], self.record()['image_id']), (5001, 'sha256:' + '8' * 64))

    def test_key_mode_puts_the_packages_into_every_key(self):
        plain = {name: self.service.key(name) for name in ('base', 'side', 'top')}
        service = self.new_service(packages='key')
        keyed = PC.key_fingerprint(FINGERPRINT, ATTRIBUTE, 'key')
        self.assertEqual(service.toolchain(), (keyed, 'amd64'))
        self.assertEqual(self.registry.calls, [])               # the record has the list: nothing is probed
        self.assertEqual(service.explain('base')['toolchain'], keyed)
        for name in plain:
            self.assertNotEqual(service.key(name), plain[name])
        self.assertEqual({name: key for name, (key, _) in service.plain_keys().items()}, plain)
        self.assertEqual(self.new_service(packages='enforce').key('base'), plain['base'])
        # A bundle saved under the plain key is not what the keyed key names.
        self.cached()
        with self.assertRaisesRegex(PC.Skip, r'^nothing cached under this key \(reg.example/cache/base:%s\)$'
                                    % service.key('base')):
            service.restore('base')
        # Its own push and restore work under the keyed key.
        self.make('base')
        ref = service.push('base')
        self.assertEqual(ref, 'reg.example/cache/base:' + service.key('base'))
        self.set_aside('base')
        self.assertEqual(service.restore('base'), ref)

    def test_key_mode_refuses_other_packages_until_they_are_recorded_on_purpose(self):
        old = self.new_service(packages='key').key('base')
        self.rebuilt_image()
        service = self.new_service(packages='key')
        self.assertEqual(service.key('base'), old)              # reading the record asks nothing
        for action in (service.restore, service.push, service.migrate):
            self.make('base') if action == service.push else None
            with self.subTest(action=action.__name__), self.assertRaises(PC.CacheError) as caught:
                action('base')
            self.assertEqual(str(caught.exception), (
                'the local %s image (%s) has other build packages than the recorded toolchain: %s (1); with '
                'TAKX_PROTO_CACHE_PACKAGES=key that changes every key. Accept it with `provider_cache.py toolchain '
                '--record`' % (PC.TOOLCHAIN, self.OTHER_ID, self.CHANGE)))
        self.assertEqual(self.record(), toolchain_record())
        self.assertEqual(self.registry.published, 0)
        out = io.StringIO()
        recording = self.new_service(packages='key')
        with mock.patch.object(PC.time, 'time', return_value=5000.2):
            self.assertEqual(PC.main(['toolchain', '--record'], service=recording, out=out), 0)
        drifted = PC.packages_attribute(toolchain_block(), PC.parse_probe(probe_text(**DRIFTED)))
        self.assertEqual(out.getvalue(), PC.key_fingerprint(FINGERPRINT, drifted, 'key') + '\tamd64\n')
        self.assertEqual((self.record()['outputs_before'], self.record()['fingerprint']), (5001, FINGERPRINT))
        self.assertNotEqual(self.new_service(packages='key').key('base'), old)
        self.assertEqual(self.new_service().key('base'), self.service.key('base'))   # report keys never moved

    def test_keys_already_derived_follow_the_packages_the_local_check_accepted(self):
        service = self.new_service(packages='key')
        service.rerecord = True
        old = service.key('base')
        self.rebuilt_image()
        self.make('base')
        os.utime(self.root / 'status/base.json', (2 ** 31, 2 ** 31))
        ref = service.push('base')
        self.assertNotEqual(ref, 'reg.example/cache/base:' + old)
        self.assertEqual(ref, 'reg.example/cache/base:' + self.new_service(packages='key').key('base'))

    def test_record_without_a_package_list_is_completed_from_the_same_image(self):
        old = toolchain_record()
        for field in ('build_packages', 'build_packages_sha256', 'build_selection'):
            del old[field]
        self.write_json('status/toolchain.json', old)
        # Deriving a key needs no list unless the list is in the key.
        self.assertEqual(self.new_service().toolchain(), (FINGERPRINT, 'amd64'))
        self.assertEqual(self.new_service(packages='enforce').toolchain(), (FINGERPRINT, 'amd64'))
        self.assertEqual((self.registry.calls, self.record()), ([], old))
        for mode in PC.PACKAGE_MODES:
            self.write_json('status/toolchain.json', old)
            del self.registry.calls[:], self.messages[:]
            service = self.new_service(packages=mode)
            with self.subTest(mode=mode):
                self.assertEqual(service.toolchain(local=mode != 'key'),
                                 (PC.key_fingerprint(FINGERPRINT, ATTRIBUTE, mode), 'amd64'))
                self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)])
                self.assertEqual(PC.recorded_packages(toolchain_block(), self.record()), ATTRIBUTE)
                self.assertIsNone(self.record()['outputs_before'])   # the same whole list: nothing changed
                self.assertEqual(len(self.messages), 1)
        # Another whole list under a record without one counts as other packages.
        for mode in PC.PACKAGE_MODES:
            self.write_json('status/toolchain.json', dict(old, packages_sha256='0' * 64))
            del self.messages[:]
            service = self.new_service(packages=mode)
            told = 'the earlier record lists no build packages and the whole package list differs (1)'
            with self.subTest(other_list=mode), mock.patch.object(PC.time, 'time', return_value=5000.2):
                if mode == 'key':
                    with self.assertRaisesRegex(PC.CacheError, re.escape(told)):
                        service.toolchain(local=True)
                    self.assertIsNone(self.record()['outputs_before'])
                    continue
                service.toolchain(local=True)
                self.assertEqual(self.record()['outputs_before'], 5001)
                self.assertIn(told, self.messages[0])

    def test_another_selection_takes_the_list_again_without_calling_it_a_change(self):
        value = manifest()
        value['toolchain']['build_packages'] = {'sections': ['devel'], 'names': []}
        service = PC.ProviderCache(value, PC.WorkTree(self.root, self.repo), self.registry, 'reg.example/cache', '',
                                   self.messages.append)
        self.assertEqual(service.toolchain(local=True), (FINGERPRINT, 'amd64'))
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN), ('probe_toolchain', PC.TOOLCHAIN)])
        record = self.record()
        self.assertEqual(record['build_packages'], ['gcc-14 14.2.0-19 amd64', 'linux-libc-dev 6.12.117-1 all'])
        self.assertEqual((record['build_selection'], record['outputs_before']),
                         ({'sections': ['devel'], 'names': []}, None))
        self.assertEqual(len(self.messages), 1)

    def test_unknown_mode_is_refused_before_anything_is_done(self):
        for mode in ('', 'Key', 'off', 'strict', None):
            with self.subTest(mode=mode), self.assertRaisesRegex(
                    PC.CacheError, '^TAKX_PROTO_CACHE_PACKAGES is %s, not one of report, enforce, key$'
                    % re.escape(repr(mode))):
                self.new_service(packages=mode)
        for mode in PC.PACKAGE_MODES:
            self.assertEqual(self.new_service(packages=mode).packages, mode)
        self.assertEqual(self.new_service().packages, 'report')
        self.assertEqual(self.registry.calls, [])

    def test_mode_comes_from_the_environment(self):
        for value, expected in ((None, 'report'), ('', 'report'), ('enforce', 'enforce'), ('key', 'key')):
            environ = {'TAKX_PROTO_ROOT': str(self.root)}
            if value is not None:
                environ['TAKX_PROTO_CACHE_PACKAGES'] = value
            with self.subTest(value=value):
                self.assertEqual(PC.build_service(environ).packages, expected)
        err = io.StringIO()
        with mock.patch.dict(os.environ, {'TAKX_PROTO_CACHE_PACKAGES': 'sometimes', 'TAKX_PROTO_ROOT': str(self.root)}), \
                mock.patch.object(PC.sys, 'stderr', err):
            self.assertEqual(PC.main(['key', 'proj']), 1)
        self.assertEqual(err.getvalue(), "provider-cache: TAKX_PROTO_CACHE_PACKAGES is 'sometimes', not one of "
                                         "report, enforce, key\n")

    def test_packages_command_prints_the_record_and_the_fingerprint_of_each_mode(self):
        out = io.StringIO()
        self.assertEqual(PC.main(['toolchain', '--packages'], service=self.new_service(packages='enforce'), out=out), 0)
        self.assertEqual(json.loads(out.getvalue()), dict(
            ATTRIBUTE, image_id=TOOLCHAIN_ID, arch='amd64', mode='enforce', fingerprints={
                'report': FINGERPRINT, 'enforce': FINGERPRINT, 'key': PC.key_fingerprint(FINGERPRINT, ATTRIBUTE, 'key')}))
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN)])   # the local image is the recorded one

    def test_index_names_the_mode_of_its_keys(self):
        index = Path(self.tmp.name) / 'index.tsv'
        for mode in PC.PACKAGE_MODES:
            service = self.new_service(packages=mode)
            self.assertEqual(PC.main(['status', '--write-index', str(index)], service=service, out=io.StringIO()), 0)
            lines = index.read_text().splitlines()
            self.assertEqual(lines[7:10], ['# toolchain fingerprint ' + PC.key_fingerprint(FINGERPRINT, ATTRIBUTE, mode),
                                           '# architecture amd64', '# toolchain packages mode ' + mode])


class PackagesMigrate(ServiceCase):
    """migrate towards keys that name the build packages, and what it records in the others."""

    OTHER_ID = 'sha256:' + '9' * 64
    legacy_push = Migrate.legacy_push

    def setUp(self):
        super().setUp()
        self.top = self.make('base')
        self.good = snapshot(self.top)
        self.old_ref = self.legacy_push('base')
        del self.registry.calls[:]
        del self.messages[:]

    def keyed(self):
        return self.new_service(packages='key')

    def test_bundle_under_the_plain_key_becomes_addressable_under_the_keyed_key(self):
        plain_ref = self.service.push('base')
        plain_digest = self.registry.digests[plain_ref]
        old_labels, old_content = copy.deepcopy(self.registry.images[plain_ref])
        del self.registry.calls[:], self.messages[:]
        service = self.keyed()
        key = service.key('base')
        ref = service.migrate('base')
        self.assertEqual(ref, 'reg.example/cache/base:' + key)
        # The plain key is tried first; the schema-1 key is not even asked for.
        self.assertEqual([call for call in self.registry.calls if call[0] in ('probe', 'fetch', 'adopt', 'forget')], [
            ('probe', ref), ('probe', plain_ref), ('fetch', plain_ref), ('adopt', plain_ref, ref),
            ('forget', plain_ref), ('probe', ref)])
        labels, content = self.registry.images[ref]
        self.assertEqual(labels, dict(old_labels, **{'dev.uds.takx.key': key}))
        self.assertEqual({name for name in content if content[name] != old_content[name]}, {'meta.json'})
        meta, old_meta = json.loads(content['meta.json']), json.loads(old_content['meta.json'])
        self.assertEqual(meta, dict(old_meta, key=key, key_inputs=service.explain('base'), rekeyed_from={
            'schema': 2, 'key': old_meta['key'], 'ref': plain_ref, 'digest': plain_digest}))
        self.assertEqual(meta['toolchain'], dict(ATTRIBUTE, image_id=TOOLCHAIN_ID))
        self.assertNotIn('adopted_from', meta)
        self.assertEqual(self.registry.images[plain_ref], (old_labels, old_content))   # the source is untouched
        tar_sha = hashlib.sha256(content['base.tar']).hexdigest()
        self.assertEqual(self.messages, [
            'provider-cache: base is now also %s (was %s under the key without the build packages, 9 entries '
            'verified, tar sha256 %s; built with the build packages of the toolchain here)' % (ref, plain_ref, tar_sha)])
        self.set_aside('base')
        self.assertEqual(service.restore('base'), ref)
        self.assertEqual(snapshot(self.top), self.good)
        self.assertEqual(self.leftovers(), [])

    def test_bundle_of_other_or_unknown_packages_is_not_given_a_keyed_key(self):
        plain_ref = self.service.push('base')
        labels, content = self.registry.images[plain_ref]
        meta = json.loads(content['meta.json'])
        other = [line.replace('14.2.0-19', '14.2.0-20') for line in BUILD_PACKAGES]
        cases = (({'toolchain': {'build_packages': other, 'build_packages_sha256': PC.packages_digest(other)}},
                  'built with other toolchain build packages than the ones here (1): gcc-14:amd64 14.2.0-20 -> 14.2.0-19'),
                 ({'toolchain': None}, 'records no toolchain package list (saved before it was recorded)'),
                 ({'toolchain': {'image_id': self.OTHER_ID}},
                  'records no toolchain package list (saved before it was recorded, by toolchain image %s)'
                  % self.OTHER_ID))
        for change, detail in cases:
            content['meta.json'] = json.dumps(dict(meta, **change)).encode()
            del self.registry.calls[:]
            service = self.keyed()
            with self.subTest(change=change), self.assertRaises(PC.CacheError) as caught:
                service.migrate('base')
            self.assertEqual(str(caught.exception), 'base: the bundle under %s %s, so it cannot be addressed by a key '
                                                    'that names the build packages here' % (plain_ref, detail))
            self.assertNotIn('adopt', [call[0] for call in self.registry.calls])
            self.assertEqual((self.registry.kept, self.leftovers()), ([], []))
            self.assertNotIn('reg.example/cache/base:' + service.key('base'), self.registry.images)

    def test_keyed_key_takes_a_schema_1_bundle_only_from_the_image_that_is_here(self):
        service = self.keyed()
        ref = service.migrate('base')
        meta = json.loads(self.registry.images[ref][1]['meta.json'])
        self.assertEqual(meta['toolchain'], dict(ATTRIBUTE, image_id=TOOLCHAIN_ID))
        self.assertEqual(meta['adopted_from']['toolchain_image_id'], TOOLCHAIN_ID)
        self.assertNotIn('rekeyed_from', meta)
        self.assertIn('under the schema-1 key', self.messages[-1])
        # Built by another image than the local one: the same packages cannot be claimed.
        del self.registry.images[ref]
        self.registry.toolchain = self.OTHER_ID
        self.write_json('status/toolchain.json', toolchain_record(image_id=self.OTHER_ID))
        with self.assertRaisesRegex(PC.CacheError, 'records no toolchain package list .*by toolchain image %s.*'
                                    'cannot be addressed by a key that names the build packages here' % TOOLCHAIN_ID):
            self.keyed().migrate('base')
        self.assertNotIn(ref, self.registry.images)
        # The other modes adopt it all the same, and claim nothing about its packages.
        plain = self.new_service().migrate('base')
        meta = json.loads(self.registry.images[plain][1]['meta.json'])
        self.assertNotIn('toolchain', meta)
        self.assertIn('; records no toolchain package list (saved before it was recorded, by toolchain image %s))'
                      % TOOLCHAIN_ID, self.messages[-1])
        self.set_aside('base')
        self.assertEqual(self.new_service().restore('base'), plain)
        self.assertEqual(json.loads((self.root / 'status/base.json').read_text())['toolchain_packages'],
                         'records no toolchain package list (saved before it was recorded, by toolchain image %s)'
                         % TOOLCHAIN_ID)

    def test_nothing_under_either_earlier_key_is_a_clean_skip_and_errors_are_refusals(self):
        self.registry.images.clear()
        service = self.keyed()
        plain_ref = 'reg.example/cache/base:' + self.service.key('base')
        with self.assertRaises(PC.Skip) as caught:
            service.migrate('base')
        self.assertEqual(str(caught.exception), 'nothing cached under the key without the build packages or the '
                                                'schema-1 key (%s)' % self.old_ref)
        answers = iter(['absent', 'error'])
        with mock.patch.object(self.registry, 'probe', side_effect=lambda ref: next(answers)), \
                self.assertRaisesRegex(PC.CacheError, '^base: the registry could not be queried for %s$'
                                       % re.escape(plain_ref)):
            self.keyed().migrate('base')
        answers = iter(['absent', 'absent', 'unauthorized'])
        with mock.patch.object(self.registry, 'probe', side_effect=lambda ref: next(answers)), \
                self.assertRaisesRegex(PC.CacheError, '^base: the registry could not be queried for %s$'
                                       % re.escape(self.old_ref)):
            self.keyed().migrate('base')


class Unpublished(ServiceCase):
    """A provider that providers.json keeps out of the cache ('unpublished')."""

    WHY = 'CDDL-only reference JAR, build-time only'

    def setUp(self):
        super().setUp()
        value = manifest()
        value['providers']['side']['unpublished'] = self.WHY
        self.service = PC.ProviderCache(value, PC.WorkTree(self.root, self.repo), self.registry, 'reg.example/cache',
                                        '', self.messages.append)

    def test_it_is_neither_saved_nor_restored_nor_migrated(self):
        self.make('side')
        for action in (self.service.push, self.service.restore, self.service.migrate):
            with self.subTest(action=action.__name__), self.assertRaises(PC.Skip) as caught:
                action('side')
            self.assertEqual(str(caught.exception), 'not kept in the provider cache (%s)' % self.WHY)
        self.assertEqual((self.registry.calls, self.registry.published, self.messages), ([], 0, []))
        # Also when an entry of an earlier time is still under its key.
        ref = self.service.ref('side')
        self.registry.store(ref, {}, {})
        self.set_aside('side')
        with self.assertRaises(PC.Skip):
            self.service.restore('side')
        self.assertFalse(os.path.lexists(self.root / 'P/side'))
        self.assertEqual(self.registry.calls, [])
        # The other providers are not affected, and the key of the unpublished one is still derived.
        self.make('base')
        self.assertEqual(self.service.push('base'), 'reg.example/cache/base:' + self.service.key('base'))
        self.assertEqual(self.service.key('side'), self.new_service().key('side'))

    def test_command_line_skips_it_cleanly_and_the_index_leaves_it_out(self):
        self.make('side')
        self.make('base')
        err = io.StringIO()
        with mock.patch.object(PC.sys, 'stderr', err):
            self.assertEqual(PC.main(['push', 'side'], service=self.service, out=io.StringIO()), 3)
            self.assertEqual(PC.main(['push', 'base', 'side'], service=self.service, out=io.StringIO()), 3)
        self.assertEqual(err.getvalue().splitlines()[0],
                         'provider-cache: push side skipped: not kept in the provider cache (%s)' % self.WHY)
        self.registry.state = 'present'
        index, out = Path(self.tmp.name) / 'index.tsv', io.StringIO()
        self.assertEqual(PC.main(['status', '--remote', '--write-index', str(index)], service=self.service, out=out), 0)
        rows = {line.split('\t')[0]: line.split('\t')[3] for line in out.getvalue().splitlines()}
        self.assertEqual(rows, {'base': 'present', 'side': 'unpublished', 'top': 'present'})
        listed = [line.split('\t')[0] for line in index.read_text().splitlines() if not line.startswith('#')]
        self.assertEqual(listed, ['base', 'top'])
        self.assertNotIn(('probe', self.service.ref('side')), self.registry.calls)

    def test_the_wizard_reference_jar_is_the_one_unpublished_provider(self):
        real = PC.validate_manifest(json.loads((HERE / 'providers.json').read_text()))
        marked = {name: entry['unpublished'] for name, entry in real['providers'].items() if 'unpublished' in entry}
        self.assertEqual(list(marked), ['ui-wizard-reference'])
        self.assertIn('CDDL-only', marked['ui-wizard-reference'])
        # Nothing is built from it, so no cached provider is bound to an output the cache does not hold.
        self.assertEqual([name for name, entry in real['providers'].items()
                          if 'ui-wizard-reference' in entry['prerequisites']], [])
        index = (HERE / 'cache-index.tsv').read_text()
        self.assertNotIn('wizard', index)
        self.assertEqual({line.split('\t')[0] for line in index.splitlines() if not line.startswith('#')},
                         EXPECTED - {'ui-wizard-reference'})

    def test_real_selection_takes_what_feeds_a_build_and_leaves_what_only_fetches(self):
        real = json.loads((HERE / 'providers.json').read_text())['toolchain']
        feeding = ['gcc-14', 'g++-14', 'cpp-14', 'binutils', 'binutils-x86-64-linux-gnu', 'libc6-dev', 'libc6',
                   'libc-dev-bin', 'linux-libc-dev', 'libstdc++-14-dev', 'libgcc-14-dev', 'libgcc-s1', 'libstdc++6',
                   'libcrypt-dev', 'libegl-dev', 'libgles-dev', 'libgl-dev', 'libopengl-dev', 'libx11-dev', 'make',
                   'cmake', 'swig', 'bison', 'flex', 'm4', 'autoconf', 'libtool', 'perl', 'perl-base', 'python3',
                   'python3.13', 'libpython3.13-stdlib', 'tcl8.6', 'ant', 'patch', 'libasan8', 'libubsan1', 'libgmp10']
        fetching = ['openssl', 'libssl3t64', 'openssl-provider-legacy', 'curl', 'libcurl4t64', 'wget', 'git', 'gnupg',
                    'gpg', 'ca-certificates', 'tzdata', 'libkrb5-3', 'libgssapi-krb5-2', 'libnghttp2-14', 'rsync',
                    'apt', 'libc-bin', 'python3-pip', 'libsqlite3-0', 'zlib1g', 'libexpat1']
        sections = {'gcc-14': 'devel', 'g++-14': 'devel', 'binutils': 'devel', 'binutils-x86-64-linux-gnu': 'devel',
                    'libc6-dev': 'libdevel', 'libc-dev-bin': 'libdevel', 'linux-libc-dev': 'devel',
                    'libstdc++-14-dev': 'libdevel', 'libgcc-14-dev': 'libdevel', 'libcrypt-dev': 'libdevel',
                    'libx11-dev': 'libdevel', 'make': 'devel', 'cmake': 'devel', 'bison': 'devel', 'flex': 'devel',
                    'autoconf': 'devel', 'libtool': 'devel', 'cpp-14': 'interpreters', 'swig': 'interpreters',
                    'm4': 'interpreters', 'perl': 'perl', 'perl-base': 'perl', 'python3': 'python',
                    'python3.13': 'python', 'libpython3.13-stdlib': 'python', 'tcl8.6': 'interpreters', 'ant': 'java',
                    'patch': 'vcs', 'openssl': 'utils', 'curl': 'web', 'wget': 'web', 'git': 'vcs', 'rsync': 'net',
                    'python3-pip': 'python', 'apt': 'admin', 'tzdata': 'localization'}
        rows = [('ii ', name, '1', 'amd64', sections.get(name, 'libs')) for name in feeding + fetching]
        self.assertEqual(PC.build_packages(real, rows), sorted('%s 1 amd64' % name for name in feeding))


class CheckRejection(ServiceCase):
    """What the push after a rebuild does with an entry a post-restore check rejected."""

    document, checked, notes = PostRestoreCheck.document, PostRestoreCheck.checked, PostRestoreCheck.notes

    def setUp(self):
        super().setUp()
        self.runner = FakeRunner(self.root)
        self.service = self.checked()
        self.top = self.make('base')
        self.good = snapshot(self.top)
        self.ref = self.service.push('base')
        self.digest = self.registry.digests[self.ref]
        self.set_aside('base')
        del self.messages[:]

    def reject(self, name='base'):
        self.runner.code = 1
        with self.assertRaises(PC.Rejected):
            self.service.restore(name)
        self.runner.code = 0
        del self.runner.calls[:], self.messages[:], self.registry.calls[:]
        return self.notes()[self.ref]

    def unchanged(self, note=True):
        self.assertEqual((self.registry.published, self.registry.digests[self.ref]), (1, self.digest))
        self.assertNotIn('publish', [call[0] for call in self.registry.calls])
        self.assertEqual(self.notes() == {} or self.notes() is None, not note)
        self.assertEqual(self.leftovers(), [])

    def rebuild_identical(self):
        aside = [item for item in sorted(os.listdir(self.tmp.name)) if item.startswith('aside-base-')][0]
        shutil.copytree(Path(self.tmp.name) / aside, self.root / 'P/base', symlinks=True)
        self.write_json('status/base.json', {'stage': 'base', 'status': 'ok', 'outputs': [], 'seconds': 1, 'notes': 'n'})

    def test_rebuilt_output_that_is_the_entry_file_for_file_keeps_it_and_drops_the_note(self):
        self.reject()
        self.rebuild_identical()
        self.assertEqual(snapshot(self.root / 'P/base'), self.good)
        self.assertEqual(self.service.push('base'), self.ref)
        self.unchanged(note=False)
        self.assertEqual(self.runner.calls, [])          # the check is not even asked
        self.assertEqual(self.messages, [
            'provider-cache: base is already cached under this key; the output rebuilt here is file for file the '
            'cached entry, so the check that rejected it judged this machine, not the entry: ' + self.ref])
        # Without the note the next push is the plain "already cached".
        del self.messages[:]
        self.service.push('base')
        self.assertEqual(self.messages, ['provider-cache: base is already cached under this key: ' + self.ref])

    def changed(self, change):
        self.reject()
        self.rebuild_identical()
        change(self.root / 'P/base')
        self.service.push('base')
        self.assertEqual(len(self.runner.calls), 1)   # so the check is asked, and it accepts
        self.assertEqual(self.registry.published, 2)
        self.assertNotEqual(self.registry.digests[self.ref], self.digest)

    def test_a_single_changed_mode_is_another_output(self):
        self.changed(lambda top: os.chmod(top / 'install/tool', 0o700))

    def test_a_single_changed_byte_is_another_output(self):
        def flip(top):
            data = bytearray((top / 'install/lib/lib.a').read_bytes())
            data[-1] ^= 1
            (top / 'install/lib/lib.a').write_bytes(bytes(data))
        self.changed(flip)

    def test_a_single_added_file_is_another_output(self):
        self.changed(lambda top: (top / 'empty/new').write_text(''))

    def test_a_single_retargeted_link_is_another_output(self):
        def retarget(top):
            os.unlink(top / 'install/tool-1')
            os.symlink('lib', top / 'install/tool-1')
        self.changed(retarget)

    def test_check_that_rejects_the_rebuilt_output_too_keeps_the_entry_and_drops_the_note(self):
        self.reject()
        self.make('base')
        self.runner.code = 1
        self.assertEqual(self.service.push('base'), self.ref)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertEqual(self.runner.seen[-1]['tree'], sorted(os.listdir(self.root / 'P/base')))  # the rebuilt tree
        self.unchanged(note=False)
        self.assertEqual(self.messages, [
            'provider-cache: base is already cached under this key; the check that rejected it rejects the output '
            'rebuilt here as well, so it does not judge the entry: ' + self.ref])
        self.assertEqual(self.service.tree.status_of('base'), 'ok')   # the rebuilt output is not touched
        self.assertTrue((self.root / 'P/base/complete.json').is_file())

    def test_check_that_cannot_run_on_the_rebuilt_output_keeps_the_entry_and_the_note(self):
        note = self.reject()
        self.make('base')
        for code, why in ((2, 'exit 2'), (125, 'exit 125'), (-9, 'exit -9'), (None, 'exit None'),
                          (CacheErrorFromRunner(), 'the check of base did not finish in 900 s')):
            self.runner.code = code
            del self.messages[:], self.runner.calls[:]
            with self.subTest(code=code):
                self.assertEqual(self.service.push('base'), self.ref)
                self.assertEqual(len(self.runner.calls), 1)
                self.unchanged(note=True)
                self.assertEqual(self.notes()[self.ref], note)
                self.assertEqual(self.messages, [
                    'provider-cache: base is already cached under this key; a check rejected it here, but that '
                    'check could not judge the rebuilt output (%s), so the entry is not replaced: %s'
                    % (why, self.ref)])
        # Once it can run and accepts, the same note still replaces the entry.
        self.runner.code = 0
        self.service.push('base')
        self.assertEqual(self.registry.published, 2)
        self.assertEqual(self.notes(), {})

    def test_push_that_cannot_run_the_same_check_keeps_the_entry_and_the_note(self):
        note = self.reject()
        self.make('base')
        other = self.document()
        other['providers']['base']['verify']['argv'][-1] = 'test_other.py'
        changed = PC.ProviderCache(other, PC.WorkTree(self.root, self.repo), self.registry, 'reg.example/cache', '',
                                   self.messages.append, runner=self.runner)
        undeclared = self.new_service(runner=self.runner)
        for what, service in (('switched off', self.checked(verify=False)), ('no runner', self.checked(runner=None)),
                              ('another check', changed), ('no check declared', undeclared)):
            del self.messages[:]
            with self.subTest(what=what):
                self.assertEqual(service.key('base'), self.service.key('base'))
                self.assertEqual(service.push('base'), self.ref)
                self.assertEqual(self.runner.calls, [])
                self.unchanged(note=True)
                self.assertEqual(self.notes()[self.ref], note)
                self.assertEqual(self.messages, [
                    'provider-cache: base is already cached under this key; a check rejected it here, but that '
                    'check could not judge the rebuilt output (it is not among the checks this push can run), so '
                    'the entry is not replaced: ' + self.ref])

    def test_note_without_check_or_manifest_never_replaces(self):
        note = self.reject()
        self.make('base')
        for field in ('check', 'files_sha256'):
            self.service.tree.note_refusal(self.ref, {key: value for key, value in note.items() if key != field})
            del self.messages[:]
            with self.subTest(field=field):
                self.service.push('base')
                self.unchanged(note=True)
                self.assertEqual(self.runner.calls, [])
                self.assertEqual(self.messages, ['provider-cache: base is already cached under this key; the note '
                                                 'of its rejection names no check and no file manifest: ' + self.ref])

    def test_entry_that_moved_since_is_not_judged_by_the_old_rejection(self):
        self.reject()
        self.make('base')
        self.registry.digests[self.ref] = 'sha256:' + '5' * 64
        with mock.patch.object(self.service, '_files_digest', side_effect=AssertionError('not compared')):
            self.service.push('base')
        self.assertEqual((self.registry.published, self.runner.calls, self.notes()), (1, [], {}))

    def test_output_that_changes_after_the_comparison_is_not_pushed(self):
        note = self.reject()
        self.make('base')
        with mock.patch.object(self.service, '_files_digest', return_value='0' * 64), \
                self.assertRaisesRegex(PC.CacheError, '^base changed after it was compared with the cached entry$'):
            self.service.push('base')
        self.unchanged(note=True)
        self.assertEqual(self.notes()[self.ref], note)
        self.assertEqual(len(self.runner.calls), 1)

    def test_compared_digest_is_the_one_a_bundle_records(self):
        self.reject()
        self.make('base')
        compared = self.service._files_digest('base')
        self.service.push('base')
        content = self.bundle('base')[1]
        self.assertEqual(compared, hashlib.sha256(content['files.json']).hexdigest())
        self.assertEqual(compared, json.loads(content['meta.json'])['files_sha256'])

    def test_integrity_refusal_replaces_without_asking_any_check(self):
        labels, content = self.bundle('base')
        data = bytearray(content['base.tar'])
        data[700] ^= 1
        content['base.tar'] = bytes(data)
        with self.assertRaises(PC.CacheError) as caught:
            self.service.restore('base')
        self.assertNotIsInstance(caught.exception, PC.Rejected)
        note = self.notes()[self.ref]
        self.assertEqual(note['kind'], 'bundle')
        self.assertNotIn('check', note)
        self.assertNotIn('files_sha256', note)
        self.assertEqual(self.runner.calls, [])   # the tree never got as far as the check
        self.make('base')
        self.runner.code = 1                       # whatever a check would say
        with mock.patch.object(self.service, '_files_digest', side_effect=AssertionError('not compared')):
            self.service.push('base')
        self.assertEqual((self.registry.published, self.runner.calls, self.notes()), (2, [], {}))

    def test_check_failure_that_is_no_verdict_notes_nothing(self):
        for code in (2, 124, 137, CacheErrorFromRunner()):
            self.runner.code = code
            with self.subTest(code=code), self.assertRaises(PC.CacheError) as caught:
                self.service.restore('base')
            self.assertNotIsInstance(caught.exception, PC.Rejected)
            self.assertIsNone(self.notes())
        self.make('base')
        self.runner.code = 0
        del self.runner.calls[:]
        self.service.push('base')
        self.assertEqual((self.registry.published, self.registry.digests[self.ref], self.runner.calls),
                         (1, self.digest, []))

    def test_recheck_writes_below_logs_and_answers_in_three_words(self):
        shutil.rmtree(self.root / 'logs', ignore_errors=True)
        self.make('base')
        identity = self.service._checks('base')[0][1]
        for code, answer in ((0, 'passed'), (1, 'rejected'), (3, 'exit 3')):
            self.runner.code = code
            self.assertEqual(self.service._recheck('base', identity), answer)
        self.assertTrue(self.runner.seen[0]['logs'])
        self.assertEqual(self.runner.calls[0][3], str(self.root / 'logs/base-verify.log'))
        self.assertEqual(self.service._recheck('base', '0' * 64), 'it is not among the checks this push can run')
        self.assertEqual(self.service._recheck('side', identity), 'it is not among the checks this push can run')


class LaneCheckRejection(ServiceCase):
    """The same for a check the lane brings: push needs the lane's words again."""

    document, checked, notes, cli = (LaneSuppliedCheck.document, LaneSuppliedCheck.checked, LaneSuppliedCheck.notes,
                                     LaneSuppliedCheck.cli)

    def setUp(self):
        super().setUp()
        self.events = []
        self.runner = OrderedRunner(self.root, self.events)
        self.lane = FakeLaneCheck(self.root, events=self.events)
        self.service = self.checked()
        self.make('side')
        self.side = self.service.push('side')
        self.set_aside('side')
        del self.messages[:]

    def reject(self):
        self.lane.code = 1
        with self.assertRaises(PC.Rejected):
            self.service.restore('side')
        self.lane.code = 0
        del self.lane.calls[:], self.messages[:]
        return self.notes()[self.side]

    def test_push_with_the_lanes_words_replaces_what_the_lane_rejected(self):
        note = self.reject()
        digest = self.registry.digests[self.side]
        self.assertEqual((note['kind'], note['check']), ('check', PC.check_identity('lane', self.lane.command)))
        published = self.registry.published
        self.make('side')
        self.service.push('side')
        self.assertEqual(self.lane.calls, [('side', str(self.root / 'logs/side-verify.log'))])
        self.assertEqual(self.registry.published, published + 1)
        self.assertNotEqual(self.registry.digests[self.side], digest)
        self.assertEqual(self.notes(), {})

    def test_push_without_the_lanes_words_or_with_others_keeps_the_entry(self):
        note = self.reject()
        digest, published = self.registry.digests[self.side], self.registry.published
        self.make('side')
        other = FakeLaneCheck(self.root, command=('lane-check', 'other'))
        for what, check in (('none', None), ('other words', other)):
            self.service.lane_check = check
            del self.messages[:]
            with self.subTest(what=what):
                self.service.push('side')
                self.assertEqual((self.registry.published, self.registry.digests[self.side]), (published, digest))
                self.assertEqual(self.notes()[self.side], note)
                self.assertEqual(other.calls, [])
                self.assertIn('it is not among the checks this push can run', self.messages[0])

    def test_command_line_hands_the_lanes_command_to_push(self):
        program = 'exit "$1"'
        code, text, service = self.cli('restore', 'side', '--verify-with', '/bin/sh', '-c', program, 'sh', '1')
        self.assertEqual(code, 1)
        note = self.notes()[self.side]
        self.assertEqual(note['check'], PC.check_identity('lane', ['/bin/sh', '-c', program, 'sh', '1']))
        digest, published = self.registry.digests[self.side], self.registry.published
        self.make('side')
        # The same words reject the rebuilt output as well: the entry stays, the note goes.
        code, text, service = self.cli('push', 'side', '--verify-with', '/bin/sh', '-c', program, 'sh', '1')
        self.assertEqual((code, text), (0, ''))
        self.assertEqual(service.lane_check.command, ['/bin/sh', '-c', program, 'sh', '1'])
        self.assertEqual((self.registry.published, self.registry.digests[self.side], self.notes()),
                         (published, digest, {}))
        self.assertIn("\n===== provider-cache restore side: the lane's own check (/bin/sh, 5 words)\n",
                      (self.root / 'logs/side-verify.log').read_text())


if __name__ == '__main__':
    unittest.main()
