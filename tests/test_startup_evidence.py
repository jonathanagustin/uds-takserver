# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Regression checks for the retained startup qualification evidence."""
import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'bench/results/startup-pairs-20261114'
SPEC = importlib.util.spec_from_file_location('startup_evidence', ROOT / 'check.py')
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)


class StartupEvidenceTests(unittest.TestCase):
    def phase(self):
        record = EVIDENCE.decode(EVIDENCE.read(ROOT / 'records/amd64/cold.json'))
        return copy.deepcopy(record['phases']['takserver-api'])

    def test_retained_pairs_and_published_table(self):
        rows, spans = EVIDENCE.verify()
        self.assertEqual(len(rows), 8)
        self.assertEqual(spans, [['amd64', 140, 100], ['arm64', 131, 101]])
        self.assertIn(EVIDENCE.tables(rows, spans), (ROOT / 'README.md').read_text())

    def test_reported_duration_cannot_override_timestamp_evidence(self):
        phase = self.phase()
        phase['jvm_to_log_s'] += 1
        with self.assertRaisesRegex(ValueError, 'reported phase duration differs'):
            EVIDENCE.phase_row('amd64', 'cold', 'takserver-api', phase)

    def test_missing_pull_is_not_zero(self):
        phase = self.phase()
        phase['image_pull_s'] = None
        row = EVIDENCE.phase_row('amd64', 'cold', 'takserver-api', phase)
        self.assertIsNone(row[3])
        self.assertIn('not measured', EVIDENCE.tables([row], []))

    def test_duplicate_keys_nonfinite_and_hash_tampering_rejected(self):
        for payload in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                EVIDENCE.decode(payload)
        with self.assertRaisesRegex(ValueError, 'hash/size differs'):
            EVIDENCE.read(ROOT / 'records/amd64/cold.json', '0' * 64)


REPEAT_ROOT = ROOT.parent/'startup-repeat-20261115'
REPEAT_SPEC = importlib.util.spec_from_file_location('startup_repeat_evidence', REPEAT_ROOT/'check.py')
REPEAT = importlib.util.module_from_spec(REPEAT_SPEC)
REPEAT_SPEC.loader.exec_module(REPEAT)


class StartupRepeatEvidenceTests(unittest.TestCase):
    def records(self):
        return tuple(REPEAT.decode(REPEAT.read(REPEAT_ROOT/'records/amd64'/(mode+'.json')))
                     for mode in ('cold','warm'))

    def test_additional_pair_grain_and_published_table(self):
        rows,spans=REPEAT.verify()
        self.assertEqual(len(rows),4);self.assertEqual(spans,[['amd64',170,130]])
        self.assertIn(REPEAT.tables(rows,spans),(REPEAT_ROOT/'README.md').read_text())

    def test_restored_namespace_uid_is_not_a_unique_trial_identifier(self):
        cold,warm=self.records();previous=REPEAT.prior_records()
        self.assertEqual(cold['snapshot']['cluster_uid'],previous[0]['snapshot']['cluster_uid'])
        REPEAT.verify_independent(cold,warm)

    def test_reused_execution_pod_or_jvm_cannot_count_as_additional_pair(self):
        for mode in ('run','cohort','pod','jvm'):
            with self.subTest(mode=mode):
                cold,warm=self.records();old=REPEAT.prior_records()[0]
                if mode=='run':cold['begin']['caller']['run_id']=old['begin']['caller']['run_id']
                elif mode=='cohort':cold['begin']['cohort']=old['begin']['cohort']
                elif mode=='pod':cold['phases']['takserver-api']['evidence']['pod_uid']=old['phases']['takserver-api']['evidence']['pod_uid']
                else:cold['phases']['takserver-api']['jvm_observation']['jvm_start_time_unix_ms']=old['phases']['takserver-api']['jvm_observation']['jvm_start_time_unix_ms']
                with self.assertRaises(ValueError):REPEAT.verify_independent(cold,warm)

    def test_changed_execution_or_original_artifact_bytes_are_rejected(self):
        import hashlib,json,shutil,tempfile
        for mode in ('step','archive','record','zip'):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                target=Path(directory)/'evidence';shutil.copytree(REPEAT_ROOT,target)
                manifest=REPEAT.decode(REPEAT.read(target/'sources.json'))
                if mode=='step':
                    job=REPEAT.decode(REPEAT.read(target/'job.json'))
                    next(step for step in job['steps'] if step['name']=='Acquire only the matching published application package')['conclusion']='skipped'
                    raw=json.dumps(job).encode();(target/'job.json').write_bytes(raw)
                    manifest['job_metadata_sha256']=hashlib.sha256(raw).hexdigest()
                elif mode=='archive':manifest['datasets'][0]['archive_sha256']='0'*64
                elif mode=='record':
                    record=target/'records/amd64/cold.json';raw=record.read_bytes()+b'\n';record.write_bytes(raw)
                    manifest['datasets'][0]['files']['cold.json']=hashlib.sha256(raw).hexdigest()
                else:
                    archive=target/'artifact.zip';archive.write_bytes(archive.read_bytes()+b'changed')
                (target/'sources.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):REPEAT.verify(target)


if __name__ == '__main__':
    unittest.main()
