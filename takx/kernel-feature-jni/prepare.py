#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Verify or prepare ten pinned feature callback sources; no compiler or native launch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
FILES = {'takkernel/engine/src/main/jni/jnativelayer.cpp', 'takkernel/engine/src/main/jni/interop/core/ManagedLayer.cpp', 'takkernel/engine/src/main/jni/interop/renderer/core/ManagedGLLayerSpi2.cpp', 'takkernel/engine/src/main/jni/interop/feature/Interop.cpp', 'takkernel/engine/src/main/jni/interop/InterfaceMarshalContext.h', 'takkernel/engine/src/main/jni/interop/renderer/core/ManagedGLLayer2.cpp', 'takkernel/engine/src/main/jni/interop/Pointer.h', 'takkernel/engine/src/main/jni/interop/feature/ManagedFeatureDefinition.cpp', 'takkernel/engine/src/main/jni/interop/feature/ManagedFeatureDataStore2.cpp', 'takkernel/engine/src/main/jni/jnativefeaturedatastore4.cpp'}
CONTROLS = {'tests/PointerEmptyControls.cpp', 'tests/PointerEmptyControls.java', 'tests/PointerLifetimeControls.java', 'tests/PointerLifetimeControls.cpp', 'tests/LayerInboundControls.java', 'tests/LayerInboundControls.cpp', 'tests/LayerClassInitControls.java', 'tests/LayerClassInitControls.cpp', 'tests/LayerRemovalControls.java', 'tests/LayerRegistrationFault.java', 'tests/LayerVisibilityControls.java', 'tests/FeatureRegistrationFault.cpp', 'tests/ContourDrawProbe.java', 'tests/LayerConstructorFault.java', 'tests/GLSubjectFault.cpp', 'tests/GLSubjectFault.java', 'tests/FeatureRegistrationFault.java'}
FLAGS = ('native_builder_integration', 'strict_callback_accepted', 'rendered_layer_accepted', 'full_sdk_accepted', 'full_client_accepted')
LIMIT = 4 * 1024 * 1024


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink input refused')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= LIMIT, 'bounded regular input required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        data = source.read(LIMIT + 1)
    require(len(data) <= LIMIT, 'input size bound')
    return data


def contract():
    lock = json.loads(regular(HERE / 'inputs.lock.json'))
    patch = regular(HERE / 'checked-feature-callbacks.patch')
    require(type(lock['schema']) is int and lock['schema'] == 1 and
            lock['profile'] == 'native07-checked-feature-callbacks-v11' and
            lock['official_revision'] == '9f6893dd657feacc35ec5de03dad721c2e44170e' and
            lock['actual_kernel_version'] == '8.30.5' and set(lock['files']) == FILES and lock['parent_native_sha256'] == '54630be999ac532f4724e0c62a5197e42f16f3244e1063022d6121e09cd8990e',
            'source identity differs')
    require(all(lock[key] is False for key in FLAGS), 'source preparation cannot grant acceptance')
    for images in lock['files'].values():
        require(set(images) == {'before', 'after'} and all(isinstance(value, str) and
                re.fullmatch('[a-f0-9]{64}', value) for value in images.values()), 'source digest differs')
    require(digest(patch) == lock['patch_sha256'], 'patch bytes differ')
    for marker in (b'--- a/', b'+++ b/'):
        names = re.findall(b'^' + re.escape(marker) + b'([^\r\n]+)', patch, re.MULTILINE)
        require(len(names) == len(FILES) and {name.decode() for name in names} == FILES, 'patch source membership differs')
    require(set(lock['owned_controls']) == CONTROLS, 'control membership differs')
    for name, pin in lock['owned_controls'].items():
        require(digest(regular(HERE / name)) == pin, 'control source bytes differ')
    return lock, patch


def prepare(source, output, parent_receipt):
    lock, patch = contract()
    source, output = Path(source).absolute(), Path(output).absolute()
    require(not any(p.is_symlink() for p in (source, *source.parents, output, *output.parents)), 'symlink root refused')
    for root in (source, HERE.parents[1], Path(parent_receipt).absolute().parent):
        require(output != root and not output.is_relative_to(root) and not root.is_relative_to(output), 'output overlaps inputs')
    require(output.parent.is_dir() and not output.exists(), 'fresh output required')
    parent = regular(parent_receipt)
    require(digest(parent) == lock['parent_native_sha256'] == '54630be999ac532f4724e0c62a5197e42f16f3244e1063022d6121e09cd8990e', 'qualified parent receipt differs')
    producer = regular(HERE / 'prepare.py')
    originals = {name: regular(source / name) for name in FILES}
    for name, data in originals.items():
        require(digest(data) == lock['files'][name]['before'], 'official source bytes differ')
    tool = Path(shutil.which('patch') or 'patch').resolve(strict=True)
    tool_hash = hashlib.sha256(tool.read_bytes()).hexdigest()
    output.mkdir(mode=0o700)
    try:
        for name, data in originals.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        with tempfile.TemporaryFile() as stream:
            stream.write(patch)
            stream.seek(0)
            env = {key: value for key, value in os.environ.items()
                   if key not in ('BASH_ENV', 'ENV') and not key.startswith(('LD_', 'DYLD_'))}
            env['LC_ALL'] = 'C'
            result = subprocess.run([str(tool), '--batch', '--fuzz=0', '--no-backup-if-mismatch',
                                     '-p1', '-d', str(output)], stdin=stream,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20, env=env)
        require(result.returncode == 0 and len(result.stdout) + len(result.stderr) <= LIMIT, 'exact patch failed')
        require({str(path.relative_to(output)) for path in output.rglob('*') if path.is_file()} == FILES,
                'prepared source membership differs')
        for name in FILES:
            require(digest(regular(output / name)) == lock['files'][name]['after'] and
                    regular(source / name) == originals[name], 'prepared or original source changed')
        require(contract() == (lock, patch) and regular(parent_receipt) == parent and regular(HERE / 'prepare.py') == producer and
                hashlib.sha256(tool.read_bytes()).hexdigest() == tool_hash, 'source recipe changed')
        receipt = {'schema': 1, 'type': 'native07-checked-feature-source', 'scope': lock['scope'],
                   'official_revision': lock['official_revision'], 'parent_native_sha256': lock['parent_native_sha256'], 'files': lock['files'],
                   'patch_sha256': digest(patch), 'producer_sha256': digest(producer),
                   'patch_tool_sha256': tool_hash, 'owned_controls': lock['owned_controls'],
                   'native_build_or_execution': False, **{key: False for key in FLAGS}}
        (output / 'prepared.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    except BaseException:
        (output / 'first-failure.json').write_text('{"source_prepare_failed":true,"strict_callback_accepted":false}\n')
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('verify', 'prepare'), default='verify')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--parent-receipt', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.phase == 'verify':
        require(args.source is None and args.output is None and args.parent_receipt is None, 'paths require explicit prepare phase')
        contract()
    else:
        require(args.source is not None and args.output is not None and args.parent_receipt is not None, 'source and fresh output required')
        prepare(args.source, args.output, args.parent_receipt)


if __name__ == '__main__':
    main()
