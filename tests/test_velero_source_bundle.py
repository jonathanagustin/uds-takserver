# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline source-to-built-bundle controls; no signature or cluster acceptance."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import yaml
import zstandard

ROOT=Path(__file__).resolve().parents[1]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value
S=module('source_bundle_tests','tests/velero-ownership/source_bundle.py')


class SourceBundleTests(unittest.TestCase):
    def bundle(self,directory,mutate=None):
        blobs={}
        def add(value,title=None):
            data=value if isinstance(value,bytes) else json.dumps(value).encode()
            digest=S.B.byte_digest(data);blobs['blobs/sha256/'+digest]=data
            desc={'mediaType':'application/vnd.zarf.layer.v1.blob','digest':'sha256:'+digest,'size':len(data)}
            if title:desc['annotations']={'org.opencontainers.image.title':title}
            return desc
        provider={'schema':2,'architecture':'amd64','sourceSHA':'a'*40,'runID':'123','runAttempt':'1'}
        config={'kind':'ZarfPackageConfig','metadata':{'name':'core-backup-restore','version':S.B.VERSION,
            'architecture':'amd64','annotations':{'takserver.qualification/source-sha':provider['sourceSHA'],
            'takserver.qualification/run-id':provider['runID'],'takserver.qualification/run-attempt':provider['runAttempt']}}}
        if mutate:mutate('config',config)
        manifest=add({'schemaVersion':2,'config':add(config),'layers':[]})
        provider['packageManifestDigest']=manifest['digest']
        filename='zarf-package-core-backup-restore-amd64-'+S.B.VERSION+'-upstream.tar.zst'
        package={'name':'core-backup-restore','path':str((directory/'provider'/filename).absolute()),
                 'ref':S.B.VERSION+'-upstream@'+manifest['digest'],'keylessVerification':S.keyless()}
        definition={'kind':'UDSBundle','packages':[package]}
        if mutate:mutate('definition',definition)
        # An unannotated package layer matches actual UDS0.41 output.
        layers=[manifest,add(yaml.safe_dump(definition).encode(),'uds-bundle.yaml')]
        # Exercise streaming validation beyond the metadata-retention threshold
        # and a real frame window larger than128KiB.
        add(b'x'*(2*1024**2))
        root={'schemaVersion':2,'mediaType':'application/vnd.oci.image.manifest.v1+json','layers':layers}
        if mutate:mutate('root',root)
        desc=add(root);desc['mediaType']=root['mediaType']
        blobs['index.json']=json.dumps({'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json',
                                      'manifests':[desc]}).encode()
        if mutate:mutate('blobs',blobs)
        raw=io.BytesIO()
        with tarfile.open(fileobj=raw,mode='w') as archive:
            for name,data in blobs.items():
                entry=tarfile.TarInfo(name);entry.size=len(data);archive.addfile(entry,io.BytesIO(data))
        path=directory/'bundle.tar.zst';path.write_bytes(zstandard.ZstdCompressor().compress(raw.getvalue()))
        return path,provider

    def test_real_topology_retained_selection_source_annotations_and_large_blobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path,provider=self.bundle(root)
            self.assertGreater(zstandard.get_frame_parameters(path.read_bytes()[:18]).window_size,128*1024)
            self.assertEqual(S.inspect_bundle(path,provider,root/'provider'),provider['packageManifestDigest'])
            with self.assertRaises(ValueError):S.inspect_bundle(path,{**provider,'packageManifestDigest':'sha256:'+'b'*64},root/'provider')
            with self.assertRaises(ValueError):S.inspect_bundle(path,provider,root/'other-provider')

    def test_wrong_signer_issuer_ref_path_duplicate_or_repository_refuses(self):
        mutations=[lambda d:d['packages'][0]['keylessVerification'].update(certificateIdentityRegexp='.*'),
                   lambda d:d['packages'][0]['keylessVerification'].update(certificateOIDCIssuer='https://other'),
                   lambda d:d['packages'][0].update(path='/different/package'),
                   lambda d:d['packages'][0].update(ref=S.B.VERSION+'-upstream@sha256:'+'b'*64),
                   lambda d:d['packages'][0].update(repository='ghcr.io/official/package'),
                   lambda d:d['packages'].append(copy.deepcopy(d['packages'][0]))]
        for mutate in mutations:
            with self.subTest(mutation=mutate),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);path,provider=self.bundle(root,lambda kind,value:mutate(value) if kind=='definition' else None)
                with self.assertRaisesRegex(ValueError,'verification selection'):S.inspect_bundle(path,provider,root/'provider')

    def test_wrong_platform_version_run_or_source_and_changed_oci_bytes_refuse(self):
        mutations=[lambda k,v:v['metadata'].update(architecture='arm64') if k=='config' else None,
                   lambda k,v:v['metadata'].update(version='1.16.0') if k=='config' else None,
                   lambda k,v:v['metadata']['annotations'].update({'takserver.qualification/source-sha':'b'*40}) if k=='config' else None,
                   lambda k,v:v['metadata']['annotations'].update({'takserver.qualification/run-id':'999'}) if k=='config' else None,
                   lambda k,v:v['metadata']['annotations'].update({'takserver.qualification/run-attempt':'2'}) if k=='config' else None,
                   lambda k,v:v['layers'].append(copy.deepcopy(v['layers'][0])) if k=='root' else None,
                   lambda k,v:v.update({next(name for name,data in v.items() if len(data)>1024**2):b'changed'}) if k=='blobs' else None]
        for mutate in mutations:
            with self.subTest(mutation=mutate),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);path,provider=self.bundle(root,mutate)
                with self.assertRaises(ValueError):S.inspect_bundle(path,provider,root/'provider')

    def candidate(self,root,archive,provider):
        directory=root/'candidate';directory.mkdir()
        (directory/'uds-config.yaml').write_bytes((ROOT/'bundle/full/uds-config.yaml').read_bytes())
        for name in ('core','detach','clean'):
            destination=directory/name;destination.mkdir()
            shutil.copyfile(archive,destination/'uds-bundle-fixture.tar.zst')
        receipt={'schema':2,'sourceSHA':provider['sourceSHA'],'files':{'uds-config.yaml':S.B.digest(directory/'uds-config.yaml')},
                 'coreSourceProvider':provider}
        (directory/'candidate.json').write_text(json.dumps(receipt))
        return directory

    def test_actual_bind_load_deploy_path_rejects_unrelated_archives_and_carries_provider(self):
        candidate=module('source_candidate_binding_tests','tests/velero-ownership/candidate.py')
        driver=candidate.D;provider_module=candidate.C
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);archive,provider=self.bundle(root);directory=self.candidate(root,archive,provider)
            env={'GITHUB_SHA':provider['sourceSHA']}
            with patch.dict(os.environ,env,clear=True),patch.object(provider_module,'enabled',return_value=True), \
                 patch.object(provider_module,'receipt',return_value=provider), \
                 patch.object(provider_module,'paths',return_value=(root/'provider',root/'verification')), \
                 patch.object(driver,'source_provider',return_value=provider_module):
                with patch.object(sys,'argv',['candidate','bind','--directory',str(directory)]):candidate.main()
                plan=directory/'plan.json'
                value=json.loads(plan.read_text());self.assertEqual(value['schema'],2)
                self.assertEqual(value['coreSourceProvider'],provider)
                files=driver.load_plan(plan);self.assertEqual(files.core_provider,provider)
                obj=object.__new__(driver.Driver);obj.files=files;obj.core_provider=provider
                obj.state={'inputs':{key:driver.digest(path) for key,path in files.items()}}
                obj.idle=lambda:None;obj.guarded=lambda:None
                with patch.object(obj,'execute',create=True) as execute:
                    obj.deploy('core','core-backup-restore');execute.assert_called_once()
                # A stale/substituted archive is refused even if a caller updates
                # the ordinary archive hash to match it before deployment.
                files['core'].write_bytes(b'unrelated archive')
                obj.state['inputs']['core']=driver.digest(files['core'])
                with patch.object(obj,'execute',create=True) as execute:
                    with self.assertRaises((ValueError,zstandard.ZstdError,tarfile.ReadError)):obj.deploy('core','core-backup-restore')
                    execute.assert_not_called()
                plan.unlink()
                with patch.object(sys,'argv',['candidate','bind','--directory',str(directory)]),self.assertRaises((ValueError,zstandard.ZstdError,tarfile.ReadError)):candidate.main()
                self.assertFalse(plan.exists())

    def test_legacy_or_different_source_plan_cannot_enable_source_mode(self):
        driver=module('source_legacy_plan_tests','tests/velero-ownership/drive.py')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);files={}
            for name in ('core','detach','clean','config'):
                path=root/name;path.write_text((ROOT/'bundle/full/uds-config.yaml').read_text() if name=='config' else 'opaque')
                files[name]={'path':name,'sha256':S.B.digest(path)}
            path=root/'plan.json';path.write_text(json.dumps({'schema':1,'sourceSHA':'a'*40,'files':files}))
            provider=driver.source_provider()
            with patch.dict(os.environ,{'GITHUB_SHA':'a'*40},clear=True),patch.object(driver,'source_provider',return_value=provider), \
                 patch.object(provider,'enabled',return_value=True),self.assertRaises(ValueError):driver.load_plan(path)

    def test_provider_or_helper_drift_during_archive_scan_refuses(self):
        provider=module('source_bundle_race_tests','tests/velero-ownership/source_provider.py')
        with patch.object(provider,'receipt',side_effect=[{'bound':True},{'changed':True}]), \
             patch.object(provider,'paths',return_value=(Path('/provider'),Path('/verify'))), \
             patch.object(provider,'bundle_reader') as reader:
            reader.return_value.inspect_bundle.return_value='sha256:'+'b'*64
            with self.assertRaisesRegex(ValueError,'during bundle inspection'):provider.verify_bundle(Path('/archive'),{'bound':True})


if __name__=='__main__':unittest.main()
