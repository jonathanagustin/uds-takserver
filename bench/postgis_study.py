# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Fixed, counterbalanced PostgreSQL experiment policy; no workload dispatch."""

import hashlib
import json
import os
from pathlib import Path
import re
import statistics

MODE = 'synthetic-postgis'
PROFILE = 'postgis'
PROTOCOL = 'synthetic-postgis-v1'
TESTER_COMMIT = '5187abd46d827d37cfc5708805eced197a837e49'
BLOCK_REJECTION = 'PostgreSQL comparison block incomplete or identity-invalid; not ranked'
POINTS = ('baseline', 'postgis-buffers', 'postgis-wal')
SETTINGS = {
    'baseline': {},
    'postgis-buffers': {'shared_buffers': '128MB', 'work_mem': '4MB',
                        'effective_cache_size': '384MB', 'max_wal_size': '256MB', 'checkpoint_timeout': '5min'},
    'postgis-wal': {'shared_buffers': '64MB', 'work_mem': '4MB',
                    'effective_cache_size': '256MB', 'max_wal_size': '128MB', 'checkpoint_timeout': '15min'},
}
# pg_settings exposes normalized values and their units, rather than requiring
# string equality between equivalent SHOW values such as 4MB and 4096kB.
NORMALIZED = {
    'baseline': (16384, 4096, 524288, 1024, 300),
    'postgis-buffers': (16384, 4096, 49152, 256, 300),
    'postgis-wal': (8192, 4096, 32768, 128, 900),
}
UNITS = {'shared_buffers': '8kB', 'work_mem': 'kB', 'effective_cache_size': '8kB',
         'max_wal_size': 'MB', 'checkpoint_timeout': 's'}
DURABILITY = ('fsync', 'full_page_writes', 'synchronous_commit')
READBACK_SQL = """
SELECT json_build_object(
  'postmaster_start', pg_postmaster_start_time()::text,
  'database_oid', (SELECT oid::text FROM pg_database WHERE datname = current_database()),
  'system_identifier', (SELECT system_identifier::text FROM pg_control_system()),
  'database_bytes', pg_database_size(current_database()),
  'settings', (SELECT json_object_agg(name, json_build_object('setting', setting, 'unit', COALESCE(unit, '')))
               FROM pg_settings WHERE name IN
               ('shared_buffers', 'work_mem', 'effective_cache_size', 'max_wal_size', 'checkpoint_timeout',
                'fsync', 'full_page_writes', 'synchronous_commit'))
)
""".strip()


def enabled(environment=None):
    return (os.environ if environment is None else environment).get('BENCH_STUDY') == MODE


def require(condition, message):
    if not condition:
        raise RuntimeError('PostgreSQL study: ' + message)


def configurations(configs):
    selected = {name: [c for c in configs if c.get('name') == name] for name in POINTS}
    result = []
    for name in POINTS:
        require(len(selected[name]) == 1, 'requires one source configuration for ' + name)
        config = selected[name][0]
        validate_config(config)
        result.append(config)
    return result


def validate_config(config):
    name = config.get('name')
    require(name in POINTS, 'unknown configuration')
    require(not config.get('skip') and not config.get('env') and not config.get('resources'),
            'configuration must not skip or override workload resources/environment')
    require((config.get('postgres') or {}) == SETTINGS[name], 'configuration settings differ from reviewed point')
    require(config.get('restart') is (name != 'baseline'), 'configuration restart policy differs')


def plan(configs):
    configs = configurations(configs)
    # Each setting occupies each ordinal position once. Database contents and
    # host/filesystem caches remain shared; counterbalancing does not erase them.
    return [(configs[(position + repeat - 1) % 3], repeat, position + 1)
            for repeat in range(1, 4) for position in range(3)]


def identity(name, repeat):
    require(name in POINTS and type(repeat) is int and repeat in (1, 2, 3), 'invalid configuration/repeat identity')
    position = (POINTS.index(name) - repeat + 1) % 3 + 1
    return {'configuration': name, 'repeat': repeat, 'order_position': position,
            'session': (repeat - 1) * 3 + position}


def validate_limits(limits):
    require(limits.get('memory.max') == str(512 * 1024**2), 'database memory limit must remain 512Mi')
    match = re.fullmatch(r'([0-9]+) ([0-9]+)', limits.get('cpu.max', ''))
    require(match is not None and int(match[1]) > 0 and int(match[1]) * 2 == int(match[2]),
            'database CPU limit must remain 500m')


def validate_version(version):
    require(version == {'server_version_num': '180006', 'postgis_version': '3.6.4'},
            'database version differs from current reviewed source')


