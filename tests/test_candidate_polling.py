# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Exercise real candidate polling through in-memory conditional HTTP replies."""

from collections import Counter
from contextlib import redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('poll_candidate_consumer', ROOT / '.github/scripts/candidate-consumer.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)
A = C.ARTIFACTS


class Response(io.BytesIO):
    def __init__(self, payload, etag=None):
        super().__init__(json.dumps(payload).encode())
        self.headers = {'ETag': etag} if etag else {}


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.api = A.GitHub('upstream/repo', 'secret-token-fixture')
        self.context = {'repository': 'upstream/repo', 'head_repository': 'contributor/repo',
                        'head_sha': 'a' * 40, 'base_sha': 'b' * 40, 'checkout_sha': 'c' * 40, 'pull_number': 5}
        self.run = {'id': 42, 'run_attempt': 1, 'repository': {'full_name': 'upstream/repo'},
                    'head_repository': {'full_name': 'contributor/repo'}, 'head_sha': 'a' * 40,
                    'event': 'pull_request', 'path': '.github/workflows/postgis-image.yaml',
                    'status': 'in_progress', 'conclusion': None}
        self.clock = 0
        self.requests, self.sleeps = [], []
        self.failure_at = self.attempt_at = None
        self.amd_ready = self.no_runs = False

    def open(self, request, timeout):
        self.assertEqual(timeout, 60)
        self.assertEqual(request.get_header('Authorization'), 'Bearer secret-token-fixture')
        path = urlparse(request.full_url).path
        run = copy.deepcopy(self.run)
        if self.attempt_at is not None and self.clock >= self.attempt_at:
            run['run_attempt'] = 2
        if '/workflows/' in path:
            payload = {'workflow_runs': [] if self.no_runs else [run]}
        elif path.endswith('/jobs'):
            self.assertIn(f'/attempts/{run["run_attempt"]}/jobs', path)
            failed = self.failure_at is not None and self.clock >= self.failure_at
            payload = {'jobs': [{'name': 'build (amd64)', 'status': 'completed' if self.amd_ready else 'in_progress',
                                 'conclusion': 'success' if self.amd_ready else None},
                                {'name': 'build (arm64)', 'status': 'completed' if failed else 'in_progress',
                                 'conclusion': 'failure' if failed else None}]}
        elif path.endswith('/artifacts'):
            payload = {'artifacts': [{'id': 56, 'name': f'candidate-postgis-amd64-{run["run_attempt"]}',
                                     'expired': False, 'workflow_run': {'id': 42, 'head_sha': 'a' * 40}}]}
        else:
            raise AssertionError(path)
        etag = '"' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest() + '"'
        previous = request.get_header('If-none-match')
        status = 304 if previous == etag else 200
        self.requests.append({'time': self.clock, 'path': path, 'status': status, 'etag': previous})
        if status == 304:
            raise HTTPError(request.full_url, 304, 'Not Modified', {'ETag': etag}, io.BytesIO())
        return Response(payload, etag)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock += seconds

    def polling(self, callback):
        with patch.object(A, 'GitHub', return_value=self.api), patch.object(self.api.opener, 'open', side_effect=self.open), \
             patch.object(C, 'identity', return_value={}), \
             patch.object(A, 'expectation', side_effect=lambda group, arch, producer: {'architecture': arch, 'producer': producer}), \
             patch.object(C.time, 'monotonic', side_effect=lambda: self.clock), \
             patch.object(C.time, 'sleep', side_effect=self.sleep), \
             patch.object(C, 'published_images') as published, redirect_stdout(io.StringIO()):
            with self.assertRaises(ValueError) as raised:
                callback()
            published.assert_not_called()
        return str(raised.exception)

    def ready(self, timeout=3600):
        return C.ready('postgis', ['amd64', 'arm64'], self.context, Path('/unused'), timeout)

    def test_real_two_arch_ready_deduplicates_each_tick_and_measures_conditional_budget(self):
        self.assertIn('still running', self.polling(self.ready))
        self.assertEqual(self.clock, 3600)
        self.assertEqual(self.sleeps[:5], [15, 30, 60, 120, 120])
        self.assertTrue(all(0 < delay <= 120 for delay in self.sleeps))
        self.assertEqual(set(Counter(item['time'] for item in self.requests).values()), {2})
        # Includes the final deadline observation, not only ticks before it.
        self.assertEqual(len(self.requests), 68)
        self.assertEqual(Counter(item['status'] for item in self.requests), {200: 2, 304: 66})
        self.assertIsNone(self.api.snapshot_cache)

    def test_ready_arch_artifacts_are_revalidated_once_while_sibling_is_pending(self):
        self.amd_ready = True
        self.assertIn('still running', self.polling(lambda: self.ready(timeout=30)))
        self.assertEqual(set(Counter(item['time'] for item in self.requests).values()), {3})
        self.assertEqual(Counter(item['status'] for item in self.requests), {200: 3, 304: 6})

    def test_pending_amd_does_not_hide_failed_arm_after_conditional_refresh(self):
        self.failure_at = 15
        self.assertIn('native candidate build failed', self.polling(self.ready))
        self.assertEqual(self.sleeps, [15])
        self.assertEqual(self.clock, 15)
        self.assertEqual(Counter(item['status'] for item in self.requests), {200: 3, 304: 1})

    def test_new_attempt_uses_its_fresh_job_endpoint_before_terminal_failure(self):
        self.failure_at = self.attempt_at = 15
        self.assertIn('native candidate build failed', self.polling(self.ready))
        new_jobs = [item for item in self.requests if '/attempts/2/jobs' in item['path']]
        self.assertEqual(len(new_jobs), 1)
        self.assertIsNone(new_jobs[0]['etag'])
        self.assertEqual(self.sleeps, [15])

    def test_source_changes_replace_cached_payload_and_repeat_identity_validation(self):
        with patch.object(self.api.opener, 'open', side_effect=self.open):
            with self.api.snapshot():
                A.select_run(self.api, self.context, 'postgis')
            self.run['head_repository']['full_name'] = 'attacker/repo'
            with self.api.snapshot(), self.assertRaisesRegex(ValueError, 'source or workflow mismatch'):
                A.select_run(self.api, self.context, 'postgis')
        self.assertEqual([item['status'] for item in self.requests], [200, 200])

    def test_unsolicited_304_and_304_after_unversioned_payload_are_rejected(self):
        path = '/repos/upstream/repo/actions/runs'
        for prime in (False, True):
            api = A.GitHub('upstream/repo', 'secret-token-fixture')
            if prime:
                with patch.object(api.opener, 'open', return_value=Response({'private': 'old'})):
                    api.get(path)
            error = HTTPError(A.API + path, 304, 'Not Modified', {'ETag': '"new"'}, io.BytesIO())
            with self.subTest(prime=prime), patch.object(api.opener, 'open', side_effect=error), \
                    self.assertRaisesRegex(ValueError, 'without a validated ETag'):
                api.get(path)

    def test_http_failures_never_reuse_previously_validated_metadata(self):
        path = '/repos/upstream/repo/actions/runs'
        for status in (401, 403, 404, 429, 500):
            api = A.GitHub('upstream/repo', 'secret-token-fixture')
            with patch.object(api.opener, 'open', return_value=Response({'old': 'payload'}, '"validated"')):
                with api.snapshot():
                    api.get(path)
            error = HTTPError(A.API + path, status, 'Failure', {}, io.BytesIO(b'{"message":"Not Found"}'))
            with self.subTest(status=status), patch.object(api.opener, 'open', side_effect=error) as opened, \
                    patch.object(C.time, 'sleep') as slept, self.assertRaisesRegex(ValueError, f'HTTP {status}') as raised:
                with api.snapshot():
                    api.get(path)
            opened.assert_called_once()
            slept.assert_not_called()
            self.assertIs(raised.exception.__cause__, error)

    def test_missing_producer_caps_both_pollers_at_90_seconds(self):
        for native in (False, True):
            self.setUp()
            self.no_runs = True
            with self.subTest(native=native), patch.object(A, 'canonical_triggered', return_value=True):
                callback = (lambda: A.resolve(self.api, self.context, 'postgis', 'arm64')) if native else self.ready
                self.assertIn('not been scheduled', self.polling(callback))
            self.assertEqual(self.clock, 90)
            self.assertEqual(self.sleeps, [15, 30, 45])

    def test_native_acquire_uses_the_same_cadence_with_one_initial_selection_request(self):
        self.assertIn('still running', self.polling(lambda: C.acquire('postgis', 'arm64', self.context, Path('/unused'), timeout=3600)))
        self.assertEqual(len(self.requests), 69)
        self.assertEqual(Counter(item['status'] for item in self.requests), {200: 2, 304: 67})
        self.assertEqual(self.clock, 3600)


if __name__ == '__main__':
    unittest.main()
