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
TOOLCHAIN_ID = 'sha256:' + '1' * 64


def manifest():
    """Two recipes, three providers: base <- top, and an unrelated side."""
    def provider(recipe, deps, script):
        return {'lane': 'lane1.sh', 'recipe': recipe, 'prerequisites': deps,
                'locks': [recipe + '/inputs.lock.json'], 'fetched': [{'consumer': recipe.split('/')[1]}],
                'marker': 'complete.json',
                'invocation': {'env': [], 'argv': ['python3', '{repo}/' + recipe + '/' + script,
                                                   '--output', '{root}/P/x']}}
    return {'schema': 1, 'variables': {'jdk': '/jdk'}, 'recipe_exclude': ['__pycache__', '*.pyc', 'README*', 'test_*'],
            'recipes': {'takx/one': {'uses': []}, 'takx/two': {'uses': ['takx/one', 'takx/shared.py']}},
            'providers': {'base': provider('takx/one', [], 'build.py'),
                          'top': provider('takx/two', ['base'], 'build.py'),
                          'side': provider('takx/one', [], 'other.py')}}


def facts(**changes):
    value = {'recipe_files': {'takx/one/build.py': 'a' * 64}, 'locks': {'l': {'sha256': 'b' * 64, 'digests': []}},
             'fetched': [{'kind': 'lock-entry', 'name': 'n', 'digests': {'expected_sha256': 'c' * 64}}],
             'toolchain': TOOLCHAIN_ID, 'root': '/var/lib/takx-proto', 'repo': '/repo'}
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
            'toolchain': facts(toolchain='sha256:' + '2' * 64),
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
                ('marker', lambda m: m['providers']['base'].update(marker='done.json'))):
            document = manifest()
            edit(document)
            with self.subTest(what=what):
                self.assertNotEqual(self.keys(document)['base'], reference['base'])
        document = PC.key_document(manifest(), 'base', facts(), {})
        self.assertNotEqual(PC.derive_key(dict(document, schema=2)), PC.derive_key(document))

    def test_key_needs_every_identity_and_every_prerequisite_key(self):
        for field in ('toolchain', 'root', 'repo'):
            with self.subTest(field=field), self.assertRaises(PC.CacheError):
                PC.key_document(manifest(), 'base', facts(**{field: ''}), {})
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
                ('schema', lambda m: m.update(schema=2)),
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
                    meta={'schema': 1, 'provider': 'p', 'key': key, 'root': '/r', 'tar_sha256': tar,
                          'files_sha256': files, 'prerequisite_markers': {'d': 'e' * 64}},
                    status={'stage': 'p', 'status': 'ok'},
                    labels={'dev.uds.takx.provider': 'p', 'dev.uds.takx.key': key, 'dev.uds.takx.tar-sha256': tar},
                    tar_sha256=tar, recorded_tar_sha256=tar, files_sha256=files, local_markers={'d': 'e' * 64})
        PC.check_bundle(**good)
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
            'meta schema': dict(meta=dict(good['meta'], schema=2)),
            'files': dict(files_sha256='0' * 64),
            'status failed': dict(status={'stage': 'p', 'status': 'failed'}),
            'status other': dict(status={'stage': 'q', 'status': 'ok'}),
            'prerequisite': dict(local_markers={'d': '0' * 64}),
            'not a dict': dict(meta=[]),
        }
        for what, change in bad.items():
            with self.subTest(what=what), self.assertRaises(PC.CacheError):
                PC.check_bundle(**dict(good, **change))

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


def chain():
    """a <- b <- c <- d, a diamond a <- (b, e) <- f, and an unrelated z."""
    def provider(deps):
        return {'lane': 'lane1.sh', 'recipe': 'takx/one', 'prerequisites': deps, 'locks': [], 'fetched': [],
                'marker': 'complete.json', 'invocation': {'env': [], 'argv': ['build']}}
    return {'schema': 1, 'variables': {}, 'recipe_exclude': [], 'recipes': {'takx/one': {'uses': []}},
            'providers': {'d': provider(['c']), 'c': provider(['b']), 'b': provider(['a']), 'a': provider([]),
                          'e': provider(['a']), 'f': provider(['e', 'b']), 'z': provider([])}}


