# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""No-launch and failed-input guards for the explicit frozen-build consumer."""
import importlib.util
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('elevation_control_runner', HERE / 'controls.py')
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)


class ConsumerGuards(unittest.TestCase):
    def test_default_verifies_profile_without_starting_a_process(self):
        with patch.object(C.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
            C.main([])
            C.main(['--suite', 'scalar'])
            for args in (['--native', '/missing'], ['--phase', 'run']):
                with self.assertRaises(ValueError):
                    C.main(args)

    def test_unreviewed_receipt_refuses_before_output_or_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native, java, jdk = [root / name for name in ('native', 'java', 'jdk')]
            for path in (native, java, jdk):
                path.mkdir()
            (native / 'experiment-complete.json').write_text('{"accepted":true}')
            output = root / 'output'
            with patch.object(C.subprocess, 'Popen', side_effect=AssertionError('must not launch')):
                with self.assertRaisesRegex(ValueError, 'reviewed input bytes'):
                    C.run(native, java, jdk, output, suite='scalar')
            self.assertFalse(output.exists())

    def test_unknown_suite_refuses_before_reading_inputs(self):
        with patch.object(C, 'verify_inputs', side_effect=AssertionError('must not read')):
            with self.assertRaisesRegex(ValueError, 'unknown control suite'):
                C.run('/missing', '/missing', '/missing', '/missing', suite='foreign')

    def test_scalar_jni_warning_stops_before_second_case(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native, java, jdk = [root / name for name in ('native', 'java', 'jdk')]
            for path in (native, java, jdk):
                path.mkdir()
            output = root / 'output'
            original_module = C.module

            class Executor:
                @staticmethod
                def execute(argv, folder, label, *_args, **_kwargs):
                    text = ''
                    if label == 'compile-java':
                        (folder / 'classes/ElevationScalarControls.class').write_bytes(b'fixture')
                    else:
                        text = ('ELEVATION_SCALAR_CONTROLS_PASS case=source:query checks=4\n'
                                'WARNING in native method: JNI call made without checking exceptions\n')
                    (folder / (label + '.log')).write_text(text)

            def load(name, path):
                return Executor if name == 'elevation_bounded_executor' else original_module(name, path)

            with patch.object(C, 'verify_inputs', return_value=({}, [], Path('/usr/bin/g++'))), patch.object(C, 'module', side_effect=load):
                with self.assertRaisesRegex(ValueError, 'strict scoped controls failed'):
                    C.run(native, java, jdk, output, suite='scalar')
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertTrue(receipt['failed'])
            self.assertFalse(receipt['completed'])
            self.assertFalse(receipt['strict_elevation_accepted'])
            self.assertEqual(len(receipt['commands']), 2)
            self.assertFalse((output / 'scalar-1').exists())

    def test_live_log_quota_retains_first_failure_and_stops_later_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            native, java, jdk = [root / name for name in ('native', 'java', 'jdk')]
            for path in (native, java, jdk):
                path.mkdir()
            (jdk / 'bin').mkdir()
            compiler = jdk / 'bin/javac'
            compiler.write_text('#!' + sys.executable + '\nprint("x" * (3 * 1024 * 1024))\n')
            compiler.chmod(0o700)
            output = root / 'output'
            with patch.object(C, 'verify_inputs', return_value=({}, [], Path('/usr/bin/g++'))):
                with self.assertRaisesRegex(ValueError, 'log quota'):
                    C.run(native, java, jdk, output)
            receipt = json.loads((output / 'receipt.json').read_text())
            self.assertTrue(receipt['failed'])
            self.assertFalse(receipt['completed'])
            self.assertFalse(receipt['full_sdk_accepted'])
            self.assertEqual(receipt['failure_type'], 'ValueError')
            self.assertEqual(len(receipt['commands']), 1)
            log = output / 'ElevationListenerProbe/compile-java.log'
            self.assertLessEqual(log.stat().st_size, 2 * 1024 * 1024)
            self.assertEqual(receipt['commands'][0]['log_sha256'], C.sha(log))
            self.assertFalse((output / 'ElevationChunkProbe').exists())

    def test_existing_symlink_and_overlapping_outputs_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            alias = root / 'alias'
            alias.symlink_to(source, target_is_directory=True)
            for output in (source, source / 'output', alias / 'output'):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    C.fresh_output(output, (source,))
            self.assertFalse((source / 'output').exists())
