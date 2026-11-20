# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('copa_evidence', ROOT / '.github/scripts/copa-evidence.py')
E = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(E)
IMAGE = 'sha256:' + 'a' * 64


class CopaEvidenceTests(unittest.TestCase):
    def fixture(self):
        return {'Metadata': {'ImageID': IMAGE, 'OS': {'Family': 'redhat', 'Name': '9.8'},
                             'ImageConfig': {'architecture': 'amd64', 'Env': ['SECRET=sentinel']}},
                'Results': [{'Class': 'os-pkgs', 'Target': 'private-sentinel', 'Vulnerabilities': [
                    {'PkgName': name, 'VulnerabilityID': 'CVE-2026-1234', 'InstalledVersion': '1.0-1.el9_8',
                     'FixedVersion': '14:1.0-2.el9_9.2', 'Description': 'private-sentinel',
                     'PrimaryURL': 'https://user:sentinel@example.test/'} for name in sorted(E.PACKAGES)]},
                    {'Class': 'lang-pkgs', 'Vulnerabilities': [{'PkgName': 'rpm', 'Description': 'private-sentinel'}]}]}

    def value(self, fixture=None, architecture='amd64', image=IMAGE):
        with patch.dict(os.environ, {}, clear=True):
            return E.evidence(json.dumps(fixture or self.fixture()).encode(), architecture, image)

    def test_only_four_rpm_fix_records_and_os_identity_are_retained(self):
        fixture=self.fixture()
        fixture['Results'][0]['Vulnerabilities'].append({'PkgName': 'other', 'Description': 'private-sentinel'})
        value=self.value(fixture)
        self.assertEqual({v['package'] for v in value['packages']}, E.PACKAGES)
        self.assertEqual(value['os'], {'family': 'redhat', 'version': '9.8'})
        self.assertEqual(value['imageID'], IMAGE)
        self.assertEqual(value['packages'][0]['fixedVersion'], '14:1.0-2.el9_9.2')
        self.assertNotIn('sentinel', json.dumps(value))
        self.assertNotIn('https:', json.dumps(value))

    def test_duplicate_records_are_deduplicated_and_missing_fixes_are_retained(self):
        fixture=self.fixture();values=fixture['Results'][0]['Vulnerabilities'];values.append(copy.deepcopy(values[0]))
        values[1].pop('FixedVersion')
        value=self.value(fixture)
        self.assertEqual(len(value['packages']),4)
        self.assertTrue(any(v['fixedVersion']=='' for v in value['packages']))

    def test_invalid_image_platform_os_and_fix_fields_are_refused(self):
        changes=[lambda v:v['Metadata'].update(ImageID='sha256:'+'b'*64),
                 lambda v:v['Metadata']['ImageConfig'].update(architecture='arm64'),
                 lambda v:v['Metadata']['OS'].update(Name='https://user:sentinel@example.test'),
                 lambda v:v['Results'][0]['Vulnerabilities'][0].update(FixedVersion='secret\nvalue')]
        for change in changes:
            fixture=self.fixture();change(fixture)
            with self.assertRaises(ValueError):self.value(fixture)
        with self.assertRaises(ValueError):self.value(architecture='other')

    def test_duplicate_json_keys_and_excessive_results_are_refused(self):
        with self.assertRaises(ValueError):E.evidence(b'{"Metadata":{},"Metadata":{}}','amd64',IMAGE)
        fixture=self.fixture();fixture['Results']*=257
        with self.assertRaises(ValueError):self.value(fixture)

    def test_regular_bounded_input_and_exclusive_output_with_no_private_errors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'report.json';source.write_text(json.dumps(self.fixture()))
            directory=root/'out';args=[sys.executable,str(ROOT/'.github/scripts/copa-evidence.py'),
                '--report',str(source),'--directory',str(directory),'--architecture','amd64','--image-id',IMAGE]
            env={k:v for k,v in os.environ.items() if not k.startswith('GITHUB_')}
            for _ in range(2):subprocess.run(args,env=env,check=True,capture_output=True)
            self.assertEqual(len(list(directory.iterdir())),1)
            target=next(directory.iterdir());self.assertEqual(target.stat().st_mode&0o777,0o600)
            target.write_text('private-sentinel')
            result=subprocess.run(args,env=env,capture_output=True)
            self.assertNotEqual(result.returncode,0);self.assertNotIn(b'sentinel',result.stderr)
            link=root/'link';link.symlink_to(source)
            with self.assertRaises(ValueError):E.regular(link)
            fifo=root/'fifo';os.mkfifo(fifo)
            with self.assertRaises(ValueError):E.regular(fifo)
            source.write_bytes(b'x'*(E.MAX_REPORT+1))
            with self.assertRaises(ValueError):E.regular(source)

    def test_complete_workflow_binding_is_required_when_present(self):
        env={'GITHUB_SHA':'b'*40,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':'1'}
        with patch.dict(os.environ,env,clear=True):
            value=E.evidence(json.dumps(self.fixture()).encode(),'amd64',IMAGE)
            self.assertEqual(value['binding'],{'sourceSHA':'b'*40,'runID':'123','runAttempt':'1'})
        with patch.dict(os.environ,{'GITHUB_SHA':'b'*40},clear=True):
            with self.assertRaises(ValueError):E.evidence(json.dumps(self.fixture()).encode(),'amd64',IMAGE)

    def test_native_build_retains_evidence_even_after_failure(self):
        workflow=yaml.safe_load((ROOT/'.github/workflows/container.yaml').read_text())
        self.assertEqual(sum(step.get('name')=='Retain only bounded Copa RPM scan evidence' for job in workflow['jobs'].values() for step in job.get('steps',[])),1)
        for job in workflow['jobs'].values():
            builds=[s for s in job.get('steps',[]) if s.get('name')=='Build images']
            if builds:
                self.assertEqual(builds[0]['env']['COPA_EVIDENCE_DIR'],'${{ runner.temp }}/copa-evidence')
                upload=next(s for s in job['steps'] if s.get('name')=='Retain only bounded Copa RPM scan evidence')
                self.assertEqual(upload['if'],'always()')
                self.assertEqual(upload['with']['path'],'${{ runner.temp }}/copa-evidence/*.json')
        script=(ROOT/'.github/scripts/copa-patch.sh').read_text()
        self.assertLess(script.index('retain_evidence "${image}"'),script.index('  apply_patch "${image}"'))
        self.assertIn('if ! retain_evidence',script)


if __name__ == '__main__':unittest.main()
