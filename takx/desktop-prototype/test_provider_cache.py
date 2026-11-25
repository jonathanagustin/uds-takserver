# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests for provider_cache.py, providers.json and the lane hooks.

Nothing here starts docker or reaches a registry: the registry port is an
in-memory fake, and every work root and repository is a temporary directory.
"""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest

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


class FakeRegistry:
    """In-memory registry port."""

    def __init__(self):
        self.images, self.logged_in, self.docker, self.published = {}, True, True, 0
        self.toolchain, self.state = TOOLCHAIN_ID, None

    def available(self):
        return self.docker

    def image_id(self, image):
        return self.toolchain

    def probe(self, ref):
        if self.state:
            return self.state
        if not self.logged_in:
            return 'unauthorized'
        return 'present' if ref in self.images else 'absent'

    def publish(self, ref, context, files, labels):
        self.published += 1
        self.images[ref] = (dict(labels), {name: (Path(context) / name).read_bytes() for name in files})

    def fetch(self, ref, target, files):
        labels, content = self.images[ref]
        for name in files:
            (Path(target) / name).write_bytes(content[name])
        return dict(labels)


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
        self.messages = []
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
        (top / 'install/lib/lib.a').write_bytes(os.urandom(3000) if name != 'fixed' else b'x')
        (top / 'install/tool').write_text('#!/bin/sh\n')
        os.chmod(top / 'install/tool', 0o755)
        os.symlink('../lib.a', top / 'install/lib/.libs/lib.a')
        os.symlink('tool', top / 'install/tool-1')
        if marker:
            (top / 'complete.json').write_text(json.dumps({'provider': name, 'nonce': os.urandom(4).hex()}))
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
        self.make('base')
        self.service.push('base')
        with self.assertRaises(PC.CacheError):
            self.service.restore('base')
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
        self.assertEqual(self.run_cli('restore', 'base')[0], 1)
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
        for word in ('config.json', '.docker', 'DOCKER_CONFIG', 'docker login', "'login'", '--password'):
            self.assertNotIn(word, text.replace('`docker login`', ''))


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
