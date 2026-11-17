# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline trust/route readback controls; no cluster operations."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]
HELPER=ROOT/'.github/scripts/velero-controller-state.py'
spec=importlib.util.spec_from_file_location('controller_state',HELPER)
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

def fixture():
    result={}
    for ns in ('takserver','cloudtak'):
        result[ns+'-configmaps.json']={'items':[{'kind':'ConfigMap','metadata':{'name':name,'namespace':ns,'uid':ns+name},
                'data':{'ca.crt':'PRIVATE_TRUST'}} for name in M.CONFIGMAPS]}
    result['cloudtak-package.json']={'apiVersion':'uds.dev/v1alpha1','kind':'Package',
        'metadata':{'name':'cloudtak','namespace':'cloudtak','uid':'package-old','generation':1}}
    result['cloudtak-virtualservice.json']={'apiVersion':'networking.istio.io/v1','kind':'VirtualService',
        'metadata':{'name':M.VIRTUAL_SERVICE,'namespace':'cloudtak','uid':'vs-old',
            'labels':{'uds/package':'cloudtak','uds/generation':'1'},
            'ownerReferences':[{'apiVersion':'uds.dev/v1alpha1','kind':'Package','name':'cloudtak','uid':'package-old'}]},
        'spec':{'hosts':['PRIVATE_HOST'],'http':[{'route':[{'destination':{'host':'service'}}]}]}}
    return result

def write(folder,values):
    for name,value in values.items():(folder/name).write_text(json.dumps(value))

