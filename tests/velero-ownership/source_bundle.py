# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded read-only binding of a built UDS bundle to its signed Core provider."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tarfile
import time
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('owner_bundle_build', ROOT / '.github/test-infra/core-restore-source/build.py')
B = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(B)


def keyless():
    return {'certificateIdentityRegexp': '^' + re.escape(B.IDENTITY) + '$',
            'certificateOIDCIssuer': B.ISSUER}


def inspect_bundle(path, provider, provider_directory):
    """Hash every OCI blob and bind retained definition and package source metadata."""
    import zstandard
    path = B.regular(path, 16 * 1024**3)
    objects, seen, retained, scanned, started = {}, set(), 0, 0, time.monotonic()
    with path.open('rb') as raw, zstandard.ZstdDecompressor(max_window_size=128 * 1024**2).stream_reader(raw) as stream, \
         tarfile.open(fileobj=stream, mode='r|') as archive:
        for count, member in enumerate(archive):
            scanned += member.size
            B.require(count < 100000 and scanned <= 32 * 1024**3 and time.monotonic() - started < 120,
                      'bundle metadata scan exceeds bound')
            name = member.name.removeprefix('./')
            if not (name == 'index.json' or re.fullmatch('blobs/sha256/[a-f0-9]{64}', name)):
                continue
            B.require(member.isfile() and member.size >= 0 and name not in seen, 'bundle metadata entry differs')
            seen.add(name)
            data, digest, size = bytearray(), hashlib.sha256(), 0
            with archive.extractfile(member) as payload:
                while chunk := payload.read(64 * 1024):
                    size += len(chunk); digest.update(chunk)
                    B.require(size <= member.size and time.monotonic() - started < 120, 'bundle payload scan exceeds bound')
                    if member.size <= 1024**2:
                        data.extend(chunk)
            B.require(size == member.size and (name == 'index.json' or digest.hexdigest() == name.rsplit('/', 1)[1]),
                      'bundle blob digest or inventory differs')
            if member.size > 1024**2:
                continue
            data = bytes(data)
            retained += len(data); B.require(retained <= 32 * 1024**2, 'bundle metadata retention bound')
            try:
                value = json.loads(data, object_pairs_hook=B.unique)
            except (ValueError, UnicodeError):
                value = None
            objects[name] = (data, value)
    def payload(desc):
        B.require(isinstance(desc, dict) and isinstance(desc.get('digest'), str) and
                  re.fullmatch('sha256:[a-f0-9]{64}', desc['digest']) and type(desc.get('size')) is int,
                  'bundle descriptor differs')
        data, value = objects['blobs/sha256/' + desc['digest'].split(':')[1]]
        B.require(len(data) == desc['size'], 'bundle descriptor size differs')
        return data, value
    def blob(desc):
        value = payload(desc)[1]
        B.require(isinstance(value, dict), 'bundle JSON metadata differs')
        return value
    index = objects['index.json'][1]
    B.require(isinstance(index, dict) and index.get('schemaVersion') == 2 and
              index.get('mediaType') == 'application/vnd.oci.image.index.v1+json' and
              len(index.get('manifests', [])) == 1, 'bundle index differs')
    root = blob(index['manifests'][0])
    B.require(root.get('schemaVersion') == 2 and root.get('mediaType') == 'application/vnd.oci.image.manifest.v1+json' and
              isinstance(root.get('layers'), list), 'bundle root manifest differs')
    definitions = [d for d in root['layers'] if d.get('annotations', {}).get('org.opencontainers.image.title') == 'uds-bundle.yaml']
    B.require(len(definitions) == 1, 'bundle definition is ambiguous')
    class UniqueLoader(yaml.SafeLoader):
        pass
    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                                 lambda loader, node: B.unique(loader.construct_pairs(node, deep=True)))
    definition = yaml.load(payload(definitions[0])[0], Loader=UniqueLoader)
    B.require(isinstance(definition, dict) and definition.get('kind') == 'UDSBundle' and
              isinstance(definition.get('packages'), list), 'embedded bundle definition differs')
    packages = [p for p in definition['packages'] if p.get('name') == 'core-backup-restore']
    package_name = 'zarf-package-core-backup-restore-' + provider['architecture'] + '-' + B.VERSION + '-upstream.tar.zst'
    expected_path = str((provider_directory / package_name).absolute())
    expected_digest = provider['packageManifestDigest']
    B.require(isinstance(expected_digest, str) and re.fullmatch('sha256:[a-f0-9]{64}', expected_digest) and
              len(packages) == 1 and packages[0].get('path') == expected_path and 'repository' not in packages[0] and
              packages[0].get('ref') == B.VERSION + '-upstream@' + expected_digest and
              packages[0].get('keylessVerification') == keyless(), 'embedded Core verification selection differs')
    # UDS0.41 package layers have no title. The retained named package's ref
    # associates its exact canonical manifest with a unique root layer.
    matches = [d for d in root['layers'] if d.get('digest') == expected_digest]
    B.require(len(matches) == 1, 'bundle selected another Core package')
    manifest = blob(matches[0]); config = blob(manifest['config'])
    meta = config.get('metadata', {})
    B.require(manifest.get('schemaVersion') == 2 and config.get('kind') == 'ZarfPackageConfig' and
              meta.get('name') == 'core-backup-restore' and meta.get('version') == B.VERSION and
              meta.get('architecture') == provider['architecture'], 'embedded source package metadata differs')
    annotations = {k: v for k, v in meta.get('annotations', {}).items() if k.startswith('takserver.qualification/')}
    B.require(annotations == {'takserver.qualification/source-sha': provider['sourceSHA'],
                              'takserver.qualification/run-id': provider['runID'],
                              'takserver.qualification/run-attempt': provider['runAttempt']},
              'embedded source package run differs')
    return expected_digest
