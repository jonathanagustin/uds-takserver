#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in historical GEOS 3.4.3 static PIC source profile, not SDK acceptance."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import time
import sys

HERE = Path(__file__).resolve().parent
HELPERS = {'native': HERE.parent / 'native-deps/build.py', 'las': HERE.parent / 'native-las/build.py'}
MODULES = {}
for name, path in HELPERS.items():
    spec = importlib.util.spec_from_file_location('geos_' + name, path)
    MODULES[name] = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(MODULES[name])
native, las = MODULES['native'], MODULES['las']
REVISION = '9f6893dd657feacc35ec5de03dad721c2e44170e'
ARCHIVE = {'url': 'https://media.githubusercontent.com/media/TAK-Product-Center/atak-civ/' + REVISION +
           '/takthirdparty/distfiles/geos.tar.bz2', 'bytes': 1902752,
           'sha256': 'cfbf68079117c1c2b76411636444ff41d73c31093c4cab9dcc9a8c1bbe7e3897',
           'members': 1321, 'regular_files': 1176, 'expanded_bytes': 12543503}
LICENSE = {'source': 'geos-3.4.3/COPYING',
           'sha256': 'dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551'}
TOOLS = {'gcc': '14.2.0', 'make': '4.4.1', 'binutils': '2.44'}
PATCH = json.loads((HERE / 'patch.json').read_text())
FILES = ('build.py', 'inputs.lock.json', 'patch.json', 'GeosGeometry.c', 'GeosInt64.cpp', 'run-in.py')
CFLAGS = '-O2 -fPIC -std=gnu89'
CXXFLAGS = '-O2 -fPIC -std=c++11'
INTEGER_FEATURES = ('HAVE_INT64_T_64', 'HAVE_LONG_INT_64', 'HAVE_LONG_LONG_INT_64')
CONFIGURATION_FILES = ('include/config.h', 'include/geos/platform.h', 'config.log', 'config.status', 'Makefile')


def validate_lock(lock):
    if lock != {'schema_version': 1, 'revision': REVISION, 'version': '3.4.3',
                'archives': {'geos-3.4.3': ARCHIVE}, 'license': LICENSE, 'tools': TOOLS,
                'source_patch': PATCH}:
        raise ValueError('unreviewed historical GEOS source/license/tool/patch selection')


def check_patch_images(src, phase):
    for name, hashes in PATCH['files'].items():
        if native.digest(src / name) != hashes[phase]:
            raise ValueError('official GEOS patch image differs: ' + name)


def recipe():
    return {name: native.digest(HERE / name) for name in FILES} | {
        '../' + str(path.relative_to(HERE.parent)): native.digest(path) for path in HELPERS.values()}


def environment(cc, cxx=None):
    env = las.environment({'cc': {'path': cc}, 'cxx': {'path': cxx or cc}})
    for key in list(env):
        if (key.startswith(('GEOS_', 'BASH_FUNC_', 'ac_cv_', 'pgac_cv_', 'CONFIG_')) or key in
                ('MAKEFLAGS', 'GNUMAKEFLAGS', 'MFLAGS', 'MAKEFILES', 'CONFIG_SITE', 'CONFIG_SHELL', 'SHELL',
                 'AR', 'RANLIB', 'LD', 'NM', 'AS', 'STRIP', 'CPP', 'CXXCPP', 'LIBS')):
            env.pop(key)
    env.update(CFLAGS=CFLAGS, CXXFLAGS=CXXFLAGS, CONFIG_SITE='/dev/null')
    return env


def regular_files(root, names):
    result = {}
    for name in names:
        path = root / name
        if (any(parent.is_symlink() for parent in (path, *path.parents) if parent == root or root in parent.parents)
                or not path.is_file() or not 0 < path.stat().st_size <= 16 * 1024 ** 2):
            raise ValueError('missing, nonregular or oversized integer configuration/control')
        result[name] = {'bytes': path.stat().st_size, 'sha256': native.digest(path)}
    return result


