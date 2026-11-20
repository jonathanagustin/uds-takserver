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
import subprocess
import sys
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

    def test_intentional_task_pin_has_existing_separate_guard(self):
        self.assertNotIn('tasks.yaml',C.HELPERS)
        self.assertIn('.github/scripts/pin-image-tags.sh',C.HELPERS)
        self.assertIn('.github/scripts/staged-migrations.py',C.HELPERS)
        spec=importlib.util.spec_from_file_location('source_task_guard_driver',ROOT/'tests/velero-ownership/drive.py')
        driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
        self.assertIn('tasks.yaml',driver.REVIEWED_HELPERS)

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
        (verification / 'package-manifest.log').write_text('sha256:'+'b'*64+'\n')
        (verification / 'verified.json').write_text(json.dumps({'schema': 1, 'signatureVerified': True,
            'certificateIdentity': C.B.IDENTITY, 'providerSHA256': C.B.digest(directory / 'provider.json'),
            'packageSHA256': value['packageSHA256'], 'logSHA256': C.B.digest(verification / 'verify-signature.log')}))
        pins = C.inventory(directory)
        captured = {'schema': 2, 'sourceSHA': 'a' * 40, 'runID': '123', 'runAttempt': '1',
                    'architecture': 'amd64', 'files': pins, 'packageSHA256': value['packageSHA256'],
                    'providerSHA256': pins['provider.json'], 'restoreHelperSHA256': C.B.digest(ROOT / C.HELPER),
                    'helperSHA256': {name:C.B.digest(ROOT/name) for name in C.HELPERS},
                    'packageManifestDigest': 'sha256:'+'b'*64}
        (verification / 'owner-inputs.json').write_text(json.dumps(captured))
        return directory, verification, captured

    def test_receipt_rejects_changed_payload_signature_log_metadata_and_run(self):
        for change in ('payload', 'log', 'manifest-log', 'signature', 'run', 'attempt', 'source', 'extra', 'symlink'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temporary:
                directory, verification, captured = self.fixture(temporary)
                with patch.dict(os.environ, self.environment(), clear=True), patch.object(C, 'paths', return_value=(directory, verification)), patch.object(C.B, 'verify_recipe'), patch.object(C.B, 'validate_provider'), patch.object(C, 'helper_inventory', return_value=captured['helperSHA256']):
                    self.assertEqual(C.receipt(), captured)
                    if change == 'payload':(directory / 'change.patch').write_text('changed')
                    elif change == 'log':(verification / 'verify-signature.log').write_text('changed')
                    elif change == 'manifest-log':(verification / 'package-manifest.log').write_text('sha256:'+'c'*64+'\n')
                    elif change == 'signature':
                        p=verification / 'verified.json';v=json.loads(p.read_text());v['signatureVerified']=False;p.write_text(json.dumps(v))
                    elif change in ('run', 'attempt', 'source'):
                        os.environ[{'run': 'GITHUB_RUN_ID', 'attempt': 'GITHUB_RUN_ATTEMPT', 'source': 'GITHUB_SHA'}[change]] = '2' if change != 'source' else 'b' * 40
                    elif change == 'extra':(directory / 'unexpected').write_text('x')
                    else:
                        p=directory / 'change.patch';p.unlink();p.symlink_to(directory / 'LICENSE.md')
                    with self.assertRaises(ValueError):C.receipt()

    def test_every_runtime_helper_is_compared_to_exact_workflow_source_and_drift_refuses(self):
        binding={'sourceSHA':'a'*40,'runID':'123','runAttempt':'1'}
        originals={name:(ROOT/name).read_bytes() for name in C.HELPERS}
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            for name,data in originals.items():
                path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            calls=[]
            def show(argv,**kwargs):
                calls.append((argv,kwargs))
                self.assertIn('--no-replace-objects',argv)
                self.assertEqual(kwargs['env']['GIT_NO_LAZY_FETCH'],'1')
                self.assertTrue(argv[-1].startswith(binding['sourceSHA']+':'))
                return subprocess.CompletedProcess(argv,0,originals[argv[-1].split(':',1)[1]],b'')
            with patch.object(C,'ROOT',folder),patch.object(C.subprocess,'run',side_effect=show):
                self.assertEqual(set(C.helper_inventory(binding)),set(C.HELPERS))
                self.assertEqual(len(calls),len(C.HELPERS))
                for name in C.HELPERS:
                    path=folder/name;path.write_bytes(originals[name]+b'\n# changed\n')
                    with self.subTest(helper=name),self.assertRaisesRegex(ValueError,'runtime helper differs'):
                        C.helper_inventory(binding)
                    path.write_bytes(originals[name])

    def test_policy_and_controller_drift_after_construction_prevents_restore_dispatch(self):
        spec=importlib.util.spec_from_file_location('source_restore_drift_driver',ROOT/'tests/velero-ownership/drive.py')
        driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
        for name in ('tests/velero-controller-restore-policy.yaml','.github/scripts/velero-controller-state.py'):
            with self.subTest(helper=name),tempfile.TemporaryDirectory() as temporary:
                directory,verification,captured=self.fixture(temporary)
                changed={**captured['helperSHA256'],name:'c'*64}
                obj=object.__new__(driver.Driver);obj.core_provider=captured;obj.restore_helper=C.HELPER
                obj.idle=lambda:None;obj.guarded=lambda:None
                with patch.dict(os.environ,self.environment(),clear=True),patch.object(driver,'source_provider',return_value=C), \
                     patch.object(C,'paths',return_value=(directory,verification)),patch.object(C.B,'verify_recipe'), \
                     patch.object(C.B,'validate_provider'),patch.object(C,'helper_inventory',return_value=changed), \
                     patch.object(obj,'execute',create=True) as execute:
                    with self.assertRaisesRegex(ValueError,'changed after verification'):obj.accept('fresh-core','fresh','fresh')
                    execute.assert_not_called()

    def test_digest_uses_supported_quiet_flags_and_is_not_a_signature_command(self):
        # This executable is a structural CLI fixture, not a signature proof.
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);cli=root/'cli';verification=root/'verify';verification.mkdir()
            cli.write_text('#!'+sys.executable+'\nimport json,sys\n'
                           'assert "--log-level=error" in sys.argv and "--no-color" in sys.argv\n'
                           'assert not any(a.startswith("--verify") for a in sys.argv)\n'
                           'print("sha256:"+"b"*64)\n')
            cli.chmod(0o755)
            lock={'cli_sha256':C.B.digest(cli)}
            with patch.object(C.B,'contract',return_value=lock):
                self.assertEqual(C.manifest_digest(root/'package',cli,verification),'sha256:'+'b'*64)
                cli.write_text(cli.read_text()+'sys.exit(7)\n')
                lock['cli_sha256']=C.B.digest(cli)
                other=root/'failed';other.mkdir()
                with self.assertRaisesRegex(ValueError,'owned command failed'):C.manifest_digest(root/'package',cli,other)

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
        optional_names = {'Encrypt optional first failure diagnostic',
                          'Upload only encrypted first failure diagnostic'}
        optional = [s for s in job['steps'] if s.get('name') in optional_names]
        self.assertEqual(len(optional), 2)
        for step in job['steps']:
            if step.get('name') in optional_names:
                self.assertIs(step['continue-on-error'], True)
                self.assertEqual(step['timeout-minutes'], 2)
            else:
                self.assertNotIn('continue-on-error', step)
        encrypt, upload = optional
        self.assertEqual(encrypt['id'], 'encrypt-first-failure')
        self.assertEqual(encrypt['if'],
                         "always() && steps.staged-trial.outcome == 'failure' && inputs.diagnostic_recipient != ''")
        self.assertEqual(inputs['diagnostic_recipient']['default'], '')
        self.assertEqual(upload['if'],
                         "always() && steps.encrypt-first-failure.outcome == 'success' && steps.encrypt-first-failure.outputs.ready == 'true'")
        self.assertEqual(upload['with']['path'],
                         '${{ runner.temp }}/staged-encrypted-diagnostic/first-failure.age')
        self.assertEqual(upload['with']['retention-days'], 7)
        self.assertEqual(upload['with']['if-no-files-found'], 'error')
        self.assertIn(' verify --directory ', job['steps'][-1]['run'])
        shell=(ROOT / '.github/scripts/staged-migrations.sh').read_text()
        self.assertLess(shell.index('source_provider.py --cli'),shell.index('deploy-slim-core.sh'))


if __name__ == '__main__':unittest.main()
