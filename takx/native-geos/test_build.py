# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('historical_geos_profile', HERE / 'build.py')
BUILD = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BUILD)


class OfflineGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def test_exact_archive_license_tools_and_patch_selection(self):
        original = json.loads((HERE / 'inputs.lock.json').read_text())
        BUILD.validate_lock(original)
        for section, key, value in [('archives', 'geos-3.4.3', dict(BUILD.ARCHIVE, url='https://foreign/source')),
                                    ('archives', 'geos-3.4.3', dict(BUILD.ARCHIVE, expanded_bytes=1)),
                                    ('archives', 'geos-3.4.3', dict(BUILD.ARCHIVE, sha256='a' * 64)),
                                    ('license', 'source', 'geos-3.4.3/README'),
                                    ('license', 'sha256', 'a' * 64), ('tools', 'gcc', '15.1')]:
            lock = json.loads(json.dumps(original))
            lock[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                BUILD.validate_lock(lock)
        original['source_patch'] = {}
        with self.assertRaises(ValueError):
            BUILD.validate_lock(original)

    def test_patch_images_reject_wrong_original_or_patched_bytes(self):
        paths = {}
        hashes = {}
        for name in BUILD.PATCH['files']:
            path = self.root / name
            path.write_bytes(b'original-' + name.encode())
            paths[name] = path
            hashes[name] = {'before': BUILD.native.digest(path), 'after': '0' * 64}
        with patch.dict(BUILD.PATCH, {'files': hashes}):
            BUILD.check_patch_images(self.root, 'before')
            with self.assertRaises(ValueError):
                BUILD.check_patch_images(self.root, 'after')
            paths['ltmain.sh'].write_bytes(b'foreign patch')
            with self.assertRaises(ValueError):
                BUILD.check_patch_images(self.root, 'before')

    def test_offline_cache_does_not_fetch_and_rejects_tampering(self):
        entry = dict(BUILD.ARCHIVE)
        with patch.object(BUILD.native.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            with self.assertRaisesRegex(ValueError, 'offline'):
                BUILD.native.acquire(self.root, entry, False)
            (self.root / entry['sha256']).write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                BUILD.native.acquire(self.root, entry, False)

    def test_archive_link_traversal_and_expansion_are_refused(self):
        for name, kind in [('geos-3.4.3/../../escape', tarfile.REGTYPE),
                           ('geos-3.4.3/link', tarfile.SYMTYPE), ('foreign/file', tarfile.REGTYPE),
                           ('geos-3.4.3/file', tarfile.REGTYPE)]:
            with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / 'archive.tar'
                with tarfile.open(archive, 'w') as stream:
                    item = tarfile.TarInfo(name); item.type = kind; item.size = 4
                    stream.addfile(item, io.BytesIO(b'data'))
                with self.assertRaises(ValueError):
                    BUILD.las.unpack(archive, Path(directory) / 'out', 'geos-3.4.3',
                                     {'members': 1, 'regular_files': 1, 'expanded_bytes': 3})

    def test_new_external_output_and_cache_must_be_separate(self):
        cache = self.root / 'cache'; cache.mkdir()
        for out in (cache, cache / 'out', self.root):
            with self.subTest(out=out), self.assertRaises(ValueError):
                BUILD.validate_paths(out, cache)
        link = self.root / 'link'; link.symlink_to(cache)
        with self.assertRaises(ValueError):
            BUILD.validate_paths(self.root / 'new', link)
        self.assertEqual(BUILD.validate_paths(self.root / 'new', cache), (self.root / 'new', cache))

    def test_inherited_compiler_geos_and_make_injection_removed(self):
        injected = {'CFLAGS': '-DNDEBUG', 'LDFLAGS': '-lgeos', 'LD_PRELOAD': '/foreign',
                    'CPATH': '/foreign', 'CMAKE_TOOLCHAIN_FILE': '/foreign', 'GEOS_LIB': '/foreign',
                    'MAKEFLAGS': '-j20', 'GNUMAKEFLAGS': '-j20', 'AR': '/foreign', 'RANLIB': '/foreign', 'MAKEFILES': '/foreign', 'CONFIG_SITE': '/foreign',
                    'BASH_ENV': '/foreign', 'BASH_FUNC_injected%%': '() { false; }',
                    'pgac_cv_type_long_int_64': 'yes', 'pgac_cv_type_int64_t_64': 'yes',
                    'ac_cv_env_CFLAGS_value': '-DFOREIGN', 'CPP': '/foreign', 'CXXCPP': '/foreign',
                    'CONFIG_HEADERS': '/foreign', 'LIBS': '-lforeign'}
        with patch.dict(os.environ, injected):
            env = BUILD.environment('/trusted/gcc')
        for name in set(injected) - {'CFLAGS', 'CONFIG_SITE'}:
            self.assertNotIn(name, env)
        self.assertEqual(env['CFLAGS'], '-O2 -fPIC -std=gnu89')
        self.assertEqual(env['CXXFLAGS'], '-O2 -fPIC -std=c++11')
        self.assertEqual(env['CONFIG_SITE'], '/dev/null')
        self.assertEqual(env['CC'], '/trusted/gcc')

    def test_untouched_source_rejects_changes_additions_and_symlinks(self):
        path = self.root / 'source.c'; path.write_bytes(b'original')
        expected = BUILD.source_inventory(self.root)
        for change in ('edit', 'new', 'link'):
            with self.subTest(change=change):
                path.write_bytes(b'original')
                extra = self.root / 'extra'
                if extra.exists() or extra.is_symlink(): extra.unlink()
                if change == 'edit': path.write_bytes(b'changed')
                elif change == 'new': extra.write_bytes(b'new')
                else: extra.symlink_to(path)
                with self.assertRaises(ValueError): BUILD.check_sources(self.root, expected)

    def test_proof_outputs_are_regular_and_hash_bound(self):
        for name in ('GeosGeometry.c', 'consumer', 'libgeos-pic-check.so'):
            (self.root / name).write_bytes(name.encode())
        before = BUILD.outputs(self.root)
        (self.root / 'consumer').write_bytes(b'changed')
        self.assertNotEqual(before, BUILD.outputs(self.root))
        (self.root / 'consumer').unlink()
        (self.root / 'consumer').symlink_to(self.root / 'GeosGeometry.c')
        with self.assertRaises(ValueError): BUILD.outputs(self.root)


class IntegerFeatureGuards(unittest.TestCase):
    def test_only_active_true_definitions_count(self):
        observed = BUILD.active_integer_features(b'#define HAVE_LONG_INT_64 1\n#define HAVE_LONG_LONG_INT_64 1\n')
        self.assertEqual(observed, {'HAVE_INT64_T_64': False, 'HAVE_LONG_INT_64': True,
                                    'HAVE_LONG_LONG_INT_64': True})
        for raw in (b'/* #undef HAVE_LONG_INT_64 */\n', b'',
                    b'#define HAVE_LONG_INT_64 0\n', b'#define HAVE_LONG_INT_64\n',
                    b'#define HAVE_LONG_INT_64 1\n#define HAVE_LONG_INT_64 1\n',
                    b'#define HAVE_LONG_INT_64 1\n#define INT64_IS_REALLY32 1\n'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                BUILD.active_integer_features(raw)

    def test_new_probe_is_recipe_bound_and_geometry_source_unchanged(self):
        self.assertEqual(BUILD.recipe()['GeosInt64.cpp'], BUILD.native.digest(HERE / 'GeosInt64.cpp'))
        self.assertEqual(BUILD.native.digest(HERE / 'GeosGeometry.c'),
                         '7e6d624a2a989c7b3369f42d7d1752aa407d939c076c849465dfba7833639c65')


@unittest.skipUnless(platform.system() == 'Linux' and shutil.which('c++'),
                     'working Linux C++ compiler needed for executable integer-control fixtures')
class ExecutableIntegerControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.output = Path(self.tmp.name).resolve()
        self.build, self.source = self.output / 'build', self.output / 'source'
        self.build.mkdir(); self.source.mkdir()
        self.cxx = shutil.which('c++')
        self.env = BUILD.environment(self.cxx, self.cxx)
        for name in BUILD.CONFIGURATION_FILES:
            path = self.build / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('configuration fixture\n')
        self.defines = '#define HAVE_LONG_INT_64 1\n#define HAVE_LONG_LONG_INT_64 1\n'
        (self.build / 'include/config.h').write_text(self.defines)
        self.platform = self.build / 'include/geos/platform.h'
        self.platform.write_text(self.defines + '''
#if defined(HAVE_INT64_T_64)
#include <stdint.h>
typedef int64_t int64;
#elif defined(HAVE_LONG_LONG_INT_64)
typedef long long int int64;
#else
typedef long int int64;
#ifndef HAVE_LONG_INT_64
#define INT64_IS_REALLY32 1
#endif
#endif
''')
        shutil.copyfile(HERE / 'GeosInt64.cpp', self.output / 'GeosInt64.cpp')

    def run_command(self, args, name, timeout=30, capture=False):
        return BUILD.native.run(list(map(str, args)), self.output, name, timeout=timeout,
                                capture=capture, env=self.env)

    def qualify(self, phase='before', expected=None, runner=None):
        return BUILD.integer_configuration(self.build, self.source, self.output, self.cxx,
                                           runner or self.run_command, phase, expected)

    def test_real_signed_64bit_probe_and_unchanged_configuration_replay(self):
        before = self.qualify()
        self.assertEqual(before['selected_feature'], 'HAVE_LONG_LONG_INT_64')
        self.assertEqual(before['active_features']['HAVE_INT64_T_64'], False)
        self.assertEqual(set(before['files']), set(BUILD.CONFIGURATION_FILES))
        self.assertEqual(set(before['probe_files']), {'GeosInt64.cpp', 'int64-probe'})
        self.assertEqual(self.qualify('after', before), before)

    def test_missing_generated_header_and_symlink_are_refused_before_compiler(self):
        header = self.build / 'include/config.h'
        header.unlink()
        for linked in (False, True):
            if linked:
                header.symlink_to(self.platform)
            with self.subTest(linked=linked), self.assertRaisesRegex(ValueError, 'nonregular'):
                self.qualify(runner=lambda *args, **kwargs: self.fail('compiler must not run'))

    def test_conflicting_header_choices_are_refused_before_probe_compile(self):
        (self.build / 'include/config.h').write_text('#define HAVE_LONG_INT_64 1\n')
        with self.assertRaisesRegex(ValueError, 'disagree'):
            self.qualify()
        self.assertFalse((self.output / 'int64-probe').exists())

    def test_inactive_textual_features_and_active_fallback_are_refused(self):
        self.platform.write_text('#if 0\n' + self.defines + '#endif\n#define INT64_IS_REALLY32 1\n')
        with self.assertRaisesRegex(ValueError, 'active integer feature'):
            self.qualify()
        self.assertFalse((self.output / 'int64-probe').exists())

    def test_false_64bit_declaration_cannot_pass_real_width_or_signedness_control(self):
        for index, ctype in enumerate(('int', 'unsigned long long')):
            self.platform.write_text(self.defines + 'typedef ' + ctype + ' int64;\n')
            with self.subTest(ctype=ctype), self.assertRaisesRegex(ValueError, 'command failed'):
                self.qualify(str(index))
            (self.output / 'int64-probe-compile.log').unlink()

    def test_stale_configuration_and_probe_artifacts_refused_before_reexecution(self):
        before = self.qualify()
        for root, name in ((self.build, 'config.log'), (self.build, 'include/config.h'),
                           (self.output, 'GeosInt64.cpp'), (self.output, 'int64-probe')):
            path = root / name
            original = path.read_bytes()
            path.write_bytes(original + b'changed')
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'stale|source differs'):
                self.qualify('after', before, runner=lambda *args, **kwargs: self.fail('stale control must not execute'))
            path.write_bytes(original)

    def test_mutation_during_qualification_and_false_stdout_are_refused(self):
        def changed(args, name, timeout=30, capture=False):
            data = self.run_command(args, name, timeout, capture)
            if name.endswith('-arithmetic'):
                (self.build / 'config.log').write_text('changed while checking\n')
            return data
        with self.assertRaisesRegex(ValueError, 'changed during qualification'):
            self.qualify(runner=changed)
        before = BUILD.regular_files(self.build, BUILD.CONFIGURATION_FILES)
        self.assertEqual(before['config.log']['bytes'], len('changed while checking\n'))
        (self.output / 'int64-probe-compile.log').unlink()
        def false_output(args, name, timeout=30, capture=False):
            if name.endswith('-arithmetic'):
                return b'int64_bytes=4\n'
            return self.run_command(args, name, timeout, capture)
        with self.assertRaisesRegex(ValueError, 'arithmetic control failed'):
            self.qualify('false-output', runner=false_output)


@unittest.skipUnless(os.environ.get('TAKX_NATIVE_GEOS_PROOF'), 'opt-in fresh historical GEOS proof required')
class ActualComponentProof(unittest.TestCase):
    def test_fresh_entrypoint_identity_and_real_coordinate_consumer(self):
        root = Path(os.environ['TAKX_NATIVE_GEOS_PROOF']).resolve()
        receipt = json.loads((root / 'complete.json').read_text())
        self.assertEqual(receipt['recipe'], BUILD.recipe())
        self.assertEqual(receipt['outputs'], BUILD.outputs(root))
        self.assertEqual(receipt['installed'], BUILD.native.inventory(root / 'install'))
        BUILD.check_sources(root / 'source', receipt['source_inventory'])
        BUILD.las.verify_runtime(receipt['runtime_closure'])
        for tool in receipt['tools'].values():
            self.assertEqual(tool['sha256'], BUILD.native.digest(Path(tool['path'])))
        with tempfile.TemporaryDirectory() as directory:
            def run(args, name, timeout=30, capture=False):
                return BUILD.native.run(list(map(str, args)), Path(directory), name, timeout=timeout,
                                        capture=capture,
                                        env=BUILD.environment(receipt['tools']['cc']['path'], receipt['tools']['cxx']['path']))
            BUILD.integer_configuration(root / 'build', root / 'source/geos-3.4.3', root,
                                        receipt['tools']['cxx']['path'], run, 'verification', receipt['integer_configuration'])
            result = BUILD.native.run([str(root / 'consumer')], Path(directory), 'positive',
                                      timeout=10, capture=True,
                                      env=BUILD.environment(receipt['tools']['cc']['path'])).decode()
        self.assertIn('actual_release=3.4.3-CAPI-1.8.3 r4285\n', result)
        self.assertIn('geometry_and_error_controls=true\n', result)
        for name in ('jni_acceptance', 'modern_geos_api_compatibility', 'security_acceptance',
                     'target_image_abi_acceptance', 'kernel_sdk_acceptance', 'takx_acceptance'):
            self.assertFalse(receipt[name])


if __name__ == '__main__':
    unittest.main()
