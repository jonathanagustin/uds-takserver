#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/client58-public-api/run_hosted_controls.py."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load_runner():
    spec = importlib.util.spec_from_file_location('client58_hosted', HERE / 'run_hosted_controls.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HostedClient58PublicApiTests(unittest.TestCase):
    def test_verify_phase_checks_contract_without_subprocess(self):
        runner = load_runner()
        with mock.patch('subprocess.run', side_effect=AssertionError('verify must not invoke subprocess')):
            lock = runner.verify_only()
        self.assertEqual(lock['profile'], 'public-vehicle-metadata-v2')
        for flag in runner.BROAD_FLAGS:
            self.assertFalse(lock[flag])

    def test_verify_phase_rejects_qualify_paths(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, 'explicit --phase qualify'):
            runner.main(['--phase', 'verify', '--output', '/tmp/unused'])

    def test_qualify_phase_requires_explicit_inputs(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, 'required for --phase qualify'):
            runner.main(['--phase', 'qualify'])

    def test_clean_env_strips_java_and_loader_vars(self):
        runner = load_runner()
        with mock.patch.dict(
            os.environ,
            {
                'CLASSPATH': '/bad',
                'JAVA_TOOL_OPTIONS': '-Xmx1g',
                '_JAVA_OPTIONS': '-Dfoo=bar',
                'LD_PRELOAD': '/evil.so',
                'PATH': '/usr/bin',
            },
            clear=True,
        ):
            cleaned = runner.clean_env()
        self.assertEqual(cleaned['LC_ALL'], 'C')
        self.assertEqual(cleaned['PATH'], '/usr/bin')
        for forbidden in ('CLASSPATH', 'JAVA_TOOL_OPTIONS', '_JAVA_OPTIONS', 'LD_PRELOAD'):
            self.assertNotIn(forbidden, cleaned)

    def test_qualify_rejects_overlapping_output(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_jdk = root / 'jdk'
            fake_kernel = root / 'kernel'
            fake_ann = root / 'ann.jar'
            fake_slf4j = root / 'slf4j.jar'
            for d in (fake_jdk, fake_kernel):
                d.mkdir()
            fake_ann.write_bytes(b'')
            fake_slf4j.write_bytes(b'')
            with self.assertRaisesRegex(ValueError, 'output overlaps inputs'):
                runner.main([
                    '--phase', 'qualify',
                    '--java-home', str(fake_jdk),
                    '--kernel', str(fake_kernel),
                    '--annotations', str(fake_ann),
                    '--slf4j', str(fake_slf4j),
                    '--output', str(fake_kernel / 'nested'),
                ])


if __name__ == '__main__':
    unittest.main()
