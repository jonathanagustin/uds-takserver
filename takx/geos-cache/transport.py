#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in protected GEOS publication; authenticate before restoring remote bytes."""
import argparse
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import time

import cache
import producer

REPOSITORY = 'jonathanagustin/uds-takserver'
WORKFLOW = REPOSITORY + '/.github/workflows/geos-local-qualification.yaml'
REGISTRY = 'ghcr.io/' + REPOSITORY + '/native-cache/geos-capi'
PREDICATE = 'https://github.com/' + REPOSITORY + '/attestations/geos-capi/v1'
FLAGS = ('registry_roundtrip', 'publisher_authenticated', 'sdk_accepted', 'security_accepted')


def context(env):
    cache.require(env.get('GITHUB_REPOSITORY') == REPOSITORY
                  and env.get('GITHUB_REF') == 'refs/heads/main'
                  and env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
                  and env.get('GITHUB_WORKFLOW_REF') == WORKFLOW + '@refs/heads/main'
                  and env.get('GITHUB_WORKFLOW_SHA') == env.get('GITHUB_SHA')
                  and env.get('RUNNER_ENVIRONMENT') == 'github-hosted'
                  and re.fullmatch('[0-9a-f]{40}', env.get('GITHUB_SHA', ''))
                  and re.fullmatch('[1-9][0-9]*', env.get('GITHUB_RUN_ID', ''))
                  and re.fullmatch('[1-9][0-9]*', env.get('GITHUB_RUN_ATTEMPT', '')),
                  'protected-main hosted manual publisher required')
    return {k: env[n] for k, n in (('source_commit', 'GITHUB_SHA'),
            ('run_id', 'GITHUB_RUN_ID'), ('run_attempt', 'GITHUB_RUN_ATTEMPT'))}


def command(args, output, name, env=None, limit=cache.MAX_JSON, missing=False, timeout=120):
    """Private bounded command output; registry errors are never public artifacts."""
    cache.require(type(limit) is int and 0 < limit <= cache.MAX_CAPSULE, 'invalid command output bound')
    cache.require(type(timeout) in (int, float) and 0 < timeout <= 120, 'invalid command time bound')
    if Path(args[0]).name == 'gh':
        env = dict(os.environ if env is None else env, GH_HOST='github.com', GH_REPO=REPOSITORY)
    stdout, stderr = output / (name + '.stdout'), output / (name + '.stderr')
    deadline = time.monotonic() + timeout
    counts, limits = {'stdout': 0, 'stderr': 0}, {'stdout': limit, 'stderr': cache.MAX_JSON}
    with stdout.open('xb') as out, stderr.open('xb') as err:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                              env=env, start_new_session=True) as process:
            try:
                with selectors.DefaultSelector() as selector:
                    for stream, label in ((process.stdout, 'stdout'), (process.stderr, 'stderr')):
                        os.set_blocking(stream.fileno(), False)
                        selector.register(stream, selectors.EVENT_READ, label)
                    while selector.get_map():
                        remaining = deadline - time.monotonic()
                        cache.require(remaining > 0, 'registry command time bound')
                        for key, _ in selector.select(min(remaining, 1)):
                            label = key.data
                            block = os.read(key.fd, min(65536, limits[label] - counts[label] + 1))
                            if not block:
                                selector.unregister(key.fileobj)
                                continue
                            counts[label] += len(block)
                            cache.require(counts[label] <= limits[label], 'registry command output bound')
                            (out if label == 'stdout' else err).write(block)
                try:
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    raise ValueError('registry command time bound') from None
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    raw, error = cache.read(stdout, limit), cache.read(stderr)
    if process.returncode:
        # Only an explicit registry missing-object code permits a new key.
        # A generic HTTP 404, authorization or transport failure is not a miss.
        denied = re.search(rb'\b(?:401|403|UNAUTHORIZED|DENIED)\b', error)
        if missing and not denied and re.search(rb'\b(?:MANIFEST_UNKNOWN|NAME_UNKNOWN)\b', error):
            return None
        raise ValueError('registry command failed; private diagnostics retained')
    return raw


def load(path):
    return cache.decode(cache.read(path))


def new_output(path):
    cache.new_directory(path)
    path.chmod(0o700)