# Keys of manifest() with facts() for every provider. They pin the canonical
# form: a change here gives every cached provider a new name, so it must be
# deliberate (and then SCHEMA should change with it).
GOLDEN = {
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
            'toolchain': facts(toolchain='sha256:' + '1' * 63 + '2'),
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
        self.assertEqual(self.keys(document), GOLDEN)

    def test_schema_version_is_part_of_the_key(self):
        document = dict(manifest(), schema=2)
        self.assertEqual(PC.key_document(document, 'base', facts(), {})['schema'], 2)
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
            'schema': 1, 'provider': 'top', 'recipe': 'takx/two', 'recipe_files': facts()['recipe_files'],
            'invocation_sha256': PC.sha256_bytes(PC.canonical({
                'invocation': manifest()['providers']['top']['invocation'], 'variables': {'jdk': '/jdk'}})),
            'marker': 'complete.json', 'locks': facts()['locks'], 'fetched': facts()['fetched'],
            'toolchain': TOOLCHAIN_ID, 'root': '/var/lib/takx-proto', 'repo': '/repo',
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
        self.calls, self.fetched = [], []

    def available(self):
        return self.docker

    def image_id(self, image):
        self.calls.append(('image_id', image))
        return self.toolchain

    def probe(self, ref):
        self.calls.append(('probe', ref))
        if self.state:
            return self.state
        if not self.logged_in:
            return 'unauthorized'
        return 'present' if ref in self.images else 'absent'

    def publish(self, ref, context, files, labels):
        self.calls.append(('publish', ref))
        self.published += 1
        self.images[ref] = (dict(labels), {name: (Path(context) / name).read_bytes() for name in files})

    def fetch(self, ref, target, files):
        self.calls.append(('fetch', ref))
        self.fetched.append((ref, list(files)))
        labels, content = self.images[ref]
        for name in files:
            (Path(target) / name).write_bytes(content[name])
        return json.loads(json.dumps(labels))  # new objects, as when docker prints them


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
        self.write_json('status/fetch.json', {'status': 'ok', 'inputs': [
            {'kind': 'lock-entry', 'name': recipe, 'verified': True, 'expected_sha256': '5' * 64,
             'consumers': [{'recipe': recipe}]} for recipe in ('one', 'two')]})
        self.registry = FakeRegistry()
        self.messages, self.builds = [], 0
        self.service = self.new_service()

    def new_service(self, prefix='reg.example/cache', override='', root=None):
        return PC.ProviderCache(manifest(), PC.WorkTree(root or self.root, self.repo), self.registry, prefix,
                                override, self.messages.append)

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
        self.assertNotEqual(self.new_service(override='sha256:' + '9' * 64).key('base'), reference['base'])
        other = Path(self.tmp.name).resolve() / 'other-root'
        os.rename(self.root, other)
        self.assertNotEqual(self.new_service(root=other).key('base'), reference['base'])

    def test_key_fails_closed_without_its_inputs(self):
        for what, action in (
                ('images.json', lambda: os.unlink(self.root / 'status/images.json')),
                ('fetch.json', lambda: self.write_json('status/fetch.json', {'status': 'failed', 'inputs': []})),
                ('unverified input', lambda: self.write_json('status/fetch.json', {'status': 'ok', 'inputs': [
                    {'kind': 'lock-entry', 'name': r, 'verified': r == 'two', 'expected_sha256': '5' * 64,
                     'consumers': [{'recipe': r}]} for r in ('one', 'two')]})),
                ('lock', lambda: os.unlink(self.repo / 'takx/two/inputs.lock.json')),
                ('helper', lambda: os.unlink(self.repo / 'takx/shared.py'))):
            saved = {path: path.read_bytes() for path in (self.root / 'status/images.json',
                                                          self.root / 'status/fetch.json',
                                                          self.repo / 'takx/two/inputs.lock.json',
                                                          self.repo / 'takx/shared.py')}
            action()
            with self.subTest(what=what), self.assertRaises(PC.CacheError):
                self.new_service().key('top')
            for path, data in saved.items():
                path.write_bytes(data)


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

    def test_stale_image_record_is_refused_unless_overridden(self):
        self.make('base')
        self.registry.toolchain = 'sha256:' + '7' * 64
        with self.assertRaises(PC.CacheError):
            self.service.push('base')
        self.registry.toolchain = None
        with self.assertRaises(PC.CacheError):
            self.service.push('base')
        self.new_service(override='sha256:' + '8' * 64).push('base')
        self.assertEqual(self.registry.published, 1)


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
        self.assertEqual((service.tree.root, service.enabled, service.toolchain()),
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

    def new_service(self, prefix='reg.example/cache', override='', root=None, document=None, repo=None):
        return PC.ProviderCache(document or self.document(), PC.WorkTree(root or self.root, repo or self.repo),
                                self.registry, prefix, override, self.messages.append)

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
            ('toolchain image', lambda: self.write_json('status/images.json', {
                'status': 'ok', 'image_ids': {PC.TOOLCHAIN: 'sha256:' + '1' * 63 + '2'}}), 'bst'),
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
        self.assertEqual(self.changed(override=TOOLCHAIN_ID), '')  # the override names the same image
        self.assertEqual(self.changed(override='sha256:' + '9' * 64), 'bst')
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
        self.assertEqual((inputs['toolchain'], inputs['root'], inputs['repo']),
                         (TOOLCHAIN_ID, str(self.root), str(self.repo)))
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
                ('fetch without status', 'status/fetch.json', {'inputs': self.records()}),
                ('failed images', 'status/images.json', {'status': 'failed',
                                                         'image_ids': {PC.TOOLCHAIN: TOOLCHAIN_ID}}),
                ('other image only', 'status/images.json', {'status': 'ok', 'image_ids': {'x': TOOLCHAIN_ID}}),
                ('empty image id', 'status/images.json', {'status': 'ok', 'image_ids': {PC.TOOLCHAIN: ''}}),
                ('image id of another type', 'status/images.json', {'status': 'ok',
                                                                    'image_ids': {PC.TOOLCHAIN: 7}})):
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
        self.assertRegex(self.new_service(override='sha256:' + '9' * 64).key('base'), '^[0-9a-f]{64}$')


class Tamper(ServiceCase):
    """Every way a cached bundle can be wrong: refused, and nothing left behind."""

    def setUp(self):
        super().setUp()
        self.top = self.make('base')
        self.good = snapshot(self.top)
        self.ref = self.service.push('base')
        self.set_aside('base')
        self.before = snapshot(self.tmp.name)
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

    def assert_refused(self, message=None, errors=None):
        with self.assertRaises(errors or PC.CacheError) as caught:
            self.service.restore('base')
        self.assertEqual(snapshot(self.tmp.name), self.before)  # no directory, no status, no scratch, no escape
        self.assertFalse(os.path.lexists(self.root / 'P/base'))
        self.assertEqual(sorted(os.listdir(self.root / 'status')), ['fetch.json', 'images.json'])
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
            ('schema', lambda: with_meta(schema=2), 'bundle schema'),
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
                self.assert_refused(message)
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
                    if what == 'missing':
                        self.assert_refused(errors=OSError)
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
        self.assert_refused('connection reset')

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
            self.assertEqual(snapshot(self.tmp.name), self.before)

    def test_prerequisite_that_changes_during_the_restore(self):
        aside = self.root.parent / 'aside-top'
        self.make('top')
        self.make('base')
        ref = self.service.push('top')
        os.rename(self.root / 'P/top', aside)
        os.unlink(self.root / 'status/top.json')
        before = snapshot(self.tmp.name)
        marker = (self.root / 'P/base/complete.json').read_bytes()
        unpack = self.service.tree.unpack

        def rebuilt(path, target):
            unpack(path, target)
            (self.root / 'P/base/complete.json').write_text('{"rebuilt": true}')
        self.service.tree.unpack = rebuilt
        with self.assertRaisesRegex(PC.CacheError, '^top: a prerequisite changed during the restore$'):
            self.service.restore('top')
        (self.root / 'P/base/complete.json').write_bytes(marker)
        self.assertEqual(snapshot(self.tmp.name), before)
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
        self.assertEqual(sorted(os.listdir(self.root / 'status')), ['base.json', 'fetch.json', 'images.json'])
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
            self.assertEqual(sorted(os.listdir(self.root / 'status')), ['fetch.json', 'images.json'])
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
        service = self.new_service(override=TOOLCHAIN_ID)
        self.assertEqual(service.ref('base'), self.ref)  # the inputs are read before the directory goes
        os.rmdir(self.root / 'P')
        shutil.rmtree(self.root / 'status')
        service.restore('base')
        self.assertEqual(snapshot(self.top), self.good)
        self.assertEqual(os.listdir(self.root / 'status'), ['base.json'])

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
            before = snapshot(self.tmp.name)
            for state in (None, 'failed', 'ok'):
                if state:
                    self.status.write_text(json.dumps({'stage': 'base', 'status': state}))
                    before = snapshot(self.tmp.name)
                with self.subTest(what=what, state=state):
                    with self.assertRaisesRegex(PC.CacheError, 'already exists and is never overwritten'):
                        self.service.restore('base')
                    self.assertEqual(snapshot(self.tmp.name), before)
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
            'schema': 1, 'provider': 'top', 'key': key, 'root': str(self.root), 'repo': str(self.repo),
            'marker': 'complete.json', 'marker_sha256': marker_sha, 'tar_sha256': tar_sha,
            'files_sha256': hashlib.sha256(content['files.json']).hexdigest(),
            'prerequisite_markers': {'base': hashlib.sha256((top / 'complete.json').read_bytes()).hexdigest()},
            'key_inputs': self.service.explain('top')})
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
                         '\t'.join(('top', key, ref, tar_sha)) + '\n')
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
            stream.write('short\trow\n\none\ttwo\tthree\tfour\tfive\n')
        self.assertEqual(service.tree.pushed(), {('base', service.key('base')): record.split('\t')[3].strip(),
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

    def test_toolchain_image_must_be_the_recorded_one(self):
        self.make('base')
        for actual, shown in (('sha256:' + '7' * 64, 'sha256:' + '7' * 64), (None, 'absent'), ('', 'absent')):
            self.registry.toolchain = actual
            with self.subTest(actual=actual), self.assertRaisesRegex(
                    PC.CacheError, r'the local takx-desktop-proto:toolchain image \(%s\) is not the one recorded'
                    % shown):
                self.service.push('base')
            self.set_aside('base')
            with self.subTest(actual=actual, action='restore'), self.assertRaisesRegex(PC.CacheError, 'recorded'):
                self.service.restore('base')
            self.make('base')
        self.assertEqual(self.registry.calls, [('image_id', PC.TOOLCHAIN)] * 6)
        self.assert_nothing_pushed()
        override = self.new_service(override='sha256:' + '7' * 64)
        del self.registry.calls[:]
        ref = override.push('base')
        self.assertEqual([call[0] for call in self.registry.calls], ['probe', 'publish', 'probe'])
        self.assertNotEqual(ref.rsplit(':', 1)[1], self.service.key('base'))


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
        self.before = snapshot(self.tmp.name)
        self.stored = copy.deepcopy(self.registry.images)

    def assert_untouched(self, calls):
        self.assertEqual(self.registry.calls, calls)
        self.assertEqual(snapshot(self.tmp.name), self.before)
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
        self.assertEqual(snapshot(self.tmp.name), self.before)
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
                  '# work root %s\n# repository %s\n# toolchain %s\n# name\tkey\tref\ttar sha256\n'
                  % (self.root, self.repo, TOOLCHAIN_ID))
        row = 'base\t%s\treg.example/cache/base:%s\t%s\n' % (key, key, tar_sha)
        self.assertEqual(self.run_cli('status', '--write-index', str(index))[0], 0)
        self.assertEqual(index.read_text(), header + row)
        self.registry.images.clear()
        self.assertEqual(self.run_cli('status', '--remote', '--write-index', str(index))[0], 0)
        self.assertEqual(index.read_text(), header)  # the registry is asked, and it has nothing
        self.registry.state = 'present'
        self.assertEqual(self.run_cli('status', '--write-index', str(index), '--remote')[0], 0)
        self.assertEqual(len(index.read_text().splitlines()), 9 + 3)
        os.unlink(self.root / 'status/images.json')
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
            tree.recorded_toolchain()

    def test_key_document_refusals_name_the_provider_and_the_input(self):
        for field in ('toolchain', 'root', 'repo'):
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
            self.script('image', text)
            with self.subTest(text=text):
                self.assertEqual(self.registry.fetch('reg/p:tag', self.dir, []), expected)
        self.script('create', 'echo "  abc123  "')
        self.script('cp', 'echo "$2 $3" >> "$dir/copies"')
        self.registry.fetch('reg/p:tag', self.dir / 'target', ['p.tar', 'meta.json'])
        self.assertEqual((self.dir / 'copies').read_text().splitlines(),
                         ['abc123:/p.tar %s/target/p.tar' % self.dir,
                          'abc123:/meta.json %s/target/meta.json' % self.dir])
        self.assertEqual(self.calls()[-2:], ['rm -f abc123', 'rmi reg/p:tag'])

    def test_fetch_cleans_up_whatever_fails(self):
        for failing, expected in (('image', ['pull', 'image', 'rmi']), ('create', ['pull', 'image', 'create', 'rmi']),
                                  ('cp', ['pull', 'image', 'create', 'cp', 'rm', 'rmi'])):
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
        self.assertEqual(self.registry.fetch('reg/p:tag', self.dir, []), {})
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

    def test_bundle_identity_is_exact_in_both_directions(self):
        key, tar, files = 'b' * 64, 'c' * 64, 'd' * 64
        good = dict(name='mm', key=key, root='/r',
                    meta={'schema': 1, 'provider': 'mm', 'key': key, 'root': '/r', 'tar_sha256': tar,
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
        for other in (0, 2, '1', None, 1.5):
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
        before = snapshot(self.tmp.name)
        with mock.patch.object(PC.Path, 'write_text', side_effect=OSError(28, 'No space left on device')), \
                self.assertRaises(OSError) as caught:
            self.service.restore('base')
        self.assertEqual(caught.exception.errno, 28)
        self.assertEqual(snapshot(self.tmp.name), before)

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
            (lambda m: m.update(schema=2), '^unsupported manifest schema 2$'),
            (lambda m: m.pop('schema'), '^unsupported manifest schema None$'),
            (lambda m: m.update(schema='1'), "^unsupported manifest schema '1'$"),
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
        for selector in ({'consumer': 'x'}, {'kind': 'x'}, {'path': 'x'}):
            document['providers']['top']['fetched'] = [selector]
            PC.validate_manifest(document)
        with self.assertRaises(PC.CacheError):
            PC.ProviderCache(dict(manifest(), schema=2), None, None, 'reg')

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
                tree.recorded_toolchain()
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
        before = snapshot(self.tmp.name)
        with self.assertRaisesRegex(PC.CacheError, '^base: the provider directory already exists'):
            self.service.restore('base')
        with self.assertRaisesRegex(PC.Skip, '^a prerequisite provider is not ok here$'):
            self.service.restore('top')
        with self.assertRaisesRegex(PC.CacheError, '^base: status is ok but the completion marker is missing$'):
            self.service.push('base')
        self.assertEqual(snapshot(self.tmp.name), before)

    def test_complete_directory_with_another_status_is_kept_and_not_called_ok(self):
        top = self.make('base')
        self.service.push('base')
        for status in ('running', 'zz', 'okay', 'failed', 'OK'):
            self.write_json('status/base.json', {'stage': 'base', 'status': status})
            before = snapshot(self.tmp.name)
            with self.subTest(status=status), self.assertRaisesRegex(
                    PC.CacheError, '^base: the provider directory already exists and is never overwritten$'):
                self.service.restore('base')
            self.assertEqual(snapshot(self.tmp.name), before)
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
        self.assertEqual((service.tree.root, service.prefix, service.override),
                         (Path(os.path.abspath('relative/root')), 'reg.example/x', ''))
        self.assertEqual(set(service.manifest['providers']), EXPECTED)

    def test_help_describes_the_tool(self):
        out = io.StringIO()
        with mock.patch.object(PC.sys, 'stdout', out), self.assertRaises(SystemExit) as caught:
            PC.main(['--help'], service=self.service)
        self.assertEqual(caught.exception.code, 0)
        self.assertIn('Input-keyed cache of the desktop-prototype provider outputs.', out.getvalue())
        self.assertIn('usage: provider_cache.py ', out.getvalue())
        self.assertIn('{key,restore,push,status}', out.getvalue())

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
        labels = self.registry.fetch('reg/p:tag', target, ['p.tar', 'meta.json'])
        self.assertEqual(labels, {'dev.uds.takx.provider': 'p'})
        self.assertEqual(self.calls(), [
            'pull -q reg/p:tag', 'image inspect --format {{json .Config.Labels}} reg/p:tag',
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
                self.assertIn('restore "$1" >>', text)
                self.assertIn('push "$1" >>', text)
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

    def test_wrappers_delegate_to_the_module(self):
        for script, command in (('push-cache.sh', 'push'), ('restore-cache.sh', 'restore')):
            text = (HERE / script).read_text()
            self.assertIn('provider_cache.py', text)
            self.assertIn(command, text)
            self.assertEqual(subprocess.run(['/bin/bash', '-n', str(HERE / script)]).returncode, 0)


if __name__ == '__main__':
    unittest.main()
