#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Format complete scan mappings from caller-validated, hash-bound state.

This transports the existing consumer state; it does not authenticate a producer.
The strict adapter replays candidate-consumer.py's complete validation policy.
"""

import argparse
import hashlib
import importlib.util
from pathlib import Path
import re


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('scan_candidate_images', HERE / 'candidate-images.py')
IMAGES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(IMAGES)


def mappings(path, expected_sha256):
    IMAGES.require(re.fullmatch(r'[0-9a-f]{64}', expected_sha256) is not None, 'invalid caller state hash')
    IMAGES.require(path.is_file() and not path.is_symlink() and path.stat().st_size <= IMAGES.MAX_METADATA,
                   'bounded regular candidate state required')
    with path.open('rb') as stream:
        raw = stream.read(IMAGES.MAX_METADATA + 1)
    IMAGES.require(len(raw) <= IMAGES.MAX_METADATA, 'bounded candidate state required')
    IMAGES.require(hashlib.sha256(raw).hexdigest() == expected_sha256, 'candidate state changed after validation')
    state = IMAGES.read_json(raw)
    IMAGES.require(state['schema'] == 'uds-takserver/candidate-consumer/v1' and state['architecture'] == 'amd64',
                   'complete AMD64 consumer state required')
    registry = state['registry']
    IMAGES.require(re.fullmatch(r'127\.0\.0\.1:[0-9]{1,5}', registry) is not None
                   and 0 < int(registry.split(':')[1]) < 65536, 'numeric loopback registry required')
    IMAGES.require(set(state['groups']) == set(IMAGES.GROUPS), 'complete candidate groups required')
    result = {}
    for group, names in IMAGES.GROUPS.items():
        images = state['groups'][group]['images']
        IMAGES.require(type(images) is list and len(images) == len(names), 'complete candidate image set required')
        selected = set()
        for image in images:
            source = image['ref']
            name = source.rsplit('/', 1)[-1].split(':', 1)[0]
            IMAGES.require(name in names and name not in selected and re.fullmatch(
                re.escape(IMAGES.REPOSITORY + '/' + name) + r':[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}', source),
                'exact canonical image reference required')
            selected.add(name)
            mirror = source.replace('ghcr.io/', registry + '/', 1)
            IMAGES.require(image['mirror'] == mirror and re.fullmatch(r'sha256:[0-9a-f]{64}',
                           image['mirror_manifest_digest']) is not None, 'exact loopback manifest digest required')
            target = mirror.rsplit(':', 1)[0] + '@' + image['mirror_manifest_digest']
            IMAGES.require(source not in result and target not in result.values(), 'duplicate candidate mapping')
            result[source] = target
    with path.open('rb') as stream:
        IMAGES.require(stream.read(IMAGES.MAX_METADATA + 1) == raw, 'candidate state changed during mapping')
    return [source + '=' + target for source, target in sorted(result.items())]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--state-sha256', required=True)
    args = parser.parse_args()
    try:
        result = mappings(args.state, args.state_sha256)
    except (ValueError, OSError, KeyError, TypeError, AttributeError) as error:
        raise SystemExit('candidate-scan-mappings: invalid or changed validated consumer state') from error
    print('\n'.join(result))


if __name__ == '__main__':
    main()
