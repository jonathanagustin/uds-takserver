# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""GitHub provenance is checked independently of a candidate's own receipt."""

import copy
import io
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('candidate_artifacts', ROOT / '.github/scripts/candidate-artifacts.py')
CANDIDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CANDIDATE)


class FakeGitHub:
    repository = 'upstream/repo'

    def __init__(self):
        self.extra_jobs = []
        self.run = {'id': 42, 'run_attempt': 2, 'repository': {'full_name': self.repository},
                    'head_repository': {'full_name': 'contributor/repo'}, 'head_sha': 'a' * 40,
                    'event': 'pull_request', 'path': '.github/workflows/postgis-image.yaml', 'status': 'completed'}
        self.job = {'name': 'build (arm64)', 'status': 'completed', 'conclusion': 'success'}
        self.artifact = {'name': 'candidate-postgis-arm64-2', 'id': 56, 'expired': False,
                         'workflow_run': {'id': 42, 'head_sha': 'a' * 40}}

    def pages(self, path, field):
        if field == 'workflow_runs':
            return [self.run]
        if field == 'jobs':
            if '/42/attempts/2/jobs' not in path:
                raise AssertionError(path)
            return [self.job, *self.extra_jobs]
        if field == 'artifacts':
            return [self.artifact]
        raise AssertionError(path)


class ArtifactIdentityTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()
        self.context = {'repository': self.api.repository, 'head_repository': 'contributor/repo', 'head_sha': 'a' * 40,
                        'checkout_sha': 'd' * 40}
        self.identity = patch.object(CANDIDATE, 'expectation', side_effect=lambda group, arch, producer:
                                     {'producer': producer, 'group': group, 'architecture': arch})
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def test_success_requires_the_exact_head_run_attempt_and_native_job(self):
        artifact, expected = CANDIDATE.resolve_once(self.api, self.context, 'postgis', 'arm64')
        self.assertEqual(artifact['id'], 56)
        self.assertEqual(expected['producer']['run_id'], 42)
        self.assertEqual(expected['producer']['run_attempt'], 2)
        self.assertEqual(expected['producer']['workflow'], '.github/workflows/postgis-image.yaml')

    def test_wrong_head_repository_event_or_workflow_never_supplies_an_artifact(self):
        for key, value in [('repository', {'full_name': 'other/repo'}),
                           ('head_repository', {'full_name': 'attacker/repo'}),
                           ('event', 'pull_request_target'), ('path', '.github/workflows/other.yaml')]:
            with self.subTest(field=key):
                api = FakeGitHub()
                api.run[key] = value
                with self.assertRaisesRegex(ValueError, 'source or workflow mismatch'):
                    CANDIDATE.resolve_once(api, self.context, 'postgis', 'arm64')
        self.api.run['head_sha'] = 'b' * 40
        with self.assertRaises(CANDIDATE.Pending):
            CANDIDATE.resolve_once(self.api, self.context, 'postgis', 'arm64')

    def test_failed_skipped_missing_or_expired_native_outputs_fail_promptly(self):
        for outcome in ('failure', 'cancelled', 'skipped', 'timed_out'):
            with self.subTest(outcome=outcome):
                api = FakeGitHub()
                api.job['conclusion'] = outcome
                with self.assertRaisesRegex(ValueError, 'build failed'):
                    CANDIDATE.resolve_once(api, self.context, 'postgis', 'arm64')
        for key, value in [('name', 'candidate-postgis-arm64-1'), ('expired', True),
                           ('workflow_run', {'id': 41, 'head_sha': 'a' * 40}),
                           ('workflow_run', {'id': 42, 'head_sha': 'b' * 40})]:
            with self.subTest(field=key, value=value):
                api = FakeGitHub()
                api.artifact[key] = value
                with self.assertRaises(ValueError):
                    CANDIDATE.resolve_once(api, self.context, 'postgis', 'arm64')
        self.api.job['name'] = 'build (amd64)'
        with self.assertRaisesRegex(ValueError, 'did not finish successfully'):
            CANDIDATE.resolve_once(self.api, self.context, 'postgis', 'arm64')

    def test_only_a_running_run_may_wait_for_its_unfinished_native_job(self):
        self.api.run['status'] = 'in_progress'
        self.api.job['status'] = 'in_progress'
        with self.assertRaises(CANDIDATE.Pending):
            CANDIDATE.resolve_once(self.api, self.context, 'postgis', 'arm64')

    def test_pre_expansion_skipped_matrix_requires_the_successful_plan_and_exact_cache_artifact(self):
        self.api.run['path'] = '.github/workflows/container.yaml'
        self.api.job = {'name': 'build (${{ matrix.arch }})', 'status': 'completed', 'conclusion': 'skipped'}
        self.api.extra_jobs = [{'name': 'plan', 'status': 'completed', 'conclusion': 'success'}]
        self.api.artifact['name'] = 'candidate-cache-takserver-2'
        artifact, _ = CANDIDATE.resolve_once(self.api, self.context, 'takserver', 'arm64')
        self.assertEqual(artifact['name'], 'candidate-cache-takserver-2')
        self.api.extra_jobs.append({'name': 'build (amd64)', 'status': 'completed', 'conclusion': 'failure'})
        with self.assertRaisesRegex(ValueError, 'failed native build'):
            CANDIDATE.resolve_once(self.api, self.context, 'takserver', 'arm64')

    def test_path_admission_requires_the_current_pr_identity_and_complete_changed_files(self):
        context = {**self.context, 'base_sha': 'b' * 40, 'pull_number': 5}
        pull = {'head': {'sha': 'a' * 40, 'repo': {'full_name': 'contributor/repo'}},
                'base': {'sha': 'b' * 40}, 'changed_files': 1}
        api = type('PullAPI', (), {'repository': 'upstream/repo',
                                  'get': lambda self, path: pull,
                                  'pages': lambda self, path, field: [{'filename': 'docs/building.md'}]})()
        self.assertFalse(CANDIDATE.canonical_triggered(api, context, 'postgis'))
        api.pages = lambda path, field: [{'filename': '.github/container-and-chart/postgis/Dockerfile'}]
        self.assertTrue(CANDIDATE.canonical_triggered(api, context, 'postgis'))
        pull['changed_files'] = 2
        with self.assertRaisesRegex(ValueError, 'admission evidence'):
            CANDIDATE.canonical_triggered(api, context, 'postgis')
        pull['head']['sha'] = 'c' * 40
        with self.assertRaisesRegex(ValueError, 'PR source changed'):
            CANDIDATE.canonical_triggered(api, context, 'postgis')

    def test_artifact_zip_never_extracts_extra_files_traversal_or_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for extra in ('../escape', '/absolute', 'surprise'):
                archive = root / 'archive.zip'
                with zipfile.ZipFile(archive, 'w') as target:
                    target.writestr('receipt.json', '{}')
                    target.writestr('images.tar', 'bytes')
                    target.writestr(extra, 'unsafe')
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    CANDIDATE.unpack_artifact(archive, root / 'result')
                self.assertFalse((root / 'result').exists())
            with zipfile.ZipFile(archive, 'w') as target:
                target.writestr('receipt.json', '{}')
                member = zipfile.ZipInfo('images.tar')
                member.external_attr = 0o120777 << 16
                target.writestr(member, '/etc/passwd')
            with self.assertRaisesRegex(ValueError, 'regular file'):
                CANDIDATE.unpack_artifact(archive, root / 'result')

    def test_signed_blob_redirect_never_receives_the_github_token(self):
        request = Request('https://api.github.com/repos/upstream/repo/actions/artifacts/56/zip',
                          headers={'Authorization': 'Bearer secret-fixture', 'Accept': 'application/zip'})
        redirect = CANDIDATE.NoCredentialRedirect().redirect_request(
            request, None, 302, 'Found', {}, 'https://blob.example.test/signed-path')
        self.assertIsNone(redirect.get_header('Authorization'))
        self.assertEqual(redirect.get_header('Accept'), 'application/zip')
        with self.assertRaisesRegex(ValueError, 'outside HTTPS'):
            CANDIDATE.NoCredentialRedirect().redirect_request(request, None, 302, 'Found', {}, 'http://blob.example.test/path')

    def test_http_failures_distinguish_rate_and_permission_evidence_without_credentials(self):
        path = '/repos/upstream/repo/actions/runs?head_sha=' + 'a' * 40
        for message, reason, headers in (
                ('API rate limit exceeded for secret-fixture.', 'primary-rate-limit',
                 {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1794899999'}),
                ('You have exceeded a secondary rate limit.', 'secondary-rate-limit', {'Retry-After': '60'}),
                ('Resource not accessible by integration', 'integration-access-denied', {})):
            api = CANDIDATE.GitHub('upstream/repo', 'secret-fixture')
            error = HTTPError('https://private.invalid/signed?token=secret-fixture', 403, 'Forbidden',
                              {**headers, 'X-GitHub-Request-Id': '7C00:11EE9C:170F3C:24EA87:6AFBF9C8',
                               'Authorization': 'secret-fixture', 'Location': 'private-redirect-fixture'},
                              io.BytesIO(json.dumps({'message': message, 'private': 'private-body-fixture'}).encode()))
            with self.subTest(message=message), patch.object(api.opener, 'open', side_effect=error), \
                    self.assertRaises(ValueError) as raised:
                api.get(path)
            text = str(raised.exception)
            self.assertIn('GET /repos/upstream/repo/actions/runs returned HTTP 403', text)
            self.assertIn('reason=' + reason, text)
            for name, value in headers.items():
                self.assertIn(f'{name}={value}', text)
            self.assertIn('X-GitHub-Request-Id=7C00:11EE9C:170F3C:24EA87:6AFBF9C8', text)
            for private in ('secret-fixture', 'private.invalid', 'private-redirect-fixture', 'private-body-fixture', 'head_sha'):
                self.assertNotIn(private, text)

    def test_http_failure_diagnostics_bound_untrusted_body_and_header_values(self):
        for body in (b'<html>private-body-fixture</html>', json.dumps({'message': 'x' * 6000}).encode(),
                     json.dumps({'message': 'Download https://blob.example/signed?sig=private-signed-fixture '
                                 'Authorization: Bearer other-token-fixture'}).encode()):
            api = CANDIDATE.GitHub('upstream/repo', 'secret-fixture')
            error = HTTPError('https://api.github.com/fixture', 403, 'Forbidden',
                              {'X-RateLimit-Remaining': '0\nprivate-header-fixture',
                               'X-GitHub-Request-Id': 'private-header-fixture\n'}, io.BytesIO(body))
            with self.subTest(size=len(body)), patch.object(api.opener, 'open', side_effect=error), \
                    self.assertRaises(ValueError) as raised:
                api.get('/repos/upstream/repo/actions/runs')
            text = str(raised.exception)
            self.assertIn('reason=unclassified-api-error', text)
            self.assertLess(len(text), 256)
            self.assertNotIn('private-', text)
            self.assertNotIn('other-token-fixture', text)
            self.assertNotIn('https://', text)

    def test_every_diagnostic_header_rejects_the_actual_credential(self):
        for header, token in (('X-GitHub-Request-Id', 'ABCD:1234:ABCD:1234'),
                              ('X-RateLimit-Remaining', '123'), ('X-RateLimit-Reset', '1794899999'),
                              ('Retry-After', '60')):
            api = CANDIDATE.GitHub('upstream/repo', token)
            error = HTTPError('https://api.github.com/fixture', 403, 'Forbidden', {header: token},
                              io.BytesIO(b'{"message":"Bad credentials"}'))
            with self.subTest(header=header), patch.object(api.opener, 'open', side_effect=error), \
                    self.assertRaises(ValueError) as raised:
                api.get('/repos/upstream/repo/actions/runs')
            self.assertIn('reason=bad-credentials', str(raised.exception))
            self.assertNotIn(token, str(raised.exception))
            self.assertNotIn(header + '=', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
