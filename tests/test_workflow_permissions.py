# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Permission boundaries for read-only planning and explicit publishing jobs."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def workflow(name):
    return yaml.safe_load((ROOT / '.github/workflows' / name).read_text())


def write_scopes(permissions):
    return {name for name, level in permissions.items() if level == 'write'}


def require_dependency_review(document):
    """The official checker, rather than a feature probe, owns API failures."""
    job = document['jobs']['dependency-review']
    actions = [step for step in job['steps']
               if step.get('uses', '').startswith('actions/dependency-review-action@')]
    if len(actions) != 1:
        raise ValueError('one official dependency review action is required')
    action = actions[0]
    if job.get('if') is not None or action.get('if') is not None:
        raise ValueError('dependency review must not be conditionally skipped')
    if any(item.get('continue-on-error', False) is not False for item in (job, action)):
        raise ValueError('dependency review failures must fail the check')
    if any(step.get('id') == 'feature' for step in job['steps']):
        raise ValueError('a feature probe must not replace dependency review')


def require_fork_scan_reporting(document):
    """Optional conversation writes cannot replace retained scan evidence."""
    triggers = document.get('on', document.get(True, {}))
    if set(triggers) != {'pull_request'}:
        raise ValueError('scan must use the unprivileged pull-request trigger')
    job = document['jobs']['scan-and-compare']
    if job.get('if') is not None or job.get('continue-on-error', False):
        raise ValueError('the scan job must run normally and fail on errors')
    steps = job['steps']
    compare = next(step for step in steps if step.get('id') == 'compare-scans')
    upload = next(step for step in steps if step.get('id') == 'upload-comparison-results')
    summary = next(step for step in steps if step['name'] == 'Link comparison in job summary')
    for step in (compare, upload, summary):
        if step.get('if') is not None or step.get('continue-on-error', False):
            raise ValueError('comparison and retention must use normal success semantics')
    if upload['with'].get('path') != 'comment.md' or upload['with'].get('if-no-files-found') != 'error':
        raise ValueError('a complete comparison artifact is required')
    if not steps.index(compare) < steps.index(upload) < steps.index(summary):
        raise ValueError('retain the complete comparison before reporting')
    comments = [step for step in steps if 'gh pr comment' in step.get('run', '')]
    if len(comments) != 2:
        raise ValueError('preserve both ordinary comment paths')
    for step, large in zip(comments, ('false', 'true')):
        expected = ("github.event.pull_request.head.repo.full_name == github.repository && "
                    f"steps.compare-scans.outputs.artifact == '{large}'")
        if ' '.join(step.get('if', '').split()) != expected or steps.index(step) < steps.index(summary):
            raise ValueError('conversation writes require a same-repository PR after retention')


