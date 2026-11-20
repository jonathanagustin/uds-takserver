#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Read one reviewed immutable Draco subject; never publish or trust lookup tags."""
import argparse
import json
import os
import tempfile
from pathlib import Path
import draco as local
import fresh
import producer
import transport

HERE = Path(__file__).resolve().parent


def contract():
    pin = local.load(HERE / 'pr-consumer.lock.json')
    expected = {'schema': 1, 'source_commit': '3415a69bfd52d4210d8c7308f51f77d29a358f94',
                'run_id': '43088282608',
                'subject_digest': 'sha256:0af201d0e28cb089f11497b211c337db084d13b662012c27953ee3dcfa1f0d2c',
                'input_key': 'registry-v2-cbd008864ed102ca31f168f53629540482d70603297626c25d1e1e505cd7ed4b',
                'capsule_sha256': '86cfa7ac0071b95238828d82dcdb227154434b15f0f9efd5c2edaef6a6b6729d'}
    if pin != expected:
        raise ValueError('reviewed PR cache pin differs')
    return pin


def qualification(results, pin):
    candidates = []
    for result in results:
        statement = result.get('verificationResult', {}).get('statement', {})
        q = statement.get('predicate', {})
        subjects = statement.get('subject', [])
        if (statement.get('predicateType') == fresh.PREDICATE and
                any(s.get('digest', {}).get('sha256') == pin['subject_digest'].split(':')[1] for s in subjects) and
                q.get('schema') == 'uds-draco-qualification-v2' and
                q.get('repository') == transport.REPO and q.get('workflow') == transport.WORKFLOW and
                all(q.get(k) == pin[k] for k in ('source_commit', 'run_id', 'subject_digest', 'input_key', 'capsule_sha256')) and
                isinstance(q.get('identity'), dict) and
                'registry-v2-' + local.sha(local.canonical(q['identity'])) == pin['input_key']):
            candidates.append(q)
    if len(candidates) != 1:
        raise ValueError('verified PR cache qualification differs')
    return candidates[0]


def compatible(q):
    identity = q['identity']
    return (identity.get('recipes') == producer.recipes() and
            identity.get('source') == producer.LOCK['source'] and
            identity.get('base_image') == producer.LOCK['image'] and
            identity.get('base_image_config') == producer.LOCK['image_config'])


def restore(output):
    pin = contract()
    if output.exists() or output.is_symlink() or any(p.is_symlink() for p in output.parents):
        raise ValueError('fresh ordinary PR cache output required')
    output.mkdir(parents=True, mode=0o700)
    reference = 'oci://' + fresh.REGISTRY + '@' + pin['subject_digest']
    args = ['gh', 'attestation', 'verify', reference, '--repo', transport.REPO,
            '--signer-workflow', transport.WORKFLOW, '--source-digest', pin['source_commit'],
            '--signer-digest', pin['source_commit'], '--source-ref', 'refs/heads/main',
            '--deny-self-hosted-runners', '--format', 'json', '--predicate-type', fresh.PREDICATE]
    with tempfile.TemporaryDirectory(prefix='draco-pr-anonymous-') as temporary:
        Path(temporary, 'config.json').write_text('{"auths":{}}')
        env = dict(os.environ, DOCKER_CONFIG=temporary)
        results = local.decode(transport.command(args, output, 'verify-approved-qualification', env))
    q = qualification(results, pin)
    # Only an authenticated, intact, reviewed entry with different build inputs
    # is a compatibility miss. Authentication/transport/capsule errors propagate.
    hit = compatible(q)
    if hit:
        transport.readback(q, output / 'verified', pin['source_commit'])
    result = {'schema': 1, 'cacheHit': hit, 'reason': 'verified-compatible' if hit else 'build-inputs-differ',
              'subjectDigest': pin['subject_digest'], 'inputKey': pin['input_key'],
              'producerSource': pin['source_commit'], 'sdkAcceptance': False}
    (output / 'result.json').write_bytes(local.canonical(result))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--restore', action='store_true')
    p.add_argument('--output', type=Path)
    args = p.parse_args()
    if not args.restore:
        if args.output is not None:
            raise ValueError('explicit restore required')
        contract()
        return
    if args.output is None:
        raise ValueError('complete restore arguments required')
    print(json.dumps(restore(args.output.absolute()), sort_keys=True))


if __name__ == '__main__':
    main()
