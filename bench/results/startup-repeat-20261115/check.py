#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline integrity and independent timestamp replay for this additional AMD64 pair."""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
APPS = ('takserver-api', 'takserver-messaging')
FILES = {'bindings.json', 'cold-begin.json', 'cold.json', 'warm-begin.json', 'warm.json', 'pair.json'}
SOURCES = {'source/'+name for name in ('amd64-startup.yaml.txt',
           'startup_trials.py.txt', 'jvm_metrics.py.txt', 'phases.py.txt')}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())


def unique(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, 'duplicate JSON key')
        value[key] = item
    return value


def read(path, expected=None):
    require(not path.is_symlink() and path.is_file(), 'regular evidence file required')
    with path.open('rb') as stream:
        raw = stream.read(1024*1024+1)
    require(len(raw) <= 1024*1024 and (expected is None or sha(raw) == expected), 'evidence hash/size differs')
    return raw


def decode(raw):
    def invalid(_value):
        raise ValueError('nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)


def epoch_us(text):
    value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    require(value.tzinfo is not None, 'timezone missing')
    delta = value-datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days*86400+delta.seconds)*1000000+delta.microseconds


def numeric(value):
    require(type(value) in (int, float) and math.isfinite(value) and value >= 0, 'invalid duration')
    return value