def control(result, manifest):
    headers = {k.removeprefix('include/'): manifest['files'][k]['sha256']
               for k in ('include/geos_c.h', 'include/geos/export.h')}
    cache.require(type(result) is dict and result.get('geometry') is True
                  and result.get('whole_archive_pic') is True
                  and all(cache.hashed(result.get(k)) for k in (
                      'geometry_sha256', 'whole_archive_pic_sha256', 'header_archive_trace_sha256'))
                  and result.get('header_sha256') == headers
                  and result.get('runtime_providers') == manifest['qualification']['runtime_providers']
                  and result.get('pic_runtime_providers') == manifest['qualification']['runtime_providers'],
                  'genuine geometry/PIC controls required')


def consumers(value, manifest, subject, key):
    identity = manifest['identity']
    cache.require(value.get('schema') == 'uds-geos-capi-local-relocation-v1'
                  and value.get('subject') == subject and value.get('input_key') == key
                  and value.get('identity_sha256') == cache.sha(cache.canonical(identity))
                  and value.get('execution') == identity['execution']
                  and value.get('constraints') == identity['constraints']
                  and value.get('host_geos_available') is False
                  and value.get('original_producer_available') is False
                  and all(value.get(k) is False for k in FLAGS), 'local relocation identity differs')
    results = value.get('results')
    cache.require(type(results) is list and len(results) == 2, 'two relocated consumers required')
    for result, prefix in zip(results, ('first', 'unrelated/deeper/second')):
        control(result, manifest)
        cache.require(result.get('prefix') == prefix, 'two unrelated prefixes required')


def publication(value):
    fields = {'schema', 'repository', 'workflow', 'source_commit', 'tree', 'run_id', 'run_attempt',
              'registry', 'subject', 'capsule_sha256', 'input_key', 'identity', 'qualification',
              'producer_sha256', 'consumer_sha256', 'hosted_trial_sha256'}
    cache.require(type(value) is dict and set(value) == fields
                  and value['schema'] == 'uds-geos-capi-publication-v1'
                  and value['repository'] == REPOSITORY and value['workflow'] == WORKFLOW
                  and value['registry'] == REGISTRY
                  and all(type(value[k]) is str and re.fullmatch('[0-9a-f]{40}', value[k])
                          for k in ('source_commit', 'tree'))
                  and all(type(value[k]) is str and re.fullmatch('[1-9][0-9]*', value[k])
                          for k in ('run_id', 'run_attempt'))
                  and type(value['subject']) is str and re.fullmatch('sha256:[0-9a-f]{64}', value['subject'])
                  and all(cache.hashed(value[k]) for k in (
                      'capsule_sha256', 'producer_sha256', 'consumer_sha256', 'hosted_trial_sha256')),
                  'exact GEOS publication schema required')
    cache.require(cache.identity_key(value['identity']) == value['input_key']
                  and value['identity']['execution']['mode'] == 'native', 'native input identity required')
    cache.qualification(value['qualification'])
    return value


def existing(key, subject, output, name):
    raw = command(['crane', 'manifest', REGISTRY + ':' + key], output, name, missing=True)
    if raw is not None:
        actual = command(['crane', 'digest', REGISTRY + ':' + key], output, name + '-digest').decode().strip()
        cache.require(actual == subject, 'immutable input-key collision')


