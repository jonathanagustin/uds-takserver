#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the POSIX host launcher without running builds or containers."""
import json
import os
from pathlib import Path
import pty
import shutil
import subprocess
import sys
import tempfile
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / 'dev/run'


class DevLauncherTests(unittest.TestCase):
    def fixture(self, names=('docker', 'podman')):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        root = base / 'checkout with spaces, and comma'
        (root / 'dev').mkdir(parents=True)
        shutil.copyfile(LAUNCHER, root / 'dev/run')
        (root / 'dev/Dockerfile').write_text('# owned build-context fixture\n')
        tools = base / 'tools'
        tools.mkdir()
        # A closed PATH prevents accidental use of a real host runtime in tests.
        for name, body in {
            'dirname': 'print(os.path.dirname(sys.argv[1]) or ".")',
            'basename': 'print(os.path.basename(sys.argv[1]))',
            'uname': 'print("Linux")',
            'id': 'print(os.getuid() if sys.argv[1]=="-u" else os.getgid())',
        }.items():
            path = tools / name
            path.write_text('#!' + sys.executable + '\nimport os,sys\n' + body + '\n')
            path.chmod(0o755)
        for name in names:
            path = tools / name
            path.write_text('#!' + sys.executable + '''
import json,os,pathlib,sys
args=sys.argv[1:]
with open(os.environ['DEV_CALLS'],'a') as output:
    output.write(json.dumps([pathlib.Path(sys.argv[0]).name,*args])+'\\n')
if args==['--version']:
    print(os.environ.get('FAKE_VERSION',('podman' if pathlib.Path(sys.argv[0]).name=='podman' else 'Docker')+' version fixture'))
elif args==['info','--format','{{.Host.Security.Rootless}}']:
    print(os.environ.get('FAKE_ROOTLESS','false'))
elif args==['info']:
    sys.exit(int(os.environ.get('FAKE_INFO_STATUS','0')))
elif args and args[0]=='build':
    sys.exit(int(os.environ.get('FAKE_BUILD_STATUS','0')))
elif args and args[0]=='run':
    if 'FAKE_STDIN' in os.environ: pathlib.Path(os.environ['FAKE_STDIN']).write_bytes(sys.stdin.buffer.read())
    sys.exit(int(os.environ.get('FAKE_RUN_STATUS','0')))
else: sys.exit(99)
''')
            path.chmod(0o755)
        env = {**os.environ, 'PATH': str(tools), 'DEV_CALLS': str(base / 'calls.jsonl')}
        for name in ('CONTAINER_RUNTIME', 'DEV_TOOLCHAIN_IMAGE', 'GIT_DIR', 'GIT_COMMON_DIR', 'GIT_WORK_TREE'):
            env.pop(name, None)
        return root, tools, env

    def run_launcher(self, root, env, *args, **kwargs):
        return subprocess.run(['/bin/sh', str(root / 'dev/run'), *args], cwd=root.parent,
                              env=env, capture_output=True, timeout=10, **kwargs)

    def calls(self, env):
        path = Path(env['DEV_CALLS'])
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_default_build_context_and_isolated_run(self):
        root, tools, env = self.fixture()
        result = self.run_launcher(root, env)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls(env)
        self.assertTrue(all(row[0] == 'docker' for row in calls))
        self.assertEqual(calls[-2], ['docker', 'build', '--file', str(root / 'dev/Dockerfile'),
                                    '--tag', 'uds-takserver-dev:local', str(root / 'dev')])
        args = calls[-1]
        self.assertEqual(args[-2:], ['uds-takserver-dev:local', 'check'])
        self.assertIn('--interactive', args)
        self.assertNotIn('--tty', args)
        self.assertEqual(args[args.index('--user') + 1], str(os.getuid()) + ':' + str(os.getgid()))
        self.assertIn('HOME=/tmp/uds-dev-home', args)
        self.assertIn(str(root) + ':/workspace:rw', args)
        self.assertFalse(any('docker.sock' in value or 'privileged' in value or value.startswith('GIT_') for value in args))

    def test_runtime_precedence_explicit_path_and_literal_arguments(self):
        root, tools, env = self.fixture()
        env['CONTAINER_RUNTIME'] = str(tools / 'podman')
        env['DEV_TOOLCHAIN_IMAGE'] = 'example/dev:review'
        literal = 'spaces; $(touch unexpected) `echo text`'
        result = self.run_launcher(root, env, 'sh', '-c', literal)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(row[0] == 'podman' for row in self.calls(env)))
        self.assertEqual(self.calls(env)[-1][-4:], ['example/dev:review', 'sh', '-c', literal])
        self.assertNotIn('--userns=keep-id', self.calls(env)[-1])

    def test_podman_fallback_and_rootless_keep_id(self):
        root, tools, env = self.fixture(names=('podman',))
        env['FAKE_ROOTLESS'] = 'true'
        result = self.run_launcher(root, env, 'shell')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--userns=keep-id', self.calls(env)[-1])
        self.assertEqual(self.calls(env)[-1][-1], 'shell')

    def test_explicit_podman_resolves_macos_installation_without_switching_engines(self):
        for mode in ('normal', 'exact-missing', 'stopped'):
            with self.subTest(mode=mode):
                root, tools, env = self.fixture()
                installed = root.parent / 'standard-macos-install' / 'podman'
                installed.parent.mkdir()
                (tools / 'podman').rename(installed)
                # Project the standard installation into the fixture so neither
                # macOS nor Linux controls can invoke a real host engine.
                launcher = root / 'dev/run'
                launcher.write_text(launcher.read_text().replace('/opt/podman/bin/podman', str(installed)))
                (tools / 'uname').write_text('#!' + sys.executable + '\nprint("Darwin")\n')
                env['CONTAINER_RUNTIME'] = '/missing/podman' if mode == 'exact-missing' else 'podman'
                if mode == 'stopped':
                    env['FAKE_INFO_STATUS'] = '7'
                result = self.run_launcher(root, env, 'true')
                self.assertEqual(result.returncode == 0, mode == 'normal', result.stderr)
                calls = self.calls(env)
                self.assertTrue(all(row[0] == 'podman' for row in calls))
                self.assertEqual(any(row[1] == 'run' for row in calls), mode == 'normal')
                if mode != 'normal':
                    self.assertFalse(any(row[1] == 'build' for row in calls))

    def test_missing_invalid_and_stopped_runtime_never_build_or_switch(self):
        for mode in ('missing', 'empty', 'stopped', 'unknown', 'build-failure'):
            with self.subTest(mode=mode):
                root, tools, env = self.fixture(names=() if mode == 'missing' else ('docker', 'podman'))
                if mode == 'empty': env['CONTAINER_RUNTIME'] = ''
                if mode == 'stopped': env['FAKE_INFO_STATUS'] = '7'
                if mode == 'unknown': env['FAKE_VERSION'] = 'unknown runtime'
                if mode == 'build-failure': env['FAKE_BUILD_STATUS'] = '11'
                result = self.run_launcher(root, env)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(row[1] == 'run' for row in self.calls(env)))
                self.assertFalse(any(row[0] == 'podman' for row in self.calls(env)))
                if mode != 'build-failure': self.assertFalse(any(row[1] == 'build' for row in self.calls(env)))

    def test_run_status_and_stdin_are_preserved(self):
        root, tools, env = self.fixture()
        env['FAKE_RUN_STATUS'] = '29'
        env['FAKE_STDIN'] = str(root / 'stdin.bin')
        result = self.run_launcher(root, env, 'cat', input=b'pipeline\x00payload\n')
        self.assertEqual(result.returncode, 29)
        self.assertEqual((root / 'stdin.bin').read_bytes(), b'pipeline\x00payload\n')

    def test_normal_and_linked_git_metadata_are_read_only_without_git_overrides(self):
        for linked in (False, True):
            with self.subTest(linked=linked):
                root, tools, env = self.fixture()
                if linked:
                    metadata = root.parent / 'common git'
                    gitdir = metadata / 'worktrees/current'
                    gitdir.mkdir(parents=True)
                    (metadata / 'objects').mkdir(); (metadata / 'refs').mkdir()
                    (gitdir / 'HEAD').write_text('ref: refs/heads/main\n')
                    (gitdir / 'commondir').write_text('../..\n')
                    (gitdir / 'gitdir').write_text(str(root / '.git') + '\n')
                    (root / '.git').write_text('gitdir: ' + str(gitdir) + '\n')
                    expected = str(metadata) + ':' + str(metadata) + ':ro'
                else:
                    (root / '.git').mkdir()
                    expected = str(root / '.git') + ':/workspace/.git:ro'
                result = self.run_launcher(root, env)
                self.assertEqual(result.returncode, 0, result.stderr)
                args = self.calls(env)[-1]
                self.assertIn(expected, args)
                self.assertFalse(any(value.startswith('GIT_') for value in args))

    def test_malformed_git_pointer_refuses_before_runtime(self):
        root, tools, env = self.fixture()
        (root / '.git').write_text('gitdir: /unrelated/missing\n')
        result = self.run_launcher(root, env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(env), [])

    def test_terminal_flag_requires_both_streams_to_be_terminals(self):
        for both in (False, True):
            with self.subTest(both=both):
                root, tools, env = self.fixture()
                master, slave = pty.openpty()
                try:
                    result = subprocess.run(['/bin/sh', str(root / 'dev/run'), 'shell'], env=env,
                                            stdin=slave if both else subprocess.DEVNULL,
                                            stdout=slave, stderr=subprocess.PIPE, timeout=10)
                finally:
                    os.close(master); os.close(slave)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual('--tty' in self.calls(env)[-1], both)


if __name__ == '__main__':
    unittest.main()