def validate_state(state, name, reference=None):
    require(name in POINTS and isinstance(state, dict), 'invalid database state')
    require(isinstance(state.get('postmaster_start'), str) and bool(state['postmaster_start']),
            'database postmaster identity unavailable')
    require(re.fullmatch(r'[1-9][0-9]*', state.get('database_oid', '')) is not None,
            'database identity unavailable')
    require(re.fullmatch(r'[1-9][0-9]*', state.get('system_identifier', '')) is not None,
            'database system identity unavailable')
    require(type(state.get('database_bytes')) is int and state['database_bytes'] > 0,
            'database size unavailable')
    settings = state.get('settings') or {}
    require(set(settings) == set(UNITS) | set(DURABILITY), 'missing or unexpected database setting readback')
    for (setting, unit), value in zip(UNITS.items(), NORMALIZED[name]):
        require(settings[setting] == {'setting': str(value), 'unit': unit},
                'actual normalized ' + setting + ' differs from reviewed point')
    for setting in DURABILITY:
        require(settings[setting] == {'setting': 'on', 'unit': ''}, 'durability setting must remain on')
    if reference is not None:
        require(state['database_oid'] == reference['database_oid'] and state['system_identifier'] == reference['system_identifier'] and
                state['postmaster_start'] == reference['postmaster_start'],
                'database identity changed during a session')


def validate_restart(before, after):
    require(before.get('database_oid') == after.get('database_oid') and
            before.get('system_identifier') == after.get('system_identifier'), 'database changed during restart')
    require(bool(before.get('postmaster_start')) and before['postmaster_start'] != after.get('postmaster_start'),
            'database postmaster did not restart for this session')


def runtime_identity(record):
    """Reuse retained inventories; only PostgreSQL may change incarnation between points."""
    capacity = record.get('capacity') or {}
    require(not capacity.get('errors'), 'container inventory has collection errors')
    rows = capacity.get('containers') or []
    fields = ('pod_uid', 'container_id', 'image_id', 'memory_limit_bytes', 'restart_count')
    result, keys = [], set()
    for row in rows:
        key = (row.get('namespace'), row.get('pod'), row.get('container'))
        require(all(type(value) is str and value for value in key) and key not in keys,
                'container inventory identity missing or duplicated')
        keys.add(key)
        before, after = row.get('before') or {}, row.get('after') or {}
        require(all(before.get(field) == after.get(field) for field in fields),
                'container identity changed within a session')
        require(all(type(after.get(field)) is str and after[field] for field in fields[:3]) and
                type(after.get('memory_limit_bytes')) is int and after['memory_limit_bytes'] > 0 and
                type(after.get('restart_count')) is int and after['restart_count'] >= 0,
                'container runtime evidence missing')
        require(all(boundary.get('ready') is True and 'OOMKilled' not in
                    (boundary.get('termination_reason'), boundary.get('last_termination_reason'))
                    for boundary in (before, after)), 'container was unready or has OOM evidence')
        item = [*key, after['image_id'], after['memory_limit_bytes']]
        if key[0] == 'takserver' and key[2] == 'postgresql':
            require(after['memory_limit_bytes'] == 512 * 1024**2, 'database inventory limit differs from 512Mi')
        else:
            item += [after['pod_uid'], after['container_id'], after['restart_count']]
        result.append(item)
    names = [(key[0], key[2]) for key in keys]
    require(names.count(('takserver', 'postgresql')) == 1 and
            all(('takserver', name) in names for name in ('takserver-api', 'takserver-messaging')),
            'database and serving application inventory required')
    return sorted(result)


def record_identity(record):
    study = record.get('study') or {}
    name, repeat = study.get('configuration'), study.get('repeat')
    try:
        expected = identity(name, repeat)
        require(all(study.get(k) == v for k, v in expected.items()), 'session order identity differs')
        require(record.get('config') == f'{name}-r{repeat}', 'record filename identity differs')
        require(study.get('protocol') == PROTOCOL and study.get('repeat_count') == 3 and
                study.get('requested_clients') == 50 and study.get('warmup_s') == 300 and
                study.get('measurement_s') == 1200, 'study protocol differs')
        require(study.get('ramp_deadline_s') == 120 and study.get('self_sa_interval_s') == 5 and
                study.get('offered_self_sa_per_s') == 10 and study.get('ping_interval_ms') == 5000 and
                study.get('production_representative') is False, 'fixed workload envelope differs')
        require(study.get('source_commit') == record.get('git_sha') and
                re.fullmatch('[0-9a-f]{40}', record.get('git_sha', '')) is not None, 'source identity differs')
        require(re.fullmatch('[0-9a-f]{64}', study.get('config_sha256', '')) is not None and
                re.fullmatch('[0-9a-f]{64}', study.get('tester_sha256', '')) is not None,
                'configuration/tester binding missing')
        tester = Path(__file__).with_name('fixtures') / 'tester/tak_tester.py.txt'
        require(study.get('tester_commit') == TESTER_COMMIT and
                study['tester_sha256'] == hashlib.sha256(tester.read_bytes()).hexdigest(),
                'pinned tester source differs')
        config = study.get('config_settings')
        require(isinstance(config, dict) and config.get('name') == name and config.get('duration_s') == 1200,
                'configuration source body differs')
        validate_config(config)
        require(hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest() == study['config_sha256'],
                'configuration digest differs')
        require(record.get('arch') in ('amd64', 'arm64') and
                (study.get('runner') or {}).get('machine') in
                ({'x86_64', 'amd64'} if record['arch'] == 'amd64' else {'aarch64', 'arm64'}),
                'native runner identity differs')
        proof = study.get('ephemeral_proof') or {}
        require(proof.get('repository') == 'jonathanagustin/uds-takserver' and
                proof.get('context') == 'k3d-uds' and proof.get('sourceSHA') == record['git_sha'] and
                all(proof.get(k) for k in ('runID', 'runAttempt', 'clusterUID', 'nodeContainerID', 'markerUID', 'markerResourceVersion')),
                'disposable cluster proof missing or mismatched')
        for key in ('state_after_restart', 'state_before', 'state_after'):
            validate_state(study.get(key), name, study.get('state_after_restart'))
        validate_restart(study.get('state_before_restart') or {}, study['state_after_restart'])
        validate_limits(study.get('cgroup_before') or {})
        validate_limits(study.get('cgroup_after') or {})
        require(study['cgroup_before'] == study['cgroup_after'], 'database limits changed')
        validate_version(study.get('database_version'))
        # Import at validation time: sustained imports this mode's policy, but
        # the window decoder remains the one authoritative measured contract.
        from bench import sustained
        load = record.get('load') or {}
        require(load.get('clients') == 50, 'measured client count differs')
        measured = sustained.parse_window('BENCH_WINDOW_JSON:' + json.dumps(load.get('window')))
        require(all(load.get(key) == value for key, value in measured.items()),
                'retained load differs from recomputed measured window')
        runtime_identity(record)
        return name
    except (RuntimeError, ValueError, TypeError, KeyError, AttributeError, OSError):
        return None


