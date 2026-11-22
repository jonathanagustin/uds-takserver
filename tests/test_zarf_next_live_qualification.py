# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for tests/zarf_next_live_qualification.py."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests import zarf_next_live_qualification as q


class ZarfNextLiveQualificationTests(unittest.TestCase):
    def test_write_live_bundle_creates_defaults_values_and_hcl(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pub = root / 'good.pub'
            pub.write_text('-----BEGIN PUBLIC KEY-----\nTEST\n-----END PUBLIC KEY-----\n')
            pkg = root / 'zarf-package-values-contract-amd64-0.1.0.tar.zst'
            bundle_dir = root / 'next-include'
            q.write_live_bundle(bundle_dir, 'contract-include', pkg, pub, 'optional-addon')
            self.assertEqual((bundle_dir / 'pkg.pub').read_text(), pub.read_text())
            defaults = (bundle_dir / 'defaults.uds.hcl').read_text()
            self.assertIn('domain         = "default.example"', defaults)
            self.assertIn('cloudtak_class = "default-ssd"', defaults)
            values = (bundle_dir / 'bundle-values.yaml').read_text()
            self.assertIn('{{ .vars.domain }}', values)
            self.assertIn('{{ .vars.cloudtak_class }}', values)
            hcl = (bundle_dir / 'bundle.uds.hcl').read_text()
            self.assertIn('bundle_api_version = "uds.dev/v1alpha1"', hcl)
            self.assertIn('name    = "contract-include"', hcl)
            self.assertIn('optional_components = ["optional-addon"]', hcl)
            self.assertIn('public_key = file("pkg.pub")', hcl)

    def test_run_cmd_returns_stdout_and_bounds_error_stderr(self):
        ok = subprocess.CompletedProcess(['uds'], 0, stdout=b'ok', stderr=b'')
        with mock.patch.object(subprocess, 'run', return_value=ok):
            self.assertEqual(q.run_cmd(['uds', 'version']), b'ok')
        long_err = ('line\n' * 10 + 'X' * 1000).encode()
        bad = subprocess.CompletedProcess(['uds'], 1, stdout=b'', stderr=long_err)
        with mock.patch.object(subprocess, 'run', return_value=bad):
            with self.assertRaises(RuntimeError) as ctx:
                q.run_cmd(['uds', 'bundle', 'deploy', 'x.tar.zst'])
        self.assertIn('exit=1', str(ctx.exception))
        self.assertLessEqual(len(str(ctx.exception)), 650)

    def test_get_configmap_and_assert_configmap_absent(self):
        payload = json.dumps({'data': {'DOMAIN': 'live.example'}}).encode()
        ok = subprocess.CompletedProcess(['uds'], 0, stdout=payload, stderr=b'')
        with mock.patch.object(subprocess, 'run', return_value=ok):
            self.assertEqual(q.get_configmap('uds', 'contract'), {'DOMAIN': 'live.example'})
        missing = subprocess.CompletedProcess(['uds'], 1, stdout=b'', stderr=b'NotFound')
        with mock.patch.object(subprocess, 'run', return_value=missing):
            q.assert_configmap_absent('uds', 'optional-addon')
        present = subprocess.CompletedProcess(['uds'], 0, stdout=b'{}', stderr=b'')
        with mock.patch.object(subprocess, 'run', return_value=present):
            with self.assertRaises(RuntimeError):
                q.assert_configmap_absent('uds', 'optional-addon')

    def test_build_bundle_requires_single_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp) / 'bundle'
            bundle_dir.mkdir()
            ok = subprocess.CompletedProcess(['uds'], 0, stdout=b'', stderr=b'')
            with mock.patch.object(subprocess, 'run', return_value=ok):
                with self.assertRaises(RuntimeError):
                    q.build_bundle('uds', bundle_dir, Path('k'), {})
                archive = bundle_dir / 'uds-bundle-contract-amd64-0.1.0.tar.zst'
                archive.write_bytes(b'test')
                self.assertEqual(q.build_bundle('uds', bundle_dir, Path('k'), {}), archive)

    def test_direct_deploy_args_builds_verified_default_and_opt_in_commands(self):
        default_cmd = q.direct_deploy_args(
            ['uds', 'zarf'], Path('pkg.tar.zst'), Path('good.pub'),
            set_values=('global.domain=default.example',),
        )
        self.assertEqual(default_cmd, [
            'uds', 'zarf', 'package', 'deploy', 'pkg.tar.zst',
            '--confirm', '--key', 'good.pub', '--verify=always',
            '--set-values', 'global.domain=default.example',
        ])
        self.assertNotIn('--components', default_cmd)

        opt_in_cmd = q.direct_deploy_args(
            ['uds', 'zarf'], Path('pkg.tar.zst'), Path('good.pub'),
            components=('optional-addon', 'second-addon'),
            set_values=('global.domain=opt-in.example', 'storage.cloudtakClass=ssd'),
        )
        self.assertEqual(opt_in_cmd, [
            'uds', 'zarf', 'package', 'deploy', 'pkg.tar.zst',
            '--confirm', '--key', 'good.pub', '--verify=always',
            '--components', 'optional-addon,second-addon',
            '--set-values', 'global.domain=opt-in.example',
            '--set-values', 'storage.cloudtakClass=ssd',
        ])


if __name__ == '__main__':
    unittest.main()
