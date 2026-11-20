#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""GEOS C-API local capsule and immutable OCI layout; no registry authority."""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
import unicodedata
import zlib

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
FILES = {'lib/libgeos_c.a', 'lib/libgeos.a', 'include/geos_c.h', 'include/geos/export.h', 'COPYING'}
ARTIFACT = 'application/vnd.uds-takserver.geos-capi-cache.local.v1'
LAYER = ARTIFACT + '+tar+gzip'
MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
INDEX = 'application/vnd.oci.image.index.v1+json'
CONFIG = 'application/vnd.oci.empty.v1+json'
MAX_CAPSULE, MAX_EXPANDED, MAX_JSON = 32 * 1024**2, 64 * 1024**2, 2 * 1024**2
RECIPE = ('takx/geos-cache/cache.py', 'takx/geos-cache/producer.py', 'takx/geos-cache/qualify.py',
          'takx/geos-cache/inputs.lock.json', 'takx/native-geos/GeosGeometry.c',
          'takx/native-geos/build.py', 'takx/native-geos/inputs.lock.json', 'takx/native-geos/patch.json',
          'takx/native-las/build.py', 'takx/native-deps/build.py')
INT64_PROBE = '''#include <geos/platform.h>
#ifdef INT64_IS_REALLY32
#error unsupported_GEOS_integer_configuration
#endif
static_assert(sizeof(int64) == 8, "GEOS integer must be 64 bits");
volatile int64 a = 20000001;
volatile int64 b = 40000005;
int main() { int64 c = a*b; int64 d = (c+b)/b; return d != a+1; }
'''


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def unique(pairs):
    result = {}
    for name, value in pairs:
        require(name not in result, 'duplicate JSON key')
        result[name] = value
    return result


def decode(raw):
    require(len(raw) <= MAX_JSON, 'JSON bound')
    return json.loads(raw, object_pairs_hook=unique)


def read(path, limit=MAX_JSON):
    require(not path.is_symlink() and path.is_file(), 'ordinary file required')
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, 'file bound')
    return raw


def file_sha(path):
    return sha(read(path, MAX_EXPANDED))