class ControllerState(unittest.TestCase):
    def test_server_schema_rejection_stops_before_namespace_deletion(self):
        script=(ROOT/'tests/backup-restore-controller-trial.sh').read_text()
        start=script.index('# Fail against the actual API schema')
        end=script.index('# A PodVolumeRestore holds',start)
        setup='''set -euo pipefail
say() { :; }
k() {
  printf '%s\\n' "$*" >> "$CALLS"
  if [[ "$*" == *--dry-run=server* ]]; then return "$SCHEMA_RESULT"; fi
}
'''
        with tempfile.TemporaryDirectory() as directory:
            for rejected in (False,True):
                calls=Path(directory)/'calls';calls.write_text('')
                result=subprocess.run(['bash','-c',setup+script[start:end]],capture_output=True,text=True,
                    env={**os.environ,'work':directory,'CALLS':str(calls),'SCHEMA_RESULT':str(int(rejected))})
                self.assertEqual(result.returncode,int(rejected))
                commands=calls.read_text().splitlines()
                self.assertIn('--dry-run=server --validate=strict',commands[0])
                self.assertEqual(any(line.startswith('delete namespace') for line in commands),not rejected)
                self.assertEqual(len(commands),1 if rejected else 3)

    def test_regenerated_uids_and_metadata_allowed_but_current_owner_required(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);values=fixture();write(folder,values);before=M.snapshot(folder)
            for name,value in values.items():
                for obj in value.get('items',[value]):
                    obj['metadata'].update(uid='new-'+obj['metadata']['uid'],resourceVersion='new',annotations={'changed':'PRIVATE_METADATA'})
            values['cloudtak-package.json']['metadata']['generation']=2
            meta=values['cloudtak-virtualservice.json']['metadata'];meta['ownerReferences'][0]['uid']='new-package-old';meta['labels']['uds/generation']='2'
            write(folder,values);self.assertEqual(M.snapshot(folder),before)
            self.assertEqual(len(before['resources']),7);self.assertNotIn('PRIVATE',json.dumps(before))
            meta['ownerReferences'][0]['uid']='package-old';write(folder,values)
            with self.assertRaises(ValueError):M.snapshot(folder)

    def test_every_excluded_resource_content_change_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);original=fixture();write(folder,original);baseline=M.snapshot(folder)
            (folder/'baseline.json').write_text(json.dumps(baseline))
            for ns in ('takserver','cloudtak'):
                for index in range(3):
                    values=copy.deepcopy(original);values[ns+'-configmaps.json']['items'][index]['data']['ca.crt']='PRIVATE_CHANGED'
                    write(folder,values)
                    result=subprocess.run([sys.executable,str(HELPER),'verify',directory,str(folder/'baseline.json')],capture_output=True,text=True)
                    self.assertEqual(result.returncode,1);self.assertNotIn('PRIVATE',result.stdout+result.stderr)
            values=copy.deepcopy(original);values['cloudtak-virtualservice.json']['spec']['hosts']=['PRIVATE_CHANGED'];write(folder,values)
            self.assertNotEqual(M.snapshot(folder),baseline)

    def test_missing_duplicate_deleting_wrong_scope_and_invalid_owner_refuse(self):
        original=fixture()
        mutations=[lambda v:v['takserver-configmaps.json']['items'].pop(),
            lambda v:v['takserver-configmaps.json']['items'].__setitem__(1,copy.deepcopy(v['takserver-configmaps.json']['items'][0])),
            lambda v:v['takserver-configmaps.json']['items'][0]['metadata'].update(namespace='istio-system'),
            lambda v:v['cloudtak-virtualservice.json']['metadata'].update(deletionTimestamp='now'),
            lambda v:v['cloudtak-virtualservice.json']['metadata']['ownerReferences'].append({}),
            lambda v:v['cloudtak-virtualservice.json']['metadata']['labels'].update({'uds/generation':'0'}),
            lambda v:v['cloudtak-package.json'].update(kind='Secret'),
            lambda v:v['takserver-configmaps.json']['items'][0].update(data={})]
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            for mutate in mutations:
                values=copy.deepcopy(original);mutate(values);write(folder,values)
                with self.assertRaises(ValueError):M.snapshot(folder)
            write(folder,original);(folder/'cloudtak-package.json').write_text('{"kind":"Package","kind":"Secret"}')
            with self.assertRaises(ValueError):M.snapshot(folder)
            (folder/'cloudtak-package.json').write_bytes(b'x'*(512*1024+1))
            with self.assertRaises(ValueError):M.snapshot(folder)

    def test_policy_excludes_only_seven_exact_names_and_retains_catchall(self):
        policy=yaml.safe_load((ROOT/'tests/velero-controller-restore-policy.yaml').read_text())
        self.assertEqual(set(policy),{'version','namespacedFilterPolicies'});self.assertEqual(policy['version'],'v1')
        excluded=set()
        for entry in policy['namespacedFilterPolicies']:
            self.assertEqual(len(entry['namespaces']),1);ns=entry['namespaces'][0]
            self.assertIn(ns,('takserver','cloudtak'))
            catch=[f for f in entry['resourceFilters'] if f['kinds']==['*']]
            self.assertEqual(catch,[{'kinds':['*']}])
            for rule in entry['resourceFilters']:
                if rule in catch:continue
                self.assertEqual(set(rule),{'kinds','excludedNames'})
                self.assertEqual(len(rule['kinds']),1)
                for name in rule['excludedNames']:
                    self.assertFalse(any(c in name for c in '*?['));excluded.add((ns,rule['kinds'][0],name))
        self.assertEqual(excluded,{(ns,'configmaps',name) for ns in ('takserver','cloudtak') for name in M.CONFIGMAPS}|
                         {('cloudtak','virtualservices.networking.istio.io',M.VIRTUAL_SERVICE)})
        script=(ROOT/'tests/backup-restore-controller-trial.sh').read_text()
        self.assertLess(script.index('controller-state.py" snapshot'),script.index('k delete namespace takserver cloudtak'))
        self.assertLess(script.index('controller-state.py" verify'),script.index('apps_up=0'))
        self.assertIn('    name: ${controller_policy}',script)
        self.assertLess(script.index('k create --dry-run=server --validate=strict'),script.index('k delete namespace takserver cloudtak'))
        self.assertIn('v["immutable"]=True',script)
        self.assertNotIn('existingResourcePolicy:',script)
        # Each ownership-trial stage has its own restore name. Reusing a run-only
        # immutable ConfigMap would fail the second acceptance in the same run.
        self.assertIn('controller_policy="${restore_name}-policy"',script)
        driver=(ROOT/'tests/velero-ownership/source_provider.py').read_text()
        self.assertIn("'tests/velero-controller-restore-policy.yaml'",driver)
        self.assertIn("'.github/scripts/velero-controller-state.py'",driver)


if __name__=='__main__':unittest.main()
