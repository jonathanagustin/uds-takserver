# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""PostgreSQL experiment policy fixtures; no cluster or load is dispatched."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from bench import postgis_study as policy, report, sustained
from tests.test_bench import runner
from tests import test_bench_sustained as baseline_tests


VERSION = {'server_version_num': '180006', 'postgis_version': '3.6.4'}
LIMITS = {'memory.max': str(512 * 1024**2), 'cpu.max': '50000 100000'}
PROOF = {'repository': 'jonathanagustin/uds-takserver', 'context': 'k3d-uds',
         'sourceSHA': 'a'*40, 'runID': '123', 'runAttempt': '1', 'clusterUID': 'cluster',
         'nodeContainerID': 'node', 'markerUID': 'marker', 'markerResourceVersion': '1'}


def configurations():
    return [runner.load_config(runner.ROOT / f'bench/configs/{name}.yaml') for name in policy.POINTS]


def state(name, start='2026-11-17 01:00:00+00'):
    return {'postmaster_start': start, 'database_oid': '16384', 'system_identifier': '123456789',
            'database_bytes': 1048576,
            'settings': {**{key: {'setting': str(value), 'unit': unit}
                           for (key, unit), value in zip(policy.UNITS.items(), policy.NORMALIZED[name])},
                         **{key: {'setting': 'on', 'unit': ''} for key in policy.DURABILITY}}}


def record(name, repeat, arch='amd64'):
    config = {**next(c for c in configurations() if c['name'] == name), 'duration_s': 1200}
    study = sustained.provenance(config, 'a'*40, runner.ROOT / 'bench/fixtures/tester/tak_tester.py.txt')
    study.update(protocol=policy.PROTOCOL, **policy.identity(name, repeat), ephemeral_proof=dict(PROOF),
                 runner={'machine': 'x86_64' if arch == 'amd64' else 'aarch64'},
                 database_version=dict(VERSION), cgroup_before=dict(LIMITS), cgroup_after=dict(LIMITS),
                 state_before_restart=state(name, 'previous'), state_after_restart=state(name),
                 state_before=state(name), state_after=state(name))
    for key in ('state_after_restart', 'state_before', 'state_after'):
        study[key]['postmaster_start'] = f"session-{study['session']}"
    study['state_before_restart']['postmaster_start'] = f"session-{study['session']-1}"
    value = runner.blank(arch, f'{name}-r{repeat}', 50, 1200)
    value.update(git_sha='a'*40, study=study, images={'sum_bytes': 1}, kubelet={'steady_rss_bytes': 100})
    rate = (policy.POINTS.index(name)+1)*100 + repeat
    window = baseline_tests.SustainedTests().window()
    window.update(end_monotonic=1500, end_unix=2200)
    for key in ('ready_monotonic', 'ready_unix', 'start_monotonic', 'end_monotonic', 'start_unix', 'end_unix'):
        window[key] += (study['session']-1)*2000
    for row in window['after'].values():
        row[1] = 200 + rate*24
        row[6] = 500 + rate*24
    value['load'].update(sustained.parse_window('BENCH_WINDOW_JSON:' + json.dumps(window)))
    rows = []
    for container in ('postgresql', 'takserver-api', 'takserver-messaging'):
        suffix = study['session'] if container == 'postgresql' else 0
        boundary = {'pod_uid': f'{container}-pod-{suffix}', 'container_id': f'containerd://{container}-{suffix}',
                    'image_id': f'sha256:{container}', 'memory_limit_bytes': 512 * 1024**2,
                    'restart_count': 0, 'ready': True, 'termination_reason': None, 'last_termination_reason': None}
        rows.append({'namespace': 'takserver', 'pod': container+'-0', 'container': container,
                     'before': copy.deepcopy(boundary), 'after': copy.deepcopy(boundary)})
    value['capacity'] = {'errors': [], 'containers': rows}
    return value


def block(arch='amd64'):
    return [record(config['name'], repeat, arch) for config, repeat, _position in policy.plan(configurations())]


