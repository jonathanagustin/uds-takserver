# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic trust/transport controls; no registry or native acceptance claim."""
import copy
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache as C
import transport as T
import test_cache as fixtures

ENV = {'GITHUB_REPOSITORY': T.REPOSITORY, 'GITHUB_REF': 'refs/heads/main',
       'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_SHA': 'a' * 40,
       'GITHUB_WORKFLOW_SHA': 'a' * 40, 'GITHUB_WORKFLOW_REF': T.WORKFLOW + '@refs/heads/main',
       'GITHUB_RUN_ID': '1234', 'GITHUB_RUN_ATTEMPT': '2', 'RUNNER_ENVIRONMENT': 'github-hosted'}


class RegistryContracts(unittest.TestCase):
    def setUp(self):
        # Reuse the existing strictly parsed synthetic capsule, not mocked validation.
        self.fixture = fixtures.LocalContracts()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.fixture.identity['execution'].update(engine_host_arch='amd64', mode='native')
        self.capsule, digest, self.key = self.fixture.packed()
        self.subject = C.layout(self.capsule, digest, self.key, self.root / 'layout')
        self.manifest, _ = C.verify_layout(self.root / 'layout', self.subject, self.key)
        self.inputs = self.root / 'candidate'
        native, consumers = self.inputs / 'geos-producer', self.inputs / 'geos-consumers'
        native.mkdir(parents=True); consumers.mkdir()
        shutil.copytree(self.root / 'layout', native / 'oci')
        shutil.copyfile(self.capsule, native / 'geos-capi.tar.gz')
        self.proof = {'schema': 'uds-geos-capi-fresh-local-v1', 'local_oci_subject': self.subject,
            'input_key': self.key, 'capsule_sha256': digest, 'identity': self.fixture.identity,
            'qualification': self.fixture.proof, **{k: False for k in T.FLAGS}}
        self.restored = {'schema': 'uds-geos-capi-local-relocation-v1', 'subject': self.subject,
            'input_key': self.key, 'identity_sha256': C.sha(C.canonical(self.fixture.identity)),
            'execution': self.fixture.identity['execution'], 'constraints': self.fixture.identity['constraints'],
            'host_geos_available': False, 'original_producer_available': False, **{k: False for k in T.FLAGS},
            'results': [{'prefix': p, 'geometry': True, 'whole_archive_pic': True,
                'geometry_sha256': 'b' * 64, 'whole_archive_pic_sha256': 'c' * 64,
                'header_archive_trace_sha256': 'd' * 64,
                'header_sha256': {n.removeprefix('include/'): self.manifest['files'][n]['sha256']
                                  for n in ('include/geos_c.h', 'include/geos/export.h')},
                'runtime_providers': self.fixture.proof['runtime_providers'],
                'pic_runtime_providers': self.fixture.proof['runtime_providers']}
                for p in ('first', 'unrelated/deeper/second')]}
        self.proof['producer_control'] = {k: v for k, v in self.restored['results'][0].items() if k != 'prefix'}
        self.proof['configured_int64_probe_sha256'] = 'e' * 64
        (native / 'result.json').write_bytes(C.canonical(self.proof))
        (consumers / 'restored.json').write_bytes(C.canonical(self.restored))
        self.trial = {'schema': 'uds-geos-hosted-local-trial-v1', 'run_id': '1234', 'run_attempt': '2',
            'checkout': 'a' * 40, 'tree': 'b' * 40, 'evidence': {
                'geos-producer/result.json': C.file_sha(native / 'result.json'),
                'geos-consumers/restored.json': C.file_sha(consumers / 'restored.json')}}
        (consumers / 'hosted-trial.json').write_bytes(C.canonical(self.trial))
        self.env = patch.dict(os.environ, ENV, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.recipes = patch.object(T.producer, 'recipes', return_value=self.fixture.identity['recipes'])
        self.recipes.start(); self.addCleanup(self.recipes.stop)
        git = patch.object(T.subprocess, 'check_output', side_effect=lambda args, **kw:
                           ('b' if args[-1] == 'HEAD^{tree}' else 'a') * 40 + '\n')
        git.start(); self.addCleanup(git.stop)
        self.calls = []

    def stage(self):
        with patch.object(T, 'command', side_effect=self.remote):
            self.q = T.stage(self.inputs, self.root / 'publication')
        return self.q

    def remote(self, args, output, name, env=None, limit=C.MAX_JSON, missing=False):
        self.calls.append((name, args, env))
        if name in ('existing-key', 'promotion-preflight'):
            return None
        if name.endswith('digest'):
            return (self.subject + '\n').encode()
        if name.startswith('attestation-'):
            kind = args[args.index('--predicate-type') + 1]
            statement = {'predicateType': kind, 'subject': [{'name': T.REGISTRY,
                'digest': {'sha256': self.subject[7:]}}],
                'predicate': self.q if kind == T.PREDICATE else {'synthetic': True}}
            return C.canonical([{'verificationResult': {'statement': statement}}])
        if name == 'manifest':
            return (self.root / 'layout/blobs/sha256' / self.subject[7:]).read_bytes() + b'\n'
        if name == 'blob-0': return b'{}'
        if name == 'blob-1': return self.capsule.read_bytes()
        return b''

    def test_roundtrip_authenticates_before_blob_processing_and_promotes_exact_subject(self):
        q = self.stage()
        readback = self.root / 'readback'
        with patch.object(T, 'command', side_effect=self.remote):
            T.readback(q, readback, 'a' * 40)
            (readback / 'consumer').mkdir()
            (readback / 'consumer/restored.json').write_bytes(C.canonical(self.restored))
            T.promote(q, readback, self.root / 'promotion')
        names = [c[0] for c in self.calls]
        self.assertLess(names.index('attestation-1'), names.index('manifest'))
        self.assertLess(names.index('manifest'), names.index('blob-1'))
        for name, args, env in self.calls:
            if name.startswith('attestation-'):
                self.assertIn('--deny-self-hosted-runners', args)
                for flag in ('--source-digest', '--signer-digest'):
                    self.assertEqual(args[args.index(flag) + 1], 'a' * 40)
            if name in ('manifest', 'blob-0', 'blob-1'):
                self.assertNotEqual(env['DOCKER_CONFIG'], os.environ.get('DOCKER_CONFIG'))
        accepted = T.load(self.root / 'promotion/accepted.json')
        self.assertIs(accepted['publisher_authenticated'], True)
        self.assertIs(accepted['registry_roundtrip'], True)
        self.assertIs(accepted['sdk_accepted'], False)
        self.assertIs(accepted['security_accepted'], False)
        self.assertTrue(all(self.proof[k] is False for k in T.FLAGS))

    def test_untrusted_contexts_cannot_publish(self):
        for name, value in [('GITHUB_REPOSITORY', 'other/repository'), ('GITHUB_REF', 'refs/pull/1/merge'),
                            ('GITHUB_EVENT_NAME', 'pull_request'), ('RUNNER_ENVIRONMENT', 'self-hosted'),
                            ('GITHUB_WORKFLOW_SHA', 'b' * 40), ('GITHUB_WORKFLOW_REF', 'other/workflow'),
                            ('GITHUB_RUN_ID', '0'), ('GITHUB_RUN_ATTEMPT', '1/../../other')]:
            with self.subTest(name=name), patch.dict(os.environ, {name: value}), \
                    self.assertRaises(ValueError), patch.object(T, 'command') as command:
                T.stage(self.inputs, self.root / 'refused')
            command.assert_not_called()

    def test_wrong_run_attempt_tree_or_record_hash_refuses_before_registry(self):
        for name, value in [('run_id', '9876'), ('run_attempt', '1'), ('tree', 'c' * 40),
                            ('checkout', 'c' * 40), ('evidence', {})]:
            changed = dict(self.trial, **{name: value})
            (self.inputs / 'geos-consumers/hosted-trial.json').write_bytes(C.canonical(changed))
            with self.subTest(name=name), patch.object(T, 'command') as command, self.assertRaises(ValueError):
                T.stage(self.inputs, self.root / 'refused')
            command.assert_not_called()

    def test_missing_consumer_header_provider_or_control_is_not_publishable(self):
        for field, value in [('geometry', False), ('whole_archive_pic', False), ('header_sha256', {}),
                             ('runtime_providers', {}), ('prefix', 'first')]:
            changed = copy.deepcopy(self.restored)
            changed['results'][1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                T.consumers(changed, self.manifest, self.subject, self.key)

    def test_signature_failure_stops_before_manifest_or_capsule(self):
        q = self.stage()
        with patch.object(T, 'attestations', side_effect=ValueError('bad signature')), \
                patch.object(T, 'command') as command, self.assertRaises(ValueError):
            T.readback(q, self.root / 'readback', 'a' * 40)
        command.assert_not_called()
        self.assertFalse((self.root / 'readback/oci').exists())

    def test_incomplete_or_changed_candidate_cannot_reach_publication(self):
        path = self.inputs / 'geos-producer/result.json'
        for field, value in [('producer_control', {}), ('configured_int64_probe_sha256', None),
                             ('publisher_authenticated', True), ('qualification', {})]:
            path.write_bytes(C.canonical(dict(self.proof, **{field: value})))
            with self.subTest(field=field), patch.object(T, 'command') as command, self.assertRaises(ValueError):
                T.stage(self.inputs, self.root / 'refused')
            command.assert_not_called()

    def test_promotion_rejects_another_attempt_without_tagging(self):
        q = self.stage()
        with patch.dict(os.environ, {'GITHUB_RUN_ATTEMPT': '3'}), patch.object(T, 'command') as command, \
                self.assertRaises(ValueError):
            T.promote(q, self.root / 'missing-readback', self.root / 'refused')
        command.assert_not_called()

    def test_authenticated_predicate_or_subject_mismatch_is_rejected(self):
        q = self.stage()
        for mutation in ('predicate', 'subject', 'kind'):
            statement = {'predicateType': T.PREDICATE, 'subject': [{'name': T.REGISTRY,
                'digest': {'sha256': self.subject[7:]}}], 'predicate': copy.deepcopy(q)}
            if mutation == 'predicate': statement['predicate']['run_attempt'] = '1'
            if mutation == 'subject': statement['subject'][0]['name'] = 'other/subject'
            if mutation == 'kind': statement['predicateType'] = 'untrusted/type'
            raw = C.canonical([{'verificationResult': {'statement': statement}}])
            output = self.root / mutation; output.mkdir()
            with self.subTest(mutation=mutation), patch.object(T, 'command', return_value=raw), \
                    self.assertRaises(ValueError):
                T.attestations(q, output, dict(os.environ), 'a' * 40)

    def test_remote_byte_corruption_cannot_create_layout_or_acceptance(self):
        q = self.stage()
        def corrupt(args, output, name, *rest, **kw):
            raw = self.remote(args, output, name, *rest, **kw)
            return raw + b'corruption' if name == 'blob-1' else raw
        with patch.object(T, 'command', side_effect=corrupt), self.assertRaises(ValueError):
            T.readback(q, self.root / 'readback', 'a' * 40)
        self.assertFalse((self.root / 'readback/oci').exists())
        self.assertFalse((self.root / 'readback/readback.json').exists())

    def test_key_collision_is_not_overwritten(self):
        output = self.root / 'collision'; output.mkdir()
        with patch.object(T, 'command', side_effect=[b'{}', b'sha256:' + b'f' * 64]) as command, \
                self.assertRaises(ValueError):
            T.existing(self.key, self.subject, output, 'existing')
        self.assertEqual(command.call_count, 2)

    def test_malformed_cross_component_publication_and_wrong_source_are_refused(self):
        q = self.stage()
        for name, value in [('registry', 'ghcr.io/other/cache'), ('workflow', 'other/workflow'),
                            ('schema', 'uds-draco-qualification-v2'), ('run_attempt', 2)]:
            changed = dict(q, **{name: value})
            with self.subTest(name=name), self.assertRaises(ValueError): T.publication(changed)
        output = self.root / 'untrusted-source'; output.mkdir()
        with patch.object(T, 'command') as command, self.assertRaises(ValueError):
            T.attestations(q, output, dict(os.environ), 'b' * 40)
        command.assert_not_called()


class BoundedCommands(unittest.TestCase):
    def test_only_definitive_registry_missing_codes_are_cache_misses(self):
        for error in ('MANIFEST_UNKNOWN', 'NAME_UNKNOWN', '404 Not Found', 'connection failed',
                      '401 MANIFEST_UNKNOWN', 'DENIED NAME_UNKNOWN'):
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temporary:
                args = [sys.executable, '-c', 'import sys;sys.stderr.write(sys.argv[1]);sys.exit(1)', error]
                output = Path(temporary)
                if error in ('MANIFEST_UNKNOWN', 'NAME_UNKNOWN'):
                    self.assertIsNone(T.command(args, output, 'missing', missing=True))
                else:
                    with self.assertRaises(ValueError): T.command(args, output, 'missing', missing=True)

    def test_oversized_stdout_or_stderr_fails_without_a_success_value(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream), tempfile.TemporaryDirectory() as temporary, \
                    patch.object(C, 'MAX_JSON', 32):
                args = [sys.executable, '-c', 'import sys;sys.' + stream + '.buffer.write(b"x"*4096)']
                with self.assertRaises(ValueError): T.command(args, Path(temporary), 'output', limit=32)

    def test_expired_deadline_kills_the_actual_owned_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = [sys.executable, '-c', 'import time;time.sleep(30)']
            real_popen = T.subprocess.Popen
            with patch.object(T.subprocess, 'Popen', wraps=real_popen) as launch, \
                    patch.object(T.time, 'monotonic', side_effect=[0, 121]), self.assertRaises(ValueError):
                T.command(args, Path(temporary), 'timeout')
            launch.assert_called_once()
            # A second invocation can own the same path only after the first
            # output is explicitly removed; abandoned results cannot be reused.
            with self.assertRaises(FileExistsError): T.command(args, Path(temporary), 'timeout')


class WorkflowAuthority(unittest.TestCase):
    def test_only_explicit_protected_manual_publisher_has_write_or_oidc_grants(self):
        import yaml
        path = Path(__file__).resolve().parents[2] / '.github/workflows/geos-local-qualification.yaml'
        workflow = yaml.safe_load(path.read_text())
        triggers = workflow.get('on', workflow.get(True))
        self.assertIs(triggers['workflow_dispatch']['inputs']['publish']['default'], False)
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        qualify, publish = workflow['jobs']['qualify'], workflow['jobs']['publish']
        self.assertNotIn('permissions', qualify)
        self.assertEqual(publish['permissions'], {
            'contents': 'read', 'packages': 'write', 'id-token': 'write', 'attestations': 'write'})
        self.assertEqual(publish['needs'], 'qualify')
        self.assertEqual(publish['if'],
            "github.event_name == 'workflow_dispatch' && github.ref == 'refs/heads/main' && inputs.publish")
        self.assertEqual(workflow['concurrency']['cancel-in-progress'], "${{ github.event_name == 'pull_request' }}")
        download = next(s for s in publish['steps'] if s.get('uses', '').startswith('actions/download-artifact@'))
        self.assertEqual(download['with']['artifact-ids'], '${{ needs.qualify.outputs.artifact_id }}')
        self.assertNotIn('run-id', download['with'])
        self.assertNotIn('github-token', download['with'])
        upload = publish['steps'][-1]['with']['path']
        self.assertNotIn('.stdout', upload)
        self.assertNotIn('.stderr', upload)


if __name__ == '__main__':
    unittest.main()
