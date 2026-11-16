# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline source, caller and provider guards; no network or cluster execution."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import yaml

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('core_source_builder', HERE / 'build.py')
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


class SourceCandidateGuards(unittest.TestCase):
    def test_default_verifies_without_network_or_processes(self):
        with patch.object(B.subprocess, 'run', side_effect=AssertionError('process')), patch.object(
                B.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            B.main([])
            for args in (['--output', '/missing'], ['--phase', 'build'], ['--phase', 'local']):
                with self.assertRaises(ValueError):
                    B.main(args)

    def test_lock_and_patch_mutation_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'profile'
            shutil.copytree(HERE, root)
            with patch.object(B, 'HERE', root):
                lock = root / 'inputs.lock.json'
                value = json.loads(lock.read_text())
                value['core_commit'] = 'a' * 40
                lock.write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError, 'source lock'):
                    B.contract()
                shutil.copyfile(HERE / 'inputs.lock.json', lock)
                (root / 'change.patch').write_text('foreign source patch')
                with self.assertRaisesRegex(ValueError, 'patch/license'):
                    B.contract()

    def environment(self):
        return {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': 'workflow_dispatch',
                'GITHUB_REPOSITORY': 'jonathanagustin/uds-takserver', 'GITHUB_REF': 'refs/heads/main',
                'GITHUB_WORKFLOW_REF': B.IDENTITY.removeprefix('https://github.com/'),
                'SCENARIO': 'velero', 'ARCHITECTURE': 'amd64', 'CORE_RESTORE_POLICY_CANDIDATE': 'true',
                'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '12', 'GITHUB_RUN_ATTEMPT': '1'}

    def test_only_explicit_main_amd_owner_context_can_sign(self):
        env = self.environment()
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(B.context()['sourceSHA'], 'a' * 40)
        for field, value in [('GITHUB_ACTIONS', 'false'), ('GITHUB_EVENT_NAME', 'pull_request'),
                             ('GITHUB_REF', 'refs/heads/other'), ('GITHUB_REPOSITORY', 'foreign/repo'),
                             ('SCENARIO', 'identities'), ('ARCHITECTURE', 'arm64'),
                             ('CORE_RESTORE_POLICY_CANDIDATE', 'false'), ('GITHUB_SHA', 'bad')]:
            with self.subTest(field=field), patch.dict(os.environ, {**env, field: value}, clear=True):
                with self.assertRaises(ValueError):
                    B.context()

    def test_off_context_build_refuses_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {}, clear=True), patch.object(
                B.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            output = Path(temp) / 'output'
            with self.assertRaises(ValueError):
                B.build(output, Path('/missing'), signed=True)
            self.assertFalse(output.exists())

    def tar(self, path, entries):
        with tarfile.open(path, 'w:gz') as t:
            for name, data, kind in entries:
                member = tarfile.TarInfo(name)
                if kind == 'symlink':
                    member.type = tarfile.SYMTYPE
                    member.linkname = '/foreign'
                    t.addfile(member)
                else:
                    member.size = len(data)
                    t.addfile(member, io.BytesIO(data))

    def test_source_archive_checks_content_completeness_and_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = b'exact upstream source'
            lock = {'core_commit': 'a' * 40, 'source_files': {'path/file': hashlib.sha256(data).hexdigest()}}
            archive = root / 'source.tar.gz'
            self.tar(archive, [('uds-core-' + 'a' * 40 + '/path/file', data, 'file')])
            dest = root / 'good'
            dest.mkdir()
            B.extract_source(archive, dest, lock)
            self.assertEqual((dest / 'path/file').read_bytes(), data)
            bad = [[('path/file', b'changed', 'file')], [('path/file', data, 'symlink')],
                   [('../escape', data, 'file')], [('path/file', data, 'file'), ('path/file', data, 'file')], []]
            for i, entries in enumerate(bad):
                p = root / ('bad-' + str(i) + '.tar.gz')
                self.tar(p, entries)
                target = root / ('target-' + str(i))
                target.mkdir()
                with self.assertRaises(ValueError):
                    B.extract_source(p, target, lock)

    def metadata(self):
        return ('# Copyright 2026 fixture\nkind: ZarfPackageConfig\nmetadata:\n'
                '  name: core-backup-restore\n  description: "UDS Core (Backup and Restore)"\n'
                '  version: "1.16.0"\n  annotations:\n    dev.uds.title: fixture\n')

    def test_metadata_binds_source_and_run_without_altering_other_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            p = root / 'packages/backup-restore/zarf.yaml'
            p.parent.mkdir(parents=True)
            original = self.metadata().encode()
            p.write_bytes(original)
            binding = {'sourceSHA': 'a' * 40, 'runID': '12', 'runAttempt': '1'}
            B.apply_metadata(root, binding)
            value = yaml.safe_load(p.read_text())
            self.assertEqual(value['metadata']['version'], B.VERSION)
            self.assertEqual(value['metadata']['annotations']['takserver.qualification/run-id'], '12')
            archive = root / 'corresponding.tar.gz'
            self.tar(archive, [('source/packages/backup-restore/zarf.yaml', p.read_bytes(), 'file')])
            lock = {'source_files': {'packages/backup-restore/zarf.yaml': hashlib.sha256(original).hexdigest()}}
            B.verify_corresponding_source(archive, lock, binding)
            altered = p.read_bytes().replace(b'dev.uds.title: fixture', b'dev.uds.title: foreign')
            self.tar(archive, [('source/packages/backup-restore/zarf.yaml', altered, 'file')])
            with self.assertRaises(ValueError):
                B.verify_corresponding_source(archive, lock, binding)

    def test_provider_metadata_cannot_change_caller_signer_or_source(self):
        lock = B.contract()
        binding = {'sourceSHA': 'a' * 40, 'runID': '12', 'runAttempt': '1'}
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'source.tar.gz'
            source.write_bytes(b'fixture')
            package = {'packageSHA256': 'b' * 64, 'imageManifestSHA256': lock['image_manifest_sha256'],
                       'embeddedRestoreCrdSHA256': lock['crd_after_sha256']}
            # Framing validation is not a cryptographic verifier; consumers also inspect
            # the actual archive and always invoke the pinned CLI signature verifier.
            provider = B.public_provider(binding, package, lock, source)
            B.validate_provider(provider, binding, lock)
            for field, value in [('schema', True), ('sourceSHA', 'c' * 40), ('runID', '13'),
                                 ('certificateIdentity', 'https://foreign.example'), ('signatureVerified', False),
                                 ('coreCommit', 'c' * 40), ('version', '1.16.0'), ('imageManifestSHA256', [])]:
                changed = copy.deepcopy(provider)
                changed[field] = value
                with self.subTest(field=field), self.assertRaises(ValueError):
                    B.validate_provider(changed, binding, lock)

    def test_oidc_cannot_send_job_bearer_to_another_service(self):
        env = {**self.environment(), 'ACTIONS_ID_TOKEN_REQUEST_URL': 'https://foreign.example/token',
               'ACTIONS_ID_TOKEN_REQUEST_TOKEN': 'fixture'}
        with patch.dict(os.environ, env, clear=True), patch.object(B.urllib.request, 'urlopen') as request:
            with self.assertRaisesRegex(ValueError, 'endpoint'):
                B.request_oidc()
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