class WorkflowPermissionTests(unittest.TestCase):
    def test_fork_scans_retain_small_and_large_comparisons_before_optional_comments(self):
        document = workflow('scan.yaml')
        require_fork_scan_reporting(document)
        job = document['jobs']['scan-and-compare']
        self.assertEqual(write_scopes(job['permissions']), {'pull-requests'})
        steps = job['steps']
        summary = next(step for step in steps if step['name'] == 'Link comparison in job summary')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'summary.md'
            result = subprocess.run(['bash', '-eu', '-c', summary['run']],
                env={**os.environ, 'GITHUB_STEP_SUMMARY': str(output),
                     'ARTIFACT_URL': 'https://github.com/example/repo/actions/runs/1/artifacts/2'},
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('[Download the complete comparison](https://github.com/example/repo/actions/runs/1/artifacts/2)',
                          output.read_text())

    def test_fork_scan_policy_rejects_missing_evidence_or_elevated_trigger(self):
        original = workflow('scan.yaml')
        for target, key, value in (
            ('job', 'if', 'false'), ('job', 'continue-on-error', True),
            ('compare-scans', 'continue-on-error', True),
            ('upload-comparison-results', 'if', "steps.compare-scans.outputs.artifact == 'true'"),
            ('Post markdown as comment', 'if', "steps.compare-scans.outputs.artifact == 'false'"),
            ('Post comment about artifact', 'if', "steps.compare-scans.outputs.artifact == 'true'"),
        ):
            changed = copy.deepcopy(original)
            job = changed['jobs']['scan-and-compare']
            step = job if target == 'job' else next(
                item for item in job['steps'] if item.get('id') == target or item['name'] == target)
            step[key] = value
            with self.subTest(target=target, key=key), self.assertRaises(ValueError):
                require_fork_scan_reporting(changed)
        changed = copy.deepcopy(original)
        changed.pop(True, None)
        changed['on'] = {'pull_request_target': {}}
        with self.assertRaises(ValueError):
            require_fork_scan_reporting(changed)

    def test_new_validation_jobs_do_not_inherit_write_or_oidc_access(self):
        for name in ('test.yaml', 'benchmark.yaml', 'full-core.yaml', 'dependency-review.yaml'):
            with self.subTest(workflow=name):
                document = workflow(name)
                self.assertFalse(write_scopes(document['permissions']))
                # GitHub replaces the default permission map at job level.
                # The planner has no override and must remain read-only.
                if name == 'test.yaml':
                    planner = document['jobs']['image-keys']
                    effective = planner.get('permissions', document['permissions'])
                    self.assertFalse(write_scopes(effective))

    def test_container_planner_cannot_publish_even_when_images_are_missing(self):
        document = workflow('container.yaml')
        planner = document['jobs']['plan']
        self.assertEqual(planner['permissions']['packages'], 'read')
        self.assertFalse(write_scopes(planner['permissions']))
        decision = next(step for step in planner['steps'] if step.get('id') == 'image')
        self.assertEqual(decision['env']['CHECK_ONCE'], 'true')
        # Exercise the real helper with a registry that lacks every image.
        # Even its build-capable default must never publish from this planner.
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            calls = directory / 'docker-calls.jsonl'
            docker = directory / 'docker'
            docker.write_text('#!' + sys.executable + '\n'
                              'import json,os,sys\n'
                              'with open(os.environ["PERMISSION_TEST_CALLS"],"a") as f: '
                              'f.write(json.dumps(sys.argv[1:])+"\\n")\n'
                              'sys.exit(0 if sys.argv[1:2]==["login"] else 1)\n')
            curl = directory / 'curl'
            curl.write_text('#!' + sys.executable + '\n'
                            'import sys\n'
                            'print("404" if "-w" in sys.argv else \'{"token":"fixture-pull-token"}\')\n')
            docker.chmod(0o755)
            curl.chmod(0o755)
            environment = {**os.environ, 'PATH': str(directory) + os.pathsep + os.defpath,
                           'GH_TOKEN': 'fixture-token', 'IMAGE_TAG': 'immutable-fixture',
                           'GITHUB_WORKFLOW': 'Container', 'BUILD_IF_MISSING': 'true',
                           'CHECK_ONCE': 'true', 'PERMISSION_TEST_CALLS': str(calls)}
            result = subprocess.run(['bash', str(ROOT / '.github/scripts/wait-for-tak-images.sh')],
                                    env=environment, cwd=ROOT, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1)
            observed = [json.loads(line) for line in calls.read_text().splitlines()]
            self.assertEqual(len([call for call in observed if call[:2] == ['manifest', 'inspect']]), 6)
            self.assertTrue(all(call[:1] == ['login'] or call[:2] == ['manifest', 'inspect']
                                for call in observed))

    def test_dependency_check_has_no_comment_mutation_capability(self):
        document = workflow('dependency-review.yaml')
        job = document['jobs']['dependency-review']
        self.assertFalse(write_scopes(job.get('permissions', document['permissions'])))
        action = next(step for step in job['steps']
                      if step.get('uses', '').startswith('actions/dependency-review-action@'))
        self.assertEqual(action['with']['comment-summary-in-pr'], 'never')

    def test_dependency_review_cannot_succeed_by_skipping_its_checker(self):
        document = workflow('dependency-review.yaml')
        require_dependency_review(document)
        job = document['jobs']['dependency-review']
        self.assertEqual(job['timeout-minutes'], 15)
        checkout = next(step for step in job['steps']
                        if step.get('uses', '').startswith('actions/checkout@'))
        self.assertIs(checkout['with']['persist-credentials'], False)
        action = next(step for step in job['steps']
                      if step.get('uses', '').startswith('actions/dependency-review-action@'))
        self.assertEqual(action['uses'], 'actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294')
        for target, key, value in (('job', 'if', 'false'), ('action', 'if', "steps.feature.outputs.enabled == 'true'"),
                                   ('job', 'continue-on-error', True), ('action', 'continue-on-error', True),
                                   ('action', 'uses', './unchecked-local-wrapper')):
            changed = copy.deepcopy(document)
            changed_job = changed['jobs']['dependency-review']
            item = changed_job if target == 'job' else next(step for step in changed_job['steps']
                       if step.get('uses', '').startswith('actions/dependency-review-action@'))
            item[key] = value
            with self.subTest(target=target, bypass=key), self.assertRaises(ValueError):
                require_dependency_review(changed)
        changed = copy.deepcopy(document)
        changed['jobs']['dependency-review']['steps'].insert(1, {'id': 'feature', 'run': 'exit 0'})
        with self.assertRaises(ValueError):
            require_dependency_review(changed)

    def test_real_identity_and_publishing_consumers_keep_explicit_grants(self):
        # Configured Chainguard identities use the GitHub OIDC login in the
        # shared setup task. Removing these job grants would break that path.
        consumers = {'test.yaml': ('validate-install', 'validate-upgrade', 'validate-upgrade-pg15'),
                     'benchmark.yaml': ('benchmark',),
                     'full-core.yaml': ('validate-full-core',)}
        for name, jobs in consumers.items():
            for job in jobs:
                with self.subTest(workflow=name, job=job):
                    self.assertEqual(workflow(name)['jobs'][job]['permissions']['id-token'], 'write')
        # A package producer and attestation signer are deliberately distinct
        # from read-only admission. Do not fix the score by disabling them.
        for job in ('build', 'manifest'):
            permissions = workflow('container.yaml')['jobs'][job]['permissions']
            self.assertTrue({'packages', 'id-token', 'attestations'}.issubset(write_scopes(permissions)))


if __name__ == '__main__':
    unittest.main()