def active_integer_features(raw):
    """Inspect compiler-selected definitions, including conditional fallback branches."""
    found = {}
    for line in raw.decode('ascii').splitlines():
        match = re.fullmatch(r'#define (HAVE_INT64_T_64|HAVE_LONG_INT_64|HAVE_LONG_LONG_INT_64|INT64_IS_REALLY32)(?: (.*))?', line)
        if match:
            key, value = match.groups()
            if key in found or key == 'INT64_IS_REALLY32' or value != '1':
                raise ValueError('unsupported or conflicting active integer feature')
            found[key] = True
    features = {key: key in found for key in INTEGER_FEATURES}
    if not any(features.values()):
        raise ValueError('no active GEOS 64-bit integer feature')
    return features


def integer_configuration(build, src, output, cxx, run, phase, expected=None):
    """Qualify generated headers and a real signed int64 before make and completion."""
    files = regular_files(build, CONFIGURATION_FILES)
    probe = output / 'GeosInt64.cpp'
    regular_files(output, (probe.name,))
    if native.digest(probe) != native.digest(HERE / probe.name):
        raise ValueError('integer probe source differs from recipe')
    if expected is not None and (files != expected['files'] or
            regular_files(output, ('GeosInt64.cpp', 'int64-probe')) != expected['probe_files']):
        raise ValueError('stale integer configuration or control')
    features, macros = {}, {}
    flags = [*CXXFLAGS.split(), '-Werror', '-I' + str(build / 'include'), '-I' + str(src / 'include')]
    for name, label in (('include/config.h', 'config'), ('include/geos/platform.h', 'platform')):
        raw = run([cxx, *flags, '-x', 'c++', '-dM', '-E', '-include', build / name, '/dev/null'],
                  'int64-' + phase + '-' + label, capture=True)
        features[label] = active_integer_features(raw)
        macros[label] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if features['config'] != features['platform']:
        raise ValueError('generated integer feature selections disagree')
    selected = next(key for key in ('HAVE_INT64_T_64', 'HAVE_LONG_LONG_INT_64', 'HAVE_LONG_INT_64')
                    if features['platform'][key])
    if expected is None:
        run([cxx, *flags, probe, '-o', output / 'int64-probe'], 'int64-probe-compile')
    probe_files = regular_files(output, ('GeosInt64.cpp', 'int64-probe'))
    text = run([output / 'int64-probe'], 'int64-' + phase + '-arithmetic', 10, True)
    if text != ('int64_feature=' + selected + '\nint64_bytes=8\nint64_signed=true\nint64_arithmetic=true\n').encode():
        raise ValueError('real 8-byte signed integer arithmetic control failed')
    if files != regular_files(build, CONFIGURATION_FILES) or probe_files != regular_files(output, tuple(probe_files)):
        raise ValueError('integer configuration or control changed during qualification')
    result = {'contract': 'historical-geos-int64-v1', 'cflags': CFLAGS, 'cxxflags': CXXFLAGS,
              'files': files, 'active_features': features['platform'], 'selected_feature': selected,
              'macro_outputs': macros, 'probe_files': probe_files,
              'probe_stdout_sha256': hashlib.sha256(text).hexdigest()}
    if expected is not None and result != expected:
        raise ValueError('integer configuration qualification changed')
    return result


def validate_paths(output, cache):
    output, cache = native.external(output), native.external(cache)
    if output.exists() or output == cache or output in cache.parents or cache in output.parents:
        raise ValueError('output must be new and separate from cache')
    return output, cache


def source_inventory(root):
    found = {}
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('source symlink refused')
        if path.is_file():
            found[str(path.relative_to(root))] = {'bytes': path.stat().st_size, 'sha256': native.digest(path)}
    return found


def check_sources(root, expected):
    if source_inventory(root) != expected:
        raise ValueError('untouched GEOS source changed')


def outputs(root):
    result = {}
    for name in ('GeosGeometry.c', 'consumer', 'libgeos-pic-check.so'):
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('missing or nonregular positive control')
        result[name] = {'bytes': path.stat().st_size, 'sha256': native.digest(path)}
    return result


