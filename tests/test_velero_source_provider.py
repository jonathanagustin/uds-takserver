# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Fail-closed same-run provider, isolated helper and native workflow controls."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('owner_provider_test', ROOT / 'tests/velero-ownership/source_provider.py')
C = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(C)


class OwnerProviderTests(unittest.TestCase):
    def environment(self):
        return {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_REPOSITORY': 'jonathanagustin/uds-takserver', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': C.B.IDENTITY.removeprefix('https://github.com/'),
                'SCENARIO': 'velero', 'ARCHITECTURE': 'amd64', 'RUNNER_ARCH': 'X64',
                'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
                'CORE_RESTORE_POLICY_CANDIDATE': 'true', 'CORE_RESTORE_OWNER_TRIAL': 'true'}

    def test_default_path_is_inactive_and_unknown_selection_is_refused(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(C.enabled())
        with patch.dict(os.environ, {'CORE_RESTORE_OWNER_TRIAL': 'yes'}, clear=True):
            with self.assertRaises(ValueError):C.enabled()

    def test_wrong_workflow_source_platform_and_identity_modes_are_refused(self):
        changes = {'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_REF': 'refs/heads/topic',
                   'GITHUB_WORKFLOW_REF': 'other', 'GITHUB_REPOSITORY': 'other/repo',
                   'SCENARIO': 'identities', 'ARCHITECTURE': 'both', 'RUNNER_ARCH': 'ARM64',
                   'GITHUB_RUN_ID': '0', 'GITHUB_SHA': 'bad', 'IDENTITY_METHOD': 'package',
                   'CORE_RESTORE_POLICY_CANDIDATE': 'false', 'FRESH_STRICT': 'true'}
        with patch.object(C.B.platform, 'system', return_value='Linux'), patch.object(C.B.platform, 'machine', return_value='x86_64'):
            with patch.dict(os.environ, self.environment(), clear=True):self.assertTrue(C.enabled())
            for key, value in changes.items():
                with self.subTest(key=key), patch.dict(os.environ, {**self.environment(), key: value}, clear=True):
                    with self.assertRaises(ValueError):C.enabled()
            with patch.dict(os.environ, self.environment(), clear=True), patch.object(C.B.platform, 'machine', return_value='aarch64'):
                with self.assertRaises(ValueError):C.enabled()

    def fixture(self, temporary):
        root = Path(temporary);directory = root / 'core-source-provider';directory.mkdir()
        verification = root / 'core-owner-verification';verification.mkdir()
        name = 'zarf-package-core-backup-restore-amd64-' + C.B.VERSION + '-upstream.tar.zst'
        for entry in (name, 'change.patch', 'LICENSE.md', 'inputs.lock.json', 'corresponding-source.tar.gz'):
            (directory / entry).write_text('synthetic-' + entry)
        value = {'packageSHA256': C.B.digest(directory / name)}
        (directory / 'provider.json').write_text(json.dumps(value))
        (verification / 'verify-signature.log').write_text('synthetic-signature-verification')
        (verification / 'verified.json').write_text(json.dumps({'schema': 1, 'signatureVerified': True,
            'certificateIdentity': C.B.IDENTITY, 'providerSHA256': C.B.digest(directory / 'provider.json'),
            'packageSHA256': value['packageSHA256'], 'logSHA256': C.B.digest(verification / 'verify-signature.log')}))
        pins = C.inventory(directory)
        captured = {'schema': 1, 'sourceSHA': 'a' * 40, 'runID': '123', 'runAttempt': '1',
                    'architecture': 'amd64', 'files': pins, 'packageSHA256': value['packageSHA256'],
                    'providerSHA256': pins['provider.json'], 'restoreHelperSHA256': C.B.digest(ROOT / C.HELPER)}
        (verification / 'owner-inputs.json').write_text(json.dumps(captured))
        return directory, verification, captured

    def test_receipt_rejects_changed_payload_signature_log_metadata_and_run(self):
        for change in ('payload', 'log', 'signature', 'run', 'attempt', 'source', 'extra', 'symlink'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory, verification, captured = self.fixture(temporary)
                with patch.dict(os.environ, self.environment(), clear=True), patch.object(C, 'paths', return_value=(directory, verification)), patch.object(C.B, 'verify_recipe'), patch.object(C.B, 'validate_provider'):
                    self.assertEqual(C.receipt(), captured)
                    if change == 'payload':(directory / 'change.patch').write_text('changed')
                    elif change == 'log':(verification / 'verify-signature.log').write_text('changed')
                    elif change == 'signature':
                        p=verification / 'verified.json';v=json.loads(p.read_text());v['signatureVerified']=False;p.write_text(json.dumps(v))
                    elif change in ('run', 'attempt', 'source'):
                        os.environ[{'run': 'GITHUB_RUN_ID', 'attempt': 'GITHUB_RUN_ATTEMPT', 'source': 'GITHUB_SHA'}[change]] = '2' if change != 'source' else 'b' * 40
                    elif change == 'extra':(directory / 'unexpected').write_text('x')
                    else:
                        p=directory / 'change.patch';p.unlink();p.symlink_to(directory / 'LICENSE.md')
                    with self.assertRaises(ValueError):C.receipt()

    def test_only_backup_restore_package_source_and_signer_change(self):
        original = yaml.safe_load((ROOT / 'bundle/full/uds-bundle.yaml').read_text())
        packages = copy.deepcopy(original['packages'])
        selected = next(p for p in packages if p['name'] == 'core-backup-restore')
        before = copy.deepcopy(selected)
        with patch.object(C, 'receipt'), patch.object(C, 'paths', return_value=(Path('/tmp/provider'), Path('/tmp/verification'))):
            C.replace_package(selected, Path('/tmp/bundle'))
            for package, old in zip(packages, original['packages']):
                if package['name'] != 'core-backup-restore':self.assertEqual(package, old)
            self.assertEqual(selected['overrides'], before['overrides'])
            self.assertNotIn('repository', selected)
            self.assertEqual(selected['ref'], C.B.VERSION + '-upstream')
            self.assertEqual(selected['keylessVerification']['certificateOIDCIssuer'], C.B.ISSUER)
            import re
            expression=selected['keylessVerification']['certificateIdentityRegexp']
            self.assertIsNotNone(re.fullmatch(expression, C.B.IDENTITY))
            self.assertIsNone(re.fullmatch(expression, C.B.IDENTITY.replace('/main','/other')))
            with self.assertRaises(ValueError):C.replace_package(next(p for p in packages if p['name'] == 'core-base'), Path('/tmp/bundle'))

    def test_driver_refuses_mutated_experimental_helpers_before_deployment(self):
        spec=importlib.util.spec_from_file_location('provider_driver_test', ROOT / 'tests/velero-ownership/drive.py')
        driver=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        for relative in C.HELPERS:
            def command(argv):
                name=argv[-1].removeprefix('HEAD:')
                return 'mutated' if name == relative else (ROOT / name).read_text()
            with self.subTest(relative=relative), patch.object(driver, 'source_provider', return_value=C), patch.object(C, 'enabled', return_value=True):
                with self.assertRaisesRegex(ValueError, 'helper differs'):
                    driver.verify_helpers(command)

    def test_workflow_only_consumes_same_attempt_with_readonly_permissions(self):
        doc=yaml.safe_load((ROOT / '.github/workflows/core-restore-source.yaml').read_text())
        inputs=doc.get('on', doc.get(True))['workflow_dispatch']['inputs']
        self.assertFalse(inputs['run_owner_trial']['default'])
        job=doc['jobs']['owner-trial']
        self.assertEqual(job['needs'], ['build', 'api-smoke'])
        self.assertEqual(job['permissions'], {'contents': 'read', 'packages': 'read', 'actions': 'read'})
        self.assertIn('inputs.run_owner_trial', job['if'])
        self.assertIn("github.ref == 'refs/heads/main'", job['if'])
        downloads=[s for s in job['steps'] if s.get('uses','').startswith('actions/download-artifact@')]
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0]['with']['name'], 'core-restore-source-${{ github.run_id }}-${{ github.run_attempt }}-${{ inputs.architecture }}')
        self.assertNotIn('continue-on-error', job)
        self.assertTrue(all('continue-on-error' not in s for s in job['steps']))
        self.assertIn(' verify --directory ', job['steps'][-1]['run'])
        shell=(ROOT / '.github/scripts/staged-migrations.sh').read_text()
        self.assertLess(shell.index('source_provider.py --cli'),shell.index('deploy-slim-core.sh'))


if __name__ == '__main__':unittest.main()
