#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Build the Kernel Java overlay JAR of the desktop prototype.

Booting the official TAKX 5.7 client showed places where the composed public
Kernel (8.30.5 plus project patches) does not honour a contract the client
relies on at run time. Each such place is a small source patch in
``kernel-overlay/*.patch``. This script applies the patches, with fuzz 0, to a
copy of the composed Kernel Java sources that the Kernel stage compiled,
compiles only the patched files against the composed Kernel classes with the
Kernel stage's own javac flags, and writes the resulting classes into one
overlay JAR. The assembly puts that JAR ahead of the Kernel JAR in the
``gov.tak.kernel`` module. The Kernel JAR, its receipt and its controls are
not touched; the controls were therefore run without this overlay.

Nothing here adds an API or fakes a result: every overlay class is the Kernel
class of the same name rebuilt from its real source with the recorded diff.
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile

LABEL = 'Kernel 8.30.5 + project patches, desktop-prototype Java overlay'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', default=os.environ.get('TAKX_PROTO_ROOT', '/var/lib/takx-proto'))
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    started = time.time()
    here = os.path.dirname(os.path.abspath(__file__))
    patch_dir = os.path.join(here, 'kernel-overlay')
    patches = sorted(name for name in os.listdir(patch_dir) if name.endswith('.patch'))
    if not patches:
        raise SystemExit('kernel_overlay: no patches')
    composed = os.path.join(args.root, 'P', 'kernel-composed-java')
    with open(os.path.join(composed, 'composed-java.json')) as stream:
        record = json.load(stream)
    kernel_classes = record['jar']['path']
    if sha256(kernel_classes) != record['jar']['sha256']:
        raise SystemExit('kernel_overlay: the composed Kernel classes JAR changed since it was recorded')
    out = args.output
    if os.path.exists(out):
        raise SystemExit('kernel_overlay: output %s exists' % out)
    stage = os.path.join(out, 'stage')
    classes = os.path.join(out, 'classes')
    os.makedirs(stage)
    os.makedirs(classes)

    # Copy only the files the patches name, verified against the digests the
    # Kernel stage recorded for the sources it compiled.
    targets = []
    for name in patches:
        with open(os.path.join(patch_dir, name), encoding='utf-8') as stream:
            for line in stream:
                if line.startswith('+++ b/'):
                    targets.append(line[6:].split('\t')[0].strip())
    if len(set(targets)) != len(targets):
        raise SystemExit('kernel_overlay: two patches touch the same file')
    preimages = {}
    for relative in targets:
        source = os.path.join(composed, 'stage', relative)
        expected = record['sources'].get(relative)
        if expected is None or sha256(source) != expected:
            raise SystemExit('kernel_overlay: %s is not the source the Kernel stage compiled' % relative)
        target = os.path.join(stage, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        preimages[relative] = expected
    applied = []
    for name in patches:
        path = os.path.join(patch_dir, name)
        subprocess.run(['patch', '--batch', '--fuzz=0', '--no-backup-if-mismatch', '-p1', '-d', stage, '-i', path],
                       check=True)
        applied.append({'patch': 'takx/desktop-prototype/assemble/kernel-overlay/' + name, 'sha256': sha256(path)})

    with open(os.path.join(composed, 'classpath.txt')) as stream:
        classpath = [line.strip() for line in stream if line.strip()]
    javac = record['javac']['path']
    if sha256(javac) != record['javac']['sha256']:
        raise SystemExit('kernel_overlay: javac is not the one the Kernel stage used')
    command = [javac, '--release', '11', '-encoding', 'UTF-8', '-proc:none', '-implicit:none',
               '-sourcepath', '', '-Xlint:none', '-cp', ':'.join([kernel_classes] + classpath), '-d', classes]
    command += [os.path.join(stage, relative) for relative in targets]
    subprocess.run(command, check=True)

    produced = []
    for base, dirs, names in os.walk(classes):
        dirs.sort()
        produced += [os.path.relpath(os.path.join(base, name), classes) for name in sorted(names)]
    with zipfile.ZipFile(kernel_classes) as archive:
        known = set(archive.namelist())
    unknown = [name for name in produced if name not in known]
    if unknown:
        # A patch may not introduce a class the Kernel does not already have.
        raise SystemExit('kernel_overlay: classes absent from the Kernel JAR: %s' % unknown)
    jar = os.path.join(out, 'kernel-8.30.5-prototype-java-overlay.jar')
    with zipfile.ZipFile(jar, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in produced:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(classes, name), 'rb') as stream:
                archive.writestr(info, stream.read())
    receipt = {
        'schema': 1, 'stage': 'kernel-overlay', 'status': 'ok', 'label': LABEL,
        'kernel_classes_jar': record['jar'], 'patches': applied,
        'source_preimages': preimages,
        'source_postimages': {relative: sha256(os.path.join(stage, relative)) for relative in targets},
        'classes': {name: sha256(os.path.join(classes, name)) for name in produced},
        'jar': {'path': jar, 'sha256': sha256(jar), 'bytes': os.path.getsize(jar)},
        'javac': record['javac'], 'command': command,
        'kernel_controls_rerun_with_overlay': False,
        'seconds': round(time.time() - started, 1),
    }
    with open(os.path.join(out, 'receipt.json'), 'w', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=1, sort_keys=True)
        stream.write('\n')
    print(json.dumps({'patches': len(applied), 'classes': len(produced), 'jar_sha256': receipt['jar']['sha256']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
