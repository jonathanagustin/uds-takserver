#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Prepare the complete locked Kernel native source projection without building."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BASELINE = HERE.parent / 'kernel-native'
SPEC = importlib.util.spec_from_file_location('context_source_prepare', HERE / 'prepare.py')
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)
BASELINE_LOCK_SHA256 = '92aa9ad3cb9674f9d18166bca1878eb12ed227eb706e63d05526113d981dbade'
CAP = 64 * 1024**2
FAILURE_RESERVE = 2 * 1024**2
SPARSE_CAP = 4 * 1024**2
SECONDS = 300
NATIVE_PREFIXES = ('takkernel/engine/src/main/cpp/', 'takkernel/engine/src/main/jni/')
LICENSE = {'bytes': 55947, 'git_blob': '652daaf58c531d8abaa7fc6978d4e87427276c55',
           'sha256': '3db8b919e1f1cb3dff5114ea875d3a758ff367e6490622780d0f7092daebd8f9'}
FLAGS = ('native_build_performed', 'native_provider_verified', 'native_graph_configured',
         'whole_java_headers_generated', 'runtime_acceptance', 'gpu_qualified', 'full_sdk_accepted', 'client_accepted')


def require(value, message):
    if not value:
        raise ValueError(message)


def external(path):
    path = Path(path).absolute()
    require(not any(part.is_symlink() for part in (path, *path.parents)), 'symlink root refused')
    return path.resolve(strict=False)


def relative(name):
    path = PurePosixPath(name)
    require(not path.is_absolute() and bool(path.parts) and '..' not in path.parts and '\\' not in name,
            'unsafe native source path')
    require(name.startswith(('takkernel/engine/', 'khronos/OpenGL/api/GLES/')), 'unrelated native source path')
    return Path(*path.parts)


def contract():
    context, _ = P.load_contract()
    baseline_data = P.regular(BASELINE / 'inputs.lock.json')
    inputs = {str(BASELINE / 'inputs.lock.json'): P.digest(baseline_data)}
    baseline = json.loads(baseline_data)
    require(P.digest(json.dumps(baseline, sort_keys=True).encode()) == BASELINE_LOCK_SHA256,
            'reviewed baseline native graph contract differs')
    require(baseline['revision'] == context['official_revision'] and baseline['kernel_version'] == '8.30.5',
            'baseline and composed source revisions differ')
    require(set(baseline['patches']) == set(context['baseline_patches']), 'baseline patch membership differs')
    for name, entry in context['baseline_patches'].items():
        require(baseline['patches'][name] == {key: value for key, value in entry.items() if key != 'patch_sha256'},
                'baseline source patch images differ')
        path = BASELINE / 'patches' / name
        inputs[str(path)] = P.digest(P.regular(path))
        require(inputs[str(path)] == entry['patch_sha256'], 'baseline source patch bytes differ')
    registries = {}
    for name in ('native-sources.lock.json', 'gles1-headers.lock.json'):
        data = P.regular(BASELINE / name)
        require(P.digest(data) == baseline['locks'][name], 'locked native source registry differs')
        registries[name] = json.loads(data)
        inputs[str(BASELINE / name)] = P.digest(data)
    native, gles = (registries[name] for name in ('native-sources.lock.json', 'gles1-headers.lock.json'))
    require(len(native) == baseline['native_file_count'] == 1396 and len(gles) == 3,
            'complete baseline native source membership required')
    require(sum(row['bytes'] for row in native.values()) < CAP // 2, 'native source reservation differs')
    for name, entry in (native | gles).items():
        relative(name)
        require(isinstance(entry['bytes'], int) and 0 <= entry['bytes'] <= 16 * 1024**2 and
                re.fullmatch('[0-9a-f]{64}', entry['sha256']) and
                re.fullmatch('[0-9a-f]{40}', entry['git_blob']), 'invalid locked source entry')
    composed = {name: pin for name, pin in context['stages']['client_jni'].items()
                if name.startswith(NATIVE_PREFIXES) and pin is not None}
    require(len(composed) == 42 and len(set(composed) - set(native)) == 3,
            'composed native image inventory differs')
    for name in set(native) & set(composed):
        require(native[name]['sha256'] == context['stages']['official'][name],
                'native graph and composed official preimage differ')
    require(all(entry['target'] in composed for entry in baseline['patches'].values()),
            'baseline source corrections absent from composition')
    for path in (Path(__file__), HERE / 'prepare.py', HERE / 'inputs.lock.json', HERE / 'source-scope.json',
                 HERE / 'render-context-control.patch', HERE / 'tests/BindingStateTest.java',
                 BASELINE / 'CMakeLists.txt', REPO / 'LICENSE'):
        inputs[str(path)] = P.digest(P.regular(path))
    return context, baseline, native, gles, composed, inputs


def check_bytes(data, entry):
    require(len(data) == entry['bytes'] and P.digest(data) == entry['sha256'], 'locked official source bytes differ')
    require(hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == entry['git_blob'],
            'official Git blob identity differs')


class Runner(P.Runner):
    def __init__(self, output, deadline=None):
        super().__init__(output)
        self.deadline = time.monotonic() + SECONDS if deadline is None else deadline

    def check(self, reserve=131072):
        require(time.monotonic() < self.deadline, 'complete native source aggregate deadline')
        total = 0
        for path in self.output.rglob('*'):
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), 'nonregular source output refused')
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
        require(total + reserve + FAILURE_RESERVE <= CAP, 'complete native source output cap')
        require(shutil.disk_usage(self.output).free >= P.FLOOR + reserve, 'complete native source free-space floor')


