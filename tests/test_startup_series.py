# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline adversarial series controls; no cluster, network, or startup study."""
import copy
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

from bench import startup_series as series, startup_trials as trial
from tests.test_startup_trials import pair

SOURCE = 'a' * 40
WORKFLOW = 'b' * 64
REPO = Path(__file__).resolve().parents[1]


def transform(value, function, key=None):
    changed = function(key, value)
    if changed is not value:
        return changed
    if isinstance(value, dict):
        return {name: transform(item, function, name) for name, item in value.items()}
    if isinstance(value, list):
        return [transform(item, function) for item in value]
    return value


def fixture(arch, repetition, time_repetition=None):
    cold, warm = pair()

    def distinct_containers(key, value):
        if key in trial.APPS:
            return transform(value, lambda name, item: 'containerd://' +
                             series.checksum((key + item).encode()) if name == 'container_id' else item)
        return value

    cold, warm = transform(cold, distinct_containers), transform(warm, distinct_containers)
    run = 10000 + repetition
    caller = {'source_sha': SOURCE, 'run_id': str(run), 'run_attempt': '1'}
    bindings = {'source': series.checksum(SOURCE.encode()), 'workflow': WORKFLOW,
                'package': series.checksum(('key-' + arch).encode()),
                'package_archive': series.checksum(('archive-' + arch).encode()),
                'package_manifest': series.checksum(('manifest-' + arch).encode())}
    offset = 86400 * (repetition if time_repetition is None else time_repetition)

    def update(key, value):
        if key == 'bindings':
            return copy.deepcopy(bindings)
        if key == 'caller':
            return copy.deepcopy(caller)
        if key == 'cohort':
            return f'startup-{arch}-{run}-1'
        if key == 'pod_uid':
            return str(uuid.uuid5(uuid.NAMESPACE_OID, f'{arch}-{repetition}-{value}'))
        if key == 'container_id':
            return 'containerd://' + series.checksum(f'{arch}-{repetition}-{value}'.encode())
        if key in ('node_uid', 'uid') and value == 'node-uid-a':
            return f'node-{arch}-{repetition}'
        if key == 'architecture':
            return arch
        if key == 'config_sha256':
            return {app: series.checksum(f'config-{arch}-{repetition}-{app}'.encode()) for app in value}
        if key == 'jvm_starts':
            return {app: started + offset * 1000 for app, started in value.items()}
        if key == 'jvm_start_time_unix_ms':
            return value + offset * 1000
        if key in ('observed_start_unix', 'observed_end_unix'):
            return value + offset
        if key == 'process_start_time_seconds':
            return str(int(value) + offset)
        if isinstance(value, str) and value.startswith('2024-01-01T'):
            return (datetime.fromisoformat(value.replace('Z', '+00:00')) +
                    timedelta(seconds=offset)).isoformat().replace('+00:00', 'Z')
        return value

    return refresh({'cold.json': transform(cold, update), 'warm.json': transform(warm, update)})


def refresh(records):
    for mode in ('cold', 'warm'):
        record = records[mode + '.json']
        record['begin_sha256'] = trial.digest(record['begin'])
        records[mode + '-begin.json'] = copy.deepcopy(record['begin'])
    records['bindings.json'] = copy.deepcopy(records['cold.json']['begin']['bindings'])
    records['pair.json'] = trial.validate_pair(records['cold.json'], records['warm.json'])
    return records


class StartupSeriesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.plan = {'schema': 1, 'kind': 'startup-series-plan', 'source_sha': SOURCE,
                     'workflow_sha256': WORKFLOW, 'repetitions': 3,
                     'architectures': ['amd64', 'arm64'], 'entries': []}
        self.records = {}
        for arch in self.plan['architectures']:
            for repetition in range(1, 4):
                records = fixture(arch, repetition)
                entry = {'arch': arch, 'repetition': repetition, 'status': 'candidate',
                         'run_id': 10000 + repetition, 'run_attempt': 1,
                         'artifact_id': (100 if arch == 'amd64' else 200) + repetition,
                         'artifact_name': f'startup-pair-{arch}-{10000 + repetition}-1',
                         'archive_sha256': None, 'archive_path': f'{arch}-{repetition}.zip'}
                self.plan['entries'].append(entry)
                self.records[(arch, repetition)] = records
                self.archive(entry, records)

    def entry(self, arch='amd64', repetition=2):
        return next(item for item in self.plan['entries'] if
                    item['arch'] == arch and item['repetition'] == repetition)

    def archive(self, entry, records=None, members=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            if members is None:
                members = [(name, json.dumps(value, sort_keys=True).encode())
                           for name, value in records.items()]
            for name, body in members:
                archive.writestr(name, body)
        raw = stream.getvalue()
        (self.root / entry['archive_path']).write_bytes(raw)
        entry['archive_sha256'] = series.checksum(raw)

    def save(self):
        path = self.root / 'plan.json'
        path.write_text(json.dumps(self.plan))
        return path

    def verify(self):
        return series.verify(self.save())

    def reason(self, reason, arch='amd64', repetition=2):
        report = self.verify()
        self.assertFalse(report['accepted'])
        self.assertEqual(len(report['entries']), 6)
        row = next(item for item in report['entries'] if
                   item['arch'] == arch and item['repetition'] == repetition)
        self.assertEqual(row['reason'], reason)
        self.assertFalse(report['summaries'][arch]['complete'])
        self.assertIsNone(report['summaries'][arch]['statistics'])
        return report

    def test_complete_series_uses_original_pair_validator_and_descriptive_statistics(self):
        with patch.object(trial, 'validate_pair', wraps=trial.validate_pair) as validator:
            report = self.verify()
        self.assertTrue(report['accepted'])
        self.assertEqual(validator.call_count, 6)
        self.assertEqual(len(report['entries']), 6)
        for arch in self.plan['architectures']:
            summary = report['summaries'][arch]
            self.assertTrue(summary['complete'])
            self.assertEqual(summary['accepted_repetitions'], 3)
            self.assertEqual(summary['statistics']['cold']['pod_creation_to_ready_span_s'],
                             {'min': 6, 'median': 6, 'max': 6})
        # Same run/attempt across architectures and restored namespace identity
        # are allowed; node UIDs and identity-bearing config hashes may differ.
        self.assertEqual(report['entries'][0]['evidence']['cluster_uid_sha256'],
                         report['entries'][1]['evidence']['cluster_uid_sha256'])
        self.assertNotEqual(report['entries'][0]['evidence']['platform'],
                            report['entries'][1]['evidence']['platform'])
        self.assertNotEqual(report['entries'][0]['evidence']['configuration_sha256'],
                            report['entries'][1]['evidence']['configuration_sha256'])

    def test_failed_and_missing_slots_remain_visible_without_partial_statistics(self):
        failed, missing = self.entry(), self.entry('arm64')
        failed['status'] = 'failed'
        self.archive(failed, members=[('bindings.json', b'{}')])
        missing['status'] = 'missing'
        for name in (*series.ACQUISITION, 'archive_path'):
            missing[name] = None
        report = self.reason('declared_failed')
        self.assertEqual(next(row for row in report['entries'] if row['arch'] == 'arm64' and
                              row['repetition'] == 2)['status'], 'missing')
        self.assertEqual(report['summaries']['amd64']['accepted_repetitions'], 2)
        self.assertEqual(report['summaries']['arm64']['accepted_repetitions'], 2)

    def test_four_and_five_repetitions_and_single_native_architecture_are_supported(self):
        self.plan['architectures'] = ['amd64']
        self.plan['entries'] = [item for item in self.plan['entries'] if item['arch'] == 'amd64']
        for repetition in (4, 5):
            self.plan['repetitions'] = repetition
            entry = dict(self.entry(repetition=1), repetition=repetition, run_id=10000 + repetition,
                         artifact_id=100 + repetition, archive_path=f'amd64-{repetition}.zip',
                         artifact_name=f'startup-pair-amd64-{10000 + repetition}-1')
            self.plan['entries'].append(entry)
            self.archive(entry, fixture('amd64', repetition))
            self.assertTrue(self.verify()['accepted'])

    def test_plan_requires_all_slots_and_bounded_repeat_count(self):
        original = copy.deepcopy(self.plan)
        for alteration in ('few', 'many', 'boolean', 'missing', 'duplicate', 'extra'):
            with self.subTest(alteration=alteration):
                self.plan = copy.deepcopy(original)
                if alteration in ('few', 'many', 'boolean'):
                    self.plan['repetitions'] = {'few': 2, 'many': 6, 'boolean': True}[alteration]
                elif alteration == 'missing':
                    self.plan['entries'].pop()
                elif alteration == 'duplicate':
                    self.plan['entries'][-1] = copy.deepcopy(self.plan['entries'][0])
                else:
                    self.plan['private'] = 'PRIVATE_PLAN_SENTINEL'
                with self.assertRaisesRegex(series.Invalid, '^plan_schema$'):
                    self.verify()

    def test_exact_six_file_pair_and_begin_bindings_cannot_be_relabelled(self):
        entry = self.entry()
        original = copy.deepcopy(self.records[('amd64', 2)])
        for alteration in ('pair', 'typed_pair', 'cold_begin', 'warm_begin', 'bindings'):
            with self.subTest(alteration=alteration):
                records = copy.deepcopy(original)
                if alteration in ('pair', 'typed_pair'):
                    records['pair.json']['schema'] = True if alteration == 'typed_pair' else 9
                elif alteration in ('cold_begin', 'warm_begin'):
                    records[alteration.replace('_', '-') + '.json']['cohort'] = 'relabelled'
                else:
                    records['bindings.json']['package_archive'] = 'f' * 64
                self.archive(entry, records)
                self.reason('caller_binding' if alteration == 'bindings' else 'pair_members')

    def test_source_workflow_caller_cohort_and_package_archive_bindings_are_required(self):
        entry = self.entry()
        original = copy.deepcopy(self.records[('amd64', 2)])
        for alteration, reason in (('source', 'source_binding'), ('workflow', 'workflow_binding'),
                                   ('caller', 'caller_binding'), ('cohort', 'cohort_binding'),
                                   ('missing_archive', 'binding_missing'),
                                   ('missing_manifest', 'binding_missing')):
            with self.subTest(alteration=alteration):
                records = copy.deepcopy(original)

                def update(key, value):
                    if key == 'bindings':
                        changed = dict(value)
                        if alteration in ('source', 'workflow'):
                            changed[alteration] = 'f' * 64
                        if alteration == 'missing_archive':
                            changed.pop('package_archive')
                        if alteration == 'missing_manifest':
                            changed.pop('package_manifest')
                        return changed
                    if key == 'caller' and alteration == 'caller':
                        return dict(value, run_id='99999')
                    if key == 'cohort' and alteration == 'cohort':
                        return 'startup-amd64-99999-1'
                    return value

                records = transform(records, update)
                self.archive(entry, refresh(records))
                self.reason(reason)

    def test_package_platform_and_actual_image_drift_are_rejected(self):
        entry = self.entry()
        original = copy.deepcopy(self.records[('amd64', 2)])
        for alteration, reason in (('package', 'package_mismatch'), ('package_manifest', 'package_mismatch'),
                                   ('kernel', 'platform_mismatch'), ('image_id', 'image_mismatch'),
                                   ('architecture', 'platform_invalid')):
            with self.subTest(alteration=alteration):
                def update(key, value):
                    if key == alteration:
                        return {'package': 'f' * 64, 'package_manifest': 'f' * 64,
                                'kernel': '6.9.0', 'image_id': 'sha256:' + 'f' * 64,
                                'architecture': 'arm64'}[alteration]
                    return value

                records = transform(copy.deepcopy(original), update)
                self.archive(entry, refresh(records))
                self.reason(reason)

    def test_manifest_identity_allows_wrapper_differences_and_detects_changed_payload(self):
        entry = self.entry()
        original = copy.deepcopy(self.records[('amd64', 2)])
        records = transform(copy.deepcopy(original), lambda key, value:
                            'f' * 64 if key == 'package_archive' else value)
        self.archive(entry, refresh(records))
        report = self.verify()
        self.assertTrue(report['accepted'])
        first, second = [item['evidence'] for item in report['entries'][:2]]
        self.assertNotEqual(first['package_archive'], second['package_archive'])
        self.assertEqual(first['package_manifest'], second['package_manifest'])
        records = transform(copy.deepcopy(original), lambda key, value:
                            'f' * 64 if key == 'package_manifest' else value)
        self.assertEqual(records['cold.json']['begin']['bindings']['package_archive'],
                         first['package_archive'])
        self.archive(entry, refresh(records))
        self.reason('package_mismatch')

    def test_cross_install_reference_rewriting_does_not_change_content_identity(self):
        entry = self.entry()
        records = transform(copy.deepcopy(self.records[('amd64', 2)]),
                            lambda key, value: 'f' * 64 if key in
                            ('image_reference_sha256', 'declared_image_sha256') else value)
        self.archive(entry, refresh(records))
        self.assertTrue(self.verify()['accepted'])

    def test_reused_run_artifact_or_application_incarnation_cannot_count(self):
        entry = self.entry()
        original_entry = copy.deepcopy(entry)
        original_records = copy.deepcopy(self.records[('amd64', 2)])
        first = self.records[('amd64', 1)]['cold.json']['phases'][trial.APPS[0]]['jvm_observation']['identity']
        for alteration, reason in (('run', 'execution_reused'), ('artifact', 'artifact_reused'),
                                   ('pod_uid', 'incarnation_reused'), ('container_id', 'incarnation_reused'),
                                   ('jvm', 'incarnation_reused')):
            with self.subTest(alteration=alteration):
                entry.clear(); entry.update(original_entry)
                records = copy.deepcopy(original_records)
                if alteration == 'run':
                    entry['run_id'] = 10001
                    entry['artifact_name'] = 'startup-pair-amd64-10001-1'
                elif alteration == 'artifact':
                    entry['artifact_id'] = 101
                elif alteration == 'jvm':
                    records = fixture('amd64', 2, time_repetition=1)
                else:
                    old = records['cold.json']['phases'][trial.APPS[0]]['jvm_observation']['identity'][alteration]
                    records = transform(records, lambda _key, value:
                                        first[alteration] if value == old else value)
                self.archive(entry, refresh(records))
                self.reason(reason)

    def test_report_projects_evidence_without_echoing_unknown_payloads_or_paths(self):
        entry = self.entry()
        records = copy.deepcopy(self.records[('amd64', 2)])
        records['cold.json']['private'] = {'log': 'PRIVATE_RESOURCE_SENTINEL'}
        self.archive(entry, refresh(records))
        report = self.verify()
        self.assertTrue(report['accepted'])
        rendered = json.dumps(report)
        self.assertNotIn('PRIVATE_RESOURCE_SENTINEL', rendered)
        self.assertNotIn(str(self.root), rendered)
        self.assertNotIn(entry['archive_path'], rendered)

    def test_archive_hash_inventory_symlinks_and_json_are_checked_before_pair_validation(self):
        entry = self.entry()
        original = copy.deepcopy(self.records[('amd64', 2)])
        valid = [(name, json.dumps(value).encode()) for name, value in original.items()]
        for alteration, reason in (('hash', 'archive_hash'), ('missing', 'archive_inventory'),
                                   ('duplicate', 'archive_inventory'), ('traversal', 'archive_inventory'),
                                   ('symlink', 'archive_inventory'), ('oversized', 'archive_inventory'),
                                   ('json_duplicate', 'archive_json'), ('nonfinite', 'archive_json')):
            with self.subTest(alteration=alteration):
                members = list(valid)
                if alteration == 'missing':
                    members.pop()
                elif alteration == 'duplicate':
                    members.append(members[0])
                elif alteration == 'traversal':
                    members[0] = ('../bindings.json', b'{}')
                elif alteration == 'symlink':
                    info = zipfile.ZipInfo(members[0][0])
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    members[0] = (info, b'target')
                elif alteration == 'oversized':
                    members[0] = (members[0][0], b' ' * (series.MAX_JSON + 1))
                elif alteration == 'json_duplicate':
                    members[0] = (members[0][0], b'{"secret":1,"secret":2}')
                elif alteration == 'nonfinite':
                    members[0] = (members[0][0], b'{"secret":1e9999}')
                if alteration == 'duplicate':
                    with self.assertWarns(UserWarning):
                        self.archive(entry, members=members)
                else:
                    self.archive(entry, members=members)
                if alteration == 'hash':
                    entry['archive_sha256'] = '0' * 64
                self.reason(reason)

    def test_nul_truncated_original_zip_name_is_not_an_allowlisted_member(self):
        self.assertTrue(self.verify()['accepted'])
        entry = self.entry()
        original_name = b'bindings.jsonX../hidden'
        nul_name = b'bindings.json\x00../hidden'
        members = [(original_name.decode() if name == 'bindings.json' else name,
                    json.dumps(value).encode())
                   for name, value in self.records[('amd64', 2)].items()]
        self.archive(entry, members=members)
        path = self.root / entry['archive_path']
        raw = path.read_bytes()
        self.assertEqual(raw.count(original_name), 2)  # Local and central ZIP headers.
        altered = raw.replace(original_name, nul_name)
        self.assertEqual(len(altered), len(raw))
        with zipfile.ZipFile(io.BytesIO(altered)) as archive:
            member = next(item for item in archive.infolist() if item.filename == 'bindings.json')
            self.assertEqual(member.orig_filename, nul_name.decode())
        path.write_bytes(altered)
        entry['archive_sha256'] = series.checksum(altered)
        self.reason('archive_inventory')

    def test_relative_paths_and_symlink_components_are_refused(self):
        entry = self.entry()
        original_path = entry['archive_path']
        for path in ('../escape.zip', '/absolute.zip', 'a//b.zip', 'a/./b.zip', 'a\\b.zip'):
            with self.subTest(path=path), self.assertRaisesRegex(series.Invalid, '^plan_schema$'):
                entry['archive_path'] = path
                self.verify()
        entry['archive_path'] = original_path
        actual = self.root / original_path
        real = self.root / 'actual.zip'
        actual.rename(real)
        actual.symlink_to(real)
        self.reason('archive_path')
        actual.unlink()
        nested = self.root / 'nested'
        nested.mkdir()
        (nested / 'artifact.zip').write_bytes(real.read_bytes())
        (self.root / 'shortcut').symlink_to(nested, target_is_directory=True)
        entry['archive_path'] = 'shortcut/artifact.zip'
        self.reason('archive_path')

    def test_duplicate_or_nonfinite_plan_and_bad_pair_have_fixed_diagnostics(self):
        path = self.root / 'plan.json'
        for raw in (b'{"private":1,"private":2}', b'{"private":NaN}'):
            path.write_bytes(raw)
            with self.assertRaisesRegex(series.Invalid, '^plan_json$'):
                series.verify(path)
        records = copy.deepcopy(self.records[('amd64', 2)])
        records['cold.json']['phases'][trial.APPS[0]]['jvm_to_log_s'] += 1
        self.archive(self.entry(), records)
        self.reason('pair_invalid')

    def test_cli_is_offline_writes_private_report_and_refuses_overwrite_and_symlink(self):
        path = self.save()
        output = self.root / 'report.json'
        binary = self.root / 'bin'
        binary.mkdir()
        marker = self.root / 'NETWORK_TOOL_CALLED'
        for name in ('gh', 'kubectl', 'uds', 'curl'):
            executable = binary / name
            executable.write_text('#!/bin/sh\n: > "$STARTUP_SENTINEL"\nexit 91\n')
            executable.chmod(0o700)
        env = dict(os.environ, PATH=str(binary), STARTUP_SENTINEL=str(marker))
        command = [sys.executable, '-B', '-m', 'bench.startup_series',
                   '--plan', str(path), '--output', str(output)]
        result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(output.read_text())['accepted'])
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['reason'], 'output_path')
        self.assertNotIn(str(self.root), result.stdout + result.stderr)
        output.unlink()
        target = self.root / 'untouched'
        target.write_text('KEEP')
        output.symlink_to(target)
        result = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)['reason'], 'output_path')
        self.assertEqual(target.read_text(), 'KEEP')
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
