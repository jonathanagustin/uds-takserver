# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline policy fixtures are NOT cryptographic qualification or CI acceptance."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

SCRIPT = Path(__file__).resolve().parents[1] / '.github/scripts/install-reuse-shadow.py'
SPEC = importlib.util.spec_from_file_location('shadow', SCRIPT)
SHADOW = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHADOW)


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.base, self.head, self.merge, self.target, self.policy, self.tree = [c * 40 for c in '123456']
        self.workflow = b'name: reviewed fixture\n'
        self.receipt = {
            'schema_version': 1, 'kind': 'install-execution', 'reuse_authorized': False,
            'source': {'repository': SHADOW.REPO, 'checkout_commit': self.merge, 'checkout_tree': self.tree},
            'workflow': {'path': SHADOW.WORKFLOW, 'sha256': hashlib.sha256(self.workflow).hexdigest()},
            'run': {'id': 100, 'attempt': 2, 'event': 'pull_request', 'job': 'validate-install'},
            'scenario': {'name': 'install', 'architecture': 'amd64', 'mode': 'ordinary'},
            'execution': {'step': 'Test', 'result': 'success'},
            'inputs': {'package_key': 'amd64-' + 'a' * 16, 'package_archive_sha256': 'sha256:' + 'a' * 64,
                       'checkpoint_oci_digest': 'sha256:' + 'b' * 64, 'core_oci_digest': 'sha256:' + 'c' * 64,
                       'uds_version': '0.41.0',
                       'image_digests': {name: 'sha256:' + 'd' * 64 for name in SHADOW.ENCODER.IMAGES}}}
        self.pr = {'number': 5, 'state': 'closed', 'merged': True, 'merge_commit_sha': self.target,
                   'base': {'ref': 'main', 'sha': self.base, 'repo': {'full_name': SHADOW.REPO}},
                   'head': {'sha': self.head, 'repo': {'full_name': SHADOW.REPO}}}
        self.run = {'id': 100, 'workflow_id': 20, 'path': SHADOW.WORKFLOW, 'event': 'pull_request',
                    'head_sha': self.head, 'head_repository': {'full_name': SHADOW.REPO, 'id': 1427088300},
                    'status': 'completed', 'conclusion': 'success', 'run_attempt': 2,
                    'pull_requests': [{'number': 5}]}
        self.job = {'id': 30, 'name': 'validate-install (amd64)', 'status': 'completed',
                    'conclusion': 'success', 'run_id': 100, 'run_attempt': 2,
                    'steps': [self.step(name) for name in ('Test', 'Authenticate optional install receipt',
                                                         'Upload authenticated install receipt')]}
        self.artifact = {'id': 40, 'name': 'install-receipt-X64-online-100-2', 'expired': False}
        self.api = mock.Mock()
        self.gets = {'pulls/5': self.pr,
                     f'git/commits/{self.merge}': {'sha': self.merge, 'tree': {'sha': self.tree},
                                                 'parents': [{'sha': self.base}, {'sha': self.head}]},
                     f'git/commits/{self.target}': {'sha': self.target, 'tree': {'sha': self.tree},
                                                  'parents': [{'sha': self.base}]},
                     'actions/workflows/test.yaml': {'id': 20}, 'actions/runs/100': self.run}
        self.api.get.side_effect = lambda path: self.gets[path]
        self.api.file.return_value = self.workflow
        self.runs = [self.run]
        self.jobs = [self.job]
        self.artifacts = [self.artifact]
        self.api.pages.side_effect = lambda path, key: {'workflow_runs': self.runs, 'jobs': self.jobs,
                                                       'artifacts': self.artifacts}[key]

    @staticmethod
    def step(name):
        return {'name': name, 'status': 'completed', 'conclusion': 'success'}

    def verification(self, raw):
        # Structural policy fixture only. CLI obtains this exclusively from gh crypto verification.
        ref = 'refs/pull/5/merge'
        signer = f'https://github.com/{SHADOW.REPO}/{SHADOW.WORKFLOW}@{ref}'
        invocation = f'https://github.com/{SHADOW.REPO}/actions/runs/100/attempts/2'
        cert = {'issuer': 'https://token.actions.githubusercontent.com', 'subjectAlternativeName': signer,
                'buildSignerURI': signer, 'buildSignerDigest': self.merge,
                'sourceRepositoryDigest': self.merge, 'sourceRepositoryURI': f'https://github.com/{SHADOW.REPO}',
                'sourceRepositoryRef': ref, 'sourceRepositoryIdentifier': '1427088300',
                'sourceRepositoryOwnerIdentifier': '5193877', 'runnerEnvironment': 'github-hosted',
                'runInvocationURI': invocation, 'buildTrigger': 'pull_request',
                'buildConfigURI': signer, 'buildConfigDigest': self.merge}
        statement = {'_type': 'https://in-toto.io/Statement/v1',
                     'predicateType': 'https://slsa.dev/provenance/v1',
                     'subject': [{'name': f'install-receipt-{self.receipt["scenario"]["name"]}-amd64.json',
                                  'digest': {'sha256': hashlib.sha256(raw).hexdigest()}}],
                     'predicate': {'buildDefinition': {
                         'buildType': 'https://actions.github.io/buildtypes/workflow/v1',
                         'externalParameters': {'workflow': {'path': SHADOW.WORKFLOW, 'ref': ref,
                                                            'repository': f'https://github.com/{SHADOW.REPO}'}},
                         'resolvedDependencies': [{'digest': {'gitCommit': self.merge},
                                                   'uri': f'git+https://github.com/{SHADOW.REPO}@{ref}'}]},
                         'runDetails': {'metadata': {'invocationId': invocation}}}}
        return [{'verificationResult': {'signature': {'certificate': cert}, 'statement': statement}}]

    def prospective(self):
        return {'context': {'event': 'push', 'run_id': 200, 'attempt': 1,
                            'arch': 'amd64', 'scenario': 'install'},
                'checkout': {'source': {'checkout_commit': self.target, 'checkout_tree': self.tree},
                             'workflow': copy.deepcopy(self.receipt['workflow'])},
                'inputs': copy.deepcopy(self.receipt['inputs'])}

    def test_acquired_inputs_match_but_shadow_always_requires_fresh(self):
        raw = json.dumps(self.receipt).encode()
        self.api.artifact.return_value = raw
        result = SHADOW.shadow(self.api, raw, self.verification(raw), 5, self.target,
                               self.policy, self.prospective())
        self.assertTrue(result['evidence_qualified'])
        self.assertTrue(result['prospective_comparison']['inputs_equal'])
        self.assertEqual(result['prospective_comparison']['historical_inputs_sha256'],
                         result['prospective_comparison']['prospective_inputs_sha256'])
        self.assertEqual(result['decision'], 'execute-fresh')
        self.assertFalse(result['reuse_authorized'])
        self.assertIn('prospective-input-equivalence-not-proven', result['blockers'])

    def test_cli_consumes_acquired_snapshot_after_historical_verification(self):
        raw = json.dumps(self.receipt).encode()
        self.api.artifact.return_value = raw
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'receipt.json').write_bytes(raw)
            (root / 'bundle.jsonl').write_text('{}')
            (root / 'prospective.json').write_text(json.dumps(self.prospective()))
            argv = ['shadow', '--receipt', str(root / 'receipt.json'), '--bundle', str(root / 'bundle.jsonl'),
                    '--pr', '5', '--target', self.target, '--policy-ref', self.policy,
                    '--prospective', str(root / 'prospective.json')]
            with mock.patch.object(sys, 'argv', argv), mock.patch.object(SHADOW, 'GitHub', return_value=self.api), \
                    mock.patch.object(SHADOW, 'command', return_value=self.verification(raw)), \
                    mock.patch.object(sys, 'stdout', new=io.StringIO()) as output:
                SHADOW.main()
            result = json.loads(output.getvalue())
        self.assertTrue(result['prospective_comparison']['inputs_equal'])
        self.assertEqual(result['decision'], 'execute-fresh')
        self.assertFalse(result['reuse_authorized'])

    def test_each_image_digest_difference_is_observed(self):
        for image in SHADOW.ENCODER.IMAGES:
            prospective = self.prospective()
            prospective['inputs']['image_digests'][image] = 'sha256:' + 'e' * 64
            with self.subTest(image=image):
                self.assertFalse(SHADOW.compare_prospective(self.receipt, prospective,
                                                           self.target)['inputs_equal'])

    def test_every_actual_input_difference_stays_fresh(self):
        for field in SHADOW.ENCODER.INPUTS:
            with self.subTest(field=field):
                prospective = self.prospective()
                if field == 'image_digests':
                    prospective['inputs'][field]['cloudtak'] = 'sha256:' + 'e' * 64
                elif field == 'package_key':
                    prospective['inputs'][field] = 'amd64-' + 'e' * 16
                elif field == 'uds_version':
                    prospective['inputs'][field] = '0.42.0'
                else:
                    prospective['inputs'][field] = 'sha256:' + 'e' * 64
                self.assertFalse(SHADOW.compare_prospective(self.receipt, prospective,
                                                           self.target)['inputs_equal'])

    def test_prospective_source_run_scenario_and_schema_fail_closed(self):
        changes = [lambda p: p['context'].update(event='pull_request'),
                   lambda p: p['context'].update(run_id=True),
                   lambda p: p['context'].update(arch='arm64'),
                   lambda p: p['context'].update(scenario='install-airgap'),
                   lambda p: p['checkout']['source'].update(checkout_commit=self.head),
                   lambda p: p['checkout']['source'].update(checkout_tree=self.head),
                   lambda p: p['checkout']['workflow'].update(sha256='a' * 64),
                   lambda p: p['inputs'].update(secret='SENTINEL'),
                   lambda p: p['inputs']['image_digests'].pop('cloudtak')]
        for change in changes:
            p = self.prospective(); change(p)
            with self.assertRaises(SHADOW.Unavailable) as caught:
                SHADOW.compare_prospective(self.receipt, p, self.target)
            self.assertNotIn('SENTINEL', str(caught.exception))

    def test_execution_class_match_and_mismatch_stay_fresh(self):
        self.job['labels'] = ['ubuntu-22.04']
        self.job['runner_group_name'] = 'GitHub Actions'
        raw = json.dumps(self.receipt).encode()
        self.api.artifact.return_value = raw

        matched = SHADOW.shadow(self.api, raw, self.verification(raw), 5, self.target,
                                self.policy, expected_runner='ubuntu-22.04')
        self.assertTrue(matched['evidence_qualified'])
        self.assertEqual(matched['execution_class_comparison'], {
            'historical_runner': 'ubuntu-22.04',
            'expected_runner': 'ubuntu-22.04',
            'runner_group': 'GitHub Actions',
            'execution_class_equal': True,
            'reuse_authorized': False,
        })
        self.assertEqual(matched['decision'], 'execute-fresh')
        self.assertFalse(matched['reuse_authorized'])
        self.assertIn('execution-class-policy-not-established', matched['blockers'])
        self.assertNotIn('execution-class-differs', matched['blockers'])

        mismatched = SHADOW.shadow(self.api, raw, self.verification(raw), 5, self.target,
                                   self.policy, expected_runner='ubuntu-24.04')
        self.assertTrue(mismatched['evidence_qualified'])
        self.assertFalse(mismatched['execution_class_comparison']['execution_class_equal'])
        self.assertEqual(mismatched['blockers'][0], 'execution-class-differs')
        self.assertEqual(mismatched['decision'], 'execute-fresh')
        self.assertFalse(mismatched['reuse_authorized'])

    def test_execution_class_invalid_labels_group_or_expected_runner_fail_closed(self):
        valid_job = {'labels': ['ubuntu-22.04'], 'runner_group_name': 'GitHub Actions'}
        self.assertTrue(SHADOW.compare_execution_class(
            {'labels': ['ubuntu-22.04-arm'], 'runner_group_name': 'GitHub Actions'},
            'arm64', 'ubuntu-22.04-arm')['execution_class_equal'])

        bad_cases = [
            (valid_job, 'amd64', 'ubuntu-22.04-arm', 'execution-class-expected-runner-invalid'),
            (valid_job, 'amd64', 'self-hosted', 'execution-class-expected-runner-invalid'),
            (valid_job, 'ppc64', 'ubuntu-22.04', 'execution-class-expected-runner-invalid'),
            ({'labels': ['ubuntu-22.04'], 'runner_group_name': 'Default'}, 'amd64', 'ubuntu-22.04',
             'execution-class-runner-group-invalid'),
            ({'labels': ['ubuntu-22.04']}, 'amd64', 'ubuntu-22.04',
             'execution-class-runner-group-invalid'),
            ({'labels': [], 'runner_group_name': 'GitHub Actions'}, 'amd64', 'ubuntu-22.04',
             'execution-class-job-labels-invalid'),
            ({'labels': ['ubuntu-22.04', 'self-hosted'], 'runner_group_name': 'GitHub Actions'},
             'amd64', 'ubuntu-22.04', 'execution-class-job-labels-invalid'),
            ({'labels': ['ubuntu-22.04-arm'], 'runner_group_name': 'GitHub Actions'},
             'amd64', 'ubuntu-22.04', 'execution-class-job-labels-invalid'),
            ({'labels': ['SENTINEL-runner'], 'runner_group_name': 'GitHub Actions'},
             'amd64', 'ubuntu-22.04', 'execution-class-job-labels-invalid'),
        ]
        for job, arch, expected, reason in bad_cases:
            with self.subTest(job=job, arch=arch, expected=expected):
                with self.assertRaises(SHADOW.Unavailable) as caught:
                    SHADOW.compare_execution_class(job, arch, expected)
                self.assertEqual(str(caught.exception), reason)
                self.assertNotIn('SENTINEL', str(caught.exception))

    def test_cli_accepts_expected_runner_and_keeps_fresh(self):
        self.job['labels'] = ['ubuntu-22.04']
        self.job['runner_group_name'] = 'GitHub Actions'
        raw = json.dumps(self.receipt).encode()
        self.api.artifact.return_value = raw
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'receipt.json').write_bytes(raw)
            (root / 'bundle.jsonl').write_text('{}')
            argv = ['shadow', '--receipt', str(root / 'receipt.json'), '--bundle', str(root / 'bundle.jsonl'),
                    '--pr', '5', '--target', self.target, '--policy-ref', self.policy,
                    '--expected-runner', 'ubuntu-22.04']
            with mock.patch.object(sys, 'argv', argv), mock.patch.object(SHADOW, 'GitHub', return_value=self.api), \
                    mock.patch.object(SHADOW, 'command', return_value=self.verification(raw)), \
                    mock.patch.object(sys, 'stdout', new=io.StringIO()) as output:
                SHADOW.main()
            result = json.loads(output.getvalue())
        self.assertTrue(result['execution_class_comparison']['execution_class_equal'])
        self.assertEqual(result['decision'], 'execute-fresh')
        self.assertFalse(result['reuse_authorized'])

    def result(self, verified_change=None):
        raw = json.dumps(self.receipt).encode()
        self.api.artifact.return_value = raw
        verified = self.verification(raw)
        if verified_change:
            verified_change(verified)
        result = SHADOW.shadow(self.api, raw, verified, 5, self.target, self.policy)
        self.assertFalse(result['reuse_authorized'])
        self.assertEqual(result['decision'], 'execute-fresh')
        return result

    def test_structural_evidence_can_pass_but_always_requires_fresh(self):
        result = self.result()
        self.assertTrue(result['evidence_qualified'])
        self.assertIn('prospective-input-equivalence-not-proven', result['blockers'])

    def test_schema_unknown_fields_and_true_authorization_refused(self):
        for key, value in [('schema_version', 2), ('reuse_authorized', True), ('extra', 'secret')]:
            with self.subTest(key=key):
                prior = copy.deepcopy(self.receipt)
                self.receipt[key] = value
                self.assertFalse(self.result()['evidence_qualified'])
                self.receipt = prior

    def test_synthetic_parents_and_target_tree_must_match_exactly(self):
        for change in ('source-parent', 'target-parent', 'tree', 'target-two-parents'):
            with self.subTest(change=change):
                saved = copy.deepcopy(self.gets)
                if change == 'source-parent':
                    self.gets[f'git/commits/{self.merge}']['parents'].reverse()
                elif change == 'target-parent':
                    self.gets[f'git/commits/{self.target}']['parents'][0]['sha'] = self.head
                elif change == 'tree':
                    self.gets[f'git/commits/{self.target}']['tree']['sha'] = 'f' * 40
                else:
                    self.gets[f'git/commits/{self.target}']['parents'].append({'sha': self.head})
                self.assertFalse(self.result()['evidence_qualified'])
                self.gets = saved

    def test_pr423_stale_snapshot_uses_actual_tested_base_not_metadata(self):
        # Actual observed PR423 base/head/synthetic/tree values. The target
        # commit and crypto object are STRUCTURAL fixtures, not a real squash
        # or signature claim. Authentic qualification is a separate live probe.
        stale_snapshot = '904905f0a88dca238e9a283280e13f1017e8ed5d'
        tested_base = '8691a11b0db743923cbe99f8c3f486a9bfa3b04d'
        tested_head = '2a5afcf2244de21271ba688bd348b9f10c3188d3'
        tested_merge = '0109e4ac6bb05801fb6460b0c4274afbff3e0078'
        tested_tree = 'f8b066fe58de38ed2547bb0b9de7534684d48a46'
        self.gets[f'git/commits/{tested_merge}'] = {
            'sha': tested_merge, 'tree': {'sha': tested_tree},
            'parents': [{'sha': tested_base}, {'sha': tested_head}]}
        self.merge = tested_merge
        self.pr['base']['sha'] = stale_snapshot
        self.pr['head']['sha'] = tested_head
        self.run['head_sha'] = tested_head
        self.receipt['source'].update(checkout_commit=tested_merge, checkout_tree=tested_tree)
        self.gets[f'git/commits/{self.target}'].update(
            tree={'sha': tested_tree}, parents=[{'sha': tested_base}])
        result = self.result()
        self.assertTrue(result['evidence_qualified'])
        self.assertFalse(result['reuse_authorized'])
        for blocker in ('prospective-input-equivalence-not-proven',
                        'execution-class-policy-not-established',
                        'complete-producer-dependency-policy-not-established',
                        'consumer-enforcement-not-enabled'):
            self.assertIn(blocker, result['blockers'])

    def test_actual_base_change_is_rejected_even_when_pr_snapshot_matches_target(self):
        # Equal trees do not excuse a different actual main base after testing.
        advanced = 'a' * 40
        self.pr['base']['sha'] = advanced
        self.gets[f'git/commits/{self.target}']['parents'] = [{'sha': advanced}]
        self.assertFalse(self.result()['evidence_qualified'])

    def test_changed_current_pr_head_is_not_the_tested_head(self):
        self.pr['head']['sha'] = 'a' * 40
        self.run['head_sha'] = self.pr['head']['sha']
        self.assertFalse(self.result()['evidence_qualified'])

    def test_missing_extra_or_malformed_parents_fail_closed(self):
        for source_parents, target_parents in (([], [{'sha': self.base}]),
                                                ([{'sha': self.base}], [{'sha': self.base}]),
                                                ([{'sha': self.base}, {'sha': self.head}], []),
                                                ([{'sha': 'invalid'}, {'sha': self.head}],
                                                 [{'sha': 'invalid'}])):
            self.gets[f'git/commits/{self.merge}']['parents'] = source_parents
            self.gets[f'git/commits/{self.target}']['parents'] = target_parents
            self.assertFalse(self.result()['evidence_qualified'])

    def test_open_or_fork_pr_is_not_same_repository_squash(self):
        for change in ('open', 'fork', 'wrong-merge'):
            saved = copy.deepcopy(self.pr)
            if change == 'open':
                self.pr['merged'] = False
            elif change == 'fork':
                self.pr['head']['repo']['full_name'] = 'other/fork'
            else:
                self.pr['merge_commit_sha'] = self.head
            self.assertFalse(self.result()['evidence_qualified'])
            self.pr.clear()
            self.pr.update(saved)

    def test_newer_failure_or_later_attempt_prevents_old_green(self):
        self.runs.append({**self.run, 'id': 101, 'conclusion': 'failure'})
        self.assertFalse(self.result()['evidence_qualified'])
        self.runs.pop()
        self.run['run_attempt'] = 3
        self.assertFalse(self.result()['evidence_qualified'])

    def test_postmerge_empty_pr_array_keeps_signed_selected_run(self):
        self.run['pull_requests'] = []
        self.assertTrue(self.result()['evidence_qualified'])

    def test_newer_failed_cancelled_or_queued_empty_pr_array_invalidates_old_green(self):
        self.run['pull_requests'] = []
        for status, conclusion in (('completed', 'failure'), ('completed', 'cancelled'), ('queued', None)):
            self.runs.append({**self.run, 'id': 101, 'status': status, 'conclusion': conclusion,
                              'pull_requests': []})
            self.assertIn('not-latest-applicable-run', self.result()['blockers'])
            self.runs.pop()

    def older_run(self, status, conclusion, attempt):
        self.runs.append({**self.run, 'id': 99, 'run_attempt': attempt, 'status': status,
                          'conclusion': conclusion, 'pull_requests': []})
        return self.result()

    def test_older_id_queued_rerun_cannot_be_masked_by_newer_green(self):
        result = self.older_run('queued', None, 2)
        self.assertIn('other-applicable-run-unsettled-or-rerun-order-unproven', result['blockers'])

    def test_older_id_running_rerun_cannot_be_masked_by_newer_green(self):
        result = self.older_run('in_progress', None, 2)
        self.assertIn('other-applicable-run-unsettled-or-rerun-order-unproven', result['blockers'])

    def test_older_id_failed_rerun_cannot_be_masked_by_newer_green(self):
        result = self.older_run('completed', 'failure', 2)
        self.assertIn('other-applicable-run-unsettled-or-rerun-order-unproven', result['blockers'])

    def test_older_id_cancelled_rerun_cannot_be_masked_by_newer_green(self):
        result = self.older_run('completed', 'cancelled', 2)
        self.assertIn('other-applicable-run-unsettled-or-rerun-order-unproven', result['blockers'])

    def test_older_id_unsettled_first_attempt_also_requires_fresh(self):
        for status in ('queued', 'in_progress'):
            result = self.older_run(status, None, 1)
            self.assertIn('other-applicable-run-unsettled-or-rerun-order-unproven', result['blockers'])
            self.runs.pop()

    def test_known_older_completed_first_attempt_can_be_superseded(self):
        for conclusion in ('failure', 'cancelled'):
            result = self.older_run('completed', conclusion, 1)
            self.assertTrue(result['evidence_qualified'])
            self.runs.pop()

    def test_run_list_identity_must_match_query_even_for_older_rows(self):
        for change in ({'event': 'push'}, {'head_sha': 'a' * 40}, {'workflow_id': 21},
                       {'head_repository': {}}, {'head_repository': {'full_name': SHADOW.REPO}},
                       {'head_repository': {'full_name': 'other/fork', 'id': 1427088300}},
                       {'head_repository': {'full_name': SHADOW.REPO, 'id': 99}},
                       {'run_attempt': None}):
            self.runs.append({**self.run, 'id': 99, **change})
            self.assertIn('run-list-identity-missing-contradictory-or-ambiguous', self.result()['blockers'])
            self.runs.pop()

    def test_positively_identified_foreign_fork_does_not_supersede_same_repo_run(self):
        self.runs.append({**self.run, 'id': 101,
                          'head_repository': {'full_name': 'other/fork', 'id': 99}})
        self.assertTrue(self.result()['evidence_qualified'])

    def test_missing_selected_run_and_duplicate_run_ids_fail_fresh(self):
        self.runs[:] = [{**self.run, 'id': 99}]
        self.assertFalse(self.result()['evidence_qualified'])
        self.runs[:] = [self.run, copy.deepcopy(self.run)]
        self.assertFalse(self.result()['evidence_qualified'])

    def test_selected_run_listing_cannot_mask_changed_attempt_or_result(self):
        for change in ({'run_attempt': 3}, {'status': 'queued', 'conclusion': None},
                       {'conclusion': 'failure'}):
            self.runs[:] = [{**self.run, **change}]
            self.assertIn('selected-run-list-attempt-or-result-changed', self.result()['blockers'])
        self.runs[:] = [self.run]

    def test_actual_execution_failure_skip_missing_or_duplicate_rejected(self):
        for state in ('failure', 'skipped', 'missing', 'duplicate'):
            saved = copy.deepcopy(self.job['steps'])
            if state == 'missing':
                self.job['steps'].pop(0)
            elif state == 'duplicate':
                self.job['steps'].append(self.step('Test'))
            else:
                self.job['steps'][0]['conclusion'] = state
            self.assertFalse(self.result()['evidence_qualified'])
            self.job['steps'] = saved

    def test_offline_requires_real_cleanup_success(self):
        self.receipt['scenario']['name'] = 'install-airgap'
        name = 'Install and test without node internet egress'
        self.receipt['execution']['step'] = name
        self.job['name'] = 'validate-install (amd64, airgap)'
        self.job['steps'][0]['name'] = name
        self.artifact['name'] = 'install-receipt-X64-airgap-100-2'
        self.assertFalse(self.result()['evidence_qualified'])
        self.job['steps'].append(self.step('Restore airgap node egress'))
        self.assertTrue(self.result()['evidence_qualified'])

    def test_duplicate_job_or_artifact_and_expired_artifact_rejected(self):
        self.jobs.append(copy.deepcopy(self.job))
        self.assertFalse(self.result()['evidence_qualified'])
        self.jobs.pop()
        self.artifacts.append(copy.deepcopy(self.artifact))
        self.assertFalse(self.result()['evidence_qualified'])
        self.artifacts.pop()
        self.artifact['expired'] = True
        self.assertFalse(self.result()['evidence_qualified'])

    def test_issuer_invocation_subject_and_repository_id_exact_binding(self):
        for key in ('issuer', 'runInvocationURI', 'sourceRepositoryIdentifier', 'buildSignerDigest'):
            def alter(v, key=key):
                v[0]['verificationResult']['signature']['certificate'][key] = 'wrong'
            self.assertFalse(self.result(alter)['evidence_qualified'])
        def altered_subject(v):
            v[0]['verificationResult']['statement']['subject'][0]['digest']['sha256'] = '0' * 64
        self.assertFalse(self.result(altered_subject)['evidence_qualified'])

    def test_reviewed_workflow_and_helpers_not_just_content_key(self):
        self.api.file.side_effect = lambda commit, path: self.workflow if commit == self.merge else b'changed'
        self.assertFalse(self.result()['evidence_qualified'])

    def test_api_failure_becomes_bounded_fresh_result(self):
        self.api.get.side_effect = SHADOW.Unavailable('command-failed')
        result = self.result()
        self.assertFalse(result['evidence_qualified'])
        self.assertEqual(result['blockers'][0], 'command-failed')

    def test_signed_receipt_must_equal_actual_artifact_bytes(self):
        self.api.artifact.side_effect = lambda artifact_id: b'other'
        self.assertIn('artifact-receipt-bytes-mismatch', self.result()['blockers'])

    def artifact_change(self, change):
        """Change remote metadata only after the first run checks have passed."""
        raw = json.dumps(self.receipt).encode()

        def download(artifact_id):
            change()
            return raw

        self.api.artifact.side_effect = download
        return self.result()

    def test_selected_rerun_during_artifact_download_requires_fresh(self):
        for status, conclusion in (('queued', None), ('in_progress', None),
                                   ('completed', 'failure'), ('completed', 'success')):
            with self.subTest(status=status, conclusion=conclusion):
                original = copy.deepcopy(self.run)
                result = self.artifact_change(lambda: self.run.update(
                    run_attempt=3, status=status, conclusion=conclusion))
                self.assertFalse(result['evidence_qualified'])
                self.assertNotIn('evidence', result)
                self.assertIn('run-identity-attempt-or-result-mismatch', result['blockers'])
                self.run.clear()
                self.run.update(original)

    def test_newer_run_during_artifact_download_requires_fresh(self):
        for status, conclusion in (('queued', None), ('completed', 'failure'),
                                   ('completed', 'cancelled'), ('completed', 'success')):
            with self.subTest(status=status, conclusion=conclusion):
                result = self.artifact_change(lambda: self.runs.append(
                    {**self.run, 'id': 101, 'status': status, 'conclusion': conclusion}))
                self.assertFalse(result['evidence_qualified'])
                self.assertIn('not-latest-applicable-run', result['blockers'])
                self.runs.pop()

    def test_older_run_rerun_during_artifact_download_requires_fresh(self):
        older = {**self.run, 'id': 99, 'run_attempt': 1}
        self.runs.append(older)
        result = self.artifact_change(lambda: older.update(
            run_attempt=2, status='completed', conclusion='failure'))
        self.assertFalse(result['evidence_qualified'])
        self.assertIn('other-applicable-run-unsettled-or-rerun-order-unproven', result['blockers'])

    def test_final_run_listing_failure_requires_fresh(self):
        def unavailable(path, key):
            raise SHADOW.Unavailable('command-failed')

        result = self.artifact_change(lambda: setattr(self.api.pages, 'side_effect', unavailable))
        self.assertFalse(result['evidence_qualified'])
        self.assertIn('command-failed', result['blockers'])

    def test_final_run_read_failure_requires_fresh(self):
        result = self.artifact_change(lambda: setattr(
            self.api.get, 'side_effect', SHADOW.Unavailable('command-failed')))
        self.assertFalse(result['evidence_qualified'])
        self.assertIn('command-failed', result['blockers'])

    def test_unchanged_evidence_is_rechecked_after_artifact_download(self):
        result = self.result()
        self.assertTrue(result['evidence_qualified'])
        self.assertEqual(self.api.get.call_args_list[-1], mock.call('actions/runs/100'))
        self.assertEqual(self.api.pages.call_args_list[-1], mock.call(
            f'actions/workflows/20/runs?event=pull_request&head_sha={self.head}', 'workflow_runs'))
        self.assertEqual(self.api.get.call_args_list.count(mock.call('actions/runs/100')), 2)


