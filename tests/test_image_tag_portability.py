# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Real image-tag entry points with isolated hash tools and failing producers."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RECIPES = '.github/container-and-chart'
NAMES = ('docker', 'cloudtak', 'postgis')
SOURCES = {'docker': 'takserver', 'cloudtak': 'cloudtak'}


class ImageTagPortability(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'repo'
        self.root.mkdir()
        self.real_git = shutil.which('git')
        self.bash = os.environ.get('IMAGE_TAG_TEST_BASH', shutil.which('bash'))
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        for name in NAMES:
            directory = self.root / RECIPES / name
            directory.mkdir(parents=True)
            shutil.copy(ROOT / RECIPES / name / 'build.sh', directory / 'build.sh')
            (directory / 'recipe.txt').write_text('recipe input\n')
        (self.root / RECIPES / 'docker/version.txt').write_text('5.7-RELEASE-14\n')
        modules = []
        for source, filename in (('takserver', 'src/build.gradle'), ('cloudtak', 'Dockerfile')):
            directory = self.root / 'upstream' / source
            directory.mkdir(parents=True)
            subprocess.run([self.real_git, 'init', '-q', str(directory)], check=True)
            path = directory / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('source input\n')
            subprocess.run([self.real_git, '-C', str(directory), 'add', '.'], check=True)
            subprocess.run([self.real_git, '-C', str(directory), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'source'], check=True)
            modules.append(f'[submodule "upstream/{source}"]\n\tpath = upstream/{source}\n'
                           f'\turl = https://example.invalid/{source}\n\tbranch = 2.3.4\n')
        (self.root / '.gitmodules').write_text(''.join(modules))
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture')
        self.binary = self.root.parent / 'bin'
        self.binary.mkdir()
        for name in ('dirname', 'tr', 'cut', 'uname'):
            (self.binary / name).symlink_to(shutil.which(name))
        self.runtime_log = self.root.parent / 'runtime.log'
        runtime = self.binary / 'docker'
        runtime.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$RUNTIME_LOG"\nexit 98\n')
        runtime.chmod(0o755)
        wrapper = self.binary / 'git'
        wrapper.write_text(f'''#!{sys.executable}
import os, sys
args = sys.argv[1:]
directory, tail = (args[1], args[2:]) if args[:1] == ['-C'] else ('', args)
fault = os.environ.get('GIT_FAILURE', '')
recipe = '.github/container-and-chart/' + os.environ['IMAGE_FIXTURE']
source = {{'docker': 'takserver', 'cloudtak': 'cloudtak'}}.get(os.environ['IMAGE_FIXTURE'])
failed = ((fault == 'root' and tail == ['rev-parse', '--show-toplevel']) or
          (fault == 'version' and tail[:1] == ['config']) or
          (fault == 'pin' and tail == ['rev-parse', 'HEAD:upstream/' + str(source)]) or
          (fault == 'tree' and tail == ['rev-parse', 'HEAD:' + recipe]) or
          (fault == 'source-head' and tail == ['rev-parse', 'HEAD']) or
          (fault == 'source-tree' and tail == ['rev-parse', 'HEAD^{{tree}}']) or
          (fault == 'source-status' and tail[:1] == ['status'] and '/upstream/' in directory) or
          (fault == 'recipe-status' and tail == ['status', '--porcelain', '--', recipe]))
if failed:
    if fault not in ('source-status', 'recipe-status'):
        print('a' * 40)  # Even plausible output must not turn failure into a tag.
    raise SystemExit(47)
os.execv({self.real_git!r}, [{self.real_git!r}, *args])
''')
        wrapper.chmod(0o755)
        self.environment = {**os.environ, 'PATH': str(self.binary), 'RUNTIME_LOG': str(self.runtime_log),
                            'PUSH_IMAGES': 'true', 'ARCH': 'arm64', 'PLATFORMS': 'linux/arm64',
                            'CONTAINER_RUNTIME': 'docker', 'GITHUB_REF': 'refs/pull/1/merge',
                            'GITHUB_EVENT_NAME': 'unit-test',
                            'WRITE_VERSION_TAG': 'false'}

    def git(self, *args):
        return subprocess.check_output([self.real_git, *args], cwd=self.root, text=True,
                                       stderr=subprocess.DEVNULL).strip()

    def hash_tools(self, mode):
        for name in ('sha256sum', 'shasum'):
            (self.binary / name).unlink(missing_ok=True)
        names = {'normal': ('sha256sum', 'shasum'), 'fallback': ('shasum',),
                 'missing': (), 'sha-fails': ('sha256sum', 'shasum'), 'shasum-fails': ('shasum',)}[mode]
        for name in names:
            target = self.binary / name
            if mode == 'sha-fails' and name == 'sha256sum' or mode == 'shasum-fails':
                target.write_text('#!/bin/sh\nprintf \'%064d  -\\n\' 0\nexit 43\n')
                target.chmod(0o755)
            else:
                path = shutil.which(name)
                if path:
                    target.symlink_to(path)
        if mode == 'fallback':
            self.assertTrue((self.binary / 'shasum').exists(), 'shasum is required for this control')

    def run_script(self, name, mode='--tag', fault=''):
        self.runtime_log.unlink(missing_ok=True)
        command = [self.bash, str(self.root / RECIPES / name / 'build.sh')]
        if mode is not None:
            command.append(mode)
        return subprocess.run(command,
                              cwd=self.root, env={**self.environment, 'IMAGE_FIXTURE': name,
                                                  'GIT_FAILURE': fault},
                              text=True, capture_output=True, timeout=10)

    def expected_tag(self, name):
        tree = self.git('rev-parse', f'HEAD:{RECIPES}/{name}')
        if name == 'postgis':
            if self.git('status', '--porcelain', '--', f'{RECIPES}/{name}'):
                tree += '-dirty'
            inputs, version = tree, '18-3.6'
        else:
            inputs = self.git('rev-parse', 'HEAD:upstream/' + SOURCES[name]) + '-' + tree
            version = '5.7-RELEASE-14' if name == 'docker' else '2.3.4'
        return version + '-' + hashlib.sha256(inputs.encode()).hexdigest()[:12] + '\n'

    def assert_stopped(self, result, mode):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        if mode == '--tag':
            self.assertEqual(result.stdout, '')
        self.assertFalse(self.runtime_log.exists(), result.stdout + result.stderr)

    def test_normal_and_shasum_only_tags_match_exact_input_bytes(self):
        for tools in ('normal', 'fallback'):
            self.hash_tools(tools)
            for name in NAMES:
                with self.subTest(tools=tools, name=name):
                    result = self.run_script(name)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, self.expected_tag(name))
                    self.assertFalse(self.runtime_log.exists())

    def test_preferred_sha256sum_does_not_invoke_available_fallback(self):
        if not shutil.which('sha256sum'):
            self.skipTest('host has no sha256sum; shasum-only path has a separate control')
        self.hash_tools('normal')
        fallback = self.binary / 'shasum'
        fallback.unlink(missing_ok=True)
        fallback.write_text('#!/bin/sh\nexit 96\n')
        fallback.chmod(0o755)
        for name in NAMES:
            with self.subTest(name=name):
                result = self.run_script(name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, self.expected_tag(name))

    def test_hash_failure_stops_tag_registry_and_build_entry_points(self):
        for tools in ('missing', 'sha-fails', 'shasum-fails'):
            self.hash_tools(tools)
            for name in NAMES:
                modes = ('--tag', '--if-changed') if name == 'docker' else ('--tag', '--wait', '--merge', '--if-changed')
                for mode in modes:
                    with self.subTest(tools=tools, name=name, mode=mode):
                        self.assert_stopped(self.run_script(name, mode), mode)

    def test_git_lookup_failure_cannot_be_hidden_by_a_later_success(self):
        self.hash_tools('normal')
        for name in NAMES:
            faults = ['tree'] + (['pin'] if name in SOURCES else ['recipe-status'])
            if name != 'docker':
                faults.append('root')
            if name == 'cloudtak':
                faults.append('version')
            modes = ('--tag', '--if-changed') if name == 'docker' else ('--tag', '--wait', '--merge', '--if-changed')
            for fault in faults:
                for mode in modes:
                    with self.subTest(name=name, fault=fault, mode=mode):
                        self.assert_stopped(self.run_script(name, mode, fault), mode)

    def test_source_and_recipe_status_failures_stop_before_runtime_calls(self):
        self.hash_tools('normal')
        for name in SOURCES:
            faults = ['source-head', 'source-status', 'recipe-status']
            if name == 'docker':
                faults.append('source-tree')
            for fault in faults:
                with self.subTest(name=name, fault=fault):
                    self.assert_stopped(self.run_script(name, '--if-changed', fault), '--if-changed')

    def test_pinned_source_and_clean_worktree_guards_still_reject_changes(self):
        self.hash_tools('fallback')
        for name, source in SOURCES.items():
            with self.subTest(name=name):
                directory = self.root / 'upstream' / source
                path = directory / ('Dockerfile' if name == 'cloudtak' else 'src/build.gradle')
                path.write_text('changed source\n')
                self.assert_stopped(self.run_script(name, '--if-changed'), '--if-changed')
                subprocess.run([self.real_git, '-C', str(directory), 'add', '.'], check=True)
                subprocess.run([self.real_git, '-C', str(directory), '-c', 'user.name=Fixture',
                                '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'changed source'],
                               check=True)
                self.assert_stopped(self.run_script(name, '--if-changed'), '--if-changed')

    def test_dirty_recipe_and_changed_committed_recipe_keep_existing_key_rules(self):
        self.hash_tools('fallback')
        for name in NAMES:
            with self.subTest(name=name):
                initial = self.run_script(name).stdout
                path = self.root / RECIPES / name / 'recipe.txt'
                path.write_text('changed recipe input\n')
                dirty = self.run_script(name)
                self.assertEqual(dirty.returncode, 0, dirty.stderr)
                self.assertEqual(dirty.stdout, self.expected_tag(name))
                if name == 'postgis':
                    self.assertNotEqual(dirty.stdout, initial)
                else:
                    self.assertEqual(dirty.stdout, initial)
                self.git('add', str(path))
                self.git('commit', '-qm', 'changed recipe')
                committed = self.run_script(name)
                self.assertEqual(committed.returncode, 0, committed.stderr)
                self.assertEqual(committed.stdout, self.expected_tag(name))
                self.assertNotEqual(committed.stdout, initial)

    def test_inputs_match_build_labels_without_fetch_build_or_hash_tools(self):
        self.hash_tools('missing')
        for name in NAMES:
            with self.subTest(name=name):
                result = self.run_script(name, '--inputs')
                self.assertEqual(result.returncode, 0, result.stderr)
                tree = self.git('rev-parse', f'HEAD:{RECIPES}/{name}')
                prefix = self.git('rev-parse', 'HEAD:upstream/' + SOURCES[name]) + '-' if name in SOURCES else ''
                self.assertEqual(result.stdout, prefix + tree + '\n')
                self.assertFalse(self.runtime_log.exists())
                path = self.root / RECIPES / name / 'recipe.txt'
                path.write_text('dirty recipe\n')
                dirty = self.run_script(name, '--inputs')
                self.assertEqual(dirty.returncode, 0, dirty.stderr)
                self.assertEqual(dirty.stdout, prefix + tree + '-dirty\n')

    def test_inputs_reject_source_drift_and_propagate_git_errors_without_stdout(self):
        self.hash_tools('normal')
        for name in NAMES:
            for fault in ('tree', 'recipe-status') + (('pin', 'source-status', 'source-head') if name in SOURCES else ()):
                with self.subTest(name=name, fault=fault):
                    result = self.run_script(name, '--inputs', fault)
                    self.assert_stopped(result, '--inputs')
                    self.assertEqual(result.stdout, '')
        for name, source in SOURCES.items():
            path = self.root / 'upstream' / source / ('Dockerfile' if name == 'cloudtak' else 'src/build.gradle')
            path.write_text('dirty source\n')
            result = self.run_script(name, '--inputs')
            self.assert_stopped(result, '--inputs')
            self.assertEqual(result.stdout, '')
            path.write_text('source input\n')
            (path.parent / 'untracked-source-input').write_text('unexpected source\n')
            untracked = self.run_script(name, '--inputs')
            self.assert_stopped(untracked, '--inputs')
            self.assertEqual(untracked.stdout, '')

    def test_cloudtak_bake_optional_allow_preserves_arguments_with_bash_nounset(self):
        self.hash_tools('normal')
        (self.binary / 'grep').symlink_to(shutil.which('grep'))
        runtime = self.binary / 'docker'
        runtime.write_text(f'''#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
with open(os.environ['RUNTIME_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args == ['buildx', 'bake', '--help']:
    print('--allow stringArray' if os.environ['BAKE_ALLOW_CAPABILITY'] == 'true' else 'bake help')
    raise SystemExit(0)
raise SystemExit(83 if args[:2] == ['buildx', 'bake'] and '--load' in args else 97)
''')
        runtime.chmod(0o755)
        for capability in (False, True):
            with self.subTest(allow=capability):
                self.environment['BAKE_ALLOW_CAPABILITY'] = str(capability).lower()
                result = self.run_script('cloudtak', None)
                self.assertEqual(result.returncode, 83, result.stdout + result.stderr)
                calls = [json.loads(line) for line in self.runtime_log.read_text().splitlines()]
                expected = ['buildx', 'bake', '-f', 'docker-bake.hcl']
                if capability:
                    expected += ['--allow', 'fs.read=' + str(self.root)]
                self.assertEqual(calls, [['buildx', 'bake', '--help'], [*expected, '--load', 'cloudtak']])
                self.assertNotIn('unbound variable', result.stderr)


if __name__ == '__main__':
    unittest.main()
