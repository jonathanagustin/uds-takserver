# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline refusal and negative-control coverage for the Core API smoke."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import yaml
import unittest
from unittest.mock import Mock, patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('core_source_api', HERE / 'api.py')
A = importlib.util.module_from_spec(spec)
spec.loader.exec_module(A)


def schema(policy):
    fields = {'backupName': {'type': 'string'}}
    if policy:
        fields['resourcePolicy'] = {'type': 'object', 'properties': {
            'apiGroup': {'type': 'string'}, 'kind': {'type': 'string'}, 'name': {'type': 'string'}},
            'required': ['kind', 'name']}
    return {'group': 'velero.io', 'names': {'kind': 'Restore', 'plural': 'restores'}, 'scope': 'Namespaced',
            'versions': [{'name': 'v1', 'served': True, 'storage': True, 'schema': {
                'openAPIV3Schema': {'type': 'object', 'properties': {'spec': {'type': 'object', 'properties': fields}}}}}]}


def crd(policy):
    return {'apiVersion': 'apiextensions.k8s.io/v1', 'kind': 'CustomResourceDefinition',
            'metadata': {'name': 'restores.velero.io', 'uid': '12345678-1234-1234-1234-123456789abc',
                'labels': {'app.kubernetes.io/managed-by': 'Helm', 'component': 'velero'},
                'annotations': {'meta.helm.sh/release-name': 'uds-velero-config',
                                'meta.helm.sh/release-namespace': 'velero'}}, 'spec': schema(policy),
            'status': {'conditions': [{'type': 'Established', 'status': 'True'},
                                      {'type': 'NamesAccepted', 'status': 'True'}]}}


