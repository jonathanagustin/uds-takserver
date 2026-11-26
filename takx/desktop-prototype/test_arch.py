#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests of the architecture port of the desktop prototype.

They cover arch.sh (the one table of names), the override patches and
arch_tree.py (the patched copy of the tree used on arm64), the arm64 pins, the
architecture selection of the Python stages, the CI driver (ci/pipeline.py)
and the opt-in workflow. Nothing is built, no image is started and no network
is used. Run:

  python3 -B -m unittest discover -s takx/desktop-prototype -p 'test_arch.py'
"""
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ARCH = HERE / 'arch.sh'
WORKFLOW = REPO / '.github/workflows/takx-desktop-prototype.yaml'
LOCK = json.loads((HERE / 'inputs.arm64.lock.json').read_text())
STAGES = ('lane1.sh', 'lane2.sh', 'lane3.sh', 'lane4.sh', 'kernel.sh', 'client.sh')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TREE = load('arch_tree', HERE / 'arch_tree.py')
PIPELINE = load('ci_pipeline', HERE / 'ci/pipeline.py')


def arch(*args, **env):
    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
    environment.update(env)
    return subprocess.run(['bash', str(ARCH), *args], env=environment, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, timeout=60)


class ArchTable(unittest.TestCase):
    AMD64 = {'arch': 'amd64', 'machine': 'x86_64', 'debian': 'amd64', 'multiarch': 'x86_64-linux-gnu',
             'docker': 'linux/amd64', 'loader': 'ld-linux-x86-64.so.2', 'temurin': 'x64',
             'jogamp': 'natives-linux-amd64', 'bytedeco': 'linux-x86_64', 'openssl': 'linux-x86_64',
             'vcpkg': 'x64-linux', 'gnu': 'x86_64-pc-linux-gnu', 'commoncommo': 'linux-amd64',
             'kernel_jar': 'kernel-8.30.5-linux-amd64-public-cpu.jar', 'kernel_libs': 'linux64',
             'runner': 'ubuntu-24.04'}
    ARM64 = {'arch': 'arm64', 'machine': 'aarch64', 'debian': 'arm64', 'multiarch': 'aarch64-linux-gnu',
             'docker': 'linux/arm64', 'loader': 'ld-linux-aarch64.so.1', 'temurin': 'aarch64',
             'jogamp': 'natives-linux-aarch64', 'bytedeco': 'linux-arm64', 'openssl': 'linux-aarch64',
             'vcpkg': 'arm64-linux', 'gnu': 'aarch64-unknown-linux-gnu', 'commoncommo': 'linux-arm64',
             'kernel_jar': 'kernel-8.30.5-linux-arm64-public-cpu.jar', 'kernel_libs': 'linux64',
             'runner': 'ubuntu-24.04-arm'}

    def test_every_name_of_both_architectures(self):
        # The amd64 column is what the prototype used before it knew a second
        # architecture; a change there would change the amd64 build.
        for name, expected in (('amd64', self.AMD64), ('arm64', self.ARM64)):
            result = arch('json', name)
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            self.assertEqual(json.loads(result.stdout), expected)
            self.assertEqual(arch('fields').stdout.split(), list(expected))
            for field, value in expected.items():
                self.assertEqual(arch('get', field, name).stdout, value + '\n')

    def test_setting_and_machine_spellings(self):
        for value, expected in (('amd64', 'amd64'), ('x86_64', 'amd64'), ('arm64', 'arm64'), ('aarch64', 'arm64')):
            self.assertEqual(arch('get', 'arch', TAKX_PROTO_ARCH=value).stdout, expected + '\n')
            with tempfile.TemporaryDirectory() as directory:
                fake = Path(directory) / 'uname'
                fake.write_text('#!/bin/sh\necho %s\n' % value)
                fake.chmod(0o755)
                path = '%s:%s' % (directory, os.environ.get('PATH', '/usr/bin:/bin'))
                self.assertEqual(arch('get', 'arch', PATH=path).stdout, expected + '\n')
                # An explicit setting wins over the machine; an empty one does not count.
                self.assertEqual(arch('get', 'machine', PATH=path, TAKX_PROTO_ARCH='arm64').stdout, 'aarch64\n')
                self.assertEqual(arch('get', 'arch', PATH=path, TAKX_PROTO_ARCH='').stdout, expected + '\n')

    def test_anything_else_is_refused(self):
        for args, env, message in ((('get', 'arch'), {'TAKX_PROTO_ARCH': 'riscv64'}, "unsupported architecture 'riscv64'"),
                                   (('get', 'arch', 'i386'), {}, "unsupported architecture 'i386'"),
                                   (('json', 'ppc64le'), {}, "unsupported architecture 'ppc64le'"),
                                   (('get', 'nothing', 'arm64'), {}, "unknown field 'nothing'"),
                                   (('get',), {}, 'usage:'), ((), {}, 'usage:'), (('bogus',), {}, 'usage:')):
            with self.subTest(args=args, env=env):
                result = arch(*args, **env)
                self.assertEqual((result.returncode, result.stdout), (2, ''))
                self.assertIn(message, result.stderr)

    def test_python_stages_read_the_same_table(self):
        modules = [load('fetch_inputs_arch', HERE / 'fetch_inputs.py'), load('compose_arch', HERE / 'kernel/compose.py'),
                   load('client_build_arch', HERE / 'client/build.py'),
                   load('resolve_runtime_arch', HERE / 'client/resolve_runtime.py')]
        for name, expected in (('amd64', self.AMD64), ('arm64', self.ARM64)):
            with mock.patch.dict(os.environ, {'TAKX_PROTO_ARCH': name}):
                for module in modules:
                    self.assertEqual(module.arch_names(), expected, module.__name__)

    def test_no_stage_maps_the_machine_itself(self):
        # Outside arch.sh the classifier and file names of the other architecture do not appear.
        for path in [HERE / 'fetch_inputs.py', HERE / 'kernel/compose.py', HERE / 'client/build.py',
                     HERE / 'run-in-toolchain.sh', HERE / 'fetch.sh'] + [HERE / 'stages' / name for name in STAGES]:
            text = path.read_text()
            for literal in ('natives-linux-aarch64', 'linux-arm64-public-cpu', 'aarch64-linux-gnu', 'ld-linux-aarch64'):
                self.assertNotIn(literal, text, path.name)


class StageHandOver(unittest.TestCase):
    def test_every_stage_script_hands_over_once_right_after_it_knows_its_path(self):
        for name in STAGES + ('../fetch.sh',):
            text = (HERE / 'stages' / name).read_text()
            with self.subTest(script=name):
                self.assertEqual(text.count('takx_arch_enter "${BASH_SOURCE[0]}" "$@"'), 1)
                here = text.index('here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"')
                enter = text.index('takx_arch_enter "${BASH_SOURCE[0]}" "$@"')
                self.assertLess(here, enter)
                # Nothing that reads the repository or the work root runs before the hand-over.
                between = text[here:enter]
                for word in ('mkdir', 'docker', 'python3', 'repo='):
                    self.assertNotIn(word, between)

    def test_scripts_parse(self):
        for path in [ARCH, HERE / 'fetch.sh', HERE / 'run-in-toolchain.sh'] + sorted((HERE / 'ci').glob('*.sh')) \
                + [HERE / 'stages' / name for name in STAGES]:
            result = subprocess.run(['/bin/bash', '-n', str(path)], stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(result.returncode, 0, (path.name, result.stderr))
            self.assertTrue(os.access(path, os.X_OK), path.name)

    def test_tracked_lock_of_the_client_is_per_architecture(self):
        text = (HERE / 'stages/client.sh').read_text()
        self.assertIn('tracked_lock="$repo/takx/desktop-prototype/client/inputs.lock.json"', text)
        self.assertIn('tracked_lock="${lock_home}/takx/desktop-prototype/client/inputs.lock.${arch}${lineage:+.${lineage}}.json"',
                      text)
        self.assertIn('if [[ "${arch}" != amd64 || -n "${lineage}" ]]; then', text)

    def test_tracked_lock_of_the_client_is_per_build_lineage(self):
        # The hand-built amd64 lock is not the lock of the workflow's providers, and is not edited for them.
        names = sorted(p.name for p in (HERE / 'client').glob('inputs.lock*.json'))
        self.assertEqual(names, ['inputs.lock.amd64.ci.json', 'inputs.lock.arm64.ci.json', 'inputs.lock.json'])
        locks = {name: json.loads((HERE / 'client' / name).read_text()) for name in names}
        built = ('proto-P:commoncommo/install/java/jcommoncommo.jar', 'proto-P:kernel-java/classpath/gdal-2.4.4-mod-java.jar',
                 'proto-P:kernel-java/classpath/assimp-4.0.1-mod-jassimp-java.jar')
        for name in built:
            digests = {lock['inputs']['classpath-jar'][name] for lock in locks.values()}
            self.assertEqual(len(digests), 3, 'each lineage built %s itself' % name)
        for lock in locks.values():
            self.assertEqual(lock['official_revision'], '0b59a9bb2d0e90dc35c9edfff1c2056daab2b713')
        # Everything that is fetched or checked out, not built, is the same in the two amd64 lineages.
        hand, ci = locks['inputs.lock.json'], locks['inputs.lock.amd64.ci.json']
        for group in hand['inputs']:
            differing = sorted(key for key in set(hand['inputs'][group]) | set(ci['inputs'][group])
                               if hand['inputs'][group].get(key) != ci['inputs'][group].get(key))
            expected = list(built) if group == 'classpath-jar' else \
                ['proto-P:kernel-composed-java/kernel-8.30.5-composed-classes.jar'] if group == 'composed-kernel-jar' else []
            self.assertEqual(differing, sorted(expected), group)
        # The workflow names its lineage; the stage refuses a name that could leave the directory.
        self.assertIn("env['TAKX_CLIENT_LOCK_LINEAGE'] = 'ci'", (HERE / 'ci/pipeline.py').read_text())
        refused = subprocess.run(['bash', str(HERE / 'stages/client.sh'), 'lock'], capture_output=True, text=True, timeout=60,
                                 env=dict(os.environ, TAKX_CLIENT_LOCK_LINEAGE='../x', TAKX_PROTO_ARCH='amd64',
                                          TAKX_PROTO_ROOT=tempfile.gettempdir() + '/takx-no-such-root'))
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn('TAKX_CLIENT_LOCK_LINEAGE must be lowercase letters and digits', refused.stderr)


class Overrides(unittest.TestCase):
    PATCHES = sorted((HERE / 'overrides/arm64').glob('*.patch'))

    def test_amd64_uses_the_tracked_tree(self):
        self.assertEqual(TREE.patch_files(REPO, 'amd64'), [])
        self.assertEqual(TREE.check('amd64', REPO), [])
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(TREE.Refusal, 'no override patches for amd64'):
                TREE.ensure('amd64', Path(root), REPO)
            self.assertEqual(os.listdir(root), [])

    def test_one_commented_patch_per_recipe(self):
        self.assertEqual([p.name for p in self.PATCHES], [
            'kernel-native.patch', 'kernel-scene.patch', 'kernel-spatial-db.patch', 'native-assimp.patch',
            'native-commoncommo.patch', 'native-crypto.patch', 'native-curl.patch', 'native-deps.patch',
            'native-gdal-jni.patch', 'native-geos.patch', 'native-gles.patch', 'native-kml.patch', 'native-las.patch',
            'native-mdbtools.patch', 'native-proj.patch', 'native-sqlcipher.patch'])
        for patch in self.PATCHES:
            recipe = patch.name[:-len('.patch')]
            lines = patch.read_text().splitlines()
            with self.subTest(patch=patch.name):
                self.assertEqual(lines[:2], ['# Copyright 2026 uds-takserver authors',
                                             '# SPDX-License-Identifier: GPL-3.0-only'])
                self.assertIn('# Why:', lines)
                self.assertTrue((REPO / 'takx' / recipe).is_dir())
                for target in TREE.patch_targets(patch):
                    self.assertTrue(target.startswith('takx/%s/' % recipe))
                    self.assertTrue((REPO / target).is_file())
                    self.assertFalse(Path(target).name.startswith('test'), 'recipe tests are not patched')

    def test_prototype_patch_of_the_assembler_is_recorded_and_commented(self):
        patch, = TREE.stage_patch_files(REPO, 'arm64')
        self.assertEqual(patch.name, 'assemble.patch')
        lines = patch.read_text().splitlines()
        self.assertEqual(lines[:2], ['# Copyright 2026 uds-takserver authors', '# SPDX-License-Identifier: GPL-3.0-only'])
        self.assertIn('# Why:', lines)
        self.assertEqual(TREE.stage_patch_targets(patch), ['takx/desktop-prototype/assemble/assemble.py'])
        self.assertEqual(TREE.stage_patch_files(REPO, 'amd64'), [])
        added = [line[1:] for line in lines if line.startswith('+') and not line.startswith('+++')]
        self.assertEqual(len(added), 3)
        names = json.loads(subprocess.check_output(['bash', str(HERE / 'arch.sh'), 'json', 'arm64'], timeout=30))
        self.assertTrue(any("natives-(?!%s$)" % names['jogamp'][len('natives-'):] in line for line in added))
        self.assertTrue(any(names['kernel_jar'] in line for line in added))
        # The tracked assembler belongs to another part of the prototype and may
        # move on; then the patch is skipped, which is reported, not a failure.
        assembler = HERE / 'assemble/assemble.py'
        before = hashlib.sha256(assembler.read_bytes()).hexdigest()
        row = TREE.check('arm64', REPO, stage=True)[-1]
        self.assertEqual(row['name'], 'prototype/assemble.patch')
        if 'skipped' in row:
            warnings.warn('overrides/arm64/prototype/assemble.patch no longer applies: %s' % row['skipped'])
        else:
            self.assertEqual(sorted(row['files']), ['takx/desktop-prototype/assemble/assemble.py'])
            self.assertEqual(row['files']['takx/desktop-prototype/assemble/assemble.py']['before'], before)
        # The file itself, whatever state its owners have it in, was not touched by the check.
        self.assertEqual(hashlib.sha256(assembler.read_bytes()).hexdigest(), before)

    def test_every_patch_applies_with_fuzz_zero_to_the_tracked_recipes(self):
        rows = TREE.check('arm64', REPO)
        self.assertEqual([row['name'] for row in rows], [p.name for p in self.PATCHES])
        changed = [name for row in rows for name in row['files']]
        self.assertEqual(len(changed), 17)
        self.assertEqual(len(set(changed)), 17)
        for row in rows:
            for name, digests in row['files'].items():
                self.assertEqual(digests['before'], hashlib.sha256((REPO / name).read_bytes()).hexdigest())
                self.assertNotEqual(digests['before'], digests['after'])
        # The tracked recipes were not touched by the check.
        self.assertEqual(subprocess.run(['git', '-C', str(REPO), 'status', '--porcelain', '--', 'takx/native-deps',
                                         'takx/native-commoncommo', 'takx/kernel-native'], stdout=subprocess.PIPE,
                                        text=True, timeout=60).stdout, '')

    def test_patched_recipes_still_accept_x86_64(self):
        # Offline recipe tests mock platform.machine() to x86_64, so a gate
        # that accepted only aarch64 would fail them in the patched tree.
        for patch in self.PATCHES:
            added = [line[1:] for line in patch.read_text().splitlines() if line.startswith('+') and not line.startswith('+++')]
            for line in added:
                if 'platform.machine() not in' in line:
                    self.assertRegex(line, r"\('x86_64', ?'aarch64'\)")
                if 'ld-linux-aarch64' in line:
                    self.assertIn('x86-64', line)

    def test_layout_override_applies_to_the_official_engine_cmake(self):
        patches = sorted((HERE / 'overrides/arm64/layout').glob('*.patch'))
        self.assertEqual([p.name for p in patches], ['kernel-engine-cmake.patch'])
        self.assertFalse((HERE / 'overrides/amd64').exists())
        text = patches[0].read_text()
        self.assertEqual(text.splitlines()[:2], ['# Copyright 2026 uds-takserver authors',
                                                 '# SPDX-License-Identifier: GPL-3.0-only'])
        name = 'takkernel/engine/src/main/cpp/CMakeLists.txt'
        self.assertIn('+++ b/' + name, text)
        submodule = REPO / 'upstream/atak-civ'
        shown = subprocess.run(['git', '-C', str(submodule), 'show', 'HEAD:' + name], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, timeout=60)
        if shown.returncode or not shutil.which('patch'):
            self.skipTest('upstream/atak-civ or the patch tool is not available')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / name
            target.parent.mkdir(parents=True)
            target.write_bytes(shown.stdout)
            result = subprocess.run(['patch', '--batch', '--fuzz=0', '--no-backup-if-mismatch', '-p1', '-d', directory,
                                     '-i', str(patches[0])], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotRegex(result.stdout, r'fuzz|offset')
            after = target.read_text()
        # x86_64 keeps the flag; only aarch64 loses it.
        self.assertEqual(after.count('-msse3'), shown.stdout.decode().count('-msse3'))
        self.assertIn('if(NOT CMAKE_SYSTEM_PROCESSOR MATCHES "^(aarch64|arm64)$")\n        set(CMAKE_CXX_FLAGS  '
                      '"${CMAKE_CXX_FLAGS} -msse3")\n    endif()', after)


class Pins(unittest.TestCase):
    def test_dockerfile_carries_the_arm64_pins_next_to_the_untouched_amd64_ones(self):
        text = (HERE / 'Dockerfile').read_text()
        toolchain = LOCK['toolchain']
        self.assertIn('ARG TEMURIN_URL_ARM64=' + toolchain['temurin']['url'] + '\n', text)
        self.assertIn('ARG TEMURIN_SHA256_ARM64=' + toolchain['temurin']['sha256'] + '\n', text)
        for wheel in toolchain['wheels'].values():
            self.assertIn("'%s --hash=sha256:%s'" % (wheel['requirement'], wheel['sha256']), text)
        self.assertIn(toolchain['base_image']['reference'], text)
        # The amd64 pins as they were before the port.
        for line in (
                'ARG TEMURIN_URL=https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21%2B9/'
                'OpenJDK17U-jdk_x64_linux_hotspot_17.0.21_9.tar.gz',
                'ARG TEMURIN_SHA256=8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711',
                "'cmake==3.31.6 --hash=sha256:1c8b05df0602365da91ee6a3336fe57525b137706c4ab5675498f662ae1dbcec'",
                "'ninja==1.11.1.4 --hash=sha256:096487995473320de7f65d622c3f1d16c3ad174797602218ca8c967f51ec38a0'"):
            self.assertEqual(text.count(line + '\n') + text.count(line + ' \\\n'), 1, line)
        self.assertNotIn('x86_64-linux-gnu', text)
        self.assertEqual(text.count('case "${TARGETARCH:-$(dpkg --print-architecture)}" in'), 2)

    def test_commoncommo_patch_carries_the_debian_pins_of_the_lock(self):
        debian = LOCK['debian']
        patch = (HERE / 'overrides/arm64/native-commoncommo.patch').read_text()
        self.assertIn('+    "packages_sha256": "%s",' % debian['packages_sha256'], patch)
        self.assertIn('+    "packages_bytes": %d,' % debian['packages_bytes'], patch)
        package = debian['packages'][1]
        for key in ('Version', 'Filename', 'Size', 'SHA256'):
            self.assertIn('+        "%s": "%s"' % (key, package[key]), patch)
        self.assertIn('main/binary-arm64/Packages.xz', patch)
        tracked = json.loads((REPO / 'takx/native-commoncommo/inputs.lock.json').read_text())['tools']
        # Same signed release, same ant; the amd64 pins themselves are not in the arm64 lock.
        self.assertEqual(tracked['inrelease_sha256'], debian['inrelease_sha256'])
        self.assertEqual(tracked['packages'][0], debian['packages'][0])
        self.assertNotEqual(tracked['packages_sha256'], debian['packages_sha256'])
        self.assertIn('_amd64.deb', tracked['packages'][1]['Filename'])
        self.assertIn('_arm64.deb', package['Filename'])

    def test_jdk_identity_is_pinned_per_architecture(self):
        build = load('client_build_pins', HERE / 'client/build.py')
        specification = json.loads((REPO / 'takx/client-compiler/inputs.lock.json').read_text())['files']
        with mock.patch.dict(os.environ, {'TAKX_PROTO_ARCH': 'amd64'}):
            pins, note = build.jdk_pins()
        self.assertEqual(pins, {name: specification[build.JDK_PREFIX + name] for name in build.JDK_FILES})
        self.assertIsNone(note)
        with mock.patch.dict(os.environ, {'TAKX_PROTO_ARCH': 'arm64'}):
            arm, note = build.jdk_pins()
        self.assertEqual(arm, LOCK['jdk_files']['files'])
        self.assertIn('inputs.arm64.lock.json', note)
        self.assertEqual(sorted(arm), sorted(pins))
        for name in pins:
            self.assertNotEqual(arm[name], pins[name], name)

    def test_native_classifier_pins_use_the_classifiers_of_the_table(self):
        names = ArchTable.ARM64
        jars = LOCK['runtime_natives']['jars']
        self.assertEqual(sorted(key.rsplit(':', 1)[1] for key in jars),
                         sorted([names['jogamp']] * 3 + [names['bytedeco']]))
        for key, entry in jars.items():
            group, artifact, version, classifier = key.split(':')
            self.assertTrue(entry['url'].endswith('/%s/%s/%s-%s-%s.jar' % (artifact, version, artifact, version, classifier)))
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
            self.assertRegex(entry['sha1'], r'^[0-9a-f]{40}$')
        resolve = load('resolve_runtime_pins', HERE / 'client/resolve_runtime.py')
        self.assertEqual(resolve.native_pins('arm64'), {key: entry['sha256'] for key, entry in jars.items()})
        self.assertEqual(resolve.native_pins('amd64'), {})


class Fixture(unittest.TestCase):
    """A small repository with one recipe and one override patch."""

    BEFORE = 'import platform\nif platform.machine() != "x86_64":\n    raise ValueError("amd64 only")\nprint("built")\n'
    PATCH = ('# Copyright 2026 uds-takserver authors\n# SPDX-License-Identifier: GPL-3.0-only\n# Why: test\n'
             '--- a/takx/recipe/build.py\n+++ b/takx/recipe/build.py\n@@ -1,4 +1,4 @@\n import platform\n'
             '-if platform.machine() != "x86_64":\n+if platform.machine() not in ("x86_64", "aarch64"):\n'
             '     raise ValueError("amd64 only")\n print("built")\n')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name).resolve()
        self.repo, self.root = self.tmp / 'repo', self.tmp / 'root'
        (self.repo / 'takx/recipe').mkdir(parents=True)
        (self.repo / 'takx/recipe/build.py').write_text(self.BEFORE)
        (self.repo / 'takx/recipe/__pycache__').mkdir()
        (self.repo / 'takx/recipe/__pycache__/build.cpython-313.pyc').write_bytes(b'cache')
        (self.repo / 'takx/other').mkdir()
        (self.repo / 'takx/other/build.py').write_text('print("other")\n')
        script = self.repo / 'takx/desktop-prototype/stages/lane.sh'
        script.parent.mkdir(parents=True)
        script.write_text('#!/bin/sh\n')
        script.chmod(0o755)
        self.overrides = self.repo / 'takx/desktop-prototype/overrides/arm64'
        self.overrides.mkdir(parents=True)
        (self.overrides / 'recipe.patch').write_text(self.PATCH)
        (self.repo / 'LICENSE').write_text('license\n')
        self.tree = self.root / 'overrides/arm64/tree'

    def ensure(self):
        with contextlib.redirect_stderr(io.StringIO()):  # the progress lines of arch_tree.py
            return TREE.ensure('arm64', self.root, self.repo)


@unittest.skipUnless(shutil.which('patch'), 'the patch tool is required')
class ArchTree(Fixture):
    def test_tree_is_a_patched_copy_and_the_repository_is_untouched(self):
        self.assertEqual(self.ensure(), self.tree)
        self.assertEqual((self.repo / 'takx/recipe/build.py').read_text(), self.BEFORE)
        self.assertIn('not in ("x86_64", "aarch64")', (self.tree / 'takx/recipe/build.py').read_text())
        self.assertEqual((self.tree / 'takx/other/build.py').read_text(), 'print("other")\n')
        self.assertEqual((self.tree / 'LICENSE').read_text(), 'license\n')
        self.assertTrue(os.access(self.tree / 'takx/desktop-prototype/stages/lane.sh', os.X_OK))
        self.assertFalse((self.tree / 'takx/recipe/__pycache__').exists())
        for name in ('upstream', '.git'):
            self.assertEqual(os.listdir(self.tree / name), [])
        self.assertEqual((self.tree / '.takx-arch-tree.base').read_text(), '%s\n' % self.repo)
        marker = json.loads((self.tree / '.takx-arch-tree.json').read_text())
        self.assertEqual((marker['arch'], marker['base_repo'], marker['schema']), ('arm64', str(self.repo), 1))
        before = hashlib.sha256(self.BEFORE.encode()).hexdigest()
        after = hashlib.sha256((self.tree / 'takx/recipe/build.py').read_bytes()).hexdigest()
        self.assertEqual(marker['patches'], [{'name': 'recipe.patch', 'sha256': hashlib.sha256(self.PATCH.encode()).hexdigest(),
                                              'files': {'takx/recipe/build.py': {'before': before, 'after': after}}}])
        self.assertEqual(marker['files']['takx/recipe/build.py'], after)
        self.assertEqual(marker['file_count'], len(marker['files']))
        self.assertIn('unchanged', marker['note'])

    def test_current_tree_is_reused_and_a_stale_one_is_replaced_not_deleted(self):
        self.ensure()
        identity = (self.tree / '.takx-arch-tree.json').stat().st_ino
        self.ensure()
        self.assertEqual((self.tree / '.takx-arch-tree.json').stat().st_ino, identity)
        self.assertFalse((self.root / 'failed').exists())
        # The repository changed, a patch changed, the tree was tampered with, a file appeared.
        changes = [lambda: (self.repo / 'takx/other/build.py').write_text('print("changed")\n'),
                   lambda: (self.overrides / 'recipe.patch').write_text(self.PATCH.replace('Why: test', 'Why: reworded')),
                   lambda: (self.tree / 'takx/other/build.py').write_text('print("tampered")\n'),
                   lambda: (self.tree / 'takx/other/extra.py').write_text('extra\n')]
        for number, change in enumerate(changes, 1):
            change()
            self.ensure()
            self.assertEqual(sorted(os.listdir(self.root / 'failed')), ['arch-tree-arm64-%d' % n for n in range(1, number + 1)])
            self.assertEqual((self.tree / 'takx/other/build.py').read_text(), (self.repo / 'takx/other/build.py').read_text())
            self.assertFalse((self.tree / 'takx/other/extra.py').exists())
        self.assertEqual([p.name for p in (self.root / 'overrides/arm64').iterdir() if p.name.startswith('tree.staging')], [])

    def test_tree_in_use_is_not_replaced(self):
        self.ensure()
        (self.repo / 'takx/other/build.py').write_text('print("changed")\n')
        with open(self.root / 'overrides/arm64/.lock') as stage:
            fcntl.flock(stage, fcntl.LOCK_SH)  # what arch.sh holds for a running stage
            with self.assertRaisesRegex(TREE.Refusal, 'in use by a running stage'):
                self.ensure()
            self.assertEqual((self.tree / 'takx/other/build.py').read_text(), 'print("other")\n')
        self.ensure()
        self.assertEqual((self.tree / 'takx/other/build.py').read_text(), 'print("changed")\n')

    def test_inside_a_tree_the_tree_is_the_answer(self):
        tree = self.ensure()
        self.assertEqual(TREE.ensure('arm64', self.root, tree), tree)

    def test_patch_that_does_not_match_exactly_is_refused_and_nothing_is_published(self):
        for text, message in (
                (self.PATCH.replace(' import platform\n', ' import os\n'), 'does not apply with fuzz 0'),
                (self.PATCH.replace('@@ -1,4 +1,4 @@\n import platform\n', '@@ -2,3 +2,3 @@\n'), None),
                (self.PATCH.replace('takx/recipe/build.py', 'takx/other/build.py'), 'may only change files below takx/recipe/'),
                (self.PATCH.replace('takx/recipe/build.py', 'takx/recipe/../other/build.py'), 'may only change files below'),
                (self.PATCH.replace('+++ b/takx/recipe/build.py', '+++ b/takx/recipe/new.py'), 'renames are not supported'),
                ('# only a comment\n', 'no file header found')):
            (self.overrides / 'recipe.patch').write_text(text)
            with self.subTest(message=message):
                if message is None:  # a hunk without leading context still has to match line for line
                    self.ensure()
                    shutil.rmtree(self.root)
                    continue
                with self.assertRaisesRegex(TREE.Refusal, message):
                    self.ensure()
                self.assertFalse(self.tree.exists())
                self.assertEqual([p.name for p in (self.root / 'overrides/arm64').iterdir() if p.name != '.lock'], [])
        (self.overrides / 'recipe.patch').write_text(self.PATCH)
        (self.overrides / 'desktop-prototype.patch').write_text(self.PATCH.replace('takx/recipe/', 'takx/desktop-prototype/'))
        with self.assertRaisesRegex(TREE.Refusal, 'not named after a recipe'):
            self.ensure()

    # One line of context on each side: a hunk with less context before than
    # after is taken to belong to the start of the file and is never moved.
    STAGE_BEFORE = 'import re\nNATIVES = "natives-linux-amd64"\nJAR = "kernel-amd64.jar"\nprint(NATIVES, JAR)\n'
    STAGE_PATCH = ('# Copyright 2026 uds-takserver authors\n# SPDX-License-Identifier: GPL-3.0-only\n# Why: test\n'
                   '--- a/takx/desktop-prototype/assemble/assemble.py\n+++ b/takx/desktop-prototype/assemble/assemble.py\n'
                   '@@ -1,3 +1,3 @@\n import re\n-NATIVES = "natives-linux-amd64"\n+NATIVES = "natives-linux-aarch64"\n'
                   ' JAR = "kernel-amd64.jar"\n')

    def stage_fixture(self, before=None, patch=None):
        target = self.repo / 'takx/desktop-prototype/assemble/assemble.py'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.STAGE_BEFORE if before is None else before)
        (self.overrides / 'prototype').mkdir(exist_ok=True)
        (self.overrides / 'prototype/assemble.patch').write_text(self.STAGE_PATCH if patch is None else patch)
        return target

    def test_prototype_patch_changes_the_copy_of_a_stage_file_only(self):
        target = self.stage_fixture()
        self.assertEqual(self.ensure(), self.tree)
        self.assertEqual(target.read_text(), self.STAGE_BEFORE)
        copied = self.tree / 'takx/desktop-prototype/assemble/assemble.py'
        self.assertIn('natives-linux-aarch64', copied.read_text())
        marker = json.loads((self.tree / '.takx-arch-tree.json').read_text())
        self.assertEqual([row['name'] for row in marker['patches']], ['recipe.patch'])
        self.assertEqual(marker['stage_patches'], [{
            'name': 'prototype/assemble.patch', 'sha256': hashlib.sha256(self.STAGE_PATCH.encode()).hexdigest(),
            'files': {'takx/desktop-prototype/assemble/assemble.py': {
                'before': hashlib.sha256(self.STAGE_BEFORE.encode()).hexdigest(),
                'after': hashlib.sha256(copied.read_bytes()).hexdigest()}}}])
        self.assertEqual(marker['files']['takx/desktop-prototype/assemble/assemble.py'],
                         hashlib.sha256(copied.read_bytes()).hexdigest())
        # The tree is current; a changed prototype patch replaces it.
        self.assertEqual(self.ensure(), self.tree)
        self.assertFalse((self.root / 'failed').exists())
        (self.overrides / 'prototype/assemble.patch').write_text(self.STAGE_PATCH.replace('# Why: test', '# Why: again'))
        self.assertEqual(self.ensure(), self.tree)
        self.assertEqual(len(list((self.root / 'failed').iterdir())), 1)

    def test_prototype_patch_follows_lines_that_moved_but_not_lines_that_changed(self):
        before = '# a new first line\n# and another\n' + self.STAGE_BEFORE
        self.stage_fixture(before=before)
        self.assertEqual(self.ensure(), self.tree)
        copied = (self.tree / 'takx/desktop-prototype/assemble/assemble.py').read_text()
        self.assertEqual(copied, before.replace('natives-linux-amd64', 'natives-linux-aarch64'))
        row, = json.loads((self.tree / '.takx-arch-tree.json').read_text())['stage_patches']
        self.assertEqual(sorted(row), ['files', 'moved', 'name', 'sha256'])
        self.assertEqual(row['moved'], ['Hunk #1 succeeded at 3 (offset 2 lines)'])
        # A recipe patch is never followed: the same shift there refuses the tree.
        self.assertRegex('Hunk #1 succeeded at 3 (offset 2 lines)', TREE.INEXACT)
        self.assertNotRegex('Hunk #1 succeeded at 3 (offset 2 lines)', TREE.FUZZY)
        self.assertRegex('Hunk #1 succeeded at 3 with fuzz 1 (offset 2 lines)', TREE.FUZZY)

    def test_prototype_patch_that_went_stale_is_skipped_and_the_tree_is_still_built(self):
        for label, before in (('context', self.STAGE_BEFORE.replace('JAR = "kernel-amd64.jar"', 'JAR = "kernel.jar"')),
                              ('changed', self.STAGE_BEFORE.replace('natives-linux-amd64', 'natives-' + 'x'))):
            with self.subTest(label):
                shutil.rmtree(self.root, ignore_errors=True)
                self.stage_fixture(before=before)
                self.assertEqual(self.ensure(), self.tree)
                self.assertEqual((self.tree / 'takx/desktop-prototype/assemble/assemble.py').read_text(), before)
                self.assertIn('not in ("x86_64", "aarch64")', (self.tree / 'takx/recipe/build.py').read_text())
                marker = json.loads((self.tree / '.takx-arch-tree.json').read_text())
                row, = marker['stage_patches']
                self.assertEqual((row['name'], sorted(row)), ('prototype/assemble.patch', ['name', 'sha256', 'skipped']))
                self.assertIn('does not apply exactly', row['skipped'])
                # Nothing of the failed attempt is left in the copy (no reject or backup file).
                self.assertEqual(TREE.listing(self.tree), marker['files'])
                self.assertEqual(sorted(p.name for p in (self.tree / 'takx/desktop-prototype/assemble').iterdir()),
                                 ['assemble.py'])
        shutil.rmtree(self.root)
        (self.repo / 'takx/desktop-prototype/assemble/assemble.py').unlink()
        self.ensure()
        row, = json.loads((self.tree / '.takx-arch-tree.json').read_text())['stage_patches']
        self.assertIn('does not exist in the tree', row['skipped'])

    def test_prototype_patch_may_only_name_the_listed_stage_files(self):
        self.assertEqual(TREE.STAGE_PREFIXES, ('takx/desktop-prototype/assemble/',))
        for patch, message in (
                (self.STAGE_PATCH.replace('desktop-prototype/assemble/assemble.py', 'desktop-prototype/arch.sh'),
                 'may only change files below takx/desktop-prototype/assemble/'),
                (self.STAGE_PATCH.replace('desktop-prototype/assemble/assemble.py', 'recipe/build.py'), 'may only change files below'),
                (self.STAGE_PATCH.replace('assemble/assemble.py', 'assemble/../provider_cache.py'), 'may only change files below'),
                ('# only a comment\n', 'no file header found')):
            with self.subTest(message=message):
                self.stage_fixture(patch=patch)
                with self.assertRaisesRegex(TREE.Refusal, message):
                    self.ensure()
                self.assertFalse(self.tree.exists())

    def test_command_line_reports_prototype_patches(self):
        script = [sys.executable, '-I', '-B', str(HERE / 'arch_tree.py'), 'check', '--arch', 'arm64', '--repo', str(self.repo)]
        self.stage_fixture()
        checked = subprocess.run(script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual(checked.stdout, 'arm64 recipe.patch: fuzz 0, takx/recipe/build.py\n'
                         'arm64 prototype/assemble.patch: fuzz 0, takx/desktop-prototype/assemble/assemble.py\n'
                         'arm64: 1 of 1 prototype patches apply exactly (a skipped one affects only its stage)\n'
                         'arm64: 1 override patches apply exactly\n')
        self.stage_fixture(before='# moved\n' + self.STAGE_BEFORE)
        checked = subprocess.run(script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        self.assertIn('arm64 prototype/assemble.patch: fuzz 0, takx/desktop-prototype/assemble/assemble.py; '
                      '1 hunks at another line than recorded\n', checked.stdout)
        self.assertIn('arm64: 1 of 1 prototype patches apply exactly', checked.stdout)
        self.stage_fixture(before=self.STAGE_BEFORE.replace('JAR = "kernel-amd64.jar"', 'JAR = "kernel.jar"'))
        checked = subprocess.run(script, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual(checked.returncode, 0)
        self.assertIn('arm64 prototype/assemble.patch: NOT APPLIED, does not apply exactly', checked.stdout)
        self.assertIn('arm64: 0 of 1 prototype patches apply exactly', checked.stdout)
        self.assertTrue(checked.stdout.endswith('arm64: 1 override patches apply exactly\n'))

    def test_shifted_hunk_is_refused(self):
        (self.repo / 'takx/recipe/build.py').write_text('# a new first line\n' + self.BEFORE)
        with self.assertRaisesRegex(TREE.Refusal, 'applied inexactly|does not apply'):
            self.ensure()
        self.assertFalse(self.tree.exists())

    def test_work_root_inside_the_repository_and_symbolic_links_are_refused(self):
        with self.assertRaisesRegex(TREE.Refusal, 'outside the repository'):
            TREE.ensure('arm64', self.repo / 'work', self.repo)
        os.symlink('/etc/passwd', self.repo / 'takx/other/link')
        with self.assertRaisesRegex(TREE.Refusal, 'symbolic link'):
            self.ensure()

    def test_command_line(self):
        script = [sys.executable, '-I', '-B', str(HERE / 'arch_tree.py')]
        common = ['--repo', str(self.repo), '--root', str(self.root)]
        done = subprocess.run(script + ['ensure', '--arch', 'arm64'] + common, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual((done.returncode, done.stdout), (0, '%s\n' % self.tree))
        self.assertIn('1 patches, 1 files changed', done.stderr)
        shown = subprocess.run(script + ['show', '--arch', 'arm64'] + common, stdout=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual(json.loads(shown.stdout)['patches'][0]['name'], 'recipe.patch')
        checked = subprocess.run(script + ['check', '--arch', 'arm64'] + common, stdout=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual(checked.stdout, 'arm64 recipe.patch: fuzz 0, takx/recipe/build.py\n'
                                         'arm64: 1 override patches apply exactly\n')
        refused = subprocess.run(script + ['ensure', '--arch', 'amd64'] + common, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, timeout=60)
        self.assertEqual((refused.returncode, refused.stdout), (2, ''))
        self.assertIn('the tracked tree is used as it is', refused.stderr)


class Pipeline(unittest.TestCase):
    PROVIDERS = json.loads((HERE / 'providers.json').read_text())['providers']

    def test_groups_cover_every_provider_in_dependency_waves(self):
        self.assertEqual(PIPELINE.check(), [])
        data = PIPELINE.groups()
        built = [p for config in data['groups'].values() for p in config['builds']]
        self.assertEqual(sorted(built), sorted(self.PROVIDERS))
        # Each stage script is asked for exactly the providers its group builds.
        for name, config in data['groups'].items():
            if config['builds']:
                asked = [step for command in config['run'] for step in command[1:]]
                self.assertEqual(sorted(asked), sorted(config['builds']), name)
                for command in config['run']:
                    for step in command[1:]:
                        self.assertEqual(self.PROVIDERS[step]['lane'], command[0], step)

    def test_prerequisites_are_the_whole_closure(self):
        groups = PIPELINE.groups()['groups']
        self.assertEqual(PIPELINE.restore_list(groups['native-base'], self.PROVIDERS), [])
        self.assertEqual(sorted(PIPELINE.restore_list(groups['scene'], self.PROVIDERS)),
                         ['assimp', 'draco', 'gis-java', 'proj', 'sqlcipher'])
        self.assertEqual(sorted(PIPELINE.restore_list(groups['kml'], self.PROVIDERS)),
                         ['assimp', 'cesium', 'cesium-acq', 'draco', 'las', 'proj', 'scene', 'sqlcipher'])
        everything = PIPELINE.restore_list(groups['client'], self.PROVIDERS)
        self.assertEqual(sorted(everything), sorted(self.PROVIDERS))
        for index, name in enumerate(everything):  # prerequisites first
            for dependency in self.PROVIDERS[name]['prerequisites']:
                self.assertLess(everything.index(dependency), index)

    def test_inconsistent_groups_are_reported(self):
        def problems(change):
            data = json.loads(json.dumps(PIPELINE.groups()))
            change(data['groups'])
            return PIPELINE.check(data, self.PROVIDERS)
        self.assertIn('provider kml is built by no group', problems(lambda g: g.pop('kml')))
        self.assertTrue(any('sqlcipher is built by' in p for p in problems(lambda g: g['java']['builds'].append('sqlcipher'))))
        self.assertTrue(any(p.startswith('kml (wave a) needs') for p in problems(lambda g: g['kml'].update(wave='a'))))
        self.assertIn('java: unknown provider nope', problems(lambda g: g['java']['builds'].append('nope')))
        self.assertIn('java: no stage script lane9.sh', problems(lambda g: g['java']['run'].append(['lane9.sh'])))

    def test_trigger_file_and_request(self):
        both = ['amd64', 'arm64']
        self.assertEqual(PIPELINE.parse_trigger(''), {'arches': both, 'providers': 'build'})
        self.assertEqual(PIPELINE.parse_trigger('# note\n\narches = arm64\n'), {'arches': ['arm64'], 'providers': 'build'})
        self.assertEqual(PIPELINE.parse_trigger('arches=arm64, amd64,arm64\n'), {'arches': both, 'providers': 'build'})
        self.assertEqual(PIPELINE.parse_trigger('providers = cached\narches=amd64\n'),
                         {'arches': ['amd64'], 'providers': 'cached'})
        self.assertEqual(PIPELINE.parse_trigger('providers=build\n'), {'arches': both, 'providers': 'build'})
        for text in ('arches=\n', 'arches=riscv64\n', 'run this\n', 'cache=off\n', 'providers=\n', 'providers=skip\n'):
            with self.subTest(text=text), self.assertRaises(PIPELINE.Refusal):
                PIPELINE.parse_trigger(text)
        with tempfile.TemporaryDirectory() as directory:
            trigger = Path(directory) / 'trigger'
            with self.assertRaisesRegex(PIPELINE.Refusal, 'does not exist'):
                PIPELINE.plan(trigger, '')
            self.assertEqual(PIPELINE.plan(trigger, '["arm64"]'), {'arches': ['arm64'], 'providers': 'build'})
            trigger.write_text('arches=amd64\nproviders=cached\n')
            self.assertEqual(PIPELINE.plan(trigger, ''), {'arches': ['amd64'], 'providers': 'cached'})
            # A dispatch request names architectures only and always builds the providers.
            self.assertEqual(PIPELINE.plan(trigger, '["arm64","amd64"]'), {'arches': both, 'providers': 'build'})
            for bad in ('arm64', '[]', '["x"]', '{"a":1}', '[1]'):
                with self.subTest(bad=bad), self.assertRaises(PIPELINE.Refusal):
                    PIPELINE.plan(trigger, bad)
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(PIPELINE.main(['plan', '--trigger', str(trigger)]), 0)
            self.assertEqual(printed.getvalue(), 'arches=["amd64"]\nproviders=cached\n')

    def test_processor_record_names_the_model_and_known_extensions(self):
        with tempfile.TemporaryDirectory() as directory:
            x86 = Path(directory) / 'x86'
            x86.write_text('processor\t: 0\nmodel name\t: Example CPU @ 2.0GHz\nflags\t\t: fpu sse3 avx2 avx512f zzz\n'
                           'processor\t: 1\nmodel name\t: Other\nflags\t\t: fpu\n')
            arm = Path(directory) / 'arm'
            arm.write_text('processor\t: 0\nFeatures\t: fp asimd sve\nCPU part\t: 0xd40\n')
            self.assertEqual(PIPELINE.cpu_record(x86), {'model': 'Example CPU @ 2.0GHz', 'extensions': ['sse3', 'avx2', 'avx512f']})
            self.assertEqual(PIPELINE.cpu_record(arm), {'model': '0xd40', 'extensions': ['asimd', 'sve']})
            self.assertEqual(PIPELINE.cpu_record(Path(directory) / 'absent'), {'model': 'unknown', 'extensions': []})

    def test_summary_lists_statuses_and_the_architecture_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'status').mkdir()
            self.assertIn('No status file was written.', PIPELINE.summary(root))
            (root / 'status/proj.json').write_text(json.dumps({'stage': 'proj', 'status': 'ok', 'seconds': 17,
                                                               'notes': 'a | b\nc', 'restored_from': 'reg/proj:key'}))
            (root / 'status/broken.json').write_text('{')
            (root / 'status/list.json').write_text('[]')
            tree = root / 'overrides/arm64/tree'
            tree.mkdir(parents=True)
            (tree / '.takx-arch-tree.json').write_text(json.dumps({'arch': 'arm64', 'patches': [{'name': 'x.patch'}]}))
            self.assertNotIn('Prototype patch', PIPELINE.summary(root))
            (tree / '.takx-arch-tree.json').write_text(json.dumps({
                'arch': 'arm64', 'patches': [{'name': 'x.patch'}], 'stage_patches': [
                    {'name': 'prototype/a.patch', 'files': {'f': {}}},
                    {'name': 'prototype/c.patch', 'files': {'f': {}}, 'moved': ['Hunk #1 succeeded at 9 (offset 2 lines)']},
                    {'name': 'prototype/b.patch', 'skipped': 'does not apply exactly:\n hunk 1'}]}))
            text = PIPELINE.summary(root)
        self.assertIn('Prototype patch `prototype/a.patch`: applied to the copy.', text)
        self.assertIn('Prototype patch `prototype/c.patch`: applied to the copy (1 hunks at another line than recorded).', text)
        self.assertIn('Prototype patch `prototype/b.patch`: NOT applied, does not apply exactly: hunk 1.', text)
        self.assertIn('| proj | ok | 17 | restored from the provider cache: reg/proj:key. a \\| b c |', text)
        self.assertIn('| broken | unreadable |  |  |', text)
        self.assertIn('Architecture tree: `arm64`, 1 override patches (x.patch).', text)
        self.assertIn('Kernel 8.30.5 + project patches and CommonCommo 1.17.2 patched', text)


@unittest.skipUnless(shutil.which('patch'), 'the patch tool is required')
class PipelineRun(Fixture):
    """pipeline.py run against stub stages, a stub cache and no docker."""

    CACHE = ('import json, os, sys\nroot, name = os.environ["TAKX_PROTO_ROOT"], sys.argv[2]\n'
             'open(root + "/cache-calls", "a").write(os.environ["TAKX_PROTO_CACHE"] + " " + " ".join(sys.argv[1:]) + " " + '
             '__file__ + "\\n")\n'
             'if os.environ["TAKX_PROTO_CACHE"] + ":" + name not in os.environ.get("STUB_HITS", "").split():\n    sys.exit(3)\n'
             'os.makedirs(root + "/P/" + name)\nopen(root + "/P/" + name + "/complete.json", "w").write("{}")\n'
             'json.dump({"stage": name, "status": "ok", "restored_from": os.environ["TAKX_PROTO_CACHE"]}, '
             'open(root + "/status/" + name + ".json", "w"))\n')
    STAGE = ('#!/bin/bash\necho "$0 $* wait=$TAKX_PREREQ_TIMEOUT,$TAKX_LANE3_WAIT_S" >> "$TAKX_PROTO_ROOT/stage-calls"\n'
             'exit "${STUB_STAGE_EXIT:-0}"\n')

    def setUp(self):
        super().setUp()
        proto = self.repo / 'takx/desktop-prototype'
        for name in ('arch.sh', 'arch_tree.py'):
            shutil.copy2(HERE / name, proto / name)
        (proto / 'ci').mkdir()
        (proto / 'provider_cache.py').write_text(self.CACHE)
        entry = {'lane': 'lane.sh', 'recipe': 'takx/recipe', 'locks': [], 'fetched': [], 'marker': 'complete.json',
                 'invocation': {'argv': ['x'], 'env': []}}
        (proto / 'providers.json').write_text(json.dumps({'providers': {
            'base': dict(entry, prerequisites=[]), 'top': dict(entry, prerequisites=['base'])}}))
        (proto / 'stages/lane.sh').write_text(self.STAGE)
        self.groups = {'schema': 1, 'waves': ['a', 'b'], 'groups': {
            'first': {'wave': 'a', 'builds': ['base'], 'run': [['lane.sh', 'base']]},
            'second': {'wave': 'b', 'builds': ['top'], 'run': [['lane.sh', 'top'], ['lane.sh', 'again']]}}}
        self.proto = proto

    def run_group(self, group, arch, **env):
        environment = {'TAKX_PROTO_ARCH': arch, 'TAKX_PROTO_CACHE': 'primary', 'TAKX_CI_CACHE_FALLBACK': 'second',
                       'TAKX_PROTO_ROOT': str(self.root)}
        environment.update(env)
        with mock.patch.dict(os.environ, environment), mock.patch.object(PIPELINE, 'groups', lambda: self.groups), \
                mock.patch('builtins.print'), contextlib.redirect_stderr(io.StringIO()):
            code = PIPELINE.run_group(group, self.root, self.proto)
        record = json.loads((self.root / 'status' / ('ci-%s.json' % group)).read_text())
        return code, record

    def lines(self, name):
        path = self.root / name
        return path.read_text().splitlines() if path.exists() else []

    def test_amd64_runs_the_tracked_stages_and_never_builds_a_tree(self):
        code, record = self.run_group('first', 'amd64')
        self.assertEqual((code, record['status'], record['restored']), (0, 'ok', {}))
        self.assertEqual(record['stages_from'], str(self.proto))
        self.assertEqual(self.lines('stage-calls'), ['%s/stages/lane.sh base wait=0,0' % self.proto])
        self.assertFalse((self.root / 'overrides').exists())
        # The second cache is asked for the group's own provider; the stage restores from the first itself.
        self.assertEqual(self.lines('cache-calls'), ['second restore base %s/provider_cache.py' % self.proto])

    def test_stages_are_told_the_lineage_of_the_providers_they_consume(self):
        stage = self.proto / 'stages/lane.sh'
        stage.write_text('#!/bin/bash\necho "lineage=${TAKX_CLIENT_LOCK_LINEAGE-unset}" >> "$TAKX_PROTO_ROOT/stage-calls"\n')
        for source, expected in (('ci-reused', 'ci'), ('ci-built', 'ci'), ('reference', ''), ('', '')):
            with self.subTest(source=source):
                shutil.rmtree(self.root, ignore_errors=True)
                with mock.patch.dict(os.environ):
                    os.environ.pop('TAKX_CLIENT_LOCK_LINEAGE', None)
                    code, record = self.run_group('first', 'amd64', TAKX_CI_IMAGE_SOURCE=source)
                self.assertEqual((code, record['client_lock_lineage']), (0, expected))
                self.assertEqual(self.lines('stage-calls'), ['lineage=%s' % (expected or 'unset')])

    def test_arm64_runs_everything_from_the_patched_tree(self):
        code, record = self.run_group('first', 'arm64')
        proto = self.tree / 'takx/desktop-prototype'
        self.assertEqual((code, record['status'], record['arch']), (0, 'ok', 'arm64'))
        self.assertEqual(record['stages_from'], str(proto))
        self.assertEqual(self.lines('stage-calls'), ['%s/stages/lane.sh base wait=0,0' % proto])
        self.assertEqual(self.lines('cache-calls'), ['second restore base %s/provider_cache.py' % proto])
        self.assertIn('"aarch64"', (self.tree / 'takx/recipe/build.py').read_text())

    def test_missing_prerequisite_blocks_the_group_before_any_stage(self):
        code, record = self.run_group('second', 'amd64')
        self.assertEqual((code, record['status'], record['restored'], record['commands']), (1, 'blocked', {'base': None}, []))
        self.assertIn('prerequisites are not in the provider cache: base', record['notes'])
        self.assertEqual(self.lines('stage-calls'), [])
        self.assertEqual([line.split()[:3] for line in self.lines('cache-calls')],
                         [['primary', 'restore', 'base'], ['second', 'restore', 'base'], ['second', 'restore', 'top']])

    def test_prerequisite_comes_from_the_first_cache_that_has_it(self):
        for hits, source in (('primary:base', 'primary'), ('second:base', 'second'), ('primary:base second:base', 'primary')):
            shutil.rmtree(self.root, ignore_errors=True)
            code, record = self.run_group('second', 'amd64', STUB_HITS=hits)
            with self.subTest(hits=hits):
                self.assertEqual((code, record['status'], record['restored']), (0, 'ok', {'base': source}))
                self.assertEqual(self.lines('stage-calls'), ['%s/stages/lane.sh top wait=0,0' % self.proto,
                                                             '%s/stages/lane.sh again wait=0,0' % self.proto])
        # A second run finds the prerequisite on disk and asks no cache for it.
        (self.root / 'cache-calls').unlink()
        code, record = self.run_group('second', 'amd64')
        self.assertEqual((code, record['restored']), (0, {'base': 'local'}))
        self.assertEqual([line.split()[:3] for line in self.lines('cache-calls')], [['second', 'restore', 'top']])

    def test_failed_stage_fails_the_group_but_later_stages_still_run(self):
        code, record = self.run_group('second', 'amd64', STUB_HITS='primary:base', STUB_STAGE_EXIT='5')
        self.assertEqual((code, record['status']), (1, 'failed'))
        self.assertEqual([(c['command'], c['exit']) for c in record['commands']],
                         [(['lane.sh', 'top'], 5), (['lane.sh', 'again'], 5)])
        self.assertIn('failed: lane.sh top; lane.sh again', record['notes'])

    def test_unknown_group_is_refused(self):
        with mock.patch.object(PIPELINE, 'groups', lambda: self.groups), self.assertRaisesRegex(PIPELINE.Refusal, 'unknown group'):
            PIPELINE.run_group('nope', self.root, self.proto)

    def test_group_records_the_machine_of_the_elf_objects_it_built(self):
        self.groups['groups']['first']['audit'] = ['extra', 'absent']
        self.groups['groups']['first']['audit_root'] = ['dist/tree', 'dist/absent']
        (self.root / 'dist/tree/native').mkdir(parents=True)
        (self.root / 'dist/tree/native/libjni.so').write_bytes(elf(183))
        (self.root / 'P/base/lib').mkdir(parents=True)
        (self.root / 'P/extra').mkdir()
        (self.root / 'P/base/lib/libone.so').write_bytes(elf(183))
        (self.root / 'P/base/lib/libtwo.a').write_bytes(archive([b'/ symbols', elf(183), elf(183) + b'x']))
        (self.root / 'P/base/notes.txt').write_text('not an object')
        (self.root / 'P/extra/tool').write_bytes(elf(62))
        code, record = self.run_group('first', 'arm64')
        self.assertEqual(code, 0)
        self.assertEqual(record['elf'], {
            'base': {'expected': 'aarch64', 'objects': {'aarch64': 3}, 'not_expected': []},
            'extra': {'expected': 'aarch64', 'objects': {'x86_64': 1}, 'not_expected': ['tool']},
            'dist/tree': {'expected': 'aarch64', 'objects': {'aarch64': 1}, 'not_expected': []}})
        self.assertEqual(PIPELINE.groups()['groups']['boot']['audit_root'], ['dist/takx57'])


def elf(machine, order=1):
    """The first bytes of an ELF64 file of one machine number."""
    return b'\x7fELF\x02' + bytes([order]) + b'\x01' + bytes(9) + b'\x03\x00' + \
        machine.to_bytes(2, 'little' if order == 1 else 'big') + bytes(44)


def archive(members):
    data = b'!<arch>\n'
    for number, member in enumerate(members):
        data += ('m%-15d0           0     0     644     %-10d`\n' % (number, len(member))).encode() + member
        data += b'\n' * (len(member) & 1)
    return data


class InputsStore(unittest.TestCase):
    """ci/inputs.sh: the key, and the refusals that need no registry."""

    SCRIPT = HERE / 'ci/inputs.sh'

    def call(self, *arguments, **env):
        environment = dict(os.environ, CI_IMAGES='registry.invalid/ci', **env)
        return subprocess.run(['bash', str(self.SCRIPT), *arguments], capture_output=True, text=True, env=environment,
                              timeout=60)

    @unittest.skipUnless((HERE.parent.parent / '.git').exists(), 'needs the Git index of the repository')
    def test_key_names_the_architecture_and_depends_on_the_toolchain(self):
        keys = {}
        for arch in ('amd64', 'arm64'):
            for identity in ('sha256:' + 'a' * 64, 'sha256:' + 'b' * 64):
                done = self.call('key', arch, identity)
                self.assertEqual(done.returncode, 0, done.stderr)
                self.assertRegex(done.stdout, r'\Aregistry\.invalid/ci/inputs:%s-[0-9a-f]{16}\n\Z' % arch)
                keys[arch, identity] = done.stdout
        self.assertEqual(len(set(keys.values())), 4)
        self.assertEqual(self.call('key', 'arm64', 'sha256:' + 'a' * 64).stdout, keys['arm64', 'sha256:' + 'a' * 64])

    def test_key_refuses_an_unknown_architecture_and_a_missing_identity(self):
        self.assertNotEqual(self.call('key', 'riscv64', 'sha256:' + 'a' * 64).returncode, 0)
        self.assertEqual(self.call('key', 'arm64', 'latest').returncode, 2)
        self.assertEqual(self.call('key', 'arm64').returncode, 2)
        self.assertEqual(self.call('nonsense').returncode, 2)

    def test_save_refuses_a_fetch_that_is_not_ok_and_restore_refuses_a_used_work_root(self):
        with tempfile.TemporaryDirectory() as root:
            done = self.call('save', 'registry.invalid/ci/inputs:x', TAKX_PROTO_ROOT=root)
            self.assertEqual(done.returncode, 1)
            self.assertIn('status/fetch.json is not ok', done.stderr)
            os.makedirs(os.path.join(root, 'status'))
            Path(root, 'status/fetch.json').write_text('{"status": "failed"}')
            self.assertEqual(self.call('save', 'registry.invalid/ci/inputs:x', TAKX_PROTO_ROOT=root).returncode, 1)
            os.makedirs(os.path.join(root, 'cache'))
            done = self.call('restore', 'registry.invalid/ci/inputs:x', TAKX_PROTO_ROOT=root)
            self.assertEqual(done.returncode, 1)
            self.assertIn('restored into an empty work root only', done.stderr)
            done = self.call('seed', 'registry.invalid/ci/inputs:x', TAKX_PROTO_ROOT=root)
            self.assertEqual(done.returncode, 1)
            self.assertIn('restored into an empty work root only', done.stderr)

    def test_a_fetch_may_start_from_the_latest_tag_and_then_from_tracked_digest_references(self):
        tracked = json.loads((HERE / 'ci/inputs-seeds.json').read_text())
        self.assertEqual(sorted(tracked['seeds']), ['amd64', 'arm64'])
        for arch in ('amd64', 'arm64'):
            done = self.call('seeds', arch)
            self.assertEqual(done.returncode, 0, done.stderr)
            lines = done.stdout.splitlines()
            self.assertEqual(lines[0], 'registry.invalid/ci/inputs:%s-latest' % arch)
            self.assertEqual(lines[1:], tracked['seeds'][arch])
            self.assertEqual(self.call('latest', arch).stdout.splitlines(), lines[:1])
            for reference in lines[1:]:
                # A tracked seed names existing bytes by digest, never a tag that can move.
                self.assertRegex(reference, r'\Aghcr\.io/[a-z0-9./_-]+/takx-desktop-proto-ci/inputs@sha256:[0-9a-f]{64}\Z')
        self.assertNotEqual(self.call('seeds', 'riscv64').returncode, 0)
        self.assertEqual(self.call('seed').returncode, 2)
        self.assertEqual(self.call('save').returncode, 2)

    def test_a_seed_takes_only_the_stores_the_fetch_verifies_itself(self):
        text = self.SCRIPT.read_text()
        self.assertIn("unpack_stored \"${reference}\" 'takx-inputs/cache/*' 'takx-inputs/jars/runtime-extras/*'", text)
        # The inputs job fetches after a seed and stores what that fetch produced.
        enter = (HERE / 'ci/enter.sh').read_text()
        self.assertLess(enter.index('inputs.sh" seed '), enter.index('"${proto}/fetch.sh"'))
        self.assertLess(enter.index('"${proto}/fetch.sh"'), enter.index('inputs.sh" save '))


class StockLoaderDirectory(unittest.TestCase):
    """The Kernel controls must name the extraction directory as the stock loader does.

    Found on amd64 in the workflow: the composed Kernel JAR had the MD5
    04d88bf9..., the loader extracted into .../engine/4d88bf9.../lib/linux64 and
    the controls put .../engine/04d88bf9.../lib/linux64 on java.library.path, so
    gdalJNI.isAvailable() was false in six controls.
    """

    @staticmethod
    def module(name):
        spec = importlib.util.spec_from_file_location('takx_proto_kernel_' + name, HERE / 'kernel' / (name + '.py'))
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        return loaded

    def test_one_leading_zero_of_the_md5_is_dropped_as_hashing_utils_does(self):
        samples, number = {}, 0
        while len(samples) < 3:
            data = b'kernel jar %d' % number
            value = hashlib.md5(data).hexdigest()
            kind = 'two' if value.startswith('00') else 'one' if value.startswith('0') else 'none'
            samples.setdefault(kind, (data, value))
            number += 1
        with tempfile.TemporaryDirectory() as directory:
            for name in ('controls', 'compose'):
                function = self.module(name).stock_loader_directory_name
                for kind, (data, value) in samples.items():
                    with self.subTest(module=name, md5=kind):
                        jar = Path(directory) / 'kernel.jar'
                        jar.write_bytes(data)
                        # Only the first zero goes: a digest that starts with 00 keeps one.
                        expected = value if kind == 'none' else value[1:]
                        self.assertEqual(function(jar), expected)
                        self.assertEqual(len(function(jar)), 32 if kind == 'none' else 31)

    def test_neither_script_predicts_the_directory_from_the_plain_digest(self):
        for name in ('controls.py', 'compose.py'):
            text = (HERE / 'kernel' / name).read_text()
            self.assertEqual(text.count('hashlib.md5('), 1, name)
            self.assertIn('stock_loader_directory_name(', text.split('hashlib.md5(')[1], name)

    @unittest.skipUnless((REPO / 'upstream/atak-civ/takkernel/engine/src/main/java/com/atakmap/filesystem/HashingUtils.java')
                         .is_file(), 'needs the atak-civ submodule')
    def test_rule_is_the_one_in_the_pinned_kernel_source(self):
        base = REPO / 'upstream/atak-civ/takkernel/engine/src'
        hashing = ' '.join((base / 'main/java/com/atakmap/filesystem/HashingUtils.java').read_text().split())
        self.assertIn('if (checksum.startsWith("0")) checksum = checksum.substring(1);', hashing)
        loader = ' '.join((base / 'jre/java/com/atakmap/map/EngineLibrary.java').read_text().split())
        self.assertIn('id = HashingUtils.md5sum(d);', loader)
        self.assertIn('combine(System.getProperty("java.io.tmpdir"), "takkernel", "engine", id)', loader)


class ElfAudit(unittest.TestCase):
    def test_machine_names(self):
        self.assertEqual(PIPELINE.elf_machine(elf(183)[:20]), 'aarch64')
        self.assertEqual(PIPELINE.elf_machine(elf(62)[:20]), 'x86_64')
        self.assertEqual(PIPELINE.elf_machine(elf(183, order=2)[:20]), 'aarch64')
        self.assertEqual(PIPELINE.elf_machine(elf(243)[:20]), 'machine-243')
        for other in (b'', b'\x7fELF', b'#!/bin/sh\n' + bytes(20), b'PK\x03\x04' + bytes(20)):
            self.assertIsNone(PIPELINE.elf_machine(other[:20]))

    def test_archive_members_are_counted_and_a_truncated_archive_ends_quietly(self):
        whole = archive([elf(183), b'odd', elf(62), elf(183)])
        self.assertEqual(PIPELINE.archive_machines(io.BytesIO(whole[8:])), ['aarch64', 'x86_64', 'aarch64'])
        self.assertEqual(PIPELINE.archive_machines(io.BytesIO(whole[8:8 + 60 + 64 + 30])), ['aarch64'])
        self.assertEqual(PIPELINE.archive_machines(io.BytesIO(b'not a member header' * 5)), [])


class Workflow(unittest.TestCase):
    TEXT = WORKFLOW.read_text()

    def test_opt_in_triggers_only(self):
        head = self.TEXT[self.TEXT.index('\non:\n'):self.TEXT.index('\npermissions:\n')]
        self.assertEqual(head, '\non:\n  push:\n    branches:\n      - claude/awesome-rubin-2godw2\n    paths:\n'
                               '      - "takx/desktop-prototype/ci/trigger"\n'
                               '  # Usable only once this file is on the default branch.\n  workflow_dispatch:\n'
                               '    inputs:\n      arches:\n'
                               '        description: JSON list of architectures to build (amd64, arm64)\n'
                               '        type: string\n        default: \'["amd64","arm64"]\'\n')
        for word in ('pull_request', 'schedule:', 'workflow_run'):
            self.assertNotIn(word, self.TEXT)

    def test_least_privilege_and_no_credentials(self):
        self.assertIn('\npermissions:\n  contents: read\n\n', self.TEXT)
        self.assertEqual(self.TEXT.count('packages: write'), 5)  # toolchain, inputs and the three provider waves
        self.assertEqual(self.TEXT.count('packages: read'), 1)   # the final job pushes nothing
        for scope in ('id-token', 'contents: write', 'actions: write', 'pull-requests', 'write-all'):
            self.assertNotIn(scope, self.TEXT)
        self.assertEqual(set(re.findall(r'secrets\.(\w+)', self.TEXT)), {'GITHUB_TOKEN'})
        self.assertEqual(self.TEXT.count('persist-credentials: false'), self.TEXT.count('actions/checkout@'))

    def test_actions_are_pinned_and_jobs_are_bounded(self):
        uses = re.findall(r'uses: (\S+)(.*)', self.TEXT)
        self.assertTrue(uses)
        for action, comment in uses:
            self.assertRegex(action, r'^[\w.-]+/[\w.-]+@[0-9a-f]{40}$')
            self.assertRegex(comment, r'^ # v\d+\.\d+\.\d+$')
        jobs = re.findall(r'(?m)^  ([a-z-]+):\n(?:    .*\n|\n)+', self.TEXT[self.TEXT.index('\njobs:\n'):])
        self.assertEqual(jobs, ['plan', 'toolchain', 'inputs', 'providers-a', 'providers-b', 'providers-c', 'client'])
        limits = [int(value) for value in re.findall(r'timeout-minutes: (\d+)', self.TEXT)]
        self.assertEqual(len(limits), len(jobs))
        self.assertLess(max(limits), 360)
        self.assertIn("concurrency:\n  group: takx-desktop-prototype-${{ github.event_name }}-${{ github.ref }}\n"
                      '  cancel-in-progress: false\n', self.TEXT)
        self.assertEqual(self.TEXT.count("runs-on: ${{ matrix.arch == 'arm64' && 'ubuntu-24.04-arm' || 'ubuntu-24.04' }}"), 6)

    def test_matrix_groups_are_the_groups_of_the_driver(self):
        listed = [name for row in re.findall(r'group: \[([a-z, -]+)\]', self.TEXT) for name in row.split(', ')]
        called = re.findall(r'run-group\.sh (client|boot)\b', self.TEXT)
        groups = PIPELINE.groups()['groups']
        self.assertEqual(sorted(listed + called), sorted(groups))
        waves = {'a': ['native-base', 'geo-base', 'java'], 'b': ['cesium', 'scene', 'commo'], 'c': ['kml'],
                 'd': ['client', 'boot']}
        for wave, names in waves.items():
            for name in names:
                self.assertEqual(groups[name]['wave'], wave, name)
        # Values reach shell through env, never by interpolation into a script.
        for line in self.TEXT.splitlines():
            if line.strip().startswith(('run:', 'takx/', 'python3', 'echo')):
                self.assertNotIn('${{', line, line)


if __name__ == '__main__':
    unittest.main()
