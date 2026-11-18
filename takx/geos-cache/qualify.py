#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Real GEOS C geometry and whole-archive PIC at two clean local prefixes."""
import argparse
import os
from pathlib import Path
import re
import shutil
import tempfile

import cache
import producer


def controls(prefix, output, run, tools):
    output.mkdir(parents=True)
    cc = tools['gcc']['path']
    probe = producer.ROOT / 'takx/native-geos/GeosGeometry.c'
    capi, library = prefix / 'lib/libgeos_c.a', prefix / 'lib/libgeos.a'
    binary, pic = output / 'geometry', output / 'whole-archive-pic.so'
    compile_trace = run([cc, '-std=c11', '-O1', '-H', '-I' + str(prefix / 'include'), str(probe),
        str(capi), str(library), '-Wl,-t', '-lstdc++', '-lm', '-o', str(binary)], 'geometry-compile', 90)
    for name in ('geos_c.h', 'geos/export.h'):
        expected = str(prefix / 'include' / name)
        paths = [line.lstrip('. ').strip() for line in compile_trace.decode().splitlines()
                 if line.startswith('.') and line.rstrip().endswith('/' + name)]
        cache.require(paths == [expected], 'restored GEOS header trace differs')
    cache.require(all(str(path).encode() in compile_trace for path in (capi, library)), 'archive link trace differs')
    text = run([str(binary)], 'real-geometry', 15)
    cache.require(text == b'actual_release=3.4.3-CAPI-1.8.3 r4285\ngeometry_and_error_controls=true\n',
                  'genuine GEOS geometry/error controls differ')
    initialized = run([str(binary)], 'initialized-geometry-providers', 15,
                      env=dict(producer.ENV, LD_DEBUG='libs,files'))
    runtime = {}
    allowed = ('ld-linux-x86-64.so.2', 'libc.so.6', 'libm.so.6', 'libstdc++.so.6', 'libgcc_s.so.1')
    for value in re.findall(rb'calling init: ([^\n]+)', initialized):
        path = Path(os.fsdecode(value.strip())).resolve()
        cache.require(any(path.name.startswith(name) for name in allowed), 'foreign initialized runtime provider')
        runtime[str(path)] = cache.file_sha(path)
    cache.require(len(runtime) == 5, 'incomplete initialized runtime closure')
    run([cc, '-shared', '-Wl,--whole-archive', str(capi), str(library), '-Wl,--no-whole-archive',
         '-Wl,--no-undefined', '-lstdc++', '-lm', '-o', str(pic)], 'whole-archive-pic-link', 90)
    dynamic = run([tools['readelf']['path'], '-d', str(pic)], 'pic-dynamic')
    cache.require(b'TEXTREL' not in dynamic and b'libgeos' not in dynamic, 'invalid whole-archive PIC closure')
    pic_providers = producer.providers(pic, run, 'pic-providers')
    cache.require(pic_providers == runtime, 'PIC/geometry runtime providers differ')
    headers = {name: cache.file_sha(prefix / 'include' / name) for name in ('geos_c.h', 'geos/export.h')}
    return {'geometry': True, 'whole_archive_pic': True, 'release': '3.4.3-CAPI-1.8.3 r4285',
            'geometry_sha256': cache.file_sha(binary), 'whole_archive_pic_sha256': cache.file_sha(pic),
            'header_sha256': headers, 'header_archive_trace_sha256': cache.sha(compile_trace),
            'runtime_providers': runtime, 'pic_runtime_providers': pic_providers}


def inside(engine_arch):
    root = Path('/build'); run = producer.Runner(root, 600)
    request = cache.decode(cache.read(Path('/inputs/request.json')))
    cache.require(set(request) == {'subject', 'input_key'}, 'local restore request differs')
    manifest, _ = cache.verify_layout(Path('/inputs/oci'), request['subject'], request['input_key'])
    identity = manifest['identity']
    for name in ('source', 'work', 'install'):
        cache.require(not (root / name).exists(), 'producer inputs available to consumer')
    cache.require(not any(Path('/usr/include').glob('geos*'))
                  and not any(Path('/usr/local/include').glob('geos*'))
                  and not any(Path('/usr/lib').rglob('libgeos*'))
                  and not any(Path('/usr/local/lib').rglob('libgeos*')), 'host GEOS provider available')
    observed = producer.tools(run)
    actual_abi = producer.abi(observed, run)
    cache.require(observed == identity['tools'] and actual_abi == identity['abi']
                  and producer.recipes() == identity['recipes']
                  and identity['execution']['engine_host_arch'] == engine_arch,
                  'consumer tool/ABI/source execution differs')
    results = []
    for index, name in enumerate(('first', 'unrelated/deeper/second')):
        prefix = root / name
        cache.restore(Path('/inputs/oci'), request['subject'], request['input_key'], prefix)
        control = controls(prefix, root / ('control-' + str(index)), run, observed)
        cache.require(control['runtime_providers'] == manifest['qualification']['runtime_providers'],
                      'consumer/provider identity differs')
        for item, entry in manifest['files'].items():
            cache.require(cache.file_sha(prefix / item) == entry['sha256'], 'restored payload drift')
        results.append({'prefix': name, **control})
    cache.verify_layout(Path('/inputs/oci'), request['subject'], request['input_key'])
    cache.require(producer.tools(run) == observed and producer.recipes() == identity['recipes'],
                  'consumer tool or recipe drift')
    (root / 'restored.json').write_bytes(cache.canonical({'schema': 'uds-geos-capi-local-relocation-v1',
        **request, 'identity_sha256': cache.sha(cache.canonical(identity)), 'execution': identity['execution'],
        'constraints': identity['constraints'], 'results': results, 'original_producer_available': False,
        'host_geos_available': False, 'registry_roundtrip': False, 'publisher_authenticated': False,
        'sdk_accepted': False, 'security_accepted': False}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true')
    parser.add_argument('--engine-host-arch', choices=['amd64', 'arm64'])
    parser.add_argument('--engine', default='docker')
    parser.add_argument('--layout', type=Path)
    parser.add_argument('--subject')
    parser.add_argument('--input-key')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.inside:
        inside(args.engine_host_arch)
    else:
        cache.external_separate(args.output, args.layout)
        cache.verify_layout(args.layout, args.subject, args.input_key)
        cache.new_directory(args.output)
        with tempfile.TemporaryDirectory(prefix='geos-consumer-inputs-') as temporary:
            inputs = Path(temporary).resolve()
            shutil.copytree(args.layout, inputs / 'oci')
            (inputs / 'request.json').write_bytes(cache.canonical({'subject': args.subject, 'input_key': args.input_key}))
            producer.launch(args.engine, args.output, inputs, 'consume')


if __name__ == '__main__':
    main()