def source_inventory(root):
    result = {}
    for path in root.rglob('*'):
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            continue
        require(stat.S_ISREG(info.st_mode), 'nonregular source output refused')
        data = P.regular(path, 16 * 1024**2)
        result[str(path.relative_to(root))] = {'sha256': P.digest(data), 'bytes': len(data)}
    return result


def sparse_inventory(root, expected, deadline):
    allowed_directories = set()
    for name in expected:
        P.relative(name)
        allowed_directories.update(str(path) for path in PurePosixPath(name).parents if str(path) != '.')
    entry_limit = len(expected) + len(allowed_directories)
    pending, result, visited, total = [(root, '')], {}, 0, 0
    while pending:
        require(time.monotonic() < deadline, 'complete native source aggregate deadline')
        directory, prefix = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                require(time.monotonic() < deadline, 'complete native source aggregate deadline')
                visited += 1
                require(visited <= entry_limit, 'sparse source entry bound exceeded')
                name = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if name in allowed_directories and stat.S_ISDIR(info.st_mode):
                    pending.append((Path(entry.path), name + '/'))
                else:
                    require(name in expected and stat.S_ISREG(info.st_mode),
                            'unexpected or nonregular sparse source member')
                    require(name not in result and total + info.st_size <= SPARSE_CAP, 'sparse source byte/member bound')
                    data = P.regular(Path(entry.path))
                    total += len(data)
                    require(total <= SPARSE_CAP, 'sparse source byte bound')
                    result[name] = {'sha256': P.digest(data), 'bytes': len(data)}
    require({name: row['sha256'] for name, row in result.items()} == expected,
            'complete exact current sparse source projection required')
    return result