def complete(records):
    """A comparable native-architecture block needs all nine distinct sessions."""
    if len(records) != 9 or any(r.get('errors') or record_identity(r) is None for r in records):
        return False
    studies = [r['study'] for r in records]
    if {s['session'] for s in studies} != set(range(1, 10)):
        return False
    if len({r.get('git_sha') for r in records}) != 1 or len({r.get('arch') for r in records}) != 1:
        return False
    first = studies[0]
    if any(s['ephemeral_proof'] != first['ephemeral_proof'] or s['tester_sha256'] != first['tester_sha256'] for s in studies):
        return False
    if len({(s['state_after']['system_identifier'], s['state_after']['database_oid']) for s in studies}) != 1:
        return False
    runtime = runtime_identity(records[0])
    if any(runtime_identity(record) != runtime for record in records[1:]):
        return False
    ordered = sorted(studies, key=lambda value: value['session'])
    if len({s['state_after_restart']['postmaster_start'] for s in ordered}) != 9:
        return False
    by_session = sorted(records, key=lambda value: value['study']['session'])
    for previous, following in zip(by_session, by_session[1:]):
        before, after = previous['load']['window'], following['load']['window']
        if before['end_monotonic'] > after['ready_monotonic']:
            return False
        if previous['study']['state_after']['postmaster_start'] != following['study']['state_before_restart']['postmaster_start']:
            return False
    return all(len({s['config_sha256'] for s in studies if s['configuration'] == name}) == 1 for name in POINTS)


def qualify(records):
    """Copies carry report-only rejection reasons; original measurement bytes stay intact."""
    result = []
    for arch in sorted({str(r.get('arch')) for r in records}):
        rows = [r for r in records if str(r.get('arch')) == arch]
        valid = complete(rows)
        for record in rows:
            candidate = dict(record)
            if not valid:
                candidate['errors'] = [*(candidate.get('errors') or []),
                                       BLOCK_REJECTION]
            result.append(candidate)
    return result


def report_lines(records):
    lines = ['### Counterbalanced PostgreSQL session repeats', '',
             'Each native runner retains one database and shared data/filesystem-cache history. '
             'Every session resets the five GUCs and restarts PostgreSQL, then warms 50 live clients for at least 300 s '
             'before 1200 s measured. Cyclic configuration order balances ordinal position, not all carryover effects. '
             'These are session repeats, not independent installs, causal tuning effects or an ISA ranking. '
             'Incomplete or identity-invalid nine-session architecture blocks cannot enter the front.', '',
             '| Architecture | Configuration | Valid repeats / planned | Received messages/s median [range] | Mean ping-pong ms median [range] |',
             '| --- | --- | --- | --- | --- |']
    for arch in sorted({str(r.get('arch')) for r in records}):
        block = [r for r in records if str(r.get('arch')) == arch]
        for name in POINTS:
            valid = [r for r in block if record_identity(r) == name and
                     not [error for error in (r.get('errors') or []) if error != BLOCK_REJECTION]]
            def spread(key):
                values = [(r.get('load') or {}).get(key) for r in valid]
                if not complete(block) or len(values) != 3 or any(type(v) not in (int, float) or not 0 <= v < float('inf') for v in values):
                    return 'incomplete'
                return f'{statistics.median(values):.3f} [{min(values):.3f}, {max(values):.3f}]'
            lines.append(f'| {arch} | {name} | {len(valid)} / 3 | {spread("received_messages_per_s")} | {spread("ping_pong_ms_mean")} |')
    return lines + ['']
