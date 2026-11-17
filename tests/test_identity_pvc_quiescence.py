# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Expected PVC usage transition creates one final, strictly conditional snapshot."""
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('pvc_quiescence',Path(__file__).parent/'workload-identities/retained_state.py')
R=importlib.util.module_from_spec(spec);spec.loader.exec_module(R)

def obj(state='False',version='10'):
    reason,message=(('PodUsingPVC','A pod is currently referencing this PVC') if state=='False' else
                    ('NoPodsUsingPVC','No pods are currently referencing this PVC'))
    return {'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':'data','namespace':'takserver',
        'uid':'owned-pvc','resourceVersion':version,'generation':1,'creationTimestamp':'2026-11-15T00:00:00Z',
        'labels':{'owned':'fixture'},'finalizers':['kubernetes.io/pvc-protection'],
        'managedFields':[{'manager':'k3s','fieldsType':'FieldsV1','fieldsV1':{'f:status':{'f:conditions':{}}}}]},
        'spec':{'volumeName':'owned-pv','storageClassName':'local-path-local'},
        'status':{'phase':'Bound','capacity':{'storage':'1Gi'},'conditions':[{'type':'Unused','status':state,
            'reason':reason,'message':message,'lastTransitionTime':'2026-11-15T00:00:01Z','lastProbeTime':None}]}}

def record(value):
    return {**R.resource_record(value),'origin':{'fixture':True},'parent':{'volumeName':'owned-pv','volumeUID':'owned-volume'},
            'pvcState':R.pvc_state(value),'pvcUsageBarrier':R.pvc_usage_barrier(value)}

class Driver:
    def __init__(self,values):self.values=list(values);self.calls=[];self._retained_cleanup_drift=None
    def guard(self):self.calls.append('guard')
    def core_unchanged(self):self.calls.append('core')
    def kube(self,*args):
        if args[1]=='namespace':return '{}'
        self.calls.append('get-pvc')
        value=self.values.pop(0) if len(self.values)>1 else self.values[0]
        return json.dumps(value)
    def get(self,*args,**_kwargs):
        return {'metadata':{'uid':'owned-volume'},'spec':{'claimRef':{'uid':'owned-pvc','namespace':'takserver','name':'data'}}}