class ApiGuards(unittest.TestCase):
    def test_api_job_has_no_signing_permission_and_only_fixed_uploads(self):
        workflow = yaml.safe_load((A.B.ROOT / '.github/workflows/core-restore-source.yaml').read_text())
        job = workflow['jobs']['api-smoke']
        self.assertEqual(job['needs'], 'build')
        self.assertIn('inputs.run_api_smoke', job['if'])
        self.assertIn("github.ref == 'refs/heads/main'", job['if'])
        self.assertEqual(job['permissions'], {'contents': 'read', 'actions': 'read', 'packages': 'read'})
        self.assertEqual(job['env']['CORE_RESTORE_API_SMOKE'], 'true')
        download = next(s for s in job['steps'] if s.get('uses', '').startswith('actions/download-artifact@'))
        self.assertEqual(download['with']['name'], 'core-restore-source-${{ github.run_id }}-${{ github.run_attempt }}')
        upload = next(s for s in job['steps'] if s.get('uses', '').startswith('actions/upload-artifact@'))
        self.assertEqual({Path(p).name for p in upload['with']['path'].splitlines()},
                         {'public-result.json', 'baseline-review.json', 'candidate-review.json'})
        names = [s.get('name') for s in job['steps']]
        self.assertLess(names.index('Refuse remote or existing clusters before runner preparation'),
                        names.index('Prepare the disposable runner'))

    def test_guard_precedes_each_fixed_context_api_command(self):
        guard = Mock()
        observer = A.Observer('/pinned/uds', Path('/owned'), guard, {})
        observer.command = Mock(return_value=(True, b'{}'))
        observer.k('get', 'crd', 'restores.velero.io', label='read')
        guard.assert_called_once_with()
        argv = observer.command.call_args.args[0]
        self.assertEqual(argv[:7], ['/pinned/uds', 'zarf', 'tools', 'kubectl', '--context', 'k3d-uds', '--request-timeout=30s'])
        guard.side_effect = ValueError('cluster changed')
        observer.command.reset_mock()
        with self.assertRaisesRegex(ValueError, 'cluster changed'):
            observer.k('create', '--dry-run=server', label='probe')
        observer.command.assert_not_called()

    def test_failure_diagnostic_omits_private_messages_and_log_values(self):
        with tempfile.TemporaryDirectory() as temp:
            observer = A.Observer('/pinned/uds', Path(temp), lambda: None, {})
            observer.last_label = 'baseline-deploy'
            observer.last_log = Path(temp) / 'private.log'
            observer.last_log.write_bytes(b'private-workload-name credential-sentinel: Insufficient cpu; x509: private-host')
            result = A.failure_diagnostic(ValueError('credential-sentinel private-workload-name'), observer)
            self.assertEqual(result['category'], 'unclassified')
            self.assertEqual(result['command'], 'baseline-deploy')
            self.assertEqual(result['patterns'], ['resource-pressure', 'network-error'])
            for value in ('credential-sentinel', 'private-workload-name', 'private-host'):
                self.assertNotIn(value, json.dumps(result))
            observer.last_label = 'private-workload-name'
            self.assertEqual(A.failure_diagnostic(ValueError('opaque'), observer)['command'], 'unclassified')

    def test_failure_diagnostic_binds_known_source_callsite(self):
        try:
            A.validate_crd(crd(True), schema(False))
        except ValueError as error:
            result = A.failure_diagnostic(error)
        self.assertEqual(result['category'], 'crd-schema')
        self.assertTrue(result['frames'])
        self.assertTrue(any(f['file'].endswith('/api.py') for f in result['frames']))
        self.assertTrue(all(A.B.digest(A.B.ROOT / f['file']) == f['sourceSHA256'] for f in result['frames']))

    def test_failure_workloads_are_bounded_fixed_facts_only(self):
        observer = A.Observer('/pinned/uds', Path('/owned'), lambda: None, {})
        values = [crd(False), {'kind': 'Deployment', 'metadata': {'name': 'velero', 'namespace': 'velero'},
                   'status': {'replicas': 1, 'readyReplicas': 0, 'privateField': 'credential-sentinel'}},
                  {'items': [{'metadata': {'name': 'private-workload-name'}, 'status': {'phase': 'Pending'}}]},
                  {'items': [{'reason': 'private-reason', 'message': 'private-workload-name Insufficient cpu credential-sentinel'}]}]
        observer.k = Mock(side_effect=[(True, json.dumps(v).encode()) for v in values])
        result = observer.failure_status()
        self.assertEqual(result['status'], 'captured')
        self.assertEqual(result['replicas'], {'replicas': 1, 'readyReplicas': 0})
        self.assertEqual(result['podPhaseCounts']['Pending'], 1)
        self.assertEqual(result['eventPatternCounts']['resource-pressure'], 1)
        for value in ('credential-sentinel', 'private-workload-name', 'private-reason', 'privateField'):
            self.assertNotIn(value, json.dumps(result))
        self.assertEqual(observer.k.call_count, 4)
        self.assertTrue(all(c.args[0] == 'get' for c in observer.k.call_args_list))

    def test_crd_owner_facts_are_fixed_booleans_without_private_values(self):
        good = crd(False)
        self.assertTrue(all(A.crd_owner_facts(good).values()))
        bad = copy.deepcopy(good)
        bad['metadata']['annotations']['meta.helm.sh/release-name'] = 'private-owner-sentinel'
        bad['metadata']['labels']['app.kubernetes.io/managed-by'] = 'private-manager-sentinel'
        facts = A.crd_owner_facts(bad)
        self.assertFalse(facts['releaseMatchesCoreConfig'])
        self.assertFalse(facts['managedByHelm'])
        self.assertTrue(all(type(v) is bool for v in facts.values()))
        self.assertNotIn('private-', json.dumps(facts))
        with self.assertRaisesRegex(ValueError, 'CRD ownership differs'):
            A.validate_crd(bad, schema(False))

    def test_failure_workload_read_refuses_wrong_cluster(self):
        observer = A.Observer('/pinned/uds', Path('/owned'), Mock(side_effect=ValueError('wrong cluster')), {})
        observer.k = Mock()
        self.assertEqual(observer.failure_status(), {'status': 'cluster-guard-refused'})
        observer.k.assert_not_called()

    def test_bootstrap_accepts_only_exact_source_and_absent_or_correct_tracking(self):
        good = crd(False)
        self.assertEqual(A.bootstrap_crd(good, schema(False))['trackingFieldsPresent'], 3)
        missing = copy.deepcopy(good)
        missing['metadata']['labels'].pop('app.kubernetes.io/managed-by')
        missing['metadata']['annotations'] = {}
        self.assertEqual(A.bootstrap_crd(missing, schema(False))['trackingFieldsPresent'], 0)
        for field, value in [('uid', ''), ('ownerReferences', [{}]), ('deletionTimestamp', 'now')]:
            bad = copy.deepcopy(missing); bad['metadata'][field] = value
            with self.assertRaises(ValueError):
                A.bootstrap_crd(bad, schema(False))
        foreign = copy.deepcopy(missing)
        foreign['metadata']['annotations']['meta.helm.sh/release-name'] = 'foreign'
        with self.assertRaisesRegex(ValueError, 'conflicting tracking metadata'):
            A.bootstrap_crd(foreign, schema(False))
        with self.assertRaisesRegex(ValueError, 'source differs'):
            A.bootstrap_crd(crd(True), schema(False))

    def test_normal_baseline_reconciliation_requires_empty_inventory_and_stable_uid(self):
        missing = crd(False)
        missing['metadata']['labels'].pop('app.kubernetes.io/managed-by')
        missing['metadata']['annotations'] = {}
        observer = A.Observer('/pinned/uds', Path('/owned'), lambda: None, {})
        observer.deploy_bundle = Mock()
        observer.k = Mock(side_effect=[(True, json.dumps(v).encode())
                         for v in (missing, {'items': []}, {'items': []}, crd(False))])
        result = observer.reconcile_baseline(Path('/owned/bundle'), 'a' * 64, schema(False))
        observer.deploy_bundle.assert_called_once_with(Path('/owned/bundle'), 'a' * 64, 'baseline-reconcile')
        self.assertTrue(result['performed'])
        self.assertTrue(result['crdUidStable'])
        self.assertEqual(result['initialTrackingFieldsPresent'], 0)
        for objects in ({'items': [{}]},):
            observer.deploy_bundle.reset_mock()
            observer.k = Mock(side_effect=[(True, json.dumps(v).encode()) for v in (missing, objects)])
            with self.assertRaisesRegex(ValueError, 'no backup or restore'):
                observer.reconcile_baseline(Path('/owned/bundle'), 'a' * 64, schema(False))
            observer.deploy_bundle.assert_not_called()
        replaced = crd(False); replaced['metadata']['uid'] = 'abcdefab-1234-1234-1234-123456789abc'
        observer.k = Mock(side_effect=[(True, json.dumps(v).encode())
                         for v in (missing, {'items': []}, {'items': []}, replaced)])
        with self.assertRaisesRegex(ValueError, 'replaced the CRD'):
            observer.reconcile_baseline(Path('/owned/bundle'), 'a' * 64, schema(False))

    def test_reconciliation_does_not_adopt_foreign_or_accept_still_missing_owner(self):
        missing = crd(False)
        missing['metadata']['labels'].pop('app.kubernetes.io/managed-by')
        missing['metadata']['annotations'] = {}
        observer = A.Observer('/pinned/uds', Path('/owned'), lambda: None, {})
        observer.deploy_bundle = Mock()
        foreign = copy.deepcopy(missing)
        foreign['metadata']['labels']['app.kubernetes.io/managed-by'] = 'foreign'
        observer.k = Mock(return_value=(True, json.dumps(foreign).encode()))
        with self.assertRaisesRegex(ValueError, 'conflicting tracking metadata'):
            observer.reconcile_baseline(Path('/owned/bundle'), 'a' * 64, schema(False))
        observer.deploy_bundle.assert_not_called()
        observer.k = Mock(side_effect=[(True, json.dumps(v).encode())
                         for v in (missing, {'items': []}, {'items': [{}]})])
        with self.assertRaisesRegex(ValueError, 'no backup or restore'):
            observer.reconcile_baseline(Path('/owned/bundle'), 'a' * 64, schema(False))
        observer.deploy_bundle.assert_not_called()
        observer.k = Mock(side_effect=[(True, json.dumps(v).encode())
                         for v in (missing, {'items': []}, {'items': []}, missing)])
        with self.assertRaisesRegex(ValueError, 'CRD ownership differs'):
            observer.reconcile_baseline(Path('/owned/bundle'), 'a' * 64, schema(False))
        observer.deploy_bundle.assert_called_once()
        observer.deploy_bundle.reset_mock()
        observer.k = Mock(return_value=(True, json.dumps(crd(False)).encode()))
        result = observer.reconcile_baseline(Path('/owned/bundle'), 'a' * 64, schema(False))
        self.assertFalse(result['performed'])
        observer.deploy_bundle.assert_not_called()

    def test_bundle_deployment_refuses_changed_bytes_and_wrong_cluster(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bundle'; path.write_bytes(b'fixture')
            guard = Mock()
            observer = A.Observer('/pinned/uds', Path(temp), guard, {})
            observer.command = Mock()
            with self.assertRaisesRegex(ValueError, 'bundle changed'):
                observer.deploy_bundle(path, 'a' * 64, 'baseline-reconcile')
            observer.command.assert_not_called()
            guard.side_effect = ValueError('cluster changed')
            with self.assertRaisesRegex(ValueError, 'cluster changed'):
                observer.deploy_bundle(path, A.B.digest(path), 'baseline-reconcile')
            observer.command.assert_not_called()

    def test_default_and_off_context_launch_nothing(self):
        with patch.object(A.B.subprocess, 'run', side_effect=AssertionError('process')), patch.object(
                A.B.urllib.request, 'urlopen', side_effect=AssertionError('network')):
            A.main([])
            with self.assertRaises(ValueError):
                A.main(['--output', '/missing'])
            with patch.dict(A.os.environ, {}, clear=True), self.assertRaises(ValueError):
                A.main(['--execute', '--output', '/missing', '--cli', '/missing', '--provider-directory', '/missing'])

    def test_crd_owner_uid_and_whole_schema_are_required(self):
        good = crd(True)
        A.validate_crd(good, schema(True))
        defaults = copy.deepcopy(good)
        defaults['spec'].update(conversion={'strategy': 'None'}, preserveUnknownFields=False)
        A.validate_crd(defaults, schema(True))
        cases = []
        for field, value in [('uid', ''), ('deletionTimestamp', 'now'), ('ownerReferences', [{}])]:
            changed = copy.deepcopy(good); changed['metadata'][field] = value; cases.append(changed)
        changed = copy.deepcopy(good); changed['metadata']['annotations']['meta.helm.sh/release-name'] = 'foreign'; cases.append(changed)
        changed = copy.deepcopy(good); changed['spec']['scope'] = 'Cluster'; cases.append(changed)
        changed = copy.deepcopy(good); changed['spec']['preserveUnknownFields'] = True; cases.append(changed)
        changed = copy.deepcopy(good); changed['status']['conditions'][0]['status'] = 'False'; cases.append(changed)
        changed = copy.deepcopy(good); changed['spec']['versions'][0]['schema']['openAPIV3Schema']['properties']['spec']['properties']['foreign'] = {}; cases.append(changed)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                A.validate_crd(value, schema(True))

    def test_exact_dry_run_rejection_required(self):
        self.assertTrue(A.rejected(b'Error: strict decoding error: unknown field "spec.resourcePolicy"', 'resourcePolicy'))
        self.assertTrue(A.rejected(b'spec.resourcePolicy.kind: Required value', 'kind', True))
        for value in (b'connection refused', b'Forbidden', b'unknown field "other.resourcePolicy"',
                      b'no matches for kind "Restore"', b'TLS handshake timeout'):
            self.assertFalse(A.rejected(value, 'resourcePolicy'))

    def test_bundle_has_only_fixed_core_package_and_signer(self):
        for variant, identity in [('baseline', A.UPSTREAM_IDENTITY), ('candidate', A.B.IDENTITY)]:
            value = A.bundle(variant, Path('/owned/package'))
            self.assertEqual(len(value['packages']), 1)
            package = value['packages'][0]
            self.assertEqual(package['name'], 'core-backup-restore')
            self.assertEqual(package['keylessVerification']['certificateIdentityRegexp'], '^' + A.re.escape(identity) + '$')
            self.assertNotIn('repository', package)
            self.assertEqual(package['overrides']['velero']['velero']['values'], [
                {'path': 'deployNodeAgent', 'value': False}, {'path': 'schedules.udsbackup.paused', 'value': True}])
        with self.assertRaises(ValueError):
            A.bundle('foreign', Path('/owned/package'))

    def observer(self, root, variant, persisted=False, replaced=False, wrong_rejection=False):
        observer = A.Observer('/pinned/uds', root, lambda: None, {})
        def k(*args, label, failure=False):
            if args[:2] == ('get', 'crd'):
                value = crd(variant == 'candidate')
                if replaced and label.startswith('reread'):
                    value['metadata']['uid'] = 'abcdefab-1234-1234-1234-123456789abc'
                return True, json.dumps(value).encode()
            if args[:2] == ('get', 'restores.velero.io'):
                return True, json.dumps({'items': [{}] if persisted and label.startswith('after') else []}).encode()
            self.assertIn('--dry-run=server', args)
            self.assertIn('--validate=strict', args)
            value = json.loads(Path(args[args.index('-f') + 1]).read_text())
            if label.endswith('-unknown'):
                return False, b'unknown field "spec.schemaProbeUnexpectedField"'
            if label.endswith('-kind'):
                return False, b'spec.resourcePolicy.kind: Required value'
            if label.endswith('-name'):
                return False, b'spec.resourcePolicy.name: Required value'
            if variant == 'baseline':
                return False, b'Forbidden' if wrong_rejection else b'unknown field "spec.resourcePolicy"'
            return True, json.dumps(value).encode()
        observer.k = k
        return observer

    def test_baseline_candidate_and_failure_controls(self):
        binding = {'sourceSHA': 'a' * 40, 'runID': '12', 'runAttempt': '1'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = self.observer(root, 'baseline').observe('baseline', schema(False), binding, 'b' * 64)
            result = self.observer(root, 'candidate').observe('candidate', schema(True), binding, 'b' * 64, baseline)
            self.assertTrue(result['completed'])
            self.assertEqual(result['dryRunCases'], 4)
            self.assertFalse(result['restoreAcceptance'])
            self.assertFalse(result['restoreObjectsPersisted'])
            self.assertNotIn('12345678-1234-1234-1234-123456789abc', json.dumps(result))
            for kwargs in ({'persisted': True}, {'replaced': True}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    self.observer(root, 'candidate', **kwargs).observe('candidate', schema(True), binding, 'b' * 64, baseline)
            with self.assertRaises(ValueError):
                self.observer(root, 'baseline', wrong_rejection=True).observe('baseline', schema(False), binding, 'b' * 64)
            changed = copy.deepcopy(baseline); changed['binding']['runID'] = '13'
            with self.assertRaises(ValueError):
                self.observer(root, 'candidate').observe('candidate', schema(True), binding, 'b' * 64, changed)

    def test_timeout_or_quota_cannot_qualify_as_api_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            observer = A.Observer('/pinned/uds', Path(temp), lambda: None, {})
            for error in ('owned command deadline exceeded', 'owned command log quota exceeded'):
                with patch.object(A.R, 'execute', side_effect=ValueError(error)), self.assertRaisesRegex(ValueError, error):
                    observer.command(['fixed'], 'probe', failure=True)

    def test_fresh_candidate_requires_empty_crds_and_namespace(self):
        with tempfile.TemporaryDirectory() as temp:
            observer = A.Observer('/pinned/uds', Path(temp), Mock(), {})
            for items, namespace, accepts in [([], b'', True),
                    ([{'spec': {'group': 'other.example'}}], b'', True),
                    ([{'spec': {'group': 'velero.io'}}], b'', False),
                    ([], b'{"metadata": {"name": "velero"}}', False)]:
                with self.subTest(items=items, namespace=namespace):
                    observer.k = Mock(side_effect=[(True, json.dumps({'items': items}).encode()), (True, namespace)])
                    if accepts:
                        self.assertEqual(observer.require_fresh_velero(),
                            {'veleroCrdsAbsent': True, 'veleroNamespaceAbsent': True})
                    else:
                        with self.assertRaisesRegex(ValueError, 'fresh candidate requires'):
                            observer.require_fresh_velero()
            observer.k = Mock(side_effect=ValueError('API unavailable'))
            with self.assertRaisesRegex(ValueError, 'API unavailable'):
                observer.require_fresh_velero()

    def test_fresh_candidate_has_strict_owner_schema_and_four_api_controls(self):
        binding = {'sourceSHA': 'a' * 40, 'runID': '12', 'runAttempt': '1'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.observer(root, 'candidate').observe('candidate', schema(True), binding,
                                                            'b' * 64, mode='fresh')
            self.assertEqual(result['mode'], 'fresh')
            self.assertEqual(result['dryRunCases'], 4)
            self.assertTrue(result['coreHelmOwnerVerified'])
            self.assertFalse(result['restoreAcceptance'])
            for kwargs in ({'persisted': True}, {'replaced': True}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    self.observer(root, 'candidate', **kwargs).observe('candidate', schema(True),
                                                                  binding, 'b' * 64, mode='fresh')
            bad = crd(True)
            bad['metadata']['annotations'] = {}
            observer = self.observer(root, 'candidate')
            observer.k = Mock(return_value=(True, json.dumps(bad).encode()))
            with self.assertRaisesRegex(ValueError, 'CRD ownership differs'):
                observer.observe('candidate', schema(True), binding, 'b' * 64, mode='fresh')
            for variant, baseline in [('baseline', None), ('candidate', {'completed': True})]:
                with self.assertRaisesRegex(ValueError, 'observation mode differs'):
                    self.observer(root, variant).observe(variant, schema(True), binding,
                                                        'b' * 64, baseline, mode='fresh')

    def test_fresh_deployment_uses_one_attempt_not_zero_default(self):
        # Embedded Zarf treats zero as its default retry count; one means one attempt.
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bundle'; path.write_bytes(b'fixture')
            observer = A.Observer('/pinned/uds', Path(temp), Mock(), {})
            observer.command = Mock()
            observer.deploy_bundle(path, A.B.digest(path), 'fresh-candidate-deploy')
            argv = observer.command.call_args.args[0]
            self.assertEqual(argv[argv.index('--retries') + 1], '1')
            self.assertEqual(observer.command.call_count, 1)