class TransportTests(unittest.TestCase):
    def test_all_pages_including_expected_total_and_unique_ids(self):
        api = SHADOW.GitHub('unused')
        api.get = mock.Mock(side_effect=[{'total_count': 101, 'jobs': [{'id': i} for i in range(100)]},
                                         {'total_count': 101, 'jobs': [{'id': 100}]}])
        self.assertEqual(len(api.pages('jobs', 'jobs')), 101)
        self.assertIn('page=2', api.get.call_args.args[0])

    def test_missing_or_changed_page_and_duplicate_ids_fail(self):
        for second in ({'total_count': 101, 'jobs': []}, {'total_count': 102, 'jobs': [{'id': 100}]},
                       {'total_count': 101, 'jobs': [{'id': 99}]}):
            api = SHADOW.GitHub('unused')
            api.get = mock.Mock(side_effect=[{'total_count': 101, 'jobs': [{'id': i} for i in range(100)]}, second])
            with self.assertRaises(SHADOW.Unavailable):
                api.pages('jobs', 'jobs')

    def test_archive_rejects_traversal_symlink_extra_entries_and_large_payload(self):
        for name, mode, data, extra in [('install-receipt-install-amd64.json', stat.S_IFREG, b'ok', False),
                                       ('../escape', stat.S_IFREG, b'bad', False),
                                       ('install-receipt-install-amd64.json', stat.S_IFLNK, b'target', False),
                                       ('install-receipt-install-amd64.json', stat.S_IFREG, b'x' * 65537, False),
                                       ('install-receipt-install-amd64.json', stat.S_IFREG, b'ok', True)]:
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as archive:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                archive.writestr(info, data)
                if extra:
                    archive.writestr('extra', 'bad')
            if name.startswith('install-') and mode == stat.S_IFREG and len(data) == 2 and not extra:
                self.assertEqual(SHADOW.archive_receipt(stream.getvalue()), b'ok')
            else:
                with self.assertRaises(SHADOW.Unavailable):
                    SHADOW.archive_receipt(stream.getvalue())

    def test_real_unavailable_crypto_command_reports_fresh_not_success(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / 'receipt.json'
            bundle = Path(temp) / 'bundle.jsonl'
            fixture = PolicyTests()
            fixture.setUp()
            receipt.write_text(json.dumps(fixture.receipt))
            bundle.write_text('{}')
            result = subprocess.run([sys.executable, str(SCRIPT), '--receipt', str(receipt),
                                     '--bundle', str(bundle), '--pr', '5', '--target', fixture.target,
                                     '--policy-ref', fixture.policy, '--gh', '/missing/gh'],
                                    capture_output=True, text=True, check=True)
            value = json.loads(result.stdout)
            self.assertFalse(value['reuse_authorized'])
            self.assertFalse(value['evidence_qualified'])
            self.assertEqual(value['decision'], 'execute-fresh')

    def test_real_cli_malformed_scalar_remains_fresh_without_traceback(self):
        with tempfile.TemporaryDirectory() as temp:
            receipt = Path(temp) / 'receipt.json'
            bundle = Path(temp) / 'bundle.jsonl'
            receipt.write_text('null')
            bundle.write_text('{}')
            result = subprocess.run([sys.executable, str(SCRIPT), '--receipt', str(receipt),
                                     '--bundle', str(bundle), '--pr', '5', '--target', 'a' * 40,
                                     '--policy-ref', 'b' * 40], capture_output=True, text=True, check=True)
            self.assertEqual(result.stderr, '')
            self.assertEqual(json.loads(result.stdout)['decision'], 'execute-fresh')

    def test_bounded_local_reads_reject_symlinks_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'input'
            source.write_bytes(b'12345')
            self.assertEqual(SHADOW.bounded_file(source, 5), b'12345')
            with self.assertRaises(SHADOW.Unavailable):
                SHADOW.bounded_file(source, 4)
            link = Path(temp) / 'link'
            link.symlink_to(source)
            with self.assertRaises(OSError):
                SHADOW.bounded_file(link, 5)

    def test_deep_json_is_fresh_not_traceback(self):
        value = SHADOW.shadow(None, b'[' * 2000 + b'0' + b']' * 2000, [], 5, 'a' * 40, 'b' * 40)
        self.assertFalse(value['evidence_qualified'])
        self.assertEqual(value['decision'], 'execute-fresh')

    def test_real_timeout_kills_child_and_returns_bounded_reason(self):
        with mock.patch.object(SHADOW, 'COMMAND_TIMEOUT', 0.05):
            with self.assertRaisesRegex(SHADOW.Unavailable, 'command-timeout'):
                SHADOW.command([sys.executable, '-c', 'import time; time.sleep(10)'])

    def test_duplicate_json_fields_are_not_silently_overwritten(self):
        with self.assertRaises(ValueError):
            json.loads('{"schema_version":1,"schema_version":2}', object_pairs_hook=SHADOW.unique_fields)

    def test_real_output_limit_refuses_oversized_child(self):
        with self.assertRaises(SHADOW.Unavailable):
            SHADOW.command([sys.executable, '-c', 'import os; os.write(1, b"x" * (5 * 1024 * 1024))'])


class ScenarioSetTests(unittest.TestCase):
    def setUp(self):
        self.tested, self.tree, self.target = [c * 40 for c in '364']

    def shadow_entry(self, scenario='install', arch='amd64', job_id=30, artifact_id=40, **overrides):
        entry = {
            'schema_version': 1, 'mode': 'shadow', 'reuse_authorized': False,
            'decision': 'execute-fresh', 'evidence_qualified': True,
            'evidence': {
                'source_run': 100, 'attempt': 2, 'job_id': job_id, 'artifact_id': artifact_id,
                'scenario': scenario, 'architecture': arch,
                'tested_commit': self.tested, 'tested_tree': self.tree, 'target_commit': self.target,
            },
            'blockers': ['prospective-input-equivalence-not-proven'],
        }
        entry.update(overrides)
        return entry

    def test_complete_three_scenario_set_matches_validation_mode_contract_and_stays_fresh(self):
        results = [
            self.shadow_entry('install-airgap', 'amd64', job_id=32, artifact_id=42),
            self.shadow_entry('install', 'arm64', job_id=31, artifact_id=41),
            self.shadow_entry('install', 'amd64', job_id=30, artifact_id=40,
                              prospective_comparison={'inputs_equal': True, 'reuse_authorized': False},
                              execution_class_comparison={'execution_class_equal': True, 'reuse_authorized': False}),
        ]
        summary = SHADOW.evaluate_scenario_set(results)
        self.assertTrue(summary['set_qualified'])
        self.assertTrue(summary['complete_install_set'])
        self.assertEqual(summary['missing_scenarios'], [])
        self.assertEqual(summary['decision'], 'execute-fresh')
        self.assertFalse(summary['reuse_authorized'])
        self.assertFalse(summary['verifier_preview']['reuse_authorized'])
        self.assertNotIn('scenario-set-incomplete', summary['blockers'])

        # Cross-contract check against .github/scripts/test-validation-mode.py.
        mode_path = Path(__file__).resolve().parents[1] / '.github/scripts/test-validation-mode.py'
        mode_spec = importlib.util.spec_from_file_location('test_validation_mode', mode_path)
        mode = importlib.util.module_from_spec(mode_spec)
        mode_spec.loader.exec_module(mode)

        with self.assertRaisesRegex(ValueError, 'not authorized'):
            mode.parse_reused_install_evidence('push', 'false', 'none', summary['verifier_preview'])

        authorized_copy = copy.deepcopy(summary['verifier_preview'])
        authorized_copy['reuse_authorized'] = True
        parsed = mode.parse_reused_install_evidence('push', 'false', 'none', authorized_copy)
        self.assertEqual(len(parsed), 3)
        self.assertEqual({(row['scenario'], row['architecture']) for row in parsed},
                         mode.REQUIRED_INSTALL_SCENARIOS)

    def test_partial_set_reports_missing_scenarios_and_incomplete_blocker(self):
        results = [
            self.shadow_entry('install', 'amd64', job_id=30, artifact_id=40),
            self.shadow_entry('install-airgap', 'amd64', job_id=32, artifact_id=42),
        ]
        summary = SHADOW.evaluate_scenario_set(results)
        self.assertTrue(summary['set_qualified'])
        self.assertFalse(summary['complete_install_set'])
        self.assertEqual(summary['missing_scenarios'], [{'scenario': 'install-online', 'architecture': 'arm64'}])
        self.assertEqual(summary['blockers'][0], 'scenario-set-incomplete')
        self.assertFalse(summary['reuse_authorized'])

    def test_scenario_set_rejects_duplicates_drift_and_mismatches_fail_closed(self):
        cases = [
            ([], 'scenario-set-empty-or-oversized'),
            ([self.shadow_entry()] * 4, 'scenario-set-empty-or-oversized'),
            ([self.shadow_entry(evidence_qualified=False)], 'scenario-set-entry-unqualified'),
            ([self.shadow_entry(reuse_authorized=True)], 'scenario-set-entry-unqualified'),
            ([self.shadow_entry('install', 'amd64'), self.shadow_entry('install', 'amd64', job_id=31)],
             'scenario-set-duplicate-scenario'),
            ([self.shadow_entry('install-airgap', 'arm64')], 'scenario-set-scenario-invalid'),
            ([self.shadow_entry('install', 'amd64'),
              self.shadow_entry('install', 'arm64', evidence={
                  'source_run': 100, 'attempt': 2, 'job_id': 31, 'artifact_id': 41,
                  'scenario': 'install', 'architecture': 'arm64',
                  'tested_commit': self.tested, 'tested_tree': 'f' * 40, 'target_commit': self.target,
              })], 'scenario-set-commit-or-tree-mismatch'),
            ([self.shadow_entry(prospective_comparison={'inputs_equal': False, 'reuse_authorized': False})],
             'scenario-set-prospective-mismatch'),
            ([self.shadow_entry(execution_class_comparison={'execution_class_equal': False, 'reuse_authorized': False})],
             'scenario-set-execution-class-mismatch'),
        ]
        for payload, reason in cases:
            with self.subTest(reason=reason):
                summary = SHADOW.evaluate_scenario_set(payload)
                self.assertFalse(summary['set_qualified'])
                self.assertFalse(summary['complete_install_set'])
                self.assertEqual(summary['blockers'][0], reason)
                self.assertEqual(summary['decision'], 'execute-fresh')
                self.assertFalse(summary['reuse_authorized'])

    def test_cli_scenario_set_aggregates_bounded_files_and_rejects_symlinks_or_mixed_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for name, entry in [
                ('amd64.json', self.shadow_entry('install', 'amd64', job_id=30, artifact_id=40)),
                ('arm64.json', self.shadow_entry('install', 'arm64', job_id=31, artifact_id=41)),
                ('airgap.json', self.shadow_entry('install-airgap', 'amd64', job_id=32, artifact_id=42)),
            ]:
                path = root / name
                path.write_text(json.dumps(entry))
                files.append(path)

            argv = ['shadow', '--scenario-set', *(str(p) for p in files)]
            with mock.patch.object(sys, 'argv', argv), mock.patch.object(sys, 'stdout', new=io.StringIO()) as out:
                SHADOW.main()
            result = json.loads(out.getvalue())
            self.assertEqual(result['mode'], 'shadow-set')
            self.assertTrue(result['set_qualified'])
            self.assertTrue(result['complete_install_set'])
            self.assertEqual(result['decision'], 'execute-fresh')
            self.assertFalse(result['reuse_authorized'])

            # Symlink input must fail closed without traceback.
            link = root / 'link.json'
            link.symlink_to(files[0])
            with mock.patch.object(sys, 'argv', ['shadow', '--scenario-set', str(link)]), \
                    mock.patch.object(sys, 'stdout', new=io.StringIO()) as out:
                SHADOW.main()
            sym_result = json.loads(out.getvalue())
            self.assertEqual(sym_result['mode'], 'shadow-set')
            self.assertFalse(sym_result['set_qualified'])
            self.assertEqual(sym_result['blockers'], ['scenario-set-input-unavailable'])

            # Mixing --scenario-set with single-receipt flags must fail closed.
            with mock.patch.object(sys, 'argv', ['shadow', '--scenario-set', str(files[0]), '--pr', '5']), \
                    mock.patch.object(sys, 'stdout', new=io.StringIO()) as out:
                SHADOW.main()
            mixed_result = json.loads(out.getvalue())
            self.assertEqual(mixed_result['mode'], 'shadow-set')
            self.assertFalse(mixed_result['set_qualified'])
            self.assertEqual(mixed_result['blockers'], ['scenario-set-input-unavailable'])


if __name__ == '__main__':
    unittest.main()