def prepare(args):
    deadline = time.monotonic() + SECONDS
    context, baseline, native, gles, composed, inputs = contract()
    source, sparse, output = (external(path) for path in (args.source, args.prepared_source, args.output))
    require(source.is_dir() and sparse.is_dir(), 'cached official Git and exact sparse source roots required')
    for root in (source, sparse, REPO):
        require(output != root and not output.is_relative_to(root) and not root.is_relative_to(output),
                'fresh output must be disjoint from inputs')
        for ancestor in (output, *output.parents):
            require(not ancestor.exists() or not ancestor.samefile(root),
                    'fresh output must be physically disjoint from inputs')
    require(not output.exists() and output.parent.is_dir(), 'new output under an existing parent required')
    expected_sparse = {name: pin for name, pin in context['stages']['client_jni'].items() if pin is not None}
    require(len(expected_sparse) == 61, 'complete current sparse source membership differs')
    sparse_inventory(sparse, expected_sparse, deadline)
    for name, pin in expected_sparse.items():
        inputs[str(sparse / name)] = pin
    git = Path(shutil.which(args.git) or args.git).resolve(strict=True)
    git_pin = P.digest(P.regular(git, 32 * 1024**2))
    require(shutil.disk_usage(output.parent).free >= P.FLOOR + CAP, 'source floor and full output reservation required')
    output.mkdir(mode=0o700)
    runner = Runner(output, deadline)
    layout, declarations = output / 'layout', output / 'gles1-declarations'
    layout.mkdir()
    declarations.mkdir()
    try:
        license_data = runner.run([git, '--no-lazy-fetch', '--no-replace-objects', '-C', source,
                                   'cat-file', 'blob', LICENSE['git_blob']])
        check_bytes(license_data, LICENSE)
        (output / 'UPSTREAM-LICENSE.md').write_bytes(license_data)
        (output / 'PROJECT-LICENSE').write_bytes(P.regular(REPO / 'LICENSE'))
        for name, entry in (native | gles).items():
            data = runner.run([git, '--no-lazy-fetch', '--no-replace-objects', '-C', source,
                               'cat-file', 'blob', entry['git_blob']], limit=16 * 1024**2)
            check_bytes(data, entry)
            target = layout / relative(name) if name in native else declarations.joinpath(*relative(name).parts[-2:])
            require(not target.exists(), 'duplicate projected source path')
            target.parent.mkdir(parents=True, exist_ok=True)
            runner.check(len(data))
            target.write_bytes(data)
        for name, pin in composed.items():
            data = P.regular(sparse / name)
            require(P.digest(data) == pin, 'composed source changed before transfer')
            target = layout / relative(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            runner.check(len(data))
            target.write_bytes(data)
        expected = {name: row['sha256'] for name, row in native.items()} | composed
        inventory = source_inventory(layout)
        require({name: row['sha256'] for name, row in inventory.items()} == expected and len(inventory) == 1399,
                'complete final native source inventory differs')
        graphs = {name: native[name]['sha256'] for name in native
                  if name.endswith('.cmake') or name.endswith('/CMakeLists.txt')}
        require(all(inventory[name]['sha256'] == pin for name, pin in graphs.items()),
                'original native CMake graph changed')
        for name, pin in inputs.items():
            require(P.digest(P.regular(Path(name))) == pin, 'consumed recipe or sparse source changed')
        require(P.digest(P.regular(git, 32 * 1024**2)) == git_pin, 'source tool changed')
        require(contract()[-1] == {name: pin for name, pin in inputs.items() if not name.startswith(str(sparse) + '/')},
                'source contract changed during preparation')
        record = {'schema': 1, 'type': 'complete-locked-kernel-native-source-projection', 'completed': True,
                  'scope': 'Kernel native source projection only; dependencies, generated JNI headers, configure, compilation, linking and runtime remain required',
                  'official_revision': context['official_revision'], 'actual_kernel_version': '8.30.5',
                  'official_repository': 'https://github.com/TAK-Product-Center/atak-civ',
                  'upstream_license': {'path': 'UPSTREAM-LICENSE.md', **LICENSE},
                  'project_license': {'path': 'PROJECT-LICENSE', 'sha256': inputs[str(REPO / 'LICENSE')]},
                  'inputs': inputs, 'git': {'path': str(git), 'sha256': git_pin}, 'commands': runner.records,
                  'native_source_files': len(inventory), 'native_sources': inventory,
                  'gles1_declarations': source_inventory(declarations), 'unmodified_cmake_graph_files': graphs,
                  'baseline_expected_translation_units': baseline['source_counts'],
                  'composed_native_images': composed, 'paired_java_source_images': {
                      name: pin for name, pin in expected_sparse.items() if name.endswith('.java')},
                  'budgets': {'seconds': SECONDS, 'output_bytes': CAP, 'free_floor_bytes': P.FLOOR,
                              'sparse_input_bytes': SPARSE_CAP, 'sparse_input_files': len(expected_sparse)},
                  'remaining_gates': ['14 cross-bound provider proof families including Cesium sources/dependencies',
                                      'locked tinygltf and tinygltfloader source archives and header staging',
                                      'final whole-Java composition and regenerated JNI headers',
                                      'reviewed Linux amd64 native toolchain and actual complete CMake configuration',
                                      'fresh compilation of every native source and changed-header consumer',
                                      'link, ELF/provider audits and actual-library strict JNI controls'],
                  **{name: False for name in FLAGS}}
        data = (json.dumps(record, indent=2, sort_keys=True) + '\n').encode()
        runner.check(len(data))
        (output / 'receipt.json').write_bytes(data)
    except BaseException as error:
        failure = {'completed': False, 'error': str(error)[:4096], 'commands': runner.records,
                   **{name: False for name in FLAGS}}
        data = (json.dumps(failure, indent=2, sort_keys=True) + '\n').encode()
        if len(data) <= 2 * 1024**2:
            (output / 'failed.json').write_bytes(data)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('verify', 'prepare'), default='verify')
    for name in ('source', 'prepared-source', 'output'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--git', default='git')
    args = parser.parse_args(argv)
    paths = (args.source, args.prepared_source, args.output)
    if args.phase == 'verify':
        require(all(path is None for path in paths), 'paths require explicit source prepare phase')
        contract()
    else:
        require(all(path is not None for path in paths), 'explicit cached Git, sparse source and fresh output required')
        prepare(args)


if __name__ == '__main__':
    main()
