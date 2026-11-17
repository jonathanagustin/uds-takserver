#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline validation of a declared, bounded series of original startup ZIPs.

Run ``python -m bench.startup_series --plan plan.json --output report.json``.
The plan has schema/kind/source_sha/workflow_sha256/repetitions/architectures/
entries. Every expected architecture/repetition slot must be present, including
failed or missing attempts. Entry fields are arch/repetition/status/run_id/
run_attempt/artifact_id/artifact_name/archive_sha256/archive_path. Status is
candidate, failed or missing; unavailable acquisition fields are explicit nulls.
Acquisition metadata is caller supplied, not authenticated GitHub provenance.
"""
import argparse
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import statistics
import zipfile

from bench import startup_trials as trial

MAX_JSON = 1024 * 1024
MAX_ARCHIVE = 6 * MAX_JSON + 65536
FILES = frozenset(('bindings.json', 'cold-begin.json', 'cold.json',
                   'warm-begin.json', 'warm.json', 'pair.json'))
ARCHES = ('amd64', 'arm64')
ENTRY_FIELDS = frozenset(('arch', 'repetition', 'status', 'run_id', 'run_attempt',
                          'artifact_id', 'artifact_name', 'archive_sha256', 'archive_path'))
PLAN_FIELDS = frozenset(('schema', 'kind', 'source_sha', 'workflow_sha256',
                         'repetitions', 'architectures', 'entries'))
ACQUISITION = ('run_id', 'run_attempt', 'artifact_id', 'artifact_name', 'archive_sha256')
PHASE_METRICS = ('container_to_jvm_s', 'container_to_log_s', 'jvm_to_log_s', 'log_to_ready_s')


class Invalid(ValueError):
    """Only fixed, internally selected reason codes may leave the validator."""


def require(condition, reason):
    if not condition:
        raise Invalid(reason)


def checksum(raw):
    return hashlib.sha256(raw).hexdigest()


def same_json(first, second):
    return trial.digest(first) == trial.digest(second)


def matches(value, pattern):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def positive(value):
    return type(value) is int and 0 < value < 10**20


def decode(raw, reason):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, reason)
            result[key] = value
        return result

    def floating(value):
        number = float(value)
        require(math.isfinite(number), reason)
        return number

    def constant(_value):
        raise Invalid(reason)

    try:
        return json.loads(raw, object_pairs_hook=unique, parse_float=floating,
                          parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise Invalid(reason) from None


def absolute(path, reason):
    path = Path(path)
    require('..' not in path.parts, reason)
    return path if path.is_absolute() else Path.cwd() / path


def parent_fd(path, reason):
    """Traverse with directory descriptors; reject symlinks, including parents."""
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
        return descriptor
    except OSError:
        os.close(descriptor)
        raise Invalid(reason) from None


def read_regular(path, limit, reason):
    """No extraction, subprocess, network access, FIFO blocking or symlink reads."""
    parent = descriptor = None
    try:
        path = absolute(path, reason)
        parent = parent_fd(path, reason)
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, reason)
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            raw = stream.read(limit + 1)
        require(len(raw) <= limit, reason)
        return raw
    except OSError:
        raise Invalid(reason) from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def relative_path(value):
    require(isinstance(value, str) and 0 < len(value) <= 240, 'plan_schema')
    parts = value.split('/')
    require(not PurePosixPath(value).is_absolute() and all(
        part not in ('', '.', '..') and matches(part, r'[A-Za-z0-9_.-]{1,100}')
        for part in parts), 'plan_schema')


def validate_plan(plan):
    require(isinstance(plan, dict) and set(plan) == PLAN_FIELDS, 'plan_schema')
    require(type(plan['schema']) is int and plan['schema'] == 1 and
            plan['kind'] == 'startup-series-plan' and
            matches(plan['source_sha'], r'[a-f0-9]{40}') and
            matches(plan['workflow_sha256'], r'[a-f0-9]{64}'), 'plan_schema')
    count, arches = plan['repetitions'], plan['architectures']
    require(type(count) is int and 3 <= count <= 5 and isinstance(arches, list) and
            1 <= len(arches) <= 2 and all(arch in ARCHES for arch in arches) and
            len(set(arches)) == len(arches), 'plan_schema')
    entries = plan['entries']
    require(isinstance(entries, list) and len(entries) == count * len(arches), 'plan_schema')
    slots = set()
    for entry in entries:
        require(isinstance(entry, dict) and set(entry) == ENTRY_FIELDS, 'plan_schema')
        arch, repetition = entry['arch'], entry['repetition']
        require(arch in arches and type(repetition) is int and 1 <= repetition <= count and
                (arch, repetition) not in slots and
                entry['status'] in ('candidate', 'failed', 'missing'), 'plan_schema')
        slots.add((arch, repetition))
        require((entry['run_id'] is None and entry['run_attempt'] is None) or
                (positive(entry['run_id']) and positive(entry['run_attempt'])), 'plan_schema')
        artifact_fields = ('artifact_id', 'artifact_name', 'archive_sha256', 'archive_path')
        present = [entry[name] is not None for name in artifact_fields]
        require(all(present) or not any(present), 'plan_schema')
        if all(present):
            require(positive(entry['run_id']) and positive(entry['artifact_id']) and
                    entry['artifact_name'] ==
                    f"startup-pair-{arch}-{entry['run_id']}-{entry['run_attempt']}" and
                    matches(entry['archive_sha256'], r'[a-f0-9]{64}'), 'plan_schema')
            relative_path(entry['archive_path'])
        require(entry['status'] != 'candidate' or all(present), 'plan_schema')
    return plan


def archive_records(root, entry):
    raw = read_regular(root / entry['archive_path'], MAX_ARCHIVE, 'archive_path')
    require(checksum(raw) == entry['archive_sha256'], 'archive_hash')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            items = archive.infolist()
            require(all(item.orig_filename == item.filename and item.orig_filename in FILES
                        for item in items), 'archive_inventory')
            names = [item.filename for item in items]
            require(0 < len(items) <= len(FILES) and len(names) == len(set(names)) and
                    set(names) <= FILES and
                    (entry['status'] != 'candidate' or set(names) == FILES), 'archive_inventory')
            require(sum(item.file_size for item in items) <= 6 * MAX_JSON, 'archive_inventory')
            records = {}
            for item in items:
                require(not item.is_dir() and not item.flag_bits & 1 and
                        item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) and
                        0 <= item.file_size <= MAX_JSON and
                        stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG),
                        'archive_inventory')
                with archive.open(item) as stream:
                    body = stream.read(MAX_JSON + 1)
                require(len(body) == item.file_size and len(body) <= MAX_JSON, 'archive_inventory')
                records[item.filename] = decode(body, 'archive_json')
            return records
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, NotImplementedError, EOFError):
        raise Invalid('archive_zip') from None


def platform_projection(snapshot, arch):
    platforms = []
    for node in snapshot['platform']:
        require(node['architecture'] == arch and node['os'] == 'linux' and
                matches(node['uid'], r'[A-Za-z0-9_.-]{1,253}') and
                matches(node['kernel'], r'[A-Za-z0-9_.+-]{1,253}') and
                matches(node['os_image_sha256'], r'[a-f0-9]{64}') and
                matches(node['runtime'], r'[A-Za-z0-9_.:+/-]{1,253}') and
                matches(node['kubelet'], r'[A-Za-z0-9_.+-]{1,100}'), 'platform_invalid')
        platforms.append({name: node[name] for name in
                          ('architecture', 'os', 'kernel', 'os_image_sha256', 'runtime', 'kubelet')} |
                         {'node_uid_sha256': checksum(node['uid'].encode())})
    return sorted(platforms, key=lambda value: value['node_uid_sha256'])


def platform_signature(platforms):
    return sorted(json.dumps({key: value for key, value in item.items() if key != 'node_uid_sha256'},
                             sort_keys=True) for item in platforms)


def pair_evidence(records, plan, entry):
    cold, warm = records['cold.json'], records['warm.json']
    try:
        verified = trial.validate_pair(cold, warm)
        require(same_json(records['pair.json'], verified) and
                same_json(records['cold-begin.json'], cold['begin']) and
                same_json(records['warm-begin.json'], warm['begin']), 'pair_members')
    except Invalid:
        raise
    except Exception:
        raise Invalid('pair_invalid') from None
    bindings = records['bindings.json']
    require(isinstance(bindings, dict) and
            all(matches(bindings.get(name), r'[a-f0-9]{64}') for name in
                ('source', 'workflow', 'package', 'package_archive', 'package_manifest')), 'binding_missing')
    require(bindings['source'] == checksum(plan['source_sha'].encode()), 'source_binding')
    require(bindings['workflow'] == plan['workflow_sha256'], 'workflow_binding')
    caller = {'source_sha': plan['source_sha'], 'run_id': str(entry['run_id']),
              'run_attempt': str(entry['run_attempt'])}
    require(cold['begin']['cohort'] ==
            f"startup-{entry['arch']}-{entry['run_id']}-{entry['run_attempt']}", 'cohort_binding')
    evidence = {'package': bindings['package'], 'package_archive': bindings['package_archive'],
                'package_manifest': bindings['package_manifest'],
                'collector_sha256': cold['begin']['collector_sha256'],
                'jvm_observer_sha256': cold['begin']['jvm_observer_sha256'],
                'cohort_sha256': checksum(cold['begin']['cohort'].encode()),
                'cluster_uid_sha256': checksum(cold['snapshot']['cluster_uid'].encode()),
                'platform': platform_projection(cold['snapshot'], entry['arch']),
                'configuration_sha256': {app: cold['snapshot']['config_sha256'][app]
                                         for app in trial.APPS},
                'container_images': {}, 'reported_image_cache': [], 'boundaries': {}}
    for app in trial.APPS:
        images = []
        for image in cold['snapshot']['container_images'][app]:
            require(matches(image['name'], r'[a-z0-9-]{1,63}'), 'image_invalid')
            images.append({name: image[name] for name in ('kind', 'name', 'image_id')})
        evidence['container_images'][app] = sorted(images, key=lambda value: (value['kind'], value['name']))
    for item in cold['snapshot']['reported_image_cache']:
        evidence['reported_image_cache'].append({
            'node_uid_sha256': checksum(item['node_uid'].encode()),
            'reported_images_sha256': item['reported_images_sha256'],
            'reported_image_count': item['reported_image_count'],
            'inventory_may_be_truncated': True})
    identities = []
    for mode, record in (('cold', cold), ('warm', warm)):
        require(record['begin']['bindings'] == bindings and record['begin']['caller'] == caller,
                'caller_binding')
        phase_rows = {}
        for app in trial.APPS:
            phase = record['phases'][app]
            observation = phase['jvm_observation']
            require(observation['caller'] == caller, 'caller_binding')
            identity = observation['identity']
            identities.append((app, identity['pod_uid'], identity['container_id'],
                               observation['jvm_start_time_unix_ms']))
            phase_rows[app] = {name: phase[name] for name in
                               ('container_to_log_s', 'jvm_to_log_s', 'log_to_ready_s', 'image_pull_s')}
            phase_rows[app].update(
                container_to_jvm_s=(observation['jvm_start_time_unix_ms'] -
                                    trial.jvm.epoch_ms(identity['container_started_at'])) / 1000,
                inits=[{'name': item['name'], 'seconds': item['seconds']} for item in phase['inits']],
                pod_uid_sha256=checksum(identity['pod_uid'].encode()),
                container_id_sha256=checksum(identity['container_id'].encode()),
                jvm_start_time_unix_ms=observation['jvm_start_time_unix_ms'])
        evidence['boundaries'][mode] = {
            'pod_creation_to_ready_span_s': record['pod_creation_to_ready_span_s'], 'apps': phase_rows}
    return evidence, identities


def stats(values):
    return {'min': min(values), 'median': statistics.median(values), 'max': max(values)}


def summaries(rows, plan):
    result = {}
    for arch in plan['architectures']:
        accepted = [row for row in rows if row['arch'] == arch and row['status'] == 'accepted']
        complete = len(accepted) == plan['repetitions']
        summary = {'expected_repetitions': plan['repetitions'],
                   'accepted_repetitions': len(accepted), 'complete': complete, 'statistics': None}
        if complete:
            summary['statistics'] = {}
            for mode in ('cold', 'warm'):
                boundaries = [row['evidence']['boundaries'][mode] for row in accepted]
                summary['statistics'][mode] = {
                    'pod_creation_to_ready_span_s': stats([item['pod_creation_to_ready_span_s'] for item in boundaries]),
                    'apps': {app: {metric: stats([item['apps'][app][metric] for item in boundaries])
                                   for metric in PHASE_METRICS} for app in trial.APPS}}
        result[arch] = summary
    return result


def verify(plan_path):
    path = absolute(plan_path, 'plan_path')
    raw = read_regular(path, MAX_JSON, 'plan_path')
    plan = validate_plan(decode(raw, 'plan_json'))
    rows, seen_artifacts, seen_runs, seen_cohorts = [], set(), set(), set()
    seen_pods, seen_containers, seen_jvms, common = set(), set(), set(), {}
    for entry in sorted(plan['entries'], key=lambda value: (value['arch'], value['repetition'])):
        row = {'arch': entry['arch'], 'repetition': entry['repetition'],
               'declared_status': entry['status'], 'status': 'rejected', 'reason': None,
               'caller_declared_acquisition': {name: entry[name] for name in ACQUISITION}}
        rows.append(row)
        try:
            if entry['artifact_id'] is not None:
                require(entry['artifact_id'] not in seen_artifacts, 'artifact_reused')
                seen_artifacts.add(entry['artifact_id'])
            if entry['run_id'] is not None:
                run = (entry['arch'], entry['run_id'], entry['run_attempt'])
                require(run not in seen_runs, 'execution_reused')
                seen_runs.add(run)
            records = archive_records(path.parent, entry) if entry['archive_path'] is not None else None
            if entry['status'] != 'candidate':
                row.update(status=entry['status'], reason='declared_' + entry['status'])
                continue
            evidence, identities = pair_evidence(records, plan, entry)
            row['evidence'] = evidence
            cohort = (entry['arch'], evidence['cohort_sha256'])
            require(cohort not in seen_cohorts, 'cohort_reused')
            pods, containers = {item[1] for item in identities}, {item[2] for item in identities}
            jvms = {(entry['arch'], item[0], item[3]) for item in identities}
            require(len(pods) == len(containers) == len(jvms) == len(identities) and
                    not pods & seen_pods and not containers & seen_containers and not jvms & seen_jvms,
                    'incarnation_reused')
            comparable = {'package': evidence['package'], 'package_manifest': evidence['package_manifest'],
                          'platform': platform_signature(evidence['platform']),
                          'images': evidence['container_images']}
            previous = common.get(entry['arch'])
            if previous is not None:
                for name, reason in (('package', 'package_mismatch'), ('package_manifest', 'package_mismatch'),
                                     ('platform', 'platform_mismatch'), ('images', 'image_mismatch')):
                    require(comparable[name] == previous[name], reason)
            common[entry['arch']] = comparable
            seen_cohorts.add(cohort); seen_pods.update(pods)
            seen_containers.update(containers); seen_jvms.update(jvms)
            row.update(status='accepted', reason='pair_validated')
        except Invalid as error:
            row['reason'] = str(error)
        except Exception:
            row['reason'] = 'unsupported_schema'
    complete = all(row['status'] == 'accepted' for row in rows)
    return {'schema': 1, 'kind': 'startup-series-report', 'accepted': complete,
            'reason': 'complete' if complete else 'incomplete', 'plan_sha256': checksum(raw),
            'source_sha': plan['source_sha'], 'workflow_sha256': plan['workflow_sha256'],
            'repetitions': plan['repetitions'], 'architectures': plan['architectures'],
            'entries': rows, 'summaries': summaries(rows, plan),
            'limitations': [
                'Archive hashes check supplied bytes against caller-declared metadata; GitHub acquisition, execution and dispatch are not authenticated.',
                'Package archive and manifest SHA-256 values are recorded workflow bindings; package bytes are not independently rehashed by this offline report. Archive wrapper bytes may differ across repetitions.',
                'Repeated Pod/JVM identities are rejected; distinct physical hosts, page-cache absence and cache independence are not established.',
                'Configuration hashes include resource identities and do not prove across-install settings or secret equivalence.',
                'Phase observations and descriptive min/median/max do not establish tuning benefit, ISA ranking, class-loading duration or zero observer overhead.']}


def write_report(path, report):
    parent = descriptor = None
    try:
        path = absolute(path, 'output_path')
        parent = parent_fd(path, 'output_path')
        raw = (json.dumps(report, sort_keys=True, allow_nan=False) + '\n').encode()
        require(len(raw) <= MAX_JSON, 'output_path')
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = None
            stream.write(raw)
    except OSError:
        raise Invalid('output_path') from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        self.exit(2, 'Startup series unavailable: invalid_arguments\n')


def main():
    parser = Parser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        report = verify(args.plan)
        write_report(args.output, report)
        return 0 if report['accepted'] else 1
    except Exception as error:
        reason = str(error) if isinstance(error, Invalid) else 'unsupported_schema'
        print(json.dumps({'schema': 1, 'kind': 'startup-series-report',
                          'accepted': False, 'reason': reason}, sort_keys=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