class PostgisPolicyTests(unittest.TestCase):
    def test_mode_selection_is_explicit_and_preserves_baseline_guards(self):
        environment = {'BENCH_STUDY': policy.MODE, 'BENCH_PROFILE': policy.PROFILE, 'BENCH_CLIENTS': '50'}
        self.assertTrue(sustained.enabled(environment))
        for change in ({'BENCH_PROFILE': 'all'}, {'BENCH_PROFILE': 'baseline'}, {'BENCH_CLIENTS': '200'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                sustained.enabled({**environment, **change})
        with patch.dict(os.environ, environment):
            selected = runner.selected_configs(sorted((runner.ROOT / 'bench/configs').glob('*.yaml')))
        self.assertEqual([c['name'] for c in selected], list(policy.POINTS))
        with patch.dict(os.environ, {'BENCH_STUDY': 'short', 'BENCH_PROFILE': policy.PROFILE}):
            with self.assertRaises(ValueError):
                runner.selected_configs([])

    def test_exact_three_existing_points_and_counterbalanced_session_identity(self):
        plan = policy.plan(configurations())
        self.assertEqual([(c['name'], r, p) for c, r, p in plan], [
            ('baseline', 1, 1), ('postgis-buffers', 1, 2), ('postgis-wal', 1, 3),
            ('postgis-buffers', 2, 1), ('postgis-wal', 2, 2), ('baseline', 2, 3),
            ('postgis-wal', 3, 1), ('baseline', 3, 2), ('postgis-buffers', 3, 3)])
        self.assertEqual({policy.identity(c['name'], r)['session'] for c, r, _p in plan}, set(range(1, 10)))
        for key, value in (('resources', [{'memory_limit': '1Gi'}]), ('env', [{'name': 'JDK_JAVA_OPTIONS'}]),
                           ('postgres', {'shared_buffers': '256MB'}), ('restart', False), ('skip', True)):
            changed = configurations()
            changed[1][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                policy.plan(changed)
        with self.assertRaises(RuntimeError):
            policy.plan(configurations()[1:])
        with self.assertRaises(RuntimeError):
            policy.plan(configurations() + [configurations()[0]])

    def test_actual_normalized_settings_durability_restart_and_limits_are_required(self):
        for name in policy.POINTS:
            policy.validate_state(state(name), name)
        for key in (*policy.UNITS, *policy.DURABILITY):
            changed = state('baseline')
            changed['settings'][key]['setting'] = 'off'
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                policy.validate_state(changed, 'baseline')
        for limits in ({**LIMITS, 'memory.max': str(1024**3)}, {**LIMITS, 'cpu.max': 'max 100000'},
                       {**LIMITS, 'cpu.max': '100000 100000'}):
            with self.assertRaises(RuntimeError):
                policy.validate_limits(limits)
        with self.assertRaises(RuntimeError):
            policy.validate_restart(state('baseline'), state('baseline'))
        with self.assertRaises(RuntimeError):
            policy.validate_state(state('baseline', 'replacement'), 'baseline', state('baseline'))
        with self.assertRaises(RuntimeError):
            policy.validate_version({**VERSION, 'server_version_num': '180007'})

    def test_full_blocks_bind_configuration_repeat_source_cluster_and_readback(self):
        rows = block()
        self.assertTrue(policy.complete(rows))
        for change in ('repeat', 'source', 'cluster', 'config', 'guc', 'durability', 'native', 'db', 'version'):
            changed = copy.deepcopy(rows)
            study = changed[0]['study']
            if change == 'repeat': study['repeat'] = 2
            elif change == 'source': study['source_commit'] = 'b'*40
            elif change == 'cluster': study['ephemeral_proof']['runAttempt'] = '2'
            elif change == 'config': study['config_settings']['postgres'] = {'fsync': '0'}
            elif change == 'guc': study['state_after']['settings']['work_mem']['setting'] = '8192'
            elif change == 'durability': study['state_after']['settings']['fsync']['setting'] = 'off'
            elif change == 'native': study['runner']['machine'] = 'aarch64'
            elif change == 'db': study['state_after']['database_oid'] = '16385'
            elif change == 'version': study['database_version']['postgis_version'] = '3.6.3'
            with self.subTest(change=change):
                self.assertFalse(policy.complete(changed))
        self.assertFalse(policy.complete(rows[:-1]))
        self.assertFalse(policy.complete(rows[:-1] + [rows[0]]))

    def test_report_replays_the_actual_window_and_rejects_claimed_metrics(self):
        for key, value in (('clients', 200), ('observed_stable_clients', 1), ('duration_s', 60),
                           ('counter_source', 'parent-cumulative-ramp-inclusive'),
                           ('window', None), ('messages_received', 1), ('received_messages_per_s', 1),
                           ('ping_pong_ms_mean', 0), ('self_sa_messages_sent', 0)):
            changed = record('baseline', 1)
            changed['load'][key] = value
            with self.subTest(key=key):
                self.assertIsNone(policy.record_identity(changed))
        changed = record('baseline', 1)
        changed['load']['window']['after'].pop('0')
        self.assertIsNone(policy.record_identity(changed))
        for key, value in (('self_sa_interval_s', 999), ('ping_interval_ms', 999),
                           ('ramp_deadline_s', 1), ('offered_self_sa_per_s', 999),
                           ('tester_commit', 'f'*40), ('tester_sha256', 'f'*64),
                           ('production_representative', True)):
            changed = record('baseline', 1)
            changed['study'][key] = value
            with self.subTest(study=key):
                self.assertIsNone(policy.record_identity(changed))

    def test_distinct_claimed_labels_cannot_reuse_windows_or_postmasters(self):
        rows = block()
        changed = copy.deepcopy(rows)
        changed[1]['load'] = copy.deepcopy(changed[0]['load'])
        self.assertFalse(policy.complete(changed))
        changed = block()
        for key in ('state_after_restart', 'state_before', 'state_after'):
            changed[1]['study'][key]['postmaster_start'] = changed[0]['study'][key]['postmaster_start']
        self.assertFalse(policy.complete(changed))

    def test_comparison_rejects_changed_images_resources_apps_or_container_set(self):
        for field, value in (('image_id', 'sha256:replacement'), ('memory_limit_bytes', 1024**3),
                             ('container_id', 'containerd://replacement'), ('pod_uid', 'replacement'),
                             ('restart_count', 1)):
            rows = block()
            for boundary in ('before', 'after'):
                rows[1]['capacity']['containers'][1][boundary][field] = value
            with self.subTest(field=field):
                self.assertFalse(policy.complete(rows))
        rows = block()
        rows[1]['capacity']['containers'].pop()
        self.assertFalse(policy.complete(rows))
        rows = block()
        for boundary in ('before', 'after'):
            rows[1]['capacity']['containers'][0][boundary]['image_id'] = 'sha256:new-postgres'
        self.assertFalse(policy.complete(rows))

    def test_report_never_pools_points_or_ranks_partial_blocks_and_preserves_raw(self):
        rows = block()
        original = copy.deepcopy(rows)
        text = '\n'.join(report.study_lines(rows))
        self.assertIn('| amd64 | baseline | 3 / 3 | 102.000 [101.000, 103.000]', text)
        self.assertIn('| amd64 | postgis-buffers | 3 / 3 | 202.000 [201.000, 203.000]', text)
        self.assertIn('| amd64 | postgis-wal | 3 / 3 | 302.000 [301.000, 303.000]', text)
        matched = report.cohorts(rows[:-1] + block('arm64'))[0][1]
        self.assertTrue(all(r['errors'] for r in matched if r['arch'] == 'amd64'))
        self.assertTrue(all(not r['errors'] for r in matched if r['arch'] == 'arm64'))
        self.assertTrue(all(report.point(r)['throughput'] is None for r in matched if r['arch'] == 'amd64'))
        self.assertEqual(rows, original)
        short = {'git_sha': 'a'*40, 'load': {'clients': 50, 'duration_s': 1200}}
        self.assertEqual(len(report.cohorts([rows[0], short])), 2)

    def test_workflow_is_manual_only_for_the_new_mode_and_retains_bounded_setup(self):
        source = (runner.ROOT / '.github/workflows/benchmark.yaml').read_text()
        workflow = yaml.safe_load(source)
        inputs = workflow.get('on', workflow.get(True))['workflow_dispatch']['inputs']
        self.assertIn(policy.MODE, inputs['study']['options'])
        self.assertEqual(inputs['study']['default'], 'short')
        self.assertEqual(inputs['profile']['options'], ['all', 'baseline'])
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow['jobs']['benchmark']['timeout-minutes'])
        self.assertIn('360 || 180', workflow['jobs']['benchmark']['timeout-minutes'])
        self.assertIn("inputs.study == 'synthetic-postgis' && 90 || 110", source)
        self.assertIn('Require the native runner for the PostgreSQL study', source)
        self.assertIn('tests.test_bench_postgis_study', (runner.ROOT / '.github/workflows/unit-tests.yaml').read_text())


class PostgisExecutionTests(unittest.TestCase):
    def test_new_baseline_point_really_resets_prior_gucs_and_rollout_has_its_own_deadline(self):
        run = Mock(return_value=Mock(stdout=''))
        with patch.object(runner, 'postgres_sql', return_value='') as sql, patch.object(runner, '_postgres_custom', False):
            self.assertTrue(runner.apply_postgres({'name': 'baseline', 'study': {'protocol': policy.PROTOCOL}}, run))
        self.assertEqual(sql.call_args.args[0], ['ALTER SYSTEM RESET ALL'])
        with patch.object(runner, 'kubectl') as kube:
            runner.rollout([{'workload': 'statefulset/takserver-postgresql', 'namespace': 'takserver'}], timeout_s=120)
        self.assertEqual(kube.call_args.kwargs, {'timeout': 130})
        self.assertIn('--timeout=120s', kube.call_args.args)

    def run_plan(self, fail=None, proof=None):
        calls = []
        arch = runner.arch_name()
        def one(config, *_args, **_kwargs):
            calls.append(config)
            value = record(config['study']['configuration'], config['study']['repeat'], arch)
            value['study'].update(config['study'])
            if fail is not None and len(calls) == fail:
                value['errors'] = ['fixture failed the measurement']
            return value
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'BENCH_NATIVE_ARCH': arch}), \
                patch.object(runner, 'git_sha', return_value='a'*40), \
                patch.object(runner, 'one', side_effect=one), \
                contextlib.redirect_stderr(io.StringIO()):
            result = runner.run_postgis_study(configurations(), Path(directory),
                runner.ROOT / 'bench/fixtures/tester/tak_tester.py.txt', 1, PROOF, proof or (lambda: dict(PROOF)))
        return result, calls

    def test_executor_runs_exact_plan_and_stops_at_first_failure_or_changed_proof(self):
        result, calls = self.run_plan()
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 9)
        self.assertEqual([c['study']['session'] for c in calls], list(range(1, 10)))
        self.assertTrue(all(c['duration_s'] == 1200 for c in calls))
        failed, calls = self.run_plan(fail=3)
        self.assertEqual(failed, 1)
        self.assertEqual(len(calls), 3)
        with self.assertRaisesRegex(RuntimeError, 'proof changed'):
            self.run_plan(proof=Mock(side_effect=[dict(PROOF), {**PROOF, 'runAttempt': '2'}]))

    def test_existing_records_and_wrong_native_runner_stop_before_work(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'one') as one:
            root = Path(directory)
            path = root / runner.arch_name() / 'baseline-r1.json'
            path.parent.mkdir()
            path.write_text('retained original')
            with patch.dict(os.environ, {'BENCH_NATIVE_ARCH': runner.arch_name()}), self.assertRaisesRegex(RuntimeError, 'overwrite'):
                runner.run_postgis_study(configurations(), root, Path('fixture'), 1, PROOF, lambda: PROOF)
            self.assertEqual(path.read_text(), 'retained original')
            with patch.dict(os.environ, {'BENCH_NATIVE_ARCH': 'unknown'}), self.assertRaisesRegex(RuntimeError, 'native runner'):
                runner.run_postgis_study(configurations(), root, Path('fixture'), 1, PROOF, lambda: PROOF)
            one.assert_not_called()

    def collect(self, drift=False):
        real_one = runner.one
        def experimental(config, *args, **kwargs):
            study = record('baseline', 1)['study']
            study = {k: v for k, v in study.items() if not k.startswith(('state_', 'cgroup_')) and k != 'database_version'}
            return real_one({**config, 'study': study}, *args, **kwargs)
        before, after = state('baseline', 'previous'), state('baseline')
        final = copy.deepcopy(after)
        if drift:
            final['settings']['fsync']['setting'] = 'off'
        with patch.object(runner, 'one', side_effect=experimental), \
                patch.object(runner, 'postgis_state', side_effect=[before, after, copy.deepcopy(after), final]), \
                patch.object(runner, 'rollout', return_value=1):
            return baseline_tests.CollectorIntegrationTests().collect()

    def test_real_collector_callbacks_accept_matching_readbacks_and_reject_durability_drift(self):
        value = self.collect()
        self.assertEqual(value['errors'], [])
        self.assertEqual(value['study']['state_after']['settings']['fsync']['setting'], 'on')
        drifted = self.collect(drift=True)
        self.assertTrue(any('durability' in error for error in drifted['errors']))
        self.assertIsNone(policy.record_identity(drifted))


if __name__ == '__main__':
    unittest.main()
