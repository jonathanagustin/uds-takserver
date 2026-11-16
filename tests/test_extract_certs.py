# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the real shell helper against changing, private API fixtures."""
import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def source(kind='Secret', cert=b'certificate-A', key=b'key-A'):
    return {'apiVersion': 'v1', 'kind': kind, 'metadata': {
        'name': 'takserver-certs' if kind == 'Secret' else 'cert-migration', 'namespace': 'takserver',
        'uid': 'source-A', 'resourceVersion': '1'}, 'data' if kind == 'Secret' else 'binaryData': {
        'admin.pem': base64.b64encode(cert).decode(), 'admin.key': base64.b64encode(key).decode()}}


class ExtractCertificates(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'bin').mkdir()
        self.fake = self.root / 'bin/kubectl'
        self.fake.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
root=Path(os.environ['CERT_TEST_ROOT'])
with (root/'calls').open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
kind={'secret':'Secret','configmap':'ConfigMap'}[sys.argv[2].lower()]
if (root/'fail').exists():
 print('private-key-sentinel',file=sys.stderr);raise SystemExit(1)
p=root/(kind+'.json')
if p.exists():print(p.read_text())
''')
        self.fake.chmod(0o755)
        self.env = {**os.environ, 'PATH': str(self.root / 'bin') + ':/usr/bin:/bin', 'CERT_TEST_ROOT': str(self.root)}
        self.dest = self.root / 'certs'

    def write(self, value):
        (self.root / (value['kind'] + '.json')).write_text(json.dumps(value))

    def run_helper(self, script=None):
        return subprocess.run(['bash', str(script or ROOT / 'tests/extract-certs.sh'), str(self.dest)],
                              cwd=self.root, env=self.env, capture_output=True, text=True, timeout=10)

    def test_reinstall_replaces_cached_pair_and_preserves_caller_paths(self):
        self.write(source())
        first = self.run_helper()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout.strip(), str(self.dest))
        changed = source(cert=b'certificate-B', key=b'key-B')
        changed['metadata'].update(uid='source-B', resourceVersion='2')
        self.write(changed)
        second = self.run_helper()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.dest / 'admin.pem').read_bytes(), b'certificate-B')
        self.assertEqual((self.dest / 'admin.key').read_bytes(), b'key-B')
        self.assertEqual(len((self.root / 'calls').read_text().splitlines()), 2)
        self.assertEqual(stat.S_IMODE(self.dest.stat().st_mode), 0o700)
        for name in ('admin.pem', 'admin.key'):
            self.assertEqual(stat.S_IMODE((self.dest / name).stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ['admin.key', 'admin.pem'])

    def test_complete_legacy_pair_is_supported(self):
        self.write(source('ConfigMap'))
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.dest / 'admin.key').read_bytes(), b'key-A')

    def test_partial_secret_cannot_mix_with_legacy_pair(self):
        value = source(); del value['data']['admin.key']
        self.write(value); self.write(source('ConfigMap', key=b'other-key'))
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.dest / 'admin.pem').exists())
        self.assertEqual(len((self.root / 'calls').read_text().splitlines()), 1)

    def test_source_failures_do_not_return_cached_pair_or_private_error(self):
        self.write(source()); self.assertEqual(self.run_helper().returncode, 0)
        (self.root / 'fail').touch()
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('private-key-sentinel', result.stderr)

    def test_invalid_pair_or_wrong_source_leaves_existing_pair_unchanged(self):
        self.write(source()); self.assertEqual(self.run_helper().returncode, 0)
        bad = source(); bad['data']['admin.key'] = 'not-base64'
        wrong = source(); wrong['metadata']['namespace'] = 'other'
        deleting = source(); deleting['metadata']['deletionTimestamp'] = 'now'
        for value in (bad, wrong, deleting):
            with self.subTest(value=value):
                self.write(value)
                result = self.run_helper()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.dest / 'admin.key').read_bytes(), b'key-A')
                self.assertEqual((self.dest / 'admin.pem').read_bytes(), b'certificate-A')

    def test_symlink_destination_is_refused_before_api_read(self):
        elsewhere = self.root / 'elsewhere'; elsewhere.mkdir()
        self.dest.symlink_to(elsewhere, target_is_directory=True)
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'calls').exists())
        self.assertFalse(list(elsewhere.iterdir()))
