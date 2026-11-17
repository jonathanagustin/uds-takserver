# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Private cleanup inventory rejects unrelated bodies and ambiguous records."""
import copy
import unittest
from tests.test_package_identity_driver import P

class ConsumerEvidence(unittest.TestCase):
    def fixture(self):
        return {'schema':1,'consumers':[{'kind':'Pod','namespace':'takserver','name':'remaining',
            'uid':'bound-uid','resourceVersion':'123','deleting':True,'phase':'Succeeded',
            'owners':[{'apiVersion':'batch/v1','kind':'Job','name':'hook','uid':'job-uid'}]}]}

    def test_terminal_and_deleting_pods_remain_consumers(self):
        value=self.fixture()
        self.assertEqual(P.R.validate_consumer_failure(value),value)
        driver=type('Driver',(),{'kube':lambda *_:'present',
            'get':lambda _,kind,namespace:{'items':[{'kind':'Pod','metadata':{
                'name':'remaining','namespace':namespace,'uid':'bound-uid','resourceVersion':'123',
                'deletionTimestamp':'now','annotations':{'private':'never exported'}},
                'status':{'phase':'Succeeded'},'spec':{'containers':[{'env':['private']}]}}]
                if kind=='pods' else []}})()
        result=P.R.consumer_failure(driver)
        self.assertEqual(len(result['consumers']),2)
        self.assertNotIn('private',str(result))
        with self.assertRaisesRegex(ValueError,'still has consumers'):P.R.consumers_gone(driver)

    def test_reject_empty_foreign_duplicate_and_payload_fields(self):
        mutations=[lambda v:v.update(consumers=[]),
            lambda v:v['consumers'][0].update(namespace='istio-system'),
            lambda v:v['consumers'].append(copy.deepcopy(v['consumers'][0])),
            lambda v:v['consumers'][0].update(spec={}),
            lambda v:v['consumers'][0]['owners'][0].update(secret='private'),
            lambda v:v['consumers'][0].update(phase='arbitrary'),
            lambda v:v['consumers'][0].update(uid='x'*254),
            lambda v:v.update(schema=True)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value=self.fixture();mutate(value)
                with self.assertRaises(ValueError):P.R.validate_consumer_failure(value)

class CleanInventoryEvidence(unittest.TestCase):
    def objects(self,count=1,kind='Secret',namespace='cloudtak'):
        return [{'kind':kind,'apiVersion':'v1','metadata':{'name':'remaining-'+str(i).zfill(3),
            'namespace':namespace,'uid':'bound-'+str(i),'resourceVersion':str(i+1),
            'annotations':{'private':'PRIVATE_SENTINEL'}},'data':{'private':'PRIVATE_SENTINEL'}} for i in range(count)]

    def snapshot(self):
        return P.R.clean_inventory_failure('cloudtak','secrets',self.objects())

    def test_digest_only_body_bounded_sorted_and_no_input_mutation(self):
        objects=self.objects(20);before=copy.deepcopy(objects)
        value=P.R.clean_inventory_failure('cloudtak','secrets',objects)
        self.assertEqual(objects,before);self.assertEqual(value['totalCount'],20)
        self.assertTrue(value['truncated']);self.assertEqual(len(value['objects']),16)
        self.assertEqual(value,P.R.clean_inventory_failure('cloudtak','secrets',list(reversed(objects))))
        self.assertNotIn('PRIVATE_SENTINEL',str(value));self.assertNotIn('annotations',str(value))
        self.assertEqual(P.R.validate_clean_inventory_failure(value),value)
        changed=copy.deepcopy(objects);changed[-1]['data']['private']='changed'
        self.assertNotEqual(P.R.clean_inventory_failure('cloudtak','secrets',changed)['inventorySHA256'],value['inventorySHA256'])

    def test_reject_scope_alias_payload_counts_identity_order_and_digest(self):
        mutations=[lambda v:v.update(schema=True),lambda v:v.update(namespace='istio-system'),
            lambda v:v.update(namespace='pepr-system',kind='pods'),lambda v:v.update(kind='Secret'),
            lambda v:v.update(reason='PRIVATE_SENTINEL'),lambda v:v.update(totalCount=True),
            lambda v:v.update(totalCount=0),lambda v:v.update(totalCount=4097),
            lambda v:v.update(truncated=True),lambda v:v.update(inventorySHA256='0'*64),
            lambda v:v['objects'][0].update(data={'private':'PRIVATE_SENTINEL'}),
            lambda v:v['objects'][0].update(uid='bad/private'),lambda v:v['objects'][0].update(name='x'*254),
            lambda v:v['objects'][0].update(deleting=1),lambda v:v['objects'][0].update(objectSHA256='bad'),
            lambda v:v.update(reason='preserved-trust-changed'),lambda v:v.update(extra='private')]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value=self.snapshot();mutate(value)
                with self.assertRaises(ValueError):P.R.validate_clean_inventory_failure(value)
        for count in (0,4097):
            with self.assertRaises(ValueError):P.R.clean_inventory_failure('cloudtak','secrets',self.objects(count))
        objects=self.objects(2);objects[1]['metadata']['name']=objects[0]['metadata']['name']
        with self.assertRaises(ValueError):P.R.clean_inventory_failure('cloudtak','secrets',objects)
        for kind,namespace in [('Pod','cloudtak'),('Secret','istio-system')]:
            with self.assertRaises(ValueError):P.R.clean_inventory_failure('cloudtak','secrets',self.objects(kind=kind,namespace=namespace))

    def trial(self,objects=None,namespace='cloudtak',kind='secrets'):
        trial=P.PackageTrial.__new__(P.PackageTrial);trial.state={'preservedTrustMaps':{}}
        trial.kube=lambda *_:'present'
        trial.get=lambda query,namespace=None:{'items':objects if (namespace,query)==(target_namespace,kind) else []}
        target_namespace=namespace
        return trial

    def test_cleaner_records_last_exact_rejection_and_clears_it_after_clean_probe(self):
        objects=self.objects();trial=self.trial(objects)
        with self.assertRaisesRegex(ValueError,'prevents clean strict install: secrets'):trial.clean_application_state()
        self.assertEqual(trial._clean_application_inventory_failure,self.snapshot())
        objects.clear();self.assertEqual(trial.clean_application_state(),{'clean':True})
        self.assertIsNone(trial._clean_application_inventory_failure)

    def test_existing_default_account_core_pepr_and_valid_trust_exemptions_remain(self):
        default=self.objects(kind='ServiceAccount',namespace='takserver');default[0]['metadata']['name']='default'
        for objects,ns,kind in [(default,'takserver','serviceaccounts'),
                                (self.objects(kind='Deployment',namespace='pepr-system'),'pepr-system','deployments')]:
            trial=self.trial(objects,ns,kind);self.assertEqual(trial.clean_application_state(),{'clean':True})
            self.assertIsNone(trial._clean_application_inventory_failure)
        from unittest.mock import patch
        objects=self.objects(kind='ConfigMap',namespace='takserver');objects[0]['metadata']['name']='kube-root-ca.crt'
        trial=self.trial(objects,'takserver','configmaps')
        with patch.object(P.R,'preserved_trust',return_value=None):self.assertEqual(trial.clean_application_state(),{'clean':True})
        with patch.object(P.R,'preserved_trust',side_effect=ValueError('trust changed')):
            with self.assertRaises(ValueError):trial.clean_application_state()
        self.assertEqual(trial._clean_application_inventory_failure['reason'],'preserved-trust-changed')
        P.R.validate_clean_inventory_failure(trial._clean_application_inventory_failure)

    def test_timeout_captures_last_rejection_and_always_preserves_the_guard(self):
        for capture_fails in (False,True):
            with self.subTest(capture_fails=capture_fails):
                trial=self.trial(self.objects());captured=[];diagnostics=[];journals=[]
                timeout=ValueError('convergence timeout: original private detail')
                def wait(callback,seconds):
                    self.assertEqual(seconds,300)
                    with self.assertRaises(ValueError):callback()
                    raise timeout
                def capture(snapshot,phase,kind):
                    captured.append((snapshot,phase,kind))
                    if capture_fails:raise ValueError('PRIVATE_SENTINEL')
                trial.wait=wait;trial.capture_retained_validation=capture
                trial.capture_retained_diagnostic_callsite=lambda error:diagnostics.append(type(error).__name__)
                trial.journal=lambda stage,**fields:journals.append((stage,fields))
                with self.assertRaises(ValueError) as caught:trial.wait_clean_application_state()
                self.assertIs(caught.exception,timeout);self.assertEqual(captured,[(self.snapshot(),'rollback','cleanup-clean-inventory')])
                self.assertNotIn('PRIVATE_SENTINEL',str(journals));self.assertEqual(bool(diagnostics),capture_fails)

    def test_guard_failure_query_failure_and_unavailable_snapshot_do_not_invent_inventory(self):
        for mode in ('guard','query','invalid-object'):
            with self.subTest(mode=mode):
                objects=self.objects();trial=self.trial(objects);captured=[]
                if mode=='query':trial.get=lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('query refused'))
                if mode=='invalid-object':objects[0]['kind']='Pod'
                def wait(callback,seconds):
                    if mode=='guard':raise ValueError('guard refused')
                    with self.assertRaises(ValueError):callback()
                    raise ValueError('convergence timeout: private detail')
                trial.wait=wait;trial.capture_retained_validation=lambda *args,**kwargs:captured.append(True)
                with self.assertRaises(ValueError):trial.wait_clean_application_state()
                self.assertEqual(captured,[])

    def test_private_first_failure_is_bound_mode600_and_not_replaced(self):
        import json,stat,tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);root.chmod(0o700);trial=P.PackageTrial.__new__(P.PackageTrial)
            trial.evidence=root/'journal.json';trial.state={'guard':{'sourceSHA':'a'*40,'runID':'123','runAttempt':'1'}}
            trial.journal=lambda *args,**kwargs:None
            trial.capture_retained_validation(self.snapshot(),'rollback',kind='cleanup-clean-inventory')
            path=root/P.VALIDATION_FAILURE;first=path.read_bytes();payload=json.loads(first)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertEqual(payload['kind'],'cleanup-clean-inventory');self.assertNotIn('PRIVATE_SENTINEL',str(payload))
            self.assertEqual(payload['binding'],trial.state['guard'])
            trial.capture_retained_validation(self.snapshot(),'rollback',kind='cleanup-clean-inventory')
            self.assertEqual(first,path.read_bytes())

    def test_registry_exemption_requires_successful_authority_guard_and_keeps_other_secrets(self):
        from unittest.mock import patch
        objects=self.objects(1);objects[0]['metadata']['name']='private-registry'
        trial=self.trial(objects);trial.state['preservedRegistrySecrets']=[{'bound':'fixture'}]
        with patch.object(P.R,'preserved_registry',return_value=True) as guard:
            self.assertEqual(trial.clean_application_state(),{'clean':True})
            guard.assert_called_once_with(trial,objects[0],trial.state['preservedRegistrySecrets'])
        with patch.object(P.R,'preserved_registry',side_effect=ValueError('authority changed')):
            with self.assertRaisesRegex(ValueError,'authority changed'):trial.clean_application_state()
        objects.extend(self.objects(1))
        with patch.object(P.R,'preserved_registry',return_value=True):
            with self.assertRaisesRegex(ValueError,'prevents clean strict install: secrets'):trial.clean_application_state()
        self.assertEqual(trial._clean_application_inventory_failure['objects'][0]['name'],'remaining-000')
