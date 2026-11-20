#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Replay this artifact's integrity, bindings and independent phase calculations."""
import importlib.util
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
HELPER = 'bench/results/startup-repeat-20261115/check.py'
SPEC = importlib.util.spec_from_file_location('prior_startup_readout', REPO / HELPER)
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)
SOURCE = 'd044a34b3278f388014e599f88253d0097c83633'


def verify():
    manifest = V.decode(V.read(ROOT / 'sources.json'))
    V.require(manifest['schema'] == 1 and manifest['source_commit'] == SOURCE, 'source differs')
    V.require(manifest['independent_checker'] == {
        'path': HELPER, 'sha256': V.sha(V.read(REPO / HELPER))}, 'independent checker differs')
    V.require(set(manifest['source_files']) == V.SOURCES and
              set(manifest['metadata_files']) == {'run.json', 'job.json', 'artifact.json'}, 'inventory differs')
    for name, checksum in manifest['source_files'].items():
        V.read(ROOT / name, checksum)
    metadata = {name: V.decode(V.read(ROOT / name, checksum))
                for name, checksum in manifest['metadata_files'].items()}
    run, job, artifact = (metadata[name] for name in ('run.json', 'job.json', 'artifact.json'))
    V.require(run['id'] == 44398702032 and run['run_attempt'] == 1 and run['head_sha'] == SOURCE and
              run['event'] == 'workflow_dispatch' and run['workflow_id'] == 417819272 and
              run['path'] == '.github/workflows/startup.yaml' and
              run['status'] == 'completed' and run['conclusion'] == 'success', 'run differs')
    V.require(job['job_id'] == 135135600561 and job['run_id'] == run['id'] and job['run_attempt'] == 1 and
              job['head_sha'] == SOURCE and job['status'] == 'completed' and
              job['conclusion'] == 'success' and job['labels'] == ['ubuntu-22.04'], 'job differs')
    for name in ('Verify native architecture before setup or cluster work',
                 'Refuse a remote daemon or existing cluster before mutation',
                 'Acquire only the matching published application package',
                 'Restore the slim Core baseline without the application',
                 "Bind this job's disposable cluster and fit the runner",
                 'Begin the cold application boundary', 'Deploy and validate the normal application bundle',
                 'Finish the cold observation', 'Begin the warm application boundary',
                 'Restart only the two measured application Deployments', 'Finish and validate the warm pair'):
        steps = [step for step in job['steps'] if step['name'] == name]
        V.require(len(steps) == 1 and steps[0]['status'] == 'completed' and
                  steps[0]['conclusion'] == 'success', 'executed step differs')
    dataset = manifest['dataset']
    V.require(dataset['architecture'] == 'amd64' and dataset['run_id'] == run['id'] and
              dataset['attempt'] == 1 and dataset['job_id'] == job['job_id'] and
              dataset['workflow_id'] == run['workflow_id'] and dataset['artifact_id'] == 14472627384 and
              dataset['archive_bytes'] == 10790 and dataset['archive_sha256'] ==
              'ebcbd0e5d2f4f30417dc99a9e470e23fa26f2b40d2ef9359a117e4d12f71c697', 'dataset differs')
    V.require(artifact['id'] == dataset['artifact_id'] and artifact['expired'] is False and
              artifact['name'] == dataset['artifact_name'] == 'startup-pair-amd64-44398702032-1' and
              artifact['size_in_bytes'] == dataset['archive_bytes'] and
              artifact['digest'] == 'sha256:' + dataset['archive_sha256'] and
              artifact['workflow_run']['id'] == run['id'] and
              artifact['workflow_run']['head_sha'] == SOURCE and
              artifact['workflow_run']['repository_id'] == 1427088300 and
              artifact['workflow_run']['head_repository_id'] == 1427088300, 'artifact metadata differs')
    V.verify_original_archive(ROOT, dataset)
    V.require(set(dataset['files']) == V.FILES, 'record inventory differs')
    records = {name: V.decode(V.read(ROOT / 'records/amd64' / name, checksum))
               for name, checksum in dataset['files'].items()}
    cold, warm, pair = (records[name + '.json'] for name in ('cold', 'warm', 'pair'))
    V.require(pair == {'schema': 1, 'accepted': True, 'cold_sha256': V.canonical(cold),
                      'warm_sha256': V.canonical(warm), 'scope':
                      'one independent cold/warm application pair; no CDS, probe or class-loading-duration claim'},
              'pair differs')
    bindings = records['bindings.json']
    V.require(set(bindings) == {'source', 'package', 'package_archive', 'package_manifest', 'workflow'} and
              all(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value) for value in bindings.values()) and
              bindings['source'] == V.sha(SOURCE.encode()) and bindings['workflow'] ==
              manifest['source_files']['source/amd64-startup.yaml.txt'], 'bindings differ')
    rows, spans = [], []
    for mode, record in (('cold', cold), ('warm', warm)):
        initial = record['begin']
        V.require(initial == records[mode + '-begin.json'] and record['begin_sha256'] == V.canonical(initial) and
                  initial['caller'] == {'source_sha': SOURCE, 'run_id': str(run['id']), 'run_attempt': '1'} and
                  initial['bindings'] == bindings and initial['collector_sha256'] ==
                  manifest['source_files']['source/startup_trials.py.txt'] and initial['jvm_observer_sha256'] ==
                  manifest['source_files']['source/jvm_metrics.py.txt'], 'record source differs')
        V.require(record['mode'] == mode and set(record['phases']) == set(V.APPS) and
                  all(node['architecture'] == 'amd64' for node in record['snapshot']['platform']), 'scope differs')
        for app in V.APPS:
            rows.append(V.phase_row('amd64', mode, app, record['phases'][app]))
        span = (max(V.epoch_us(record['phases'][app]['evidence']['ready_at']) for app in V.APPS) -
                min(V.epoch_us(record['pod_created_at'][app]) for app in V.APPS)) / 1000000
        V.require(record['pod_creation_to_ready_span_s'] == span and
                  record['warm_s'] == (span if mode == 'warm' else None), 'span differs')
        spans.append(span)
    V.require(not cold['begin']['snapshot']['identities'] and
              cold['snapshot']['identities'] == warm['begin']['snapshot']['identities'] and
              V.epoch_us(cold['observed_finished_at']) <= V.epoch_us(warm['begin']['observed_started_at']),
              'boundary differs')
    for key in ('cluster_uid', 'platform', 'config_sha256', 'container_images'):
        V.require(cold['snapshot'][key] == warm['begin']['snapshot'][key] == warm['snapshot'][key], 'environment differs')
    for app in V.APPS:
        a, b = (record['phases'][app] for record in (cold, warm))
        V.require(a['evidence']['pod_uid'] != b['evidence']['pod_uid'] and
                  warm['begin']['jvm_starts'][app] == a['jvm_observation']['jvm_start_time_unix_ms'] <
                  b['jvm_observation']['jvm_start_time_unix_ms'], 'incarnation differs')
    V.verify_independent(cold, warm)
    V.verify()  # Keep the additional historical pair and its retained failures valid.
    previous = [V.decode(V.read(V.ROOT / 'records/amd64' / (mode + '.json')))
                for mode in ('cold', 'warm')]
    old_pods = {record['phases'][app]['evidence']['pod_uid'] for record in previous for app in V.APPS}
    old_jvms = {(app, record['phases'][app]['jvm_observation']['jvm_start_time_unix_ms'])
                for record in previous for app in V.APPS}
    for record in (cold, warm):
        for app in V.APPS:
            V.require(record['phases'][app]['evidence']['pod_uid'] not in old_pods and
                      (app, record['phases'][app]['jvm_observation']['jvm_start_time_unix_ms']) not in old_jvms,
                      'historical incarnation reused')
    return rows, [['amd64', *spans]]


if __name__ == '__main__':
    V.require(sys.argv[1:] in ([], ['--table']), 'use no arguments or --table')
    table = V.tables(*verify())
    if sys.argv[1:]:
        print(table)
    else:
        V.require(table in (ROOT / 'README.md').read_text(), 'readout differs')
        print('Verified exact artifact bytes, source bindings, fresh incarnations and four phase observations.')