def hashed(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def new_directory(path):
    require(not path.exists() and not path.is_symlink()
            and not any(p.is_symlink() for p in path.absolute().parents), 'new ordinary directory required')
    path.mkdir(parents=True)


def external_separate(*paths):
    """No repository, symlink alias, or writable/read-only mount overlap."""
    def views(path):
        result, tail = {}, ()
        for candidate in (path, *path.parents):
            if candidate.exists():
                observed = candidate.stat()
                result[(observed.st_dev, observed.st_ino)] = tail
            tail = (unicodedata.normalize('NFD', candidate.name).casefold(), *tail)
        return result

    def overlap(first, second):
        for identity in first.keys() & second.keys():
            a, b = first[identity], second[identity]
            if a[:len(b)] == b or b[:len(a)] == a:
                return True
        return False

    canonical_paths, previous = [], []
    repository = views(REPO_ROOT)
    for path in paths:
        require(type(path) is Path or isinstance(path, Path), 'explicit external paths required')
        absolute = path.absolute()
        require(absolute == path.resolve() and not path.is_symlink()
                and not any(p.is_symlink() for p in absolute.parents), 'canonical ordinary paths required')
        ancestry = views(absolute)
        require(not overlap(ancestry, repository), 'paths must be outside repository')
        require(not any(overlap(ancestry, other) for other in previous), 'input and output paths must be separate')
        canonical_paths.append(absolute)
        previous.append(ancestry)
    return canonical_paths


def identity_key(identity):
    lock = decode(read(HERE / 'inputs.lock.json'))
    fields = {'schema', 'component', 'base_image', 'execution', 'source_inventories', 'recipes', 'tools',
              'abi', 'environment', 'configure', 'build_commands', 'configuration_files', 'configured_features',
              'dependency_digests', 'constraints'}
    require(type(identity) is dict and set(identity) == fields and identity['schema'] == 'uds-geos-capi-inputs-v1',
            'fresh GEOS identity schema required')
    require(identity['component'] == lock['component'] and identity['base_image'] == lock['base_image']
            and identity['constraints'] == lock['constraints'] and identity['dependency_digests'] == [],
            'unsupported component inputs')
    execution = identity['execution']
    require(set(execution) == {'engine_host_arch', 'container_machine', 'mode', 'target_os', 'target_arch'}
            and execution['target_os'] == 'linux' and execution['target_arch'] == 'amd64'
            and execution['container_machine'] == 'x86_64'
            and execution['engine_host_arch'] in ('amd64', 'arm64')
            and execution['mode'] == ('native' if execution['engine_host_arch'] == 'amd64' else 'emulated'),
            'incomplete execution identity')
    for name in ('source_inventories', 'recipes', 'configuration_files'):
        require(type(identity[name]) is dict and identity[name]
                and all(type(k) is str and k and hashed(v) for k, v in identity[name].items()),
                'incomplete source or recipe identity')
    require(set(identity['recipes']) == set(RECIPE), 'complete consumed recipe closure required')
    require({'Makefile', 'config.status', 'include/config.h', 'include/geos/platform.h', 'libtool'}
            <= set(identity['configuration_files']), 'complete generated configuration required')
    features = identity['configured_features']
    require(set(features) == {'int64_macro', 'int64_bytes', 'probe_source_sha256'}
            and features['int64_macro'] in ('HAVE_INT64_T_64', 'HAVE_LONG_INT_64', 'HAVE_LONG_LONG_INT_64')
            and type(features['int64_bytes']) is int and features['int64_bytes'] == 8
            and features['probe_source_sha256'] == sha(INT64_PROBE.encode()), 'working GEOS integer configuration required')
    require(set(identity['source_inventories']) == {'unpatched', 'patched'}, 'patch lineage required')
    expected_tools = {'gcc', 'g++', 'cc1', 'cc1plus', 'as', 'ld', 'ar', 'ranlib', 'nm', 'strip',
                      'make', 'patch', 'readelf', 'python3', 'sh', 'bash', 'ldd'}
    require(type(identity['tools']) is dict and set(identity['tools']) == expected_tools, 'actual tools required')
    for name, value in identity['tools'].items():
        require(set(value) == {'path', 'sha256', 'version', 'version_command'}
                and hashed(value['sha256']) and type(value['version']) is str and value['version']
                and value['path'].startswith('/') and type(value['version_command']) is list
                and value['version_command'], 'incomplete actual tool identity')
        if name in ('gcc', 'g++'):
            require(value['version'].splitlines()[0].endswith(' 14.2.0'), 'unsupported GCC')
    abi = identity['abi']
    require(set(abi) == {'target', 'pointer_bytes', 'byte_order', 'cxx11_abi', 'exceptions', 'rtti',
                         'cxx_standard', 'glibc', 'providers'}
            and abi['target'] == 'x86_64-linux-gnu' and abi['pointer_bytes'] == '8'
            and abi['byte_order'] == '__ORDER_LITTLE_ENDIAN__' and abi['cxx11_abi'] == '1'
            and abi['exceptions'] == '1' and abi['rtti'] == '1' and abi['cxx_standard'] == '201103L'
            and type(abi['glibc']) is str and abi['glibc'] and type(abi['providers']) is dict and abi['providers']
            and all(k.startswith('/') and hashed(v) for k, v in abi['providers'].items()), 'actual ABI required')
    require(identity['environment'] == lock['environment'] and identity['configure'] == lock['configure']
            and identity['build_commands'] == lock['build_commands'], 'fixed fresh build policy required')
    return 'geos-capi-local-v1-' + sha(canonical(identity))


def qualification(value):
    require(set(value) == {'fresh_source_build', 'geometry', 'whole_archive_pic', 'archives', 'runtime_providers',
                          'tested_payload', 'registry_roundtrip', 'publisher_authenticated', 'sdk_accepted', 'security_accepted'}
            and all(value[k] is True for k in ('fresh_source_build', 'geometry', 'whole_archive_pic'))
            and all(value[k] is False for k in ('registry_roundtrip', 'publisher_authenticated',
                                               'sdk_accepted', 'security_accepted')), 'local qualification required')
    require(set(value['archives']) == {'lib/libgeos.a', 'lib/libgeos_c.a'}, 'both object closures required')
    for record in value['archives'].values():
        require(set(record) == {'objects', 'object_inventory_sha256'} and type(record['objects']) is list
                and 0 < len(record['objects']) <= 2048
                and record['object_inventory_sha256'] == sha(canonical(record['objects'])), 'object closure differs')
        for ordinal, item in enumerate(record['objects']):
            require(set(item) == {'ordinal', 'name', 'bytes', 'sha256'} and type(item['name']) is str and item['name']
                    and type(item['ordinal']) is int and item['ordinal'] == ordinal
                    and PurePosixPath(item['name']).name == item['name']
                    and type(item['bytes']) is int and 0 < item['bytes'] <= 8 * 1024**2
                    and hashed(item['sha256']), 'invalid object closure')
    require(type(value['runtime_providers']) is dict and value['runtime_providers']
            and all(k.startswith('/') and hashed(v) for k, v in value['runtime_providers'].items()),
            'actual runtime providers required')
    require(type(value['tested_payload']) is dict and set(value['tested_payload']) == FILES,
            'complete tested payload required')
    for entry in value['tested_payload'].values():
        require(set(entry) == {'bytes', 'sha256'} and type(entry['bytes']) is int
                and 0 < entry['bytes'] <= MAX_EXPANDED and hashed(entry['sha256']), 'tested payload identity required')


def pack(prefix, identity, proof, output):
    external_separate(prefix, output)
    key = identity_key(identity)
    qualification(proof)
    require(not output.exists() and not output.is_symlink(), 'capsule must be new')
    files = {}
    for name in sorted(FILES):
        path = prefix / name
        require(not any(p.is_symlink() for p in (path, *path.parents)) and path.stat().st_mode & 0o111 == 0,
                'ordinary non-executable projection required')
        files[name] = read(path, MAX_EXPANDED)
    require(sha(files['COPYING']) == identity['component']['license_sha256'], 'license differs')
    require(proof['tested_payload'] == {n: {'bytes': len(v), 'sha256': sha(v)} for n, v in files.items()},
            'tested payload changed before projection')
    manifest = {'schema': 'uds-geos-capi-capsule-v1', 'input_key': key, 'identity': identity,
                'qualification': proof, 'files': {n: {'bytes': len(v), 'sha256': sha(v), 'mode': 0o644}
                                                for n, v in files.items()}}
    files['manifest.json'] = canonical(manifest)
    require(len(files['manifest.json']) <= MAX_JSON and sum(map(len, files.values())) <= MAX_EXPANDED - 10240,
            'capsule expansion bound')
    with output.open('xb') as stream, gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            for name, raw in sorted(files.items()):
                item = tarfile.TarInfo(name)
                item.mode, item.size, item.mtime = 0o644, len(raw), 0
                archive.addfile(item, io.BytesIO(raw))
    return verify(output, file_sha(output), key)[0]


def tar_files(expanded):
    require(1024 <= len(expanded) <= MAX_EXPANDED and len(expanded) % 512 == 0, 'tar framing bound')
    result, offset = {}, 0
    while expanded[offset:offset + 512] != bytes(512):
        require(offset + 512 <= len(expanded), 'short tar header')
        member = tarfile.TarInfo.frombuf(expanded[offset:offset + 512], 'utf-8', 'strict')
        require(member.type == tarfile.REGTYPE and member.mode == 0o644 and member.name not in result
                and member.name in FILES | {'manifest.json'} and member.size > 0, 'unapproved capsule member')
        end = offset + 512 + member.size
        require(end <= len(expanded) and len(result) < 6
                and (member.name != 'manifest.json' or member.size <= MAX_JSON), 'member bound')
        expected = tarfile.TarInfo(member.name)
        expected.mode, expected.size, expected.mtime = 0o644, member.size, 0
        require(expanded[offset:offset + 512] == expected.tobuf(format=tarfile.USTAR_FORMAT),
                'noncanonical capsule metadata')
        result[member.name] = expanded[offset + 512:end]
        following = ((end + 511) // 512) * 512
        require(not any(expanded[end:following]), 'nonzero tar padding')
        offset = following
    require(len(expanded) - offset >= 1024 and not any(expanded[offset:]), 'incomplete or trailing tar data')
    require(set(result) == FILES | {'manifest.json'}, 'complete five-file projection required')
    return result


def verify(capsule, digest, key):
    raw = read(capsule, MAX_CAPSULE)
    require(hashed(digest) and sha(raw) == digest, 'capsule digest differs')
    require(raw[:10] == b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff', 'noncanonical gzip metadata')
    stream = zlib.decompressobj(31)
    try:
        expanded = stream.decompress(raw, MAX_EXPANDED + 1)
    except zlib.error:
        raise ValueError('invalid gzip framing') from None
    require(len(expanded) <= MAX_EXPANDED and stream.eof and not stream.unused_data
            and not stream.unconsumed_tail, 'incomplete or concatenated gzip member')
    files = tar_files(expanded)
    manifest = decode(files.pop('manifest.json'))
    require(set(manifest) == {'schema', 'input_key', 'identity', 'qualification', 'files'}
            and manifest['schema'] == 'uds-geos-capi-capsule-v1'
            and identity_key(manifest['identity']) == key == manifest['input_key']
            and set(manifest['files']) == FILES, 'capsule identity differs')
    qualification(manifest['qualification'])
    for name, data in files.items():
        require(manifest['files'][name] == {'bytes': len(data), 'sha256': sha(data), 'mode': 0o644},
                'payload identity differs')
        require(manifest['qualification']['tested_payload'][name] == {'bytes': len(data), 'sha256': sha(data)},
                'untested final payload')
        if name.endswith('.a'):
            require(archive_objects(data) == manifest['qualification']['archives'][name]['objects'],
                    'actual archive object closure differs')
    require(sha(files['COPYING']) == manifest['identity']['component']['license_sha256'], 'license differs')
    return manifest, files


def archive_objects(raw):
    require(raw.startswith(b'!<arch>\n'), 'ordinary static archive required')
    offset, names, objects = 8, b'', []
    while offset < len(raw):
        header = raw[offset:offset + 60]
        require(len(header) == 60 and header[58:] == b'`\n', 'invalid archive header')
        size, name = int(header[48:58]), header[:16].strip().decode()
        require(0 <= size <= 8 * 1024**2 and offset + 60 + size <= len(raw), 'archive member bound')
        body = raw[offset + 60:offset + 60 + size]
        offset += 60 + size + size % 2
        if name == '//':
            require(not names, 'duplicate ar string table')
            names = body
            continue
        if name in ('/', '/SYM64/'):
            continue
        if name.startswith('/'):
            position = int(name[1:])
            require(position < len(names) and (position == 0 or names[position - 1] == 10), 'invalid ar long name')
            name = names[position:].split(b'/\n', 1)[0].decode()
        else:
            name = name.rstrip('/')
        require(name and PurePosixPath(name).name == name and len(body) >= 64 and body[:6] == b'\x7fELF\x02\x01'
                and int.from_bytes(body[16:18], 'little') == 1
                and int.from_bytes(body[18:20], 'little') == 62 and len(objects) < 2048,
                'real x86_64 relocatable object required')
        objects.append({'ordinal': len(objects), 'name': name, 'bytes': len(body), 'sha256': sha(body)})
    require(offset == len(raw) and objects, 'incomplete archive closure')
    return objects


def descriptor(data, media):
    return {'digest': 'sha256:' + sha(data), 'size': len(data), 'mediaType': media}


def layout(capsule, digest, key, output):
    external_separate(capsule, output)
    verify(capsule, digest, key)
    new_directory(output)
    (output / 'blobs/sha256').mkdir(parents=True)
    def blob(raw, media):
        item = descriptor(raw, media)
        with (output / 'blobs/sha256' / item['digest'][7:]).open('xb') as stream:
            stream.write(raw)
        return item
    manifest = {'schemaVersion': 2, 'mediaType': MANIFEST, 'artifactType': ARTIFACT,
                'config': blob(b'{}', CONFIG), 'layers': [blob(read(capsule, MAX_CAPSULE), LAYER)],
                'annotations': {'org.opencontainers.image.source': 'https://github.com/jonathanagustin/uds-takserver',
                                'io.uds-takserver.input-key': key}}
    subject = blob(canonical(manifest), MANIFEST)
    (output / 'index.json').write_bytes(canonical({'schemaVersion': 2, 'mediaType': INDEX, 'manifests': [subject]}))
    (output / 'oci-layout').write_bytes(canonical({'imageLayoutVersion': '1.0.0'}))
    return subject['digest']


def verify_layout(directory, subject, key):
    require(not directory.is_symlink() and directory.is_dir(), 'ordinary OCI layout required')
    def entries(path, maximum):
        result = set()
        for item in path.iterdir():
            require(len(result) < maximum and not item.is_symlink(), 'extra or symlink OCI entry')
            result.add(item.name)
        return result
    require(entries(directory, 3) == {'blobs', 'oci-layout', 'index.json'}
            and entries(directory / 'blobs', 1) == {'sha256'}, 'exact OCI directories required')
    blob_names = entries(directory / 'blobs/sha256', 3)
    require(len(blob_names) == 3 and all(hashed(n) for n in blob_names)
            and all((directory / 'blobs/sha256' / n).is_file() for n in blob_names), 'exact OCI blobs required')
    require(decode(read(directory / 'oci-layout')) == {'imageLayoutVersion': '1.0.0'}, 'OCI layout version differs')
    index = decode(read(directory / 'index.json'))
    require(set(index) == {'schemaVersion', 'mediaType', 'manifests'} and type(index['schemaVersion']) is int
            and index['schemaVersion'] == 2 and index['mediaType'] == INDEX
            and type(index['manifests']) is list and len(index['manifests']) == 1, 'OCI index differs')
    blobs = set()
    def load(item, media, limit):
        require(set(item) == {'digest', 'size', 'mediaType'} and item['mediaType'] == media
                and type(item['size']) is int and 0 < item['size'] <= limit
                and type(item['digest']) is str and item['digest'].startswith('sha256:')
                and hashed(item['digest'][7:]), 'OCI descriptor differs')
        path = directory / 'blobs/sha256' / item['digest'][7:]
        require(not path.parent.is_symlink() and not path.parent.parent.is_symlink(), 'OCI symlink refused')
        raw = read(path, limit)
        require(descriptor(raw, media) == item, 'OCI blob identity differs')
        blobs.add(str(path.relative_to(directory)))
        return raw, path
    require(index['manifests'][0]['digest'] == subject, 'immutable OCI subject differs')
    manifest = decode(load(index['manifests'][0], MANIFEST, MAX_JSON)[0])
    require(set(manifest) == {'schemaVersion', 'mediaType', 'artifactType', 'config', 'layers', 'annotations'}
            and type(manifest['schemaVersion']) is int and manifest['schemaVersion'] == 2
            and manifest['mediaType'] == MANIFEST and manifest['artifactType'] == ARTIFACT
            and manifest['annotations'] == {
                'org.opencontainers.image.source': 'https://github.com/jonathanagustin/uds-takserver',
                'io.uds-takserver.input-key': key}
            and type(manifest['layers']) is list and len(manifest['layers']) == 1, 'OCI manifest differs')
    require(load(manifest['config'], CONFIG, MAX_JSON)[0] == b'{}', 'OCI config differs')
    layer, path = load(manifest['layers'][0], LAYER, MAX_CAPSULE)
    checked, files = verify(path, sha(layer), key)
    require(blobs == {'blobs/sha256/' + n for n in blob_names}, 'unexpected OCI blob')
    return checked, files


def restore(directory, subject, key, output):
    external_separate(directory, output)
    manifest, files = verify_layout(directory, subject, key)
    new_directory(output)
    for name, raw in files.items():
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw)
        target.chmod(0o644)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--layout', type=Path, required=True)
    parser.add_argument('--subject', required=True)
    parser.add_argument('--input-key', required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        restore(args.layout, args.subject, args.input_key, args.output)
    else:
        verify_layout(args.layout, args.subject, args.input_key)


if __name__ == '__main__':
    main()