def stage(inputs, output):
    bound = context(os.environ)
    cache.external_separate(inputs, output)
    proof_path, consumer_path = inputs / 'geos-producer/result.json', inputs / 'geos-consumers/restored.json'
    trial_path = inputs / 'geos-consumers/hosted-trial.json'
    proof, restored, trial = load(proof_path), load(consumer_path), load(trial_path)
    subject, key = proof['local_oci_subject'], proof['input_key']
    manifest, _ = cache.verify_layout(inputs / 'geos-producer/oci', subject, key)
    cache.require(set(proof) == {'schema', 'local_oci_subject', 'input_key', 'capsule_sha256', 'identity',
                      'qualification', 'configured_int64_probe_sha256', 'producer_control', *FLAGS}
                  and proof.get('schema') == 'uds-geos-capi-fresh-local-v1'
                  and cache.hashed(proof.get('configured_int64_probe_sha256'))
                  and all(proof.get(k) is False for k in FLAGS)
                  and proof['identity'] == manifest['identity']
                  and proof['qualification'] == manifest['qualification']
                  and proof['identity']['recipes'] == producer.recipes()
                  and proof['identity']['execution']['mode'] == 'native', 'fresh native producer differs')
    control(proof['producer_control'], manifest)
    capsule = inputs / 'geos-producer/geos-capi.tar.gz'
    checked, _ = cache.verify(capsule, proof['capsule_sha256'], key)
    cache.require(checked == manifest, 'candidate capsule differs from OCI layout')
    consumers(restored, manifest, subject, key)
    tree = subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    cache.require(head == bound['source_commit'] and trial == {
        'schema': 'uds-geos-hosted-local-trial-v1', 'run_id': bound['run_id'],
        'run_attempt': bound['run_attempt'], 'checkout': head, 'tree': tree,
        'evidence': {'geos-producer/result.json': cache.file_sha(proof_path),
                     'geos-consumers/restored.json': cache.file_sha(consumer_path)}},
        'candidate must come from this exact protected source/run/attempt')
    q = publication({'schema': 'uds-geos-capi-publication-v1', 'repository': REPOSITORY,
        'workflow': WORKFLOW, **bound, 'tree': tree, 'registry': REGISTRY, 'subject': subject,
        'capsule_sha256': proof['capsule_sha256'], 'input_key': key,
        'identity': manifest['identity'], 'qualification': manifest['qualification'],
        'producer_sha256': cache.file_sha(proof_path), 'consumer_sha256': cache.file_sha(consumer_path),
        'hosted_trial_sha256': cache.file_sha(trial_path)})
    new_output(output)
    existing(key, subject, output, 'existing-key')
    reference = REGISTRY + ':staging-' + bound['run_id'] + '-' + bound['run_attempt']
    command(['crane', 'push', str(inputs / 'geos-producer/oci'), reference], output, 'push')
    actual = command(['crane', 'digest', reference], output, 'pushed-digest').decode().strip()
    cache.require(actual == subject, 'pushed manifest digest differs')
    (output / 'qualification.json').write_bytes(cache.canonical(q))
    (output / 'subjects.txt').write_text(subject[7:] + ' ' + REGISTRY + '\n')
    return q


def attestations(q, output, env, trusted_source):
    publication(q)
    cache.require(type(trusted_source) is str and re.fullmatch('[0-9a-f]{40}', trusted_source)
                  and q['source_commit'] == trusted_source, 'exact approved producer source required')
    common = ['gh', 'attestation', 'verify', 'oci://' + REGISTRY + '@' + q['subject'],
              '--repo', REPOSITORY, '--signer-workflow', WORKFLOW, '--source-digest', trusted_source,
              '--signer-digest', trusted_source, '--source-ref', 'refs/heads/main',
              '--deny-self-hosted-runners', '--format', 'json']
    for index, (kind, expected) in enumerate((('https://slsa.dev/provenance/v1', None), (PREDICATE, q))):
        results = cache.decode(command([*common, '--predicate-type', kind], output, 'attestation-' + str(index), env))
        cache.require(type(results) is list and results, 'authenticated statements required')
        accepted = []
        for result in results:
            statement = result.get('verificationResult', {}).get('statement', {})
            if statement.get('predicateType') == kind and any(
                    s.get('name') == REGISTRY and s.get('digest', {}).get('sha256') == q['subject'][7:]
                    for s in statement.get('subject', [])):
                accepted.append(statement)
        cache.require(accepted and (expected is None or any(s.get('predicate') == expected for s in accepted)),
                      'authenticated exact subject/predicate differs')


