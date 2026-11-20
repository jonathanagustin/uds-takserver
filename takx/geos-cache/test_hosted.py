# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Exercise real failed-command capture and read-only hosted trial boundaries."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import producer

SPEC = importlib.util.spec_from_file_location('geos_capture', HERE / 'capture_failure.py')
CAPTURE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CAPTURE)


class HostedDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'producer'
        self.root.mkdir()
        self.output = Path(self.temp.name) / 'sanitized'

    def failed(self, code='print("password=PRIVATE_GEOS_PASSWORD"); raise SystemExit(7)'):
        with self.assertRaises(ValueError):
            producer.Runner(self.root)([sys.executable, '-u', '-c', code], 'configure',
                                       timeout=2, env=dict(os.environ))

    def test_actual_nonzero_complete_command_is_redacted_without_qualification(self):
        self.failed()
        CAPTURE.capture(self.root, self.output)
        text = next(self.output.glob('*.log')).read_text()
        self.assertNotIn('PRIVATE_GEOS_PASSWORD', text)
        self.assertIn('[REDACTED]', text)
        index = json.loads((self.output / 'index.json').read_text())
        self.assertFalse(index['qualification'])
        self.assertEqual(index['logs'][0]['returncode'], 7)

    def test_actual_timeout_does_not_export_a_partial_stream(self):
        with self.assertRaises(ValueError):
            producer.Runner(self.root)([sys.executable, '-u', '-c',
                                       'import time; print("PRIVATE_PREFIX"); time.sleep(3)'],
                                      'configure', timeout=.05, env=dict(os.environ))
        CAPTURE.capture(self.root, self.output)
        self.assertEqual(list(self.output.iterdir()), [self.output / 'index.json'])

    def test_monitor_tolerates_directory_removed_before_descent(self):
        transient = self.root / 'liboperation.lax'
        transient.mkdir()
        (self.root / 'retained').write_bytes(b'12345')
        scandir = os.scandir

        def remove_before_descent(path):
            if Path(path) == transient:
                transient.rmdir()
            return scandir(path)

        with patch.object(producer.os, 'scandir', side_effect=remove_before_descent):
            self.assertEqual(producer.output_bytes(self.root), 5)
        self.assertFalse(transient.exists())

    def test_monitor_propagates_directory_access_errors(self):
        with patch.object(producer.os, 'scandir', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                producer.output_bytes(self.root)

    def test_monitor_keeps_total_output_limit_and_ignores_symlinks(self):
        (self.root / 'retained').write_bytes(b'12345')
        (self.root / 'alias').symlink_to(self.root / 'retained')
        self.assertEqual(producer.output_bytes(self.root), 5)
        with patch.dict(producer.LOCK['budgets'], output_bytes=4):
            with self.assertRaisesRegex(ValueError, 'time/output bound'):
                producer.Runner(self.root)([sys.executable, '-c', 'pass'], 'configure',
                                           timeout=2, env=dict(os.environ))
        status = json.loads(next(self.root.glob('*.status.json')).read_text())
        self.assertFalse(status['complete'])

    def test_same_size_tampering_is_omitted(self):
        self.failed()
        log = next(self.root.glob('*.log'))
        log.write_bytes(b'x' * log.stat().st_size)
        CAPTURE.capture(self.root, self.output)
        self.assertEqual(list(self.output.iterdir()), [self.output / 'index.json'])

    def test_invalid_utf8_and_budget_exhaustion_do_not_export(self):
        self.failed('import os; os.write(1, b"\\xffPRIVATE_BYTES"); raise SystemExit(2)')
        CAPTURE.capture(self.root, self.output)
        self.assertEqual(list(self.output.iterdir()), [self.output / 'index.json'])
        with patch.object(CAPTURE, 'MAX_TOTAL', 1):
            CAPTURE.capture(self.root, Path(self.temp.name) / 'budget')
        self.assertEqual(list((Path(self.temp.name) / 'budget').iterdir()),
                         [Path(self.temp.name) / 'budget/index.json'])

    def test_unrecorded_and_engine_metadata_logs_are_not_selected(self):
        (self.root / '4-configure.log').write_text('UNRECORDED')
        self.failed()
        log = next(self.root.glob('*.status.json'))
        log.rename(self.root / '1-engine-info.status.json')
        CAPTURE.capture(self.root, self.output)
        self.assertEqual(json.loads((self.output / 'index.json').read_text())['logs'], [])

    def test_symlink_input_or_selected_log_is_rejected(self):
        self.failed()
        link = Path(self.temp.name) / 'alias'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            CAPTURE.capture(link, self.output)
        log = next(self.root.glob('*.log'))
        log.unlink()
        log.symlink_to('/etc/passwd')
        with self.assertRaises(ValueError):
            CAPTURE.capture(self.root, self.output)


class HostedWorkflow(unittest.TestCase):
    def test_docker_invoker_owns_private_mount_without_capability_relaxation(self):
        image = [{'Id': producer.LOCK['base_image']['config'], 'Architecture': 'amd64', 'Os': 'linux'}]
        for info, user in (({'Architecture': 'x86_64'}, ['--user', '1001:1002']),
                           ({'host': {'arch': 'amd64'}}, [])):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output, inputs = root / 'output', root / 'inputs'
                output.mkdir(mode=0o700)
                inputs.mkdir(mode=0o700)
                replies = [json.dumps(info).encode(), json.dumps(image).encode(), b'']
                with patch.object(producer.Runner, '__call__', side_effect=replies) as run, \
                        patch.object(producer, 'cleanup_owned'), \
                        patch.object(producer.os, 'getuid', return_value=1001), \
                        patch.object(producer.os, 'getgid', return_value=1002):
                    producer.launch('engine', output, inputs, 'build')
                command = run.call_args_list[-1].args[0]
                self.assertEqual(command[2:2 + len(user)], user)
                self.assertEqual('--user' in command, bool(user))
                for flag, value in (('--cap-drop', 'ALL'), ('--security-opt', 'no-new-privileges'),
                                    ('--network', 'none'), ('--cpus', '1'), ('--memory', '3g')):
                    self.assertEqual(command[command.index(flag) + 1], value)
                self.assertIn('--read-only', command)
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)

    def test_read_only_workflow_keeps_isolated_producer_and_two_prefix_consumer(self):
        workflow = yaml.safe_load((CAPTURE.ROOT / '.github/workflows/geos-local-qualification.yaml').read_text())
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        job = workflow['jobs']['qualify']
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        steps = job['steps']
        checkout = next(step for step in steps if step.get('uses', '').startswith('actions/checkout@'))
        self.assertFalse(checkout['with']['persist-credentials'])
        build = next(step for step in steps if step['name'].startswith('Build and qualify'))
        self.assertIn('takx/geos-cache/producer.py --engine docker', build['run'])
        consume = next(step for step in steps if step['name'].startswith('Restore and execute'))
        self.assertIn("proof['local_oci_subject']", consume['run'])
        self.assertIn("proof['identity']['execution']['mode'] == 'native'", consume['run'])
        self.assertIn("len(restored['results']) == 2", consume['run'])
        self.assertIn("'registry_roundtrip', 'publisher_authenticated', 'sdk_accepted', 'security_accepted'", consume['run'])
        sanitize = next(step for step in steps if step.get('id') == 'diagnostics')
        self.assertIn('!cancelled()', sanitize['if'])
        failure = next(step for step in steps if step['name'] == 'Retain sanitized failure diagnostics')
        self.assertIn("steps.diagnostics.outcome == 'success'", failure['if'])
        self.assertNotIn('/geos-producer\n', failure['with']['path'])
        self.assertNotIn('/geos-consumers\n', failure['with']['path'])


if __name__ == '__main__':
    unittest.main()
