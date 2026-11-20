#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Read one reviewed immutable GEOS C-API subject; never publish or trust lookup tags."""
import argparse
import json
import os
from pathlib import Path
import tempfile

import cache
import producer
import transport

HERE = Path(__file__).resolve().parent


def contract():
    pin = transport.load(HERE / 'pr-consumer.lock.json')
    expected = {
        'schema': 1,
        'source_commit': '8d3dd69ad95358707d65d137744291c042297c9f',
        'run_id': '44709400370',
        'run_attempt': '1',
        'subject_digest': 'sha256:82a172964c74b9f5afe8e429ccc921780d0a95a2b842a194ba52616b9ad7dba4',
        'input_key': 'geos-capi-local-v1-729850301b927b26abad38a5b029e8acb6de22cf4b8171bd336d52f8a463c1b2',
        'capsule_sha256': 'd9675543cc0eb18df38f5f38e4deb02ca5ff43c0de8d29f76d8ac8325d1ae66b',
    }
    cache.require(pin == expected, 'reviewed GEOS PR cache pin differs')
    return pin


def qualification(results, pin):
    cache.require(type(results) is list, 'authenticated statements list required')
    candidates = []
    for result in results:
        statement = result.get('verificationResult', {}).get('statement', {})
        q = statement.get('predicate', {})
        subjects = statement.get('subject', [])
        if (statement.get('predicateType') == transport.PREDICATE
                and any(s.get('name') == transport.REGISTRY
                        and s.get('digest', {}).get('sha256') == pin['subject_digest'][7:]
                        for s in subjects)
                and q.get('schema') == 'uds-geos-capi-publication-v1'
                and q.get('repository') == transport.REPOSITORY
                and q.get('workflow') == transport.WORKFLOW
                and q.get('registry') == transport.REGISTRY
                and q.get('subject') == pin['subject_digest']
                and all(q.get(k) == pin[k] for k in ('source_commit', 'run_id', 'run_attempt', 'input_key', 'capsule_sha256'))
                and isinstance(q.get('identity'), dict)
                and q['identity'].get('execution', {}).get('mode') == 'native'
                and 'geos-capi-local-v1-' + cache.sha(cache.canonical(q['identity'])) == pin['input_key']):
            candidates.append(q)
    cache.require(len(candidates) == 1, 'verified GEOS PR cache qualification differs')
    return candidates[0]


def compatible(q):
    identity = q['identity']
    lock = producer.LOCK
    return (identity.get('recipes') == producer.recipes()
            and identity.get('component') == lock['component']
            and identity.get('base_image') == lock['base_image']
            and identity.get('constraints') == lock['constraints']
            and identity.get('environment') == lock['environment']
            and identity.get('configure') == lock['configure']
            and identity.get('build_commands') == lock['build_commands'])


def restore(output):
    pin = contract()
    cache.external_separate(output)
    transport.new_output(output)
    reference = 'oci://' + transport.REGISTRY + '@' + pin['subject_digest']
    args = ['gh', 'attestation', 'verify', reference, '--repo', transport.REPOSITORY,
            '--signer-workflow', transport.WORKFLOW, '--source-digest', pin['source_commit'],
            '--signer-digest', pin['source_commit'], '--source-ref', 'refs/heads/main',
            '--deny-self-hosted-runners', '--format', 'json', '--predicate-type', transport.PREDICATE]
    with tempfile.TemporaryDirectory(prefix='geos-pr-anonymous-') as temporary:
        Path(temporary, 'config.json').write_text('{"auths":{}}')
        env = dict(os.environ, DOCKER_CONFIG=temporary)
        results = cache.decode(transport.command(args, output, 'verify-approved-qualification', env))
    q = qualification(results, pin)
    # Only an authenticated, intact, reviewed entry with different build inputs
    # is a compatibility miss. Authentication/transport/capsule errors propagate.
    hit = compatible(q)
    if hit:
        transport.readback(q, output / 'verified', pin['source_commit'])
    result = {
        'schema': 1,
        'cacheHit': hit,
        'reason': 'verified-compatible' if hit else 'build-inputs-differ',
        'subjectDigest': pin['subject_digest'],
        'inputKey': pin['input_key'],
        'producerSource': pin['source_commit'],
        'sdkAcceptance': False,
        'securityAcceptance': False,
    }
    (output / 'result.json').write_bytes(cache.canonical(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not args.restore:
        cache.require(args.output is None, 'explicit restore required')
        contract()
        return
    cache.require(args.output is not None, 'complete restore arguments required')
    print(json.dumps(restore(args.output.absolute()), sort_keys=True))


if __name__ == '__main__':
    main()
