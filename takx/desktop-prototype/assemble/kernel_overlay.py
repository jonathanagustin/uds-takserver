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

Recipe refresh: the Kernel stage is expensive, so a composed Kernel can be
older than the current ``takx/kernel-render-context`` patch. When the Java
image that recipe's lock pins for a file differs from the source the Kernel
stage compiled, this script runs the recipe's own ``prepare.py`` (which checks
every locked stage image), takes exactly those files from its output and
compiles them into the overlay as well. It refuses when the Kernel stage had
modified such a file after the recipe, or when a regenerated JNI header
differs from the one the native libraries were built with; the Kernel lane
must be rebuilt then. After a Kernel lane rebuild the refresh finds nothing.

Nothing here adds an API or fakes a result: every overlay class is a Kernel
class rebuilt from its real source, with the recorded diff or from the
recipe's locked image.
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
RECIPE = 'takx/kernel-render-context'


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def recipe_refresh(repo, root, record, official, out):
    """Sources of the render-context recipe that are newer than the composed Kernel.

    Returns ({relative: prepared file}, receipt entry or None)."""
    recipe = os.path.join(repo, RECIPE)
    with open(os.path.join(recipe, 'inputs.lock.json')) as stream:
        lock = json.load(stream)
    final = lock['stages'][lock['stage_order'][-1]]
    stale = sorted(name for name, pin in final.items()
                   if name.endswith('.java') and pin is not None and record['sources'].get(name) != pin)
    if not stale:
        return {}, None
    # The Kernel stage must have compiled the recipe's then-current image of
    # these files unmodified; otherwise a later layer would be lost here.
    with open(os.path.join(root, 'P', 'kernel-composed-src', 'context', 'prepared.json')) as stream:
        composed_with = json.load(stream)
    before = composed_with['stage_images'][composed_with['stage_order'][-1]]
    for name in stale:
        if record['sources'].get(name) != before.get(name):
            raise SystemExit('kernel_overlay: %s was changed after %s in the composed Kernel; rebuild the Kernel lane'
                             % (name, RECIPE))
    prepared = os.path.join(out, 'render-context-prepared')
    subprocess.run([sys.executable, '-I', '-B', os.path.join(recipe, 'prepare.py'), '--phase', 'prepare',
                    '--source', official, '--baseline-profile', os.path.join(repo, 'takx', 'kernel-native'),
                    '--texture-profile', os.path.join(repo, 'takx', 'kernel-texture'),
                    '--client-profile', os.path.join(repo, 'takx', 'kernel-client-interop'),
                    '--output', prepared], check=True)
    files = {}
    for name in stale:
        files[name] = os.path.join(prepared, 'source', name)
        if sha256(files[name]) != final[name]:
            raise SystemExit('kernel_overlay: prepared %s is not the locked image' % name)
    entry = {'recipe': RECIPE, 'context_patch_sha256': lock['context_patch_sha256'],
             'composed_kernel_context_patch_sha256': sha256(os.path.join(root, 'P', 'kernel-composed-src', 'context',
                                                                         'patches', 'context.patch')),
             'prepared_receipt': {'path': os.path.join(prepared, 'prepared.json'),
                                  'sha256': sha256(os.path.join(prepared, 'prepared.json'))},
             'sources': {name: {'kernel_stage_sha256': record['sources'].get(name), 'recipe_sha256': final[name]}
                         for name in stale}}
    return files, entry


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--root', default=os.environ.get('TAKX_PROTO_ROOT', '/var/lib/takx-proto'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--official-source', help='cached atak-civ checkout for the recipe refresh '
                        '(default: upstream/atak-civ of this repository)')
    args = parser.parse_args()
    started = time.time()
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(os.path.dirname(here)))
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
    refreshed, refresh = recipe_refresh(repo, args.root, record,
                                        args.official_source or os.path.join(repo, 'upstream', 'atak-civ'), out)
    if set(refreshed) & set(targets):
        raise SystemExit('kernel_overlay: a patch touches a file the recipe refresh replaces: %s'
                         % sorted(set(refreshed) & set(targets)))
    for relative, source in refreshed.items():
        target = os.path.join(stage, relative)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
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
    headers = os.path.join(out, 'jni-headers')
    if refreshed:
        command += ['-h', headers]
    command += [os.path.join(stage, relative) for relative in list(targets) + sorted(refreshed)]
    subprocess.run(command, check=True)
    # The native libraries were built against the Kernel stage's JNI headers.
    for name in sorted(os.listdir(headers)) if refreshed else ():
        built_with = os.path.join(composed, 'jni-headers', name)
        if not os.path.isfile(built_with) or sha256(built_with) != sha256(os.path.join(headers, name)):
            raise SystemExit('kernel_overlay: JNI header %s changed with the recipe; rebuild the Kernel lane' % name)

    produced = []
    for base, dirs, names in os.walk(classes):
        dirs.sort()
        produced += [os.path.relpath(os.path.join(base, name), classes) for name in sorted(names)]
    with zipfile.ZipFile(kernel_classes) as archive:
        known = set(archive.namelist())
    # A patch may not introduce a class the Kernel does not already have. A
    # refreshed recipe source may add nested classes of its own class.
    owners = tuple(relative.split('/src/main/java/', 1)[1][:-len('.java')] + '$' for relative in refreshed)
    added = [name for name in produced if name not in known]
    unknown = [name for name in added if not name.startswith(owners)]
    if unknown:
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
        'recipe_refresh': refresh, 'classes_added_by_recipe_refresh': added,
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
    print(json.dumps({'patches': len(applied), 'recipe_refreshed_sources': len(refreshed), 'classes': len(produced),
                      'jar_sha256': receipt['jar']['sha256']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
