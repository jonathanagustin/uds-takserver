# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Same-agent namespace snapshot selection, failure privacy and command contract."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'tests/agent-api-snapshot.py'
SPEC = importlib.util.spec_from_file_location('agent_api_snapshot', HELPER)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
UID = '12345678-1234-1234-1234-123456789abc'


class AgentApiSnapshotTests(unittest.TestCase):
    def pod(self):
        return {'metadata': {'namespace': 'zarf', 'name': 'agent-hook-fixture', 'uid': UID},
                'spec': {'nodeName': 'k3d-fixture-server-0', 'serviceAccountName': 'agent-hook', 'containers': [{'name': 'server'}]},
                'status': {'containerStatuses': [{'name': 'server', 'containerID': 'containerd://' + 'a' * 64,
                    'restartCount': 0, 'state': {'running': {'startedAt': '2026-11-17T00:00:00Z'}}}]}}

    def test_rejects_untrusted_exec_selection(self):
        for section, key, value in [('metadata', 'namespace', 'other'),
                                     ('metadata', 'name', 'agent-hook-../private'),
                                     ('metadata', 'uid', 'not-an-id'),
                                     ('spec', 'serviceAccountName', 'account\nsecret'),
                                     ('spec', 'nodeName', '../private')]:
            pod = copy.deepcopy(self.pod())
            pod[section][key] = value
            with self.subTest(key=key), self.assertRaises((AssertionError, TypeError)):
                MODULE.identity(pod)
        pod = self.pod()
        pod['spec']['containers'] *= 2
        with self.assertRaises(AssertionError):
            MODULE.identity(pod)

    def test_captured_identity_requires_a_bounded_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'pod.json'
            target.write_text(json.dumps(self.pod()))
            (root / 'link.json').symlink_to(target)
            os.mkfifo(root / 'pipe')
            (root / 'oversized.json').write_text(json.dumps({**self.pod(), 'opaque': 'never-print' * 7000}))
            for path in (root / 'link.json', root / 'pipe', root / 'oversized.json', root / 'missing'):
                with self.subTest(path=path.name), patch.object(MODULE, 'command') as command:
                    result = MODULE.snapshot(path)
                    self.assertEqual(result['result'], 'invalid-agent-identity')
                    self.assertFalse(result['identityQualified'])
                    self.assertNotIn('never-print', json.dumps(result))
                    command.assert_not_called()

    def invoke(self, body, count=1, live_pod=None, readback=None):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            executable = directory / 'uds'
            readback = readback or "print(Path(__file__).with_name('live-pod.json').read_text())"
            dispatch = """import sys
from pathlib import Path
if sys.argv[1:] == ['zarf', 'tools', 'kubectl', '--request-timeout=2s',
    'get', 'pod', '-n', 'zarf', 'agent-hook-fixture', '-o', 'json']:
""" + textwrap.indent(readback, '    ') + '\n    raise SystemExit(0)\n'
            executable.write_text('#!/usr/bin/env python3\n' + dispatch + body)
            executable.chmod(0o755)
            pod = directory / 'pod.json'
            pod.write_text(json.dumps(self.pod()))
            (directory / 'live-pod.json').write_text(json.dumps(live_pod or self.pod()))
            result = subprocess.run(['python3', str(HELPER), *([str(pod)] * count)],
                                    env={**os.environ, 'PATH': str(directory) + ':' + os.environ['PATH']},
                                    capture_output=True, text=True, check=True, timeout=10)
            self.assertEqual(result.stderr, '')
            return result.stdout

    def test_same_container_fixed_read_only_command_and_two_pod_cap(self):
        output = self.invoke(f"""import sys
assert sys.argv[1:] == ['zarf', 'tools', 'kubectl', '--request-timeout=2s',
    'exec', '-n', 'zarf', 'agent-hook-fixture', '-c', 'server', '--', '/zarf',
    'tools', 'kubectl', '--request-timeout=2s', 'get', 'namespace', 'velero',
    '-o', 'jsonpath={{.metadata.uid}}']
print('{UID}', end='')
""", count=3)
        records = [json.loads(line) for line in output.splitlines()]
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertEqual(record['result'], 'lookup-succeeded')
            self.assertEqual(record['agent']['uid'], UID)
            self.assertEqual(record['targetUID'], UID)
            self.assertEqual(record['snapshot'], 'postfailure')
            self.assertTrue(record['identityQualified'])
            self.assertEqual(record['agent']['containerID'], 'containerd://' + 'a' * 64)
            self.assertIsInstance(record['elapsed_ms'], int)

    def test_replaced_pod_or_restarted_container_before_exec_is_not_probed(self):
        for field in ('pod', 'container', 'restart', 'start'):
            changed = self.pod()
            if field == 'pod':
                changed['metadata']['uid'] = '87654321-4321-4321-4321-cba987654321'
            elif field == 'container':
                changed['status']['containerStatuses'][0]['containerID'] = 'containerd://' + 'b' * 64
            elif field == 'restart':
                changed['status']['containerStatuses'][0]['restartCount'] = 1
            else:
                changed['status']['containerStatuses'][0]['state']['running']['startedAt'] = '2026-11-17T00:00:01Z'
            with self.subTest(field=field):
                record = json.loads(self.invoke("raise AssertionError('must not exec changed identity')\n", live_pod=changed))
                self.assertEqual(record['result'], 'agent-identity-changed')
                self.assertFalse(record['execAttempted'])
                self.assertFalse(record['identityQualified'])
                self.assertNotIn('agent', record)
                self.assertNotIn('targetUID', record)

    def test_replacement_or_restart_during_exec_discards_lookup_attribution(self):
        for field in ('pod', 'container', 'restart', 'start'):
            changed = self.pod()
            if field == 'pod':
                changed['metadata']['uid'] = '87654321-4321-4321-4321-cba987654321'
            elif field == 'container':
                changed['status']['containerStatuses'][0]['containerID'] = 'containerd://' + 'b' * 64
            elif field == 'restart':
                changed['status']['containerStatuses'][0]['restartCount'] = 1
            else:
                changed['status']['containerStatuses'][0]['state']['running']['startedAt'] = '2026-11-17T00:00:01Z'
            body = f"Path(__file__).with_name('live-pod.json').write_text({json.dumps(changed)!r})\nprint('{UID}', end='')\n"
            with self.subTest(field=field):
                record = json.loads(self.invoke(body))
                self.assertEqual(record['result'], 'agent-identity-changed')
                self.assertTrue(record['execAttempted'])
                self.assertEqual(record['execResult'], 'lookup-succeeded')
                self.assertFalse(record['identityQualified'])
                self.assertNotIn('agent', record)
                self.assertNotIn('targetUID', record)

    def test_container_order_changes_do_not_change_named_server_identity(self):
        reordered = self.pod()
        reordered['spec']['containers'].insert(0, {'name': 'sidecar'})
        reordered['status']['containerStatuses'].insert(0, {'name': 'sidecar'})
        record = json.loads(self.invoke(f"print('{UID}', end='')\n", live_pod=reordered))
        self.assertEqual(record['result'], 'lookup-succeeded')
        self.assertTrue(record['identityQualified'])

    def test_unavailable_or_oversized_live_identity_never_exposes_response(self):
        for body in ("print('Bearer never-print'); raise SystemExit(7)",
                     "print('x' * 65537 + 'never-print')", "print('not-json-never-print')"):
            with self.subTest(body=body):
                output = self.invoke("raise AssertionError('must not exec without identity')\n", readback=body)
                record = json.loads(output)
                self.assertEqual(record['result'], 'agent-identity-unavailable')
                self.assertFalse(record['identityQualified'])
                self.assertFalse(record['execAttempted'])
                self.assertNotIn('never-print', output)
        output = self.invoke(f"Path(__file__).with_name('live-pod.json').unlink()\nprint('{UID}', end='')\n")
        record = json.loads(output)
        self.assertEqual(record['result'], 'agent-identity-unavailable')
        self.assertEqual(record['execResult'], 'lookup-succeeded')
        self.assertFalse(record['identityQualified'])
        self.assertNotIn('agent', record)
        self.assertNotIn('targetUID', record)

    def test_failure_and_oversize_outputs_never_escape(self):
        for code, stdout, expected in [(7, 'Bearer never-print', 'exec-or-lookup-failed'),
                                       (127, '', 'exec-unavailable'),
                                       (124, '', 'exec-or-lookup-timeout'),
                                       (0, 'x' * 500, 'invalid-lookup-output'),
                                       (0, UID + '\nBearer never-print', 'invalid-lookup-output')]:
            body = f"import sys\nprint({stdout!r}, end='')\nprint('password=never-print', file=sys.stderr)\nsys.exit({code})\n"
            with self.subTest(code=code, expected=expected):
                output = self.invoke(body)
                self.assertNotIn('never-print', output)
                self.assertNotIn('x' * 50, output)
                self.assertEqual(json.loads(output)['result'], expected)

    def test_real_outer_timeout_is_bounded_and_private(self):
        output = self.invoke("import time\ntime.sleep(20)\n")
        record = json.loads(output)
        self.assertEqual(record['result'], 'agent-identity-unavailable')
        self.assertEqual(record['execResult'], 'exec-or-lookup-timeout')
        self.assertFalse(record['identityQualified'])
        self.assertLess(record['elapsed_ms'], 5000)

    def test_outer_termination_cleans_exec_descendants_without_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pod = root / 'pod.json'
            pod.write_text(json.dumps(self.pod()))
            ready, escaped = root / 'ready', root / 'escaped'
            child = ("import pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                     f"time.sleep(1); pathlib.Path({str(escaped)!r}).touch()")
            executable = root / 'uds'
            executable.write_text('#!/usr/bin/env python3\n' + textwrap.dedent(f'''\
                import pathlib,signal,subprocess,sys,time
                if 'exec' not in sys.argv:
                    print(pathlib.Path({str(pod)!r}).read_text())
                    raise SystemExit(0)
                signal.signal(signal.SIGTERM,signal.SIG_IGN)
                subprocess.Popen([sys.executable,'-c',{child!r}])
                print('never-print-private-exec',file=sys.stderr,flush=True)
                print('{UID}',end='',flush=True)
                pathlib.Path({str(ready)!r}).touch()
                time.sleep(30)
                '''))
            executable.chmod(0o755)
            process = subprocess.Popen([sys.executable, '-B', str(HELPER), str(pod), str(pod)],
                env={**os.environ, 'PATH': str(root) + ':' + os.environ['PATH']},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 2
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=2)
                self.assertEqual(process.returncode, 143)
                record = json.loads(stdout)
                self.assertEqual(record['result'], 'snapshot-interrupted')
                self.assertFalse(record['identityQualified'])
                self.assertNotIn('agent', record)
                self.assertNotIn('targetUID', record)
                self.assertEqual(stderr, b'')
                time.sleep(1.1)
                self.assertFalse(escaped.exists())
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=2)

    def test_identity_reads_and_lookup_share_one_aggregate_deadline(self):
        output = self.invoke(f"import time\ntime.sleep(1)\nprint('{UID}', end='')\n",
                             readback="import time\ntime.sleep(1.2)\nprint(Path(__file__).with_name('live-pod.json').read_text())")
        record = json.loads(output)
        self.assertEqual(record['result'], 'agent-identity-unavailable')
        self.assertEqual(record['execResult'], 'lookup-succeeded')
        self.assertFalse(record['identityQualified'])
        self.assertNotIn('targetUID', record)
        self.assertLess(record['elapsed_ms'], 4000)

    def test_failure_pipeline_retains_primary_logs_before_snapshot(self):
        from tests.test_upgrade_agent_diagnostics import AgentDiagnosticsTests
        fixture = AgentDiagnosticsTests()
        pod = self.pod()
        # API metadata is selected once; nested exec uses no exported credential.
        body = f"""import json, sys
pod = {pod!r}
if 'exec' in sys.argv:
    print('{UID}', end='')
elif 'service' in sys.argv:
    print(json.dumps({{'spec': {{'selector': {{'app': 'agent-hook'}}}}}}))
elif 'pods' in sys.argv and any(a.startswith('jsonpath=') for a in sys.argv):
    print('agent-hook-fixture')
elif 'pod' in sys.argv:
    print(json.dumps(pod))
elif 'logs' in sys.argv:
    print('retained-primary-log')
"""
        output = fixture.capture_fixture("import sys\nsys.exit(1)\n", body)
        self.assertIn('lookup-succeeded', output)
        self.assertEqual(output.count('retained-primary-log'), 2)
        self.assertLess(output.rindex('retained-primary-log'), output.index('lookup-succeeded'))

    def test_probe_is_after_all_primary_logs_and_keeps_existing_deadlines(self):
        script = (ROOT / 'tests/upgrade-agent-diagnostics.sh').read_text()
        self.assertLess(script.index('safe logs -n zarf "${pod}" -c server --previous'),
                        script.index('agent-api-snapshot.py'))
        self.assertLess(script.index('done <<<"${pods}"'), script.index('agent-api-snapshot.py'))
        self.assertIn('elapsed < 30', script)
        self.assertIn("'3s'", HELPER.read_text())


if __name__ == '__main__':
    unittest.main()
