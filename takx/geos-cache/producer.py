#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Fresh bounded GEOS source build in a fixed credential-free GCC image."""
import argparse
from collections import Counter
import importlib.util
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

import cache

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LOCK = cache.decode(cache.read(HERE / 'inputs.lock.json'))
ENV = LOCK['environment']
RECIPE = cache.RECIPE


def recipes():
    return {name: cache.file_sha(ROOT / name) for name in RECIPE}


def historical_helpers():
    spec = importlib.util.spec_from_file_location('fresh_geos_source_helpers', ROOT / 'takx/native-geos/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = cache.decode(cache.read(ROOT / 'takx/native-geos/inputs.lock.json'))
    module.validate_lock(old)
    component = LOCK['component']
    cache.require(component['archive'] == old['archives']['geos-3.4.3']
                  and component['patch'] == old['source_patch'] and component['revision'] == old['revision']
                  and component['license_sha256'] == old['license']['sha256'], 'historical source pin differs')
    return module


class Runner:
    def __init__(self, root, seconds=1800):
        self.root, self.deadline, self.counter = root, time.monotonic() + seconds, 0

    def __call__(self, command, name, timeout=60, cwd=None, env=None):
        self.counter += 1
        log = self.root / (str(self.counter) + '-' + name + '.log')
        deadline = min(self.deadline, time.monotonic() + timeout)
        process, complete = None, False
        try:
            with log.open('xb') as stream:
                process = subprocess.Popen(command, cwd=cwd, env=ENV if env is None else env,
                    stdout=stream, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
                while True:
                    size = 0
                    for path in self.root.rglob('*'):
                        try:
                            if path.is_file() and not path.is_symlink():
                                size += path.stat().st_size
                        except FileNotFoundError:
                            # Configure and make legitimately remove transient files.
                            continue
                    cache.require(size <= LOCK['budgets']['output_bytes'] and log.stat().st_size <= 8 * 1024**2
                                  and time.monotonic() < deadline, 'owned command time/output bound')
                    if process.poll() is not None:
                        complete = True
                        break
                    time.sleep(0.2)
                cache.require(process.returncode == 0, 'owned command failed; inspect private local diagnostics')
        finally:
            if process:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            status = {'complete': complete, 'returncode': process.returncode if process else None,
                      'bytes': log.stat().st_size if log.exists() else 0,
                      'sha256': cache.file_sha(log) if log.exists() else None}
            log.with_suffix('.status.json').write_bytes(cache.canonical(status))
        return cache.read(log, 8 * 1024**2)


def tools(run):
    paths = {name: str(Path(shutil.which(name)).resolve())
             for name in ('gcc', 'g++', 'make', 'patch', 'readelf', 'python3', 'sh', 'bash', 'ldd')}
    for name in ('cc1', 'cc1plus', 'as', 'ld', 'ar', 'ranlib', 'nm', 'strip'):
        selected = run([paths['g++'], '-print-prog-name=' + name], 'locate-' + name).decode().strip()
        paths[name] = str(Path(shutil.which(selected) or selected).resolve())
    result = {}
    for name, path in paths.items():
        if name in ('cc1', 'cc1plus'):
            command = [path, '-version', '-fsyntax-only', '-quiet', '-o', '/dev/null', '/dev/null']
        elif name == 'sh':
            command = ['/usr/bin/dpkg-query', '--show', '--showformat=${Version}', 'dash']
        else:
            command = [path, '--version']
        result[name] = {'path': path, 'sha256': cache.file_sha(Path(path)), 'version_command': command,
                        'version': run(command, 'tool-' + name).decode().strip()}
    return result


def providers(path, run, name):
    text = run(['/usr/bin/ldd', str(path)], name).decode()
    cache.require('not found' not in text, 'missing runtime provider')
    paths = {Path(p).resolve() for p in re.findall(r'(/[A-Za-z0-9_./+\-]+)', text)}
    cache.require(paths, 'missing provider closure')
    return {str(p): cache.file_sha(p) for p in sorted(paths)}


def abi(observed, run):
    raw = run([observed['g++']['path'], '-std=c++11', '-dM', '-E', '-x', 'c++',
               '-include', 'bits/c++config.h', '-include', 'features.h', '/dev/null'], 'abi-macros')
    macros = dict(re.findall(r'^#define (\w+) (.+)$', raw.decode(), re.M))
    runtime = {}
    for index, name in enumerate(('gcc', 'g++', 'cc1', 'cc1plus', 'as', 'ld', 'ar', 'ranlib',
                                   'nm', 'strip', 'make', 'patch', 'readelf', 'python3', 'sh', 'bash')):
        runtime.update(providers(observed[name]['path'], run, 'tool-providers-' + str(index)))
    return {'target': run([observed['g++']['path'], '-dumpmachine'], 'target').decode().strip(),
            'pointer_bytes': macros['__SIZEOF_POINTER__'], 'byte_order': macros['__BYTE_ORDER__'],
            'cxx11_abi': macros['_GLIBCXX_USE_CXX11_ABI'], 'exceptions': macros['__EXCEPTIONS'],
            'rtti': macros['__GXX_RTTI'], 'cxx_standard': macros['__cplusplus'],
            'glibc': macros['__GLIBC__'] + '.' + macros['__GLIBC_MINOR__'], 'providers': runtime}


def archive_objects(path):
    return cache.archive_objects(cache.read(path, cache.MAX_EXPANDED))


def configuration_inventory(work):
    return {str(p.relative_to(work)): cache.file_sha(p) for p in sorted(work.rglob('*'))
            if p.is_file() and p.name in ('Makefile', 'config.status', 'config.h', 'platform.h', 'libtool')}


def configured_features(work):
    observed = []
    for name in ('include/config.h', 'include/geos/platform.h'):
        raw = cache.read(work / name).decode()
        macros = re.findall(r'^#define (HAVE_(?:INT64_T_64|LONG_INT_64|LONG_LONG_INT_64)) 1$', raw, re.M)
        cache.require(len(macros) == 1, 'GEOS working 64-bit feature not configured')
        observed.append(macros[0])
    cache.require(observed[0] == observed[1], 'GEOS integer configuration disagrees')
    return {'int64_macro': observed[0], 'int64_bytes': 8, 'probe_source_sha256': cache.sha(cache.INT64_PROBE.encode())}


def payload_inventory(prefix):
    return {name: {'bytes': (prefix / name).stat().st_size, 'sha256': cache.file_sha(prefix / name)}
            for name in sorted(cache.FILES)}


def tested_payload(prefix, expected, control):
    cache.require(payload_inventory(prefix) == expected and control['header_sha256'] ==
                  {name: expected['include/' + name]['sha256'] for name in ('geos_c.h', 'geos/export.h')},
                  'tested header or payload changed')


def source_build(engine_arch):
    cache.require(platform.system() == 'Linux' and platform.machine() == 'x86_64', 'Linux amd64 execution required')
    root = Path('/build'); run = Runner(root)
    before_recipes, observed = recipes(), tools(run)
    actual_abi = abi(observed, run)
    helper = historical_helpers()
    source = root / 'source'; source.mkdir()
    component = LOCK['component']
    expected = helper.las.unpack(Path('/inputs/geos.tar.bz2'), source, 'geos-3.4.3', component['archive'])
    cache.require(cache.file_sha(Path('/inputs/geos.tar.bz2')) == component['archive']['sha256']
                  and cache.file_sha(Path('/inputs/geos-pgsc.patch')) == component['patch']['sha256'],
                  'source archive or patch differs')
    unpatched = cache.sha(cache.canonical(expected))
    src = source / 'geos-3.4.3'
    helper.check_patch_images(src, 'before')
    run([observed['patch']['path'], '--fuzz=0', '--batch', '-p1', '-i', '/inputs/geos-pgsc.patch'],
        'exact-source-patch', cwd=src)
    helper.check_patch_images(src, 'after')
    patched = helper.source_inventory(source)
    cache.require(set(expected) == set(patched) and {n for n in expected if expected[n] != patched[n]} ==
                  {'geos-3.4.3/' + n for n in component['patch']['files']}, 'unexpected source changes')
    (root / 'patched-source-inventory.json').write_bytes(cache.canonical(patched))
    work, prefix = root / 'work', root / 'install'; work.mkdir()
    run(LOCK['configure'], 'configure', timeout=150, cwd=work)
    helper.check_sources(source, patched)
    configuration = configuration_inventory(work)
    features = configured_features(work)
    probe = root / 'int64-abi.cpp'; probe.write_text(cache.INT64_PROBE)
    run([observed['g++']['path'], '-std=c++11', '-O1', '-I' + str(work / 'include'), str(probe),
         '-o', str(root / 'int64-abi')], 'configured-int64-compile')
    run([str(root / 'int64-abi')], 'configured-int64-runtime')
    identity = {'schema': 'uds-geos-capi-inputs-v1', 'component': component, 'base_image': LOCK['base_image'],
        'execution': {'engine_host_arch': engine_arch, 'container_machine': platform.machine(),
                      'mode': 'native' if engine_arch == 'amd64' else 'emulated',
                      'target_os': 'linux', 'target_arch': 'amd64'},
        'source_inventories': {'unpatched': unpatched, 'patched': cache.sha(cache.canonical(patched))},
        'recipes': before_recipes, 'tools': observed, 'abi': actual_abi, 'environment': ENV,
        'configure': LOCK['configure'], 'build_commands': LOCK['build_commands'],
        'configuration_files': configuration, 'configured_features': features,
        'dependency_digests': [], 'constraints': LOCK['constraints']}
    key = cache.identity_key(identity)
    run(LOCK['build_commands'][0], 'fresh-source-build', LOCK['budgets']['build_seconds'])
    run(LOCK['build_commands'][1], 'source-install', 120)
    helper.check_sources(source, patched)
    shutil.copyfile(src / 'COPYING', prefix / 'COPYING')
    archives = {}
    built = Counter(cache.file_sha(p) for p in work.rglob('*.o'))
    for name in ('lib/libgeos.a', 'lib/libgeos_c.a'):
        objects = archive_objects(prefix / name)
        cache.require(not (Counter(o['sha256'] for o in objects) - built), 'archive objects differ from actual build')
        archives[name] = {'objects': objects, 'object_inventory_sha256': cache.sha(cache.canonical(objects))}
    import qualify
    payload = payload_inventory(prefix)
    control = qualify.controls(prefix, root / 'producer-control', run, observed)
    tested_payload(prefix, payload, control)
    proof = {'fresh_source_build': True, 'geometry': True, 'whole_archive_pic': True, 'archives': archives,
             'runtime_providers': control['runtime_providers'], 'tested_payload': payload, 'registry_roundtrip': False,
             'publisher_authenticated': False, 'sdk_accepted': False, 'security_accepted': False}
    capsule = root / 'geos-capi.tar.gz'
    cache.pack(prefix, identity, proof, capsule)
    digest = cache.file_sha(capsule)
    subject = cache.layout(capsule, digest, key, root / 'oci')
    cache.verify_layout(root / 'oci', subject, key)
    helper.check_sources(source, patched)
    cache.require(configuration_inventory(work) == configuration, 'configured build policy changed')
    cache.require(configured_features(work) == features, 'configured GEOS integer feature changed')
    tested_payload(prefix, payload, control)
    cache.require(recipes() == before_recipes and tools(run) == observed, 'recipe or tool drift')
    for path, digest_provider in actual_abi['providers'].items():
        cache.require(cache.file_sha(Path(path)) == digest_provider, 'provider drift')
    (root / 'result.json').write_bytes(cache.canonical({'schema': 'uds-geos-capi-fresh-local-v1',
        'identity': identity, 'qualification': proof, 'input_key': key, 'capsule_sha256': digest,
        'local_oci_subject': subject, 'producer_control': control,
        'configured_int64_probe_sha256': cache.file_sha(root / 'int64-abi'), 'registry_roundtrip': False,
        'publisher_authenticated': False, 'sdk_accepted': False, 'security_accepted': False}))


def acquire(directory):
    directory.mkdir(parents=True, exist_ok=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for name, entry in (('geos.tar.bz2', LOCK['component']['archive']),
                        ('geos-pgsc.patch', LOCK['component']['patch'])):
        target = directory / name
        if not target.exists():
            with opener.open(entry['url'], timeout=30) as response, target.open('xb') as stream:
                raw = response.read(entry['bytes'] + 1)
                stream.write(raw)
        raw = cache.read(target, 2 * 1024**2)
        cache.require(len(raw) == entry['bytes'] and cache.sha(raw) == entry['sha256'], 'locked source differs')


def launch(engine, output, inputs, mode):
    cache.external_separate(output, inputs)
    engine_env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(Path.home())}
    run = Runner(output, 2100)
    info = cache.decode(run([engine, 'info', '--format', 'json'], 'engine-info', env=engine_env))
    host = info['host'] if 'host' in info else info
    arch = host.get('arch') or host.get('Architecture')
    arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(arch, arch)
    cache.require(arch in ('amd64', 'arm64'), 'unsupported engine host architecture')
    image = LOCK['base_image']['reference']
    details = cache.decode(run([engine, 'image', 'inspect', image], 'image-inspect', env=engine_env))[0]
    cache.require(details['Id'].removeprefix('sha256:') == LOCK['base_image']['config'][7:]
                  and details['Architecture'] == 'amd64' and details['Os'] == 'linux', 'pinned image config differs')
    name = 'uds-geos-' + uuid.uuid4().hex
    # With all capabilities dropped, Docker's default root cannot traverse a
    # runner-owned 0700 bind mount. Use the invoker's identity, retaining the
    # private directory and every isolation flag. Rootless Podman already maps
    # its container root to the invoker; keep that existing mapping unchanged.
    user = ['--user', f'{os.getuid()}:{os.getgid()}'] if 'host' not in info else []
    command = [engine, 'run', *user, '--rm', '--pull=never', '--name', name, '--platform', 'linux/amd64', '--network', 'none',
        '--read-only', '--cpus', '1', '--memory', '3g', '--pids-limit', '256', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--tmpfs', '/tmp:rw,size=128m',
        '-v', str(ROOT) + ':/repo:ro', '-v', str(output) + ':/build:rw',
        '-v', str(inputs) + ':/inputs:ro', '--entrypoint', '/usr/bin/env', image, '-i',
        *[k + '=' + v for k, v in ENV.items()], '/usr/bin/python3',
        '/repo/takx/geos-cache/' + ('producer.py' if mode == 'build' else 'qualify.py'),
        '--inside', '--engine-host-arch', arch]
    try:
        run(command, 'isolated-' + mode, timeout=2050, env=engine_env)
    finally:
        cleanup_owned(engine, name, engine_env)


def cleanup_owned(engine, name, environment):
    # Cleanup must not reuse the expired build deadline or exhausted output quota.
    cache.require(re.fullmatch('uds-geos-[0-9a-f]{32}', name), 'owned UUID container required')
    subprocess.run([engine, 'rm', '-f', name], env=environment, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, timeout=15, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true')
    parser.add_argument('--engine-host-arch', choices=['amd64', 'arm64'])
    parser.add_argument('--engine', default='docker')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--source-cache', type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if args.inside:
        source_build(args.engine_host_arch)
    else:
        cache.require(args.output is not None and args.source_cache is not None, 'output and source cache required')
        cache.external_separate(args.output, args.source_cache)
        cache.new_directory(args.output)
        acquire(args.source_cache)
        launch(args.engine, args.output, args.source_cache, 'build')


if __name__ == '__main__':
    main()