def phase_row(arch, mode, app, phase):
    evidence, observation = phase['evidence'], phase['jvm_observation']
    require(observation['available'] is True and len(observation['samples']) == 2, 'JVM evidence unavailable')
    started = observation['jvm_start_time_unix_ms']
    require(type(started) is int and started > 0 and
            all(sample['jvm_start_time_unix_ms'] == started for sample in observation['samples']), 'JVM epoch differs')
    identity = observation['identity']
    require(all((epoch_us(evidence[key]) == epoch_us(value) if key == 'container_started_at'
                 else evidence[key] == value) for key, value in identity.items()) and
            identity['container_name'] == app and identity['restart_count'] == 0, 'phase identity differs')
    container, marker, ready = (epoch_us(evidence[key]) for key in
                                 ('container_started_at', 'startup_log_at', 'ready_at'))
    require(container <= started*1000 <= marker <= ready and
            epoch_us(evidence['jvm_started_at']) == started*1000, 'timestamp ordering differs')
    # JVM joins use millisecond epoch values; phase durations round to 3 places.
    expected = {'jvm_to_log_s': (marker//1000-started)/1000,
                'container_to_log_s': round((marker-container)/1000000, 3),
                'log_to_ready_s': round((ready-marker)/1000000, 3)}
    for name, value in expected.items():
        require(numeric(phase[name]) == value, 'reported phase duration differs: '+name)
    intervals = evidence['init_intervals']
    names = [item['name'] for item in intervals]
    require(len(names) == len(set(names)) and names == phase['declared_init_names'], 'init inventory differs')
    inits = [{'name': item['name'], 'seconds': round((epoch_us(item['finished_at'])-
              epoch_us(item['started_at']))/1000000, 3)} for item in intervals]
    require(inits == phase['inits'], 'reported init durations differ')
    by_name = {item['name']: numeric(item['seconds']) for item in inits}
    require(set(by_name) == {'wait-deps', 'import-trust', 'render-oidc'}, 'unexpected init inventory')
    pull = phase['image_pull_s']
    return [arch, mode, app, None if pull is None else numeric(pull), by_name['wait-deps'],
            by_name['import-trust'], by_name['render-oidc'], (started*1000-container)/1000000,
            expected['jvm_to_log_s'], expected['log_to_ready_s']]


def prior_records():
    import importlib.util
    prior = ROOT.parent/'startup-pairs-20261114'
    spec = importlib.util.spec_from_file_location('prior_startup_evidence', prior/'check.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    module.verify()  # Preserve the earlier qualified evidence and its source hashes.
    manifest = decode(read(prior/'sources.json'))
    dataset = next(row for row in manifest['datasets'] if row['architecture']=='amd64')
    return tuple(decode(read(prior/'records/amd64'/(mode+'.json'),dataset['files'][mode+'.json']))
                 for mode in ('cold','warm'))


def verify_independent(cold, warm):
    previous = prior_records()
    require(all(cold['begin']['caller']['run_id'] != item['begin']['caller']['run_id'] and
                cold['begin']['cohort'] != item['begin']['cohort'] for item in previous),
            'execution reuses earlier qualification')
    old_pods = {item['phases'][app]['evidence']['pod_uid'] for item in previous for app in APPS}
    old_jvms = {(app,item['phases'][app]['jvm_observation']['jvm_start_time_unix_ms'])
                for item in previous for app in APPS}
    for item in (cold,warm):
        for app in APPS:
            require(item['phases'][app]['evidence']['pod_uid'] not in old_pods and
                    (app,item['phases'][app]['jvm_observation']['jvm_start_time_unix_ms']) not in old_jvms,
                    'incarnation reuses earlier qualification')


def verify(root=ROOT):
    manifest = decode(read(root/'sources.json'))
    require(manifest['schema'] == 1 and set(manifest['source_files']) == SOURCES, 'source manifest differs')
    for name, checksum in manifest['source_files'].items():
        read(root/name, checksum)
    job = decode(read(root/'job.json',manifest['job_metadata_sha256']))
    require(job['job_id']==132972283112 and job['run_id']==43766367603 and job['run_attempt']==1 and
            job['head_sha']=='254c30c4142dae05ee3e18b54dc7c991b924fda8' and job['status']=='completed' and
            job['conclusion']=='success' and job['labels']==['ubuntu-22.04'], 'execution metadata differs')
    for name in ('Verify native architecture before setup or cluster work',
                 'Refuse a remote daemon or existing cluster before mutation',
                 'Restore the slim Core baseline without the application',
                 "Bind this job's disposable cluster and fit the runner",
                 'Acquire only the matching published application package',
                 'Begin the cold application boundary','Deploy and validate the normal application bundle',
                 'Finish the cold observation','Begin the warm application boundary',
                 'Restart only the two measured application Deployments','Finish and validate the warm pair'):
        steps=[step for step in job['steps'] if step['name']==name]
        require(len(steps)==1 and steps[0]['status']=='completed' and steps[0]['conclusion']=='success',
                'required fresh execution step differs')
    failure = manifest['incomplete_qualifications']
    require(len(failure)==1 and failure[0]['architecture']=='arm64' and failure[0]['accepted_pair'] is False and
            failure[0]['failed_step']=='Restore the slim Core baseline without the application',
            'incomplete qualification inventory differs')
    arm = decode(read(root/failure[0]['evidence_file'],failure[0]['evidence_sha256']))
    require(arm['id']==132972283122 and arm['run_id']==43766367603 and arm['run_attempt']==1 and
            arm['head_sha']==job['head_sha'] and arm['labels']==['ubuntu-22.04-arm'], 'ARM failure metadata differs')
    failed = [step for step in arm['steps'] if step['name']==failure[0]['failed_step']]
    require(len(failed)==1 and failed[0]['status']=='completed' and failed[0]['conclusion']=='failure',
            'ARM restore failure differs')
    for name in ('Begin the cold application boundary','Finish the cold observation','Finish and validate the warm pair'):
        steps=[step for step in arm['steps'] if step['name']==name]
        require(len(steps)==1 and steps[0]['status']=='completed' and steps[0]['conclusion']=='skipped',
                'unexpected ARM application measurement')
    datasets = manifest['datasets']
    require(len(datasets) == 1 and {row['architecture'] for row in datasets} == {'amd64'}, 'cohort inventory differs')
    rows, spans, clusters = [], [], set()
    for dataset in datasets:
        require(dataset['artifact_id']==14183282882 and dataset['archive_bytes']==10335 and
                dataset['archive_sha256']=='962d59dba625013fab388bdb414c4f831854d16d239e0120cc7f072c50b5c825' and
                dataset['run_id']==job['run_id'] and dataset['attempt']==job['run_attempt'] and
                dataset['job_id']==job['job_id'] and dataset['source_commit']==job['head_sha'] and
                dataset['workflow_id']==417819272, 'published archive metadata differs')
        arch = dataset['architecture']
        require(set(dataset['files']) == FILES and dataset['workflow_snapshot'] == 'source/'+arch+'-startup.yaml.txt', 'payload inventory differs')
        records = {name: decode(read(root/'records'/arch/name, checksum)) for name, checksum in dataset['files'].items()}
        cold, warm, pair = (records[name+'.json'] for name in ('cold', 'warm', 'pair'))
        require(pair == {'schema': 1, 'accepted': True, 'cold_sha256': canonical(cold),
                'warm_sha256': canonical(warm),
                'scope': 'one independent cold/warm application pair; no CDS, probe or class-loading-duration claim'}, 'pair binding differs')
        caller = {'source_sha': dataset['source_commit'], 'run_id': str(dataset['run_id']), 'run_attempt': str(dataset['attempt'])}
        episode = []
        for mode, record in (('cold', cold), ('warm', warm)):
            initial = record['begin']
            require(record['kind'] == 'startup-finish' and record['mode'] == mode and
                    initial == records[mode+'-begin.json'] and record['begin_sha256'] == canonical(initial), 'begin binding differs')
            require(initial['caller'] == caller and initial['bindings'] == records['bindings.json'] and
                    initial['bindings']['source'] == sha(dataset['source_commit'].encode()) and
                    initial['bindings']['workflow'] == manifest['source_files'][dataset['workflow_snapshot']] and
                    initial['collector_sha256'] == manifest['source_files']['source/startup_trials.py.txt'] and
                    initial['jvm_observer_sha256'] == manifest['source_files']['source/jvm_metrics.py.txt'], 'source/run/workflow binding differs')
            require(set(record['phases']) == set(APPS) and
                    all(node['architecture'] == arch for node in record['snapshot']['platform']), 'application/architecture differs')
            for app in APPS:
                rows.append(phase_row(arch, mode, app, record['phases'][app]))
            span = (max(epoch_us(record['phases'][app]['evidence']['ready_at']) for app in APPS)-
                    min(epoch_us(record['pod_created_at'][app]) for app in APPS))/1000000
            require(numeric(record['pod_creation_to_ready_span_s']) == span and
                    record['warm_s'] == (span if mode == 'warm' else None), 'reported episode span differs')
            episode.append(span)
        require(not cold['begin']['snapshot']['identities'], 'cold boundary contains applications')
        for name in ('cluster_uid', 'platform', 'config_sha256', 'container_images'):
            require(cold['snapshot'][name] == warm['begin']['snapshot'][name] == warm['snapshot'][name], 'pair environment differs')
        require(cold['snapshot']['identities'] == warm['begin']['snapshot']['identities'], 'warm boundary differs')
        for app in APPS:
            require(cold['phases'][app]['evidence']['pod_uid'] != warm['phases'][app]['evidence']['pod_uid'] and
                    cold['phases'][app]['jvm_observation']['jvm_start_time_unix_ms'] <
                    warm['phases'][app]['jvm_observation']['jvm_start_time_unix_ms'], 'incarnation reused')
        verify_independent(cold,warm)
        clusters.add(cold['snapshot']['cluster_uid'])
        spans.append([arch, *episode])
    require(len(rows) == 4 and len(clusters) == 1, 'observation/cluster grain differs')
    return rows, spans


def tables(rows, spans):
    def cell(value):
        return f'{value:.3f}' if type(value) in (int, float) else 'not measured' if value is None else str(value)
    def table(head, data):
        return '\n'.join(['| '+' | '.join(head)+' |', '| '+' | '.join(['---']*len(head))+' |',
                          *['| '+' | '.join(map(cell, row))+' |' for row in data]])
    return table(['Architecture', 'Boundary', 'Application', 'Paired pull (s)', 'Wait deps', 'Trust', 'OIDC',
                  'Container→JVM', 'JVM→log', 'Log→Ready'], rows)+'\n\n'+table(
                  ['Architecture', 'Cold Pod span (s)', 'Warm Pod span (s)'], spans)


if __name__ == '__main__':
    require(sys.argv[1:] in ([], ['--table']), 'use no arguments or --table')
    rendered = tables(*verify())
    if sys.argv[1:]:
        print(rendered)
    else:
        require(rendered in (ROOT/'README.md').read_text(), 'readout table differs')
        print('Verified original hashes, source bindings, eight observations and independent timestamp calculations.')