class QuiescenceTests(unittest.TestCase):
    def test_snapshot_cannot_rebind_a_verified_claim_or_parent(self):
        original=obj();parent={'volumeName':'owned-pv','volumeUID':'owned-volume'}
        proof={'source':{},'records':[{**R.resource_record(original),'parent':parent}]}
        for replacement in ('uid','parent'):
            current=copy.deepcopy(original)
            if replacement=='uid':current['metadata']['uid']='foreign'
            observed_parent=parent if replacement=='uid' else {'volumeName':'foreign','volumeUID':'foreign'}
            driver=SimpleNamespace(kube=lambda *_args:json.dumps(current))
            with self.subTest(replacement=replacement),patch.object(R,'verify'),patch.object(R,'claim_parent',return_value=observed_parent):
                with self.assertRaisesRegex(ValueError,'before cleanup snapshot'):R.cleanup_snapshot(driver,proof)

    def test_exact_transition_changes_only_final_record_version(self):
        before=obj();rec=record(before);saved=copy.deepcopy(rec);after=obj('True','11')
        result=R.quiescent_pvc_record(rec,after)
        self.assertEqual(rec,saved);self.assertEqual(before,obj())
        self.assertEqual(result['resourceVersion'],'11');self.assertNotIn('pvcUsageBarrier',result)
        for key in R.CLEANUP_IDENTITY_FIELDS:
            if key!='resourceVersion':self.assertEqual(result[key],rec[key])
        self.assertEqual(result['origin'],rec['origin']);self.assertEqual(result['parent'],rec['parent'])
        self.assertEqual(result['pvcState']['conditionFacts']['knownTypes'],{'Unused':['True']})

    def test_no_transition_is_pending_not_a_new_snapshot(self):
        self.assertIsNone(R.quiescent_pvc_record(record(obj()),obj()))
        self.assertIsNone(R.quiescent_pvc_record(record(obj()),obj('False','11')))

    def test_unsupported_initial_conditions_never_get_authorization(self):
        values=[]
        for key,value in [('type','PRIVATE_TYPE'),('reason','PRIVATE_REASON'),('message','PRIVATE_MESSAGE'),
                          ('lastTransitionTime','PRIVATE_TIME'),('lastTransitionTime','2026-99-99T00:00:00Z')]:
            current=obj();current['status']['conditions'][0][key]=value;values.append(current)
        for field in ('status','reason','message','lastTransitionTime'):
            current=obj();current['status']['conditions'][0].pop(field);values.append(current)
        current=obj();current['status']['conditions'].append({});values.append(current)
        current=obj();current['status']['conditions'][0]['private']='PRIVATE_VALUE';values.append(current)
        values.append(obj('True'))
        for current in values:
            with self.subTest(current=current):self.assertIsNone(R.pvc_usage_barrier(current))

    def test_any_other_object_change_is_refused(self):
        mutations=[lambda x:x['metadata'].update(uid='foreign'),lambda x:x['metadata'].update(name='foreign'),
            lambda x:x['metadata'].update(namespace='cloudtak'),lambda x:x['metadata']['labels'].update(foreign='value'),
            lambda x:x['metadata'].update(annotations={'foreign':'value'}),lambda x:x['metadata'].update(ownerReferences=[]),
            lambda x:x['metadata'].update(generation=2),lambda x:x['metadata'].update(creationTimestamp='2026-11-14T00:00:00Z'),
            lambda x:x['metadata'].update(deletionTimestamp='2026-11-15T00:00:00Z'),
            lambda x:x['metadata']['finalizers'].append('foreign'),lambda x:x['spec'].update(volumeName='foreign'),
            lambda x:x['spec'].update(foreign='value'),lambda x:x['status']['capacity'].update(storage='2Gi'),
            lambda x:x['status'].update(phase='Pending'),lambda x:x['status'].update(foreign='value'),
            lambda x:x.update(apiVersion='foreign'),lambda x:x.update(foreign='value'),
            lambda x:x['status']['conditions'][0].update(type='PRIVATE_TYPE'),
            lambda x:x['status']['conditions'][0].update(reason='PRIVATE_REASON'),
            lambda x:x['status']['conditions'][0].update(message='PRIVATE_MESSAGE'),
            lambda x:x['status']['conditions'][0].update(lastTransitionTime='PRIVATE_TIME'),
            lambda x:x['status']['conditions'][0].update(foreign='PRIVATE_VALUE')]
        for index,mutate in enumerate(mutations):
            current=obj('True','11');mutate(current)
            with self.subTest(index=index),self.assertRaises(ValueError):R.quiescent_pvc_record(record(obj()),current)

    def test_condition_change_without_new_version_is_refused(self):
        with self.assertRaisesRegex(ValueError,'unchanged resource version'):
            R.quiescent_pvc_record(record(obj()),obj('True','10'))

    def test_malformed_barrier_never_authorizes(self):
        for mutate in (lambda x:x.update(schema=True),lambda x:x.update(transition='foreign'),
                       lambda x:x.update(invariantSHA256='bad'),lambda x:x.update(extra=True)):
            rec=record(obj());mutate(rec['pvcUsageBarrier'])
            with self.assertRaises(ValueError):R.quiescent_pvc_record(rec,obj('True','11'))

    def test_manager_hint_is_not_an_authorization_signal(self):
        current=obj('True','11');current['metadata']['managedFields'][0]['manager']='PRIVATE_MANAGER'
        result=R.quiescent_pvc_record(record(obj()),current)
        self.assertNotIn('PRIVATE_MANAGER',json.dumps(result))
        current['spec']['volumeName']='foreign'
        with self.assertRaises(ValueError):R.quiescent_pvc_record(record(obj()),current)

    def test_diagnostic_capture_failure_does_not_change_authorization(self):
        record_before=record(obj())
        with patch.object(R,'pvc_state',side_effect=RuntimeError('fixture capture failure')):
            result=R.quiescent_pvc_record(record_before,obj('True','11'))
        self.assertEqual(result['resourceVersion'],'11');self.assertNotIn('pvcState',result)
        self.assertEqual(record_before['resourceVersion'],'10')

    def test_optional_diagnostics_cannot_enable_a_refresh(self):
        rec=record(obj());rec.pop('pvcUsageBarrier');driver=Driver([obj('True','11')])
        self.assertIs(R.quiescent_cleanup_record(driver,rec),rec);self.assertEqual(driver.calls,[])
        with patch.object(R,'require_origin'),patch.object(R,'consumers_gone'),patch.object(R,'delete_record') as delete:
            with self.assertRaises(ValueError):R.conditional_delete(driver,rec)
        delete.assert_not_called()

    def test_observed_completion_then_exact_delete_without_retry(self):
        rec=record(obj());driver=Driver([obj(),obj('True','11'),obj('True','11')])
        with patch.object(R,'require_origin'),patch.object(R,'consumers_gone',side_effect=lambda d:d.calls.append('consumers')),patch.object(R.time,'sleep'),patch.object(R,'delete_record') as delete:
            final=R.quiescent_cleanup_record(driver,rec);R.conditional_delete(driver,final)
        self.assertEqual(delete.call_count,1);self.assertEqual(delete.call_args.args[1]['resourceVersion'],'11')
        self.assertEqual(delete.call_args.args[1]['uid'],'owned-pvc');self.assertGreaterEqual(driver.calls.count('consumers'),3)
        self.assertEqual(rec['resourceVersion'],'10')

    def test_post_capture_race_is_refused_with_no_refresh_or_delete(self):
        driver=Driver([obj('True','11'),obj('True','12')])
        with patch.object(R,'require_origin'),patch.object(R,'consumers_gone'),patch.object(R,'delete_record') as delete:
            final=R.quiescent_cleanup_record(driver,record(obj()))
            with self.assertRaises(ValueError):R.conditional_delete(driver,final)
            self.assertIs(R.quiescent_cleanup_record(driver,final),final)
        delete.assert_not_called();self.assertEqual(final['resourceVersion'],'11')
        self.assertEqual(driver._retained_cleanup_drift['changedFields'],['resourceVersion'])

    def test_unsafe_change_keeps_baseline_and_captures_bounded_refusal(self):
        rec=record(obj());saved=copy.deepcopy(rec);current=obj('True','11');current['spec']['volumeName']='foreign'
        driver=Driver([current])
        with patch.object(R,'require_origin'),patch.object(R,'consumers_gone'),patch.object(R,'delete_record') as delete:
            with self.assertRaises(ValueError):R.quiescent_cleanup_record(driver,rec)
        delete.assert_not_called();self.assertEqual(rec,saved)
        R.validate_cleanup_drift(driver._retained_cleanup_drift)

    def test_pending_condition_deadline_never_deletes(self):
        driver=Driver([obj()]);clock=[0]
        def sleep(seconds):clock[0]+=max(seconds,20)
        with patch.object(R,'require_origin'),patch.object(R,'consumers_gone'),patch.object(R.time,'monotonic',side_effect=lambda:clock[0]),patch.object(R.time,'sleep',side_effect=sleep),patch.object(R,'delete_record') as delete:
            with self.assertRaisesRegex(ValueError,'did not settle'):R.quiescent_cleanup_record(driver,record(obj()))
        delete.assert_not_called()

if __name__=='__main__':unittest.main()