def readback(q, output, trusted_source):
    publication(q)
    cache.external_separate(output)
    new_output(output)
    with tempfile.TemporaryDirectory(prefix='geos-anonymous-') as temporary:
        Path(temporary, 'config.json').write_text('{"auths":{}}')
        env = dict(os.environ, DOCKER_CONFIG=temporary)
        # Authentication precedes capsule processing, extraction or execution.
        attestations(q, output, env, trusted_source)
        raw = command(['crane', 'manifest', REGISTRY + '@' + q['subject']], output, 'manifest', env)
        if cache.sha(raw) != q['subject'][7:] and raw.endswith(b'\n'):
            raw = raw[:-1]
        cache.require(cache.sha(raw) == q['subject'][7:], 'remote manifest digest differs')
        manifest = cache.decode(raw)
        # Match the complete canonical manifest, not just a media-type subset.
        expected = {'schemaVersion': 2, 'mediaType': cache.MANIFEST, 'artifactType': cache.ARTIFACT,
            'config': cache.descriptor(b'{}', cache.CONFIG),
            'layers': manifest.get('layers'), 'annotations': {
                'org.opencontainers.image.source': 'https://github.com/' + REPOSITORY,
                'io.uds-takserver.input-key': q['input_key']}}
        cache.require(manifest == expected and type(manifest.get('layers')) is list
                      and len(manifest['layers']) == 1, 'remote OCI manifest differs')
        layer = manifest['layers'][0]
        cache.require(type(layer) is dict and set(layer) == {'digest', 'size', 'mediaType'}
                      and layer['digest'] == 'sha256:' + q['capsule_sha256'] and layer['mediaType'] == cache.LAYER
                      and type(layer['size']) is int and 0 < layer['size'] <= cache.MAX_CAPSULE,
                      'remote capsule descriptor differs')
        for index, (desc, limit) in enumerate(((manifest['config'], cache.MAX_JSON), (layer, cache.MAX_CAPSULE))):
            data = command(['crane', 'blob', REGISTRY + '@' + desc['digest']], output, 'blob-' + str(index), env, limit)
            cache.require(cache.descriptor(data, desc['mediaType']) == desc, 'remote blob identity differs')
            if index == 0:
                cache.require(data == b'{}', 'remote config differs')
            else:
                (output / 'geos-capi.tar.gz').write_bytes(data)
        subject = cache.layout(output / 'geos-capi.tar.gz', q['capsule_sha256'], q['input_key'], output / 'oci')
        checked, _ = cache.verify_layout(output / 'oci', subject, q['input_key'])
        cache.require(subject == q['subject'] and checked['identity'] == q['identity']
                      and checked['qualification'] == q['qualification'], 'signed capsule identity differs')
    (output / 'readback.json').write_bytes(cache.canonical({'schema': 'uds-geos-capi-readback-v1',
        'publication_sha256': cache.sha(cache.canonical(q)), 'authenticated': True,
        'anonymous_readback': True, 'subject': q['subject']}))


def promote(q, read_output, output):
    bound = context(os.environ)
    publication(q)
    cache.require(all(q[k] == v for k, v in bound.items()), 'promotion must belong to this run/attempt/source')
    cache.require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == q['source_commit']
                  and subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], text=True).strip() == q['tree']
                  and producer.recipes() == q['identity']['recipes'], 'promotion source or recipe drift')
    cache.external_separate(read_output, output)
    cache.require(load(read_output / 'readback.json') == {'schema': 'uds-geos-capi-readback-v1',
        'publication_sha256': cache.sha(cache.canonical(q)), 'authenticated': True,
        'anonymous_readback': True, 'subject': q['subject']}, 'authenticated readback receipt differs')
    manifest, _ = cache.verify_layout(read_output / 'oci', q['subject'], q['input_key'])
    cache.require(manifest['identity'] == q['identity'] and manifest['qualification'] == q['qualification'],
                  'promoted bytes differ')
    restored_path = read_output / 'consumer/restored.json'
    consumers(load(restored_path), manifest, q['subject'], q['input_key'])
    new_output(output)
    attestations(q, output, os.environ, bound['source_commit'])
    existing(q['input_key'], q['subject'], output, 'promotion-preflight')
    command(['crane', 'tag', REGISTRY + '@' + q['subject'], q['input_key']], output, 'promote')
    actual = command(['crane', 'digest', REGISTRY + ':' + q['input_key']], output, 'promoted-digest').decode().strip()
    cache.require(actual == q['subject'], 'promoted key digest differs')
    accepted = {'schema': 'uds-geos-capi-registry-accepted-v1', 'publication': q,
                'registry_roundtrip': True, 'publisher_authenticated': True,
                'consumer_sha256': cache.file_sha(restored_path), 'sdk_accepted': False, 'security_accepted': False}
    (output / 'accepted.json').write_bytes(cache.canonical(accepted))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('stage', 'readback', 'promote'))
    parser.add_argument('--input', type=Path)
    parser.add_argument('--publication', type=Path)
    parser.add_argument('--trusted-source-sha')
    parser.add_argument('--readback', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.operation == 'stage':
        cache.require(args.input is not None, 'candidate required')
        stage(args.input, args.output)
    else:
        cache.require(args.publication is not None, 'publication required')
        q = load(args.publication)
        if args.operation == 'readback':
            readback(q, args.output, args.trusted_source_sha)
        else:
            cache.require(args.readback is not None, 'readback required')
            promote(q, args.readback, args.output)


if __name__ == '__main__':
    main()
