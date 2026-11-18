# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Build-time tool pins and refusal before installing unverified payloads."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'dev/install-tools'


class DevToolInstallTests(unittest.TestCase):
    def test_versions_match_project_pins_and_runtime_smoke(self):
        tools = tomllib.loads((ROOT / 'mise.toml').read_text())['tools']
        source = INSTALLER.read_text()
        smoke = (ROOT / 'dev/smoke').read_text()
        for tool in ('helm', 'shellcheck'):
            version = tools[tool]
            self.assertRegex(source, rf'(?m)^{tool}_version={re.escape(version)}$')
            self.assertIn(version, smoke)

    def fixture(self, architecture):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        binary = root / 'bin'
        binary.mkdir()
        dpkg = binary / 'dpkg'
        dpkg.write_text('#!/bin/sh\nprintf \'%s\\n\' "$TOOL_TEST_ARCH"\n')
        dpkg.chmod(0o755)
        env = {**os.environ, 'PATH': str(binary), 'TOOL_TEST_ARCH': architecture,
               'TOOL_TEST_CALLS': str(root / 'calls')}
        return root, binary, env

    def test_unknown_architecture_refuses_before_download_or_destination_creation(self):
        root, _, env = self.fixture('unsupported')
        result = subprocess.run(['/bin/sh', str(INSTALLER), str(root / 'destination')],
                                env=env, capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'only Linux amd64 and arm64', result.stderr)
        self.assertFalse((root / 'destination').exists())
        self.assertFalse((root / 'calls').exists())

    def test_both_architectures_reject_changed_payloads_before_extraction_or_install(self):
        for architecture, helm_arch, shellcheck_arch in (('amd64', 'amd64', 'x86_64'),
                                                        ('arm64', 'arm64', 'aarch64')):
            for changed in ('both', 'second-only'):
                with self.subTest(architecture=architecture, changed=changed):
                    root, binary, env = self.fixture(architecture)
                    for command in ('mktemp', 'rm', 'sha256sum'):
                        path = shutil.which(command)
                        self.assertIsNotNone(path, f'{command} required; run these controls in dev/run')
                        (binary / command).symlink_to(path)
                    installer = INSTALLER
                    if changed == 'second-only':
                        # A controlled first checksum succeeds, proving that a
                        # failed second payload cannot leave the first installed.
                        installer = root / 'install-tools-fixture'
                        source = re.sub(r'helm_sha=[a-f0-9]{64}',
                                        'helm_sha=' + hashlib.sha256(b'checked first payload').hexdigest(),
                                        INSTALLER.read_text())
                        installer.write_text(source)
                        env['TOOL_TEST_VALID_FIRST'] = 'true'
                    curl = binary / 'curl'
                    curl.write_text(f'''#!{sys.executable}
import os, pathlib, sys
args = sys.argv[1:]
first = os.environ.get('TOOL_TEST_VALID_FIRST') == 'true' and args[-1].startswith('https://get.helm.sh/')
pathlib.Path(args[args.index('--output') + 1]).write_bytes(b'checked first payload' if first else b'changed archive')
with open(os.environ['TOOL_TEST_CALLS'], 'a') as log:
    log.write(args[-1] + '\\n')
''')
                    curl.chmod(0o755)
                    for command in ('tar', 'install'):
                        marker = binary / command
                        marker.write_text('#!/bin/sh\nprintf \'forbidden\\n\' >> "$TOOL_TEST_CALLS"\nexit 97\n')
                        marker.chmod(0o755)
                    result = subprocess.run(['/bin/sh', str(installer), str(root / 'destination')],
                                            env=env, capture_output=True, timeout=5)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((root / 'destination').exists())
                    calls = (root / 'calls').read_text().splitlines()
                    self.assertEqual(len(calls), 2, calls)
                    self.assertEqual(calls[0], f'https://get.helm.sh/helm-v4.3.1-linux-{helm_arch}.tar.gz')
                    self.assertEqual(calls[1], 'https://github.com/koalaman/shellcheck/releases/download/'
                                     f'v0.11.0/shellcheck-v0.11.0.linux.{shellcheck_arch}.tar.gz')


if __name__ == '__main__':
    unittest.main()