def disk_bound(output, cache):
    size = sum(p.stat().st_size for root in (output, cache) for p in root.rglob('*')
               if p.is_file() and not p.is_symlink())
    if size > 1024 * 1024 ** 2:
        raise ValueError('GEOS profile exceeds 1 GiB output/cache bound')
    return size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    for name, default in (('cc', 'gcc'), ('cxx', 'g++'), ('make', 'make'), ('patch', 'patch'), ('readelf', 'readelf')):
        parser.add_argument('--' + name, default=default)
    parser.add_argument('--fetch-dependencies', action='store_true')
    args = parser.parse_args()
    if (platform.system(), platform.machine()) != ('Linux', 'x86_64'):
        raise ValueError('only Linux amd64 historical component profile validated')
    lock = json.loads((HERE / 'inputs.lock.json').read_text())
    validate_lock(lock)
    initial = recipe()
    output, cache = validate_paths(args.output, args.cache)
    output.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)
    deadline, commands = time.monotonic() + 600, []
    cc = str(Path(shutil.which(args.cc) or args.cc).resolve())
    cxx = str(Path(shutil.which(args.cxx) or args.cxx).resolve())
    env = environment(cc, cxx)

    def run(command, name, timeout=30, capture=False):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('GEOS profile total command deadline exhausted')
        limit = min(timeout, remaining)
        commands.append({'name': name, 'args': list(map(str, command)), 'timeout': limit})
        (output / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
        return native.run(list(map(str, command)), output, name, timeout=limit, env=env, capture=capture)

    tools = {}
    for name, value in (('cc', cc), ('cxx', cxx), ('make', args.make), ('patch', args.patch),
                        ('readelf', args.readelf), ('ar', 'ar'), ('ranlib', 'ranlib'), ('ldd', 'ldd')):
        path = Path(shutil.which(value) or value).resolve()
        version = run([path, '--version'], 'tool-' + name, capture=True).decode().strip()
        tools[name] = {'path': str(path), 'sha256': native.digest(path), 'version': version}
    if (not tools['cc']['version'].splitlines()[0].endswith(' ' + TOOLS['gcc']) or
            tools['make']['version'].splitlines()[0] != 'GNU Make ' + TOOLS['make'] or
            not tools['cxx']['version'].splitlines()[0].endswith(' ' + TOOLS['gcc']) or
            any(not tools[name]['version'].splitlines()[0].endswith(' ' + TOOLS['binutils'])
                for name in ('readelf', 'ar', 'ranlib'))):
        raise ValueError('toolchain differs from historical GEOS profile')
    env.update(AR=tools['ar']['path'], RANLIB=tools['ranlib']['path'])
    archive = native.acquire(cache, ARCHIVE, args.fetch_dependencies)
    source = output / 'source'
    source.mkdir()
    expected = las.unpack(archive, source, 'geos-3.4.3', ARCHIVE)
    native.verified(archive, ARCHIVE)
    check_sources(source, expected)
    (output / 'sources.json').write_text(json.dumps(expected, sort_keys=True) + '\n')
    src = source / 'geos-3.4.3'
    if native.digest(source / LICENSE['source']) != LICENSE['sha256']:
        raise ValueError('historical GEOS license differs')
    patch_entry = {key: PATCH[key] for key in ('url', 'bytes', 'sha256')}
    patch_file = native.acquire(cache, patch_entry, args.fetch_dependencies)
    check_patch_images(src, 'before')
    run([sys.executable, HERE / 'run-in.py', src, tools['patch']['path'], '--fuzz=0', '--batch',
         '-p1', '-i', patch_file], 'official-patch')
    check_patch_images(src, 'after')
    patched = source_inventory(source)
    changed = {name for name in expected if expected[name] != patched[name]}
    if changed != {'geos-3.4.3/' + name for name in PATCH['files']} or set(expected) != set(patched):
        raise ValueError('unexpected source patch changes')
    expected = patched
    (output / 'sources.json').write_text(json.dumps(expected, sort_keys=True) + '\n')
    build, prefix = output / 'build', output / 'install'
    build.mkdir()
    run([sys.executable, HERE / 'run-in.py', build, src / 'configure', '--disable-shared',
         '--enable-static', '--with-pic', '--disable-python', '--disable-ruby', '--disable-php',
         '--prefix=' + str(prefix)], 'configure', 90)
    check_sources(source, expected)
    disk_bound(output, cache)
    shutil.copyfile(HERE / 'GeosInt64.cpp', output / 'GeosInt64.cpp')
    integer = integer_configuration(build, src, output, cxx, run, 'before')
    run([tools['make']['path'], '-C', build, '-j1'], 'source-build', 450)
    run([tools['make']['path'], '-C', build, '-j1', 'install'], 'source-install', 60)
    library = prefix / 'lib/libgeos.a'
    if not library.is_file() or library.is_symlink() or list((prefix / 'lib').glob('*.so*')):
        raise ValueError('expected source-built static GEOS only')
    capi = prefix / 'lib/libgeos_c.a'
    if not capi.is_file() or capi.is_symlink():
        raise ValueError('missing genuine static C API')
    shutil.copyfile(source / LICENSE['source'], prefix / 'COPYING')
    installed = native.inventory(prefix)
    shutil.copyfile(HERE / 'GeosGeometry.c', output / 'GeosGeometry.c')
    run([cc, '-std=c11', '-O1', '-I' + str(prefix / 'include'), output / 'GeosGeometry.c',
         capi, library, '-lstdc++', '-lm', '-o', output / 'consumer'], 'consumer-compile')
    text = run([output / 'consumer'], 'positive-geometry', 10, True).decode()
    if text != 'actual_release=3.4.3-CAPI-1.8.3 r4285\ngeometry_and_error_controls=true\n':
        raise ValueError('historical GEOS genuine geometry controls incomplete')
    run([cc, '-shared', '-Wl,--whole-archive', capi, library, '-Wl,--no-whole-archive',
         '-Wl,--no-undefined', '-lstdc++', '-lm', '-o', output / 'libgeos-pic-check.so'], 'strict-pic-link')
    if b'TEXTREL' in run([tools['readelf']['path'], '-d', output / 'libgeos-pic-check.so'], 'pic-relocations', capture=True):
        raise ValueError('TEXTREL in historical GEOS')
    closure = run([tools['ldd']['path'], output / 'consumer'], 'runtime-closure', capture=True).decode()
    if 'not found' in closure or 'libgeos' in closure.lower():
        raise ValueError('host GEOS runtime fallback or missing dependency')
    runtime = {str(Path(word).resolve()): native.digest(Path(word)) for line in closure.splitlines()
               for word in line.split() if word.startswith('/') and Path(word).is_file()}
    if not runtime:
        raise ValueError('runtime closure unavailable')
    integer_configuration(build, src, output, cxx, run, 'after', integer)
    check_sources(source, expected)
    native.verified(archive, ARCHIVE)
    if installed != native.inventory(prefix) or initial != recipe():
        raise ValueError('installed GEOS or recipe changed during positive controls')
    if native.digest(output / 'GeosGeometry.c') != initial['GeosGeometry.c']:
        raise ValueError('positive consumer source changed')
    for tool in tools.values():
        if native.digest(Path(tool['path'])) != tool['sha256']:
            raise ValueError('build tool changed')
    record = {'schema_version': 1, 'profile': 'historical-geos-native', 'version': '3.4.3',
              'actual_release': '3.4.3-CAPI-1.8.3 r4285', 'source_revision': REVISION,
              'recipe': initial, 'archive': ARCHIVE, 'license': LICENSE, 'tools': tools,
              'source_inventory': expected, 'installed': installed, 'outputs': outputs(output),
              'runtime_closure': runtime, 'build_jobs': 1, 'disk_bytes': disk_bound(output, cache),
              'command_deadline_s': 600, 'source_patch': PATCH, 'integer_configuration': integer,
              'geometry_and_error_controls': True,
              'strict_pic': True, 'jni_acceptance': False, 'modern_geos_api_compatibility': False,
              'security_acceptance': False, 'target_image_abi_acceptance': False, 'full_ttp_acceptance': False,
              'kernel_sdk_acceptance': False, 'takx_acceptance': False, 'full_upstream_test_suite_ran': False,
              'fault_parser_experiments_run': False}
    (output / 'complete.json').write_text(json.dumps(record, indent=2) + '\n')
    print('Historical GEOS 3.4.3 source-built PIC and geometry/error controls passed')


if __name__ == '__main__':
    main()
