#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Preserve and audit native candidate image bytes without registry credentials.

The receipt is an integrity record, not an attestation. Its producer context
must be compared with independently verified GitHub run/artifact metadata.
No archive member is extracted or executed during verification.
"""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


SCHEMA = 'uds-takserver/candidate-images/v1'
INPUTS_LABEL = 'dev.uds-takserver.inputs'
REPOSITORY = 'ghcr.io/jonathanagustin/uds-takserver'
GROUPS = {
    'postgis': ('postgis',),
    'cloudtak': ('cloudtak',),
    'takserver': ('takserver-api', 'takserver-config', 'takserver-messaging',
                  'takserver-plugins', 'takserver-database-setup', 'takserver-ignite'),
}
MAX_ARCHIVE = 32 * 1024**3
MAX_METADATA = 4 * 1024**2
MAX_LAYER = 16 * 1024**3
MAX_MEMBERS = 10000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(stream, limit):
    result = hashlib.sha256()
    size = 0
    while block := stream.read(1024 * 1024):
        size += len(block)
        require(size <= limit, 'candidate content exceeds its size limit')
        result.update(block)
    return 'sha256:' + result.hexdigest()


def file_digest(path):
    with path.open('rb') as stream:
        return digest(stream, MAX_ARCHIVE)


def read_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def read_metadata(path):
    require(path.is_file() and not path.is_symlink(), 'candidate metadata must be a regular file')
    require(path.stat().st_size <= MAX_METADATA, 'candidate metadata exceeds its size limit')
    return read_json(path.read_bytes())


def validate_expected(expected):
    require(set(expected) == {'producer', 'group', 'architecture', 'inputs', 'tag'}, 'invalid candidate expectation fields')
    require(expected['group'] in GROUPS, 'unsupported candidate image group')
    require(expected['architecture'] in ('amd64', 'arm64'), 'unsupported native architecture')
    require(re.fullmatch(r'[0-9a-f]{40}(?:-[0-9a-f]{40})?', expected['inputs']) is not None, 'invalid committed image inputs')
    require(re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}', expected['tag']) is not None, 'invalid immutable image tag')
    producer = expected['producer']
    require(set(producer) == {'repository', 'head_sha', 'checkout_sha', 'workflow', 'run_id', 'run_attempt'}, 'invalid producer context fields')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', producer['repository']) is not None, 'invalid producer repository')
    require(re.fullmatch(r'[0-9a-f]{40}', producer['head_sha']) is not None, 'invalid source head')
    require(re.fullmatch(r'[0-9a-f]{40}', producer['checkout_sha']) is not None, 'invalid producer checkout commit')
    require(producer['workflow'] in ('.github/workflows/test.yaml', '.github/workflows/container.yaml',
                                     '.github/workflows/cloudtak-image.yaml', '.github/workflows/postgis-image.yaml'),
            'unrecognized producer workflow')
    require(all(type(producer[key]) is int and producer[key] > 0 for key in ('run_id', 'run_attempt')), 'invalid producer run')


def refs(expected):
    return {name: f"{REPOSITORY}/{name}:{expected['tag']}-{expected['architecture']}"
            for name in GROUPS[expected['group']]}


def normalize_archive(source_path, destination):
    """Keep only manifest-referenced regular files from Docker save output.

    Podman's Docker API also emits unused legacy layer symlinks. They are
    unnecessary for a modern Docker load; the transported archive omits them.
    """
    require(source_path.stat().st_size <= MAX_ARCHIVE, 'candidate archive exceeds its size limit')
    with tarfile.open(source_path, mode='r:') as source:
        members = source.getmembers()
        require(len(members) <= MAX_MEMBERS and len({item.name for item in members}) == len(members),
                'duplicate or excessive Docker save members')
        manifest_member = source.getmember('manifest.json')
        require(manifest_member.isfile() and manifest_member.size <= MAX_METADATA, 'invalid Docker save manifest')
        manifest = read_json(source.extractfile(manifest_member).read())
        require(isinstance(manifest, list) and 0 < len(manifest) <= 6, 'invalid Docker save image count')
        paths = ['manifest.json']
        for image in manifest:
            paths.extend([image['Config'], *image['Layers']])
        with tarfile.open(destination, mode='w:') as target:
            for name in dict.fromkeys(paths):
                path = PurePosixPath(name)
                require(not path.is_absolute() and '..' not in path.parts and str(path) == name,
                        'unsafe Docker save image path')
                member = source.getmember(name)
                require(member.isfile() and member.size <= MAX_LAYER, 'Docker save image content is not a regular file')
                output = tarfile.TarInfo(name)
                output.mode, output.size = 0o600, member.size
                target.addfile(output, source.extractfile(member))


def audit_archive(archive, expected):
    """Validate exact image set, config bytes, architecture, inputs and layers."""
    validate_expected(expected)
    require(archive.is_file() and not archive.is_symlink(), 'candidate archive must be a regular file')
    require(0 < archive.stat().st_size <= MAX_ARCHIVE, 'invalid candidate archive size')
    members, metadata = {}, {}
    with tarfile.open(archive, mode='r|') as source:
        for member in source:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and '..' not in path.parts and str(path) == member.name.rstrip('/'),
                    'unsafe candidate archive path')
            require(member.name not in members, 'duplicate candidate archive member')
            require(len(members) < MAX_MEMBERS, 'too many candidate archive members')
            require(member.isfile() or member.isdir(), 'candidate archive contains a link or special file')
            members[member.name] = {'size': member.size}
            if member.isdir():
                continue
            require(member.size <= MAX_LAYER, 'candidate layer exceeds its size limit')
            stream = source.extractfile(member)
            if member.size <= MAX_METADATA:
                raw = stream.read()
                members[member.name]['sha256'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
                # Docker save uses a JSON config and a manifest, never the
                # contents of a layer archive as candidate control metadata.
                if member.name.endswith('.json') or (member.name.startswith('blobs/sha256/') and raw.lstrip().startswith(b'{')):
                    metadata[member.name] = raw
                    require(sum(len(value) for value in metadata.values()) <= 64 * 1024**2, 'too much candidate metadata')
            else:
                members[member.name]['sha256'] = digest(stream, MAX_LAYER)
    require('manifest.json' in metadata, 'Docker archive manifest is missing')
    manifest = read_json(metadata['manifest.json'])
    wanted = refs(expected)
    require(isinstance(manifest, list) and 0 < len(manifest) <= len(wanted), 'candidate archive has an unexpected image set')
    images, layers, referenced = {}, {}, {'manifest.json'}
    for record in manifest:
        require(isinstance(record, dict) and set(record) >= {'Config', 'RepoTags', 'Layers'}, 'invalid Docker image manifest')
        require(isinstance(record['RepoTags'], list) and 0 < len(record['RepoTags']) <= len(wanted), 'invalid candidate image tags')
        config_path = record['Config']
        referenced.add(config_path)
        require(config_path in metadata, 'candidate config is missing or too large')
        config = read_json(metadata[config_path])
        require(config.get('os') == 'linux' and config.get('architecture') == expected['architecture'], 'candidate image architecture mismatch')
        require(((config.get('config') or {}).get('Labels') or {}).get(INPUTS_LABEL) == expected['inputs'], 'candidate image inputs mismatch')
        diff_ids = config.get('rootfs', {}).get('diff_ids', [])
        require(isinstance(record['Layers'], list) and len(record['Layers']) == len(diff_ids), 'candidate image layer count mismatch')
        image_layers = []
        for path, diff_id in zip(record['Layers'], diff_ids):
            referenced.add(path)
            require(path in members and 'sha256' in members[path], 'candidate layer is missing')
            require(re.fullmatch(r'sha256:[0-9a-f]{64}', diff_id) is not None, 'invalid candidate layer digest')
            require(path not in layers or layers[path] == diff_id, 'candidate layer has inconsistent identities')
            layers[path] = diff_id
            image_layers.append({'sha256': members[path]['sha256'], 'diff_id': diff_id, 'bytes': members[path]['size']})
        for ref in record['RepoTags']:
            require(ref in wanted.values(), 'candidate image has an unexpected tag')
            name = next(name for name, value in wanted.items() if ref == value)
            require(name not in images, 'duplicate candidate image')
            images[name] = {'name': name, 'ref': ref, 'config_digest': members[config_path]['sha256'], 'layers': image_layers}
    require(set(images) == set(wanted), 'candidate archive is missing an expected image')
    require({name for name, member in members.items() if 'sha256' in member} == referenced,
            'candidate archive contains unreferenced files')
    allowed_directories = {str(parent) for name in referenced for parent in PurePosixPath(name).parents if str(parent) != '.'}
    require(all(name.rstrip('/') in allowed_directories for name, member in members.items() if 'sha256' not in member),
            'candidate archive contains unreferenced directories')
    # Validate uncompressed layer bytes against the content-addressed config.
    # This catches tampering even if a damaged archive is given a new receipt.
    with tarfile.open(archive, mode='r|') as source:
        for member in source:
            if member.name not in layers:
                continue
            stream = source.extractfile(member)
            prefix = stream.read(2)
            class Prefixed(io.RawIOBase):
                def readable(self):
                    return True
                def read(self, size=-1):
                    nonlocal prefix
                    if size < 0:
                        result, prefix = prefix + stream.read(), b''
                        return result
                    result, prefix = prefix[:size], prefix[size:]
                    return result + stream.read(size - len(result))
            layer = Prefixed()
            if prefix == b'\x1f\x8b':
                layer = gzip.GzipFile(fileobj=layer)
            require(digest(layer, MAX_LAYER) == layers[member.name], 'candidate layer content digest mismatch')
    return [images[name] for name in GROUPS[expected['group']]]


def seal(directory, expected):
    images = audit_archive(directory / 'images.tar', expected)
    receipt = {'schema': SCHEMA, **expected,
               'archive': {'file': 'images.tar', 'sha256': file_digest(directory / 'images.tar'),
                           'bytes': (directory / 'images.tar').stat().st_size},
               'images': images}
    (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt


def verify(directory, expected):
    validate_expected(expected)
    receipt = read_metadata(directory / 'receipt.json')
    require(set(receipt) == {'schema', *expected.keys(), 'archive', 'images'}, 'invalid candidate receipt fields')
    require(receipt['schema'] == SCHEMA, 'candidate receipt schema mismatch')
    require(all(receipt[key] == value for key, value in expected.items()), 'candidate producer or input identity mismatch')
    archive = directory / 'images.tar'
    require(receipt['archive'] == {'file': 'images.tar', 'sha256': file_digest(archive), 'bytes': archive.stat().st_size},
            'candidate archive integrity mismatch')
    require(receipt['images'] == audit_archive(archive, expected), 'candidate image receipt mismatch')
    return receipt


def export(directory, expected, source_tag, runtime='docker'):
    validate_expected(expected)
    require(not directory.exists(), 'candidate output directory already exists')
    directory.mkdir(parents=True, mode=0o700)
    wanted = refs(expected)
    for name, ref in wanted.items():
        source = f'{REPOSITORY}/{name}:{source_tag}'
        details = json.loads(subprocess.check_output([runtime, 'image', 'inspect', source], text=True))[0]
        require(details['Os'] == 'linux' and details['Architecture'] == expected['architecture'], 'local candidate architecture mismatch')
        require((details.get('Config') or {}).get('Labels', {}).get(INPUTS_LABEL) == expected['inputs'], 'local candidate inputs mismatch')
        subprocess.run([runtime, 'image', 'tag', source, ref], check=True)
    raw = directory / 'saved.tar'
    subprocess.run([runtime, 'image', 'save', '--output', str(raw), *wanted.values()], check=True)
    normalize_archive(raw, directory / 'images.tar')
    raw.unlink()
    return seal(directory, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('export', 'seal', 'verify'))
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--source-tag')
    parser.add_argument('--runtime', default='docker')
    args = parser.parse_args()
    expected = read_metadata(args.expected)
    if args.command == 'export':
        require(args.source_tag is not None, 'source tag is required for export')
        result = export(args.directory, expected, args.source_tag, args.runtime)
    else:
        result = (seal if args.command == 'seal' else verify)(args.directory, expected)
    print(json.dumps({'group': result['group'], 'architecture': result['architecture'],
                      'archive_sha256': result['archive']['sha256'],
                      'images': {image['name']: image['config_digest'] for image in result['images']}}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError, tarfile.TarError, subprocess.CalledProcessError) as error:
        raise SystemExit(f'candidate-images: {error}') from error
