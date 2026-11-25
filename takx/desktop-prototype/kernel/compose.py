#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Desktop prototype: composed Kernel 8.30.5 + project patches, unqualified.

Phases (each writes into one new directory and never edits a recipe or lock):

  sources  run the unmodified takx/kernel-texture/prepare.py,
           takx/kernel-render-context/prepare.py and native_sources.py producers,
           then layer takx/kernel-feature-jni and takx/kernel-elevation-jni on the
           complete native layout when every locked preimage matches (fuzz 0,
           locked postimages checked).
  java     kernel-java's staged sources + the composed Java images + the
           remaining kernel-client-interop Java + the sdk-package source patches
           (stock loader, read-only IO, null shader lengths, OBJ capacity);
           one whole javac with JNI header generation; a classes-only JAR.
  native   the takx/kernel-native CMake parent over the composed layout and the
           composed JNI headers, against the 14 verified local providers.
  package  Kernel JAR = composed classes + nine native libraries + the locked
           runtime resources, laid out the way takx/sdk-package does, plus a
           receipt.

Nothing here is the private Kernel 8.57.11, and nothing is qualified: the
receipt label is 'Kernel 8.30.5 + project patches (texture, render-context),
unqualified'. Run inside the takx-desktop-proto:toolchain image.

Architecture (../arch.sh). On amd64 nothing below differs from the description
above. On arm64 this file runs from the patched copy of the tree
(../arch_tree.py), 'sources' additionally applies ../overrides/arm64/layout/
*.patch to its copy of the native layout (the upstream engine CMakeLists.txt
passes -msse3 on every Linux), the JAR is named for the architecture, and
the receipts carry an 'architecture' record. The native directory inside the
JAR stays libs/linux64: the stock loader selects it for any 64-bit os.arch.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TAKX = HERE.parents[1]
REPO = TAKX.parent
REVISION = '9f6893dd657feacc35ec5de03dad721c2e44170e'
LABEL = 'Kernel 8.30.5 + project patches (texture, render-context), unqualified'
JNI_LAYERS = (('kernel-feature-jni', 'checked-feature-callbacks.patch'),
              ('kernel-elevation-jni', 'elevation-jni-boundaries.patch'))
TU = {'core': 483, 'jni': 159, 'cesium': 173}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def dump(path, value):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(json.dumps(value, indent=1, sort_keys=True) + '\n')
    os.replace(tmp, path)


def require(value, message):
    if not value:
        raise SystemExit('compose.py: ' + message)


def module(name, path, extra_path=None):
    if extra_path and str(extra_path) not in sys.path:
        sys.path.insert(0, str(extra_path))
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(argv, log, env=None, cwd=None, timeout=None):
    argv = list(map(str, argv))
    with open(log, 'ab') as stream:
        stream.write(('\n$ ' + ' '.join(argv) + '\n').encode())
        stream.flush()
        code = subprocess.run(argv, stdout=stream, stderr=subprocess.STDOUT, env=env, cwd=cwd, timeout=timeout).returncode
    require(code == 0, 'command failed (%d), see %s: %s' % (code, log, ' '.join(argv[:6])))


def tree(root):
    root = Path(root)
    return {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def fresh(path):
    path = Path(path)
    require(not path.exists(), 'output must not exist: %s' % path)
    path.mkdir(parents=True)
    return path


def patch(directory, patch_file, log):
    run(['/usr/bin/patch', '--batch', '--fuzz=0', '--no-backup-if-mismatch', '-p1', '-d', directory, '-i', patch_file], log)


def arch_names():
    """The names of this architecture from ../arch.sh, the one table of the prototype."""
    return json.loads(subprocess.check_output(['bash', str(HERE.parent / 'arch.sh'), 'json'], timeout=30))


def architecture_record():
    """None on amd64 (receipts keep their bytes); otherwise what was overridden to build here."""
    names = arch_names()
    if names['arch'] == 'amd64':
        return None
    record = {'arch': names['arch'], 'machine': os.uname().machine, 'validated': False,
              'note': 'recipes under takx/ are validated on Linux x86_64 only; this build used the desktop-prototype '
                      'architecture overrides and is unvalidated; receipt fields that say amd64 or x86_64 name '
                      'locked profiles, not this build'}
    marker = REPO / '.takx-arch-tree.json'
    if marker.is_file():
        tree = load(marker)
        record['tree'] = {'path': str(REPO), 'base_repo': tree['base_repo'], 'fingerprint': tree['fingerprint'],
                          'receipt_sha256': sha(marker),
                          'patches': [{'patch': 'takx/desktop-prototype/overrides/%s/%s' % (tree['arch'], row['name']),
                                       'sha256': row['sha256'], 'files': sorted(row['files'])} for row in tree['patches']]}
    return record


def layout_overrides(layout, log):
    """Apply ../overrides/<arch>/layout/*.patch (fuzz 0) to the composed native layout; none exist for amd64."""
    names = arch_names()
    rows = []
    for patch_file in sorted((HERE.parent / 'overrides' / names['arch'] / 'layout').glob('*.patch')):
        patch(layout, patch_file, log)
        rows.append({'patch': 'takx/desktop-prototype/overrides/%s/layout/%s' % (names['arch'], patch_file.name),
                     'sha256': sha(patch_file), 'applied': True,
                     'kind': 'architecture build-flag override of the composed native layout, not a Kernel source patch'})
    return rows


# ---------------------------------------------------------------- sources
def phase_sources(args):
    out = fresh(args.output)
    log = out / 'sources.log'
    src = args.source
    require(subprocess.check_output([args.git, '-C', str(src), 'rev-parse', 'HEAD'], text=True).strip() == REVISION,
            'official Kernel checkout is not at ' + REVISION)
    py = [sys.executable, '-B']
    run(py + [TAKX / 'kernel-texture/prepare.py', '--source', src, '--output', out / 'texture', '--git', args.git], log)
    run(py + [TAKX / 'kernel-render-context/prepare.py', '--phase', 'prepare', '--source', src,
              '--baseline-profile', TAKX / 'kernel-native', '--texture-profile', TAKX / 'kernel-texture',
              '--client-profile', TAKX / 'kernel-client-interop', '--output', out / 'context', '--git', args.git], log)
    run(py + [TAKX / 'kernel-render-context/native_sources.py', '--phase', 'prepare', '--source', src,
              '--prepared-source', out / 'context/source', '--output', out / 'native-src', '--git', args.git], log)
    layout = out / 'layout'
    shutil.copytree(out / 'native-src/layout', layout)
    layers = []
    for recipe, name in JNI_LAYERS:
        lock = load(TAKX / recipe / 'inputs.lock.json')
        patch_file = TAKX / recipe / name
        row = {'recipe': 'takx/' + recipe, 'patch': name, 'sha256': sha(patch_file), 'files': len(lock['files'])}
        require(row['sha256'] == lock['patch_sha256'], recipe + ' patch differs from its lock')
        before = all(sha(layout / path) == images['before'] for path, images in lock['files'].items())
        if before:
            patch(layout, patch_file, log)
            require(all(sha(layout / path) == images['after'] for path, images in lock['files'].items()),
                    recipe + ' postimage differs from its lock')
            row['applied'] = True
        else:
            row.update(applied=False, reason='a locked preimage differs on the composed layout; layer left out')
        layers.append(row)
    arch_layers = layout_overrides(layout, log)
    record = {
        'label': LABEL, 'official_revision': REVISION, 'actual_kernel_version': '8.30.5',
        'producers': {name: sha(TAKX / name) for name in (
            'kernel-texture/prepare.py', 'kernel-texture/texture-fill.patch', 'kernel-texture/source.lock.json',
            'kernel-render-context/prepare.py', 'kernel-render-context/native_sources.py',
            'kernel-render-context/render-context-control.patch', 'kernel-render-context/inputs.lock.json',
            'kernel-client-interop/client-contracts.patch', 'kernel-client-interop/inputs.lock.json',
            'kernel-native/patches/atomic-counter-platform.patch', 'kernel-native/patches/jni-exception-correctness.patch')},
        'jni_layers': layers,
        'producer_receipts': {name: sha(out / name) for name in ('texture/prepared.json', 'context/prepared.json', 'native-src/receipt.json')},
        'layout_files': len(tree(layout)), 'layout': tree(layout),
        'java_images': tree(out / 'context/source'),
        'gles1_declarations': tree(out / 'native-src/gles1-declarations'),
    }
    record['java_images'] = {k: v for k, v in record['java_images'].items() if k.endswith('.java')}
    if arch_layers:  # absent on amd64, whose record keeps its shape
        record['architecture_layout_overrides'] = arch_layers
        record['architecture'] = architecture_record()
    dump(out / 'composed-sources.json', record)
    print('sources: layout %d files, %d composed Java images, JNI layers applied: %s' % (
        record['layout_files'], len(record['java_images']), [r['recipe'] for r in layers if r['applied']]))


# ------------------------------------------------------------------- java
def phase_java(args):
    out = fresh(args.output)
    log = out / 'java.log'
    composed = load(args.sources / 'composed-sources.json')
    kj = args.kernel_java
    base = load(kj / 'inputs.json')
    result = load(kj / 'result.json')
    require(result['exit_code'] == 0 and base['upstream_revision'] == REVISION, 'kernel-java proof is not usable')
    stage = out / 'stage'
    shutil.copytree(kj / 'stage', stage)
    baseline_count = len(list(stage.rglob('*.java')))
    notes = {'baseline_sources': baseline_count, 'replaced': [], 'added': []}
    for name, pin in composed['java_images'].items():
        source = args.sources / 'context/source' / name
        require(sha(source) == pin, 'composed Java image changed: ' + name)
        target = stage / name
        notes['replaced' if target.exists() else 'added'].append(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    # Remaining kernel-client-interop Java (the JNI half is already in the layout).
    client_dir = TAKX / 'kernel-client-interop'
    client = load(client_dir / 'inputs.lock.json')
    context = module('proto_context_prepare', TAKX / 'kernel-render-context/prepare.py')
    patch_bytes = (client_dir / 'client-contracts.patch').read_bytes()
    require(hashlib.sha256(patch_bytes).hexdigest() == client['patch_sha256'], 'client-contracts.patch differs from its lock')
    changed = []
    for name, images in client['files'].items():
        path = args.sources / 'native-src/layout' / name if name.endswith('.cpp') else stage / name
        pin = sha(path)
        if pin == images['after']:
            continue
        require(name.endswith('.java') and pin == images['before'], 'client-interop preimage differs: ' + name)
        changed.append(name)
    selected = out / 'client-java.patch'
    selected.write_bytes(context.selected_client_patch(patch_bytes, set(changed)))
    patch(stage, selected, log)
    for name, images in client['files'].items():
        require(sha(args.sources / 'native-src/layout' / name if name.endswith('.cpp') else stage / name) == images['after'],
                'client-interop postimage differs: ' + name)
    owned = stage / 'project-java/PublicViewportFit.java'
    owned.parent.mkdir()
    shutil.copyfile(client_dir / 'java/PublicViewportFit.java', owned)
    notes['client_interop_java_patched'] = changed
    # sdk-package source patches, applied in place so one javac produces the runtime classes.
    sdk = TAKX / 'sdk-package'
    lock = load(sdk / 'inputs.lock.json')
    families = [
        ('engine-library-public-linux.patch', lock['source_file'], lock['engine_before'], lock['engine_after'], lock['patch_sha256']),
        ('default-io-readonly.patch', *(lock['default_io_readonly'][k] for k in ('source_file', 'before', 'after', 'patch_sha256'))),
        ('gles-null-shader-lengths.patch', *(lock['gles_null_shader_lengths'][k] for k in ('source_file', 'before', 'after', 'patch_sha256'))),
        ('obj-buffer-capacity.patch', *(lock['obj_buffer_capacity'][k] for k in ('source_file', 'before', 'after', 'patch_sha256'))),
    ]
    sdk_rows = []
    for name, source_file, before, after, pin in families:
        target = stage / source_file
        require(sha(sdk / 'patches' / name) == pin, 'sdk-package patch differs from its lock: ' + name)
        row = {'patch': 'takx/sdk-package/patches/' + name, 'sha256': pin, 'target': source_file,
               'locked_preimage': sha(target) == before}
        dry = subprocess.run(['/usr/bin/patch', '--dry-run', '--batch', '--fuzz=0', '-p1', '-d', str(target.parent),
                              '-i', str(sdk / 'patches' / name)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if dry.returncode and not row['locked_preimage']:
            # The composed image of this file is not the baseline the patch was written for.
            row.update(applied=False, locked_postimage=False, result_sha256=sha(target),
                       reason='does not apply with fuzz 0 to the composed image of this file; left out')
            sdk_rows.append(row)
            continue
        patch(target.parent, sdk / 'patches' / name, log)
        row['locked_postimage'] = sha(target) == after
        require(row['locked_postimage'] or not row['locked_preimage'], 'sdk-package postimage differs: ' + name)
        row.update(applied=True, result_sha256=sha(target))
        sdk_rows.append(row)
    require(sha(TAKX / 'compat/src/main/java/android/content/res/Resources.java') ==
            sha(stage / 'compat/android/content/res/Resources.java'), 'resource adapter differs from takx/compat')
    notes['sdk_package_patches'] = sdk_rows
    sources = sorted(stage.rglob('*.java'))
    cp = []
    for row in base['classpath']:
        require(sha(row['path']) == row['sha256'], 'kernel-java classpath entry changed: ' + row['path'])
        cp.append(Path(row['path']))
    for name, meta in client['public_runtime_jars'].items():
        path = args.public_jars / name
        require(sha(path) == meta['sha256'], 'public renderer JAR differs: ' + name)
        cp.append(path)
    (out / 'sources.args').write_text(''.join(json.dumps(str(p)) + '\n' for p in sources))
    (out / 'classpath.txt').write_text('\n'.join(map(str, cp)) + '\n')
    classes, headers = out / 'classes', out / 'jni-headers'
    classes.mkdir()
    headers.mkdir()
    javac = args.java_home / 'bin/javac'
    command = [javac, '-J-Xmx768m', '-J-XX:ActiveProcessorCount=2', '--release', '11', '-encoding', 'UTF-8',
               '-proc:none', '-implicit:none', '-sourcepath', '', '-Xmaxerrs', '3000',
               '-cp', os.pathsep.join(map(str, cp)), '-d', classes, '-h', headers, '@' + str(out / 'sources.args')]
    env = {k: v for k, v in os.environ.items() if k not in ('CLASSPATH',) and not k.startswith(('JAVA_TOOL', '_JAVA', 'JDK_'))}
    started = time.time()
    run(command, log, env=env, timeout=900)
    class_tree, header_tree = tree(classes), tree(headers)
    jar = out / 'kernel-8.30.5-composed-classes.jar'
    with zipfile.ZipFile(jar, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        entries = {'META-INF/MANIFEST.MF': ('Manifest-Version: 1.0\nImplementation-Title: ' + LABEL +
                                            ' (classes only, no native libraries)\nImplementation-Version: 8.30.5\n\n').encode()}
        entries.update({name: (classes / name).read_bytes() for name in class_tree})
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    record = {
        'label': LABEL, 'official_revision': REVISION, 'composed_sources_sha256': sha(args.sources / 'composed-sources.json'),
        'kernel_java_inputs_sha256': sha(kj / 'inputs.json'), 'javac': {'path': str(javac), 'sha256': sha(javac)},
        'command': list(map(str, command)), 'seconds': round(time.time() - started, 1),
        'source_count': len(sources), 'class_count': len(class_tree), 'jni_header_count': len(header_tree),
        'reference_counts_from_kernel_render_context_client_java': {'sources': 1239, 'classes': 2702, 'jni_headers': 119},
        'notes': notes, 'classpath': [{'path': str(p), 'sha256': sha(p)} for p in cp],
        'jar': {'path': str(jar), 'sha256': sha(jar), 'bytes': jar.stat().st_size},
        'sources': {str(p.relative_to(stage)): sha(p) for p in sources}, 'classes': class_tree, 'jni_headers': header_tree,
    }
    dump(out / 'composed-java.json', record)
    print('java: %d sources -> %d classes, %d JNI headers; %s' % (len(sources), len(class_tree), len(header_tree), jar))


# ----------------------------------------------------------------- native
def phase_native(args):
    b = module('proto_kernel_native_build', TAKX / 'kernel-native/build.py', TAKX / 'kernel-native')
    p, native_sources, audit, toolchain = b.p, b.sources, b.audit, b.toolchain
    lock = b.validate_lock(p.load(TAKX / 'kernel-native/inputs.lock.json'))
    paths = p.load(args.providers)
    composed = load(args.sources / 'composed-sources.json')
    out = Path(args.output)
    if args.resume:
        require((out / 'build/build.ninja').is_file(), 'nothing to resume in %s' % out)
    else:
        fresh(out)
    log = out / 'native.log'
    started = time.time()
    results, errors = p.verify_all(paths)
    require(not errors, 'provider verification failed: ' + json.dumps(errors))
    dump(out / 'verified-providers.json', results)
    java = load(args.java / 'composed-java.json')
    if not args.resume:
        shutil.copytree(args.sources / 'layout', out / 'layout')
        shutil.copytree(args.sources / 'native-src/gles1-declarations', out / 'gles1-declarations')
        native_sources.gltf_headers(args.cache.resolve(), out)
        shutil.copytree(p.root_of(paths, 'cesium') / 'source/cesium', out / 'cesium-source')
    require(tree(out / 'layout') == composed['layout'], 'staged layout differs from the composed sources')
    require(tree(out / 'gles1-declarations') == composed['gles1_declarations'], 'GLES1 declarations differ')
    require(tree(args.java / 'jni-headers') == java['jni_headers'], 'composed JNI headers changed')
    env = b.environment(args.java_home, 'gcc', 'g++', out)
    tools = {name: Path(shutil.which(value) or value).resolve() for name, value in
             (('cmake', args.cmake), ('ninja', args.ninja), ('cc', 'gcc'), ('cxx', 'g++'))}
    identities = {}
    for name, path in tools.items():
        version = subprocess.check_output([str(path), '--version'], text=True).strip()
        identities[name] = {'path': str(path), 'sha256': sha(path), 'version': version}
    require(identities['cmake']['version'].startswith('cmake version ' + lock['tools']['cmake']) and
            identities['ninja']['version'].startswith(lock['tools']['ninja']) and
            all(identities[n]['version'].splitlines()[0].endswith(' ' + lock['tools']['gcc']) for n in ('cc', 'cxx')),
            'the kernel-native reviewed toolchain is required')

    def capture(argv, label, timeout):
        return subprocess.run(list(map(str, argv)), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, check=True).stdout
    actual = toolchain.capture(tools, out, capture, env)
    dump(out / 'actual-toolchain.json', actual)
    values = b.cmake_paths(paths, out)
    values['JNI_HEADERS'] = args.java / 'jni-headers'
    build = out / 'build'
    # CMake parent: a patched copy of takx/kernel-native/CMakeLists.txt (see
    # overrides/kernel-native-cmake.patch for the reason). The tracked file is
    # never edited; the copy is rebuilt and compared on every run.
    override_patch = HERE.parent / 'overrides/kernel-native-cmake.patch'
    parent = args.overrides / 'kernel-native'
    staging = out / 'cmake-parent.staging'
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    for name in ('CMakeLists.txt', 'core-sqlcipher.map'):
        shutil.copyfile(TAKX / 'kernel-native' / name, staging / name)
    patch(staging, override_patch, log)
    if not parent.is_dir() or tree(parent) != tree(staging):
        require(not args.resume, 'the CMake parent override changed; a resumed build would be stale')
        shutil.rmtree(parent, ignore_errors=True)
        parent.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staging, parent)
    shutil.rmtree(staging)
    cmake_parent = {'base': 'takx/kernel-native/CMakeLists.txt', 'base_sha256': sha(TAKX / 'kernel-native/CMakeLists.txt'),
                    'override_patch': 'takx/desktop-prototype/overrides/kernel-native-cmake.patch',
                    'override_patch_sha256': sha(override_patch), 'directory': str(parent), 'files': tree(parent)}
    if not args.resume:
        run([tools['cmake'], '-S', parent, '-B', build, '-G', 'Ninja', '-DCMAKE_BUILD_TYPE=Release',
             '-DCMAKE_MAKE_PROGRAM=' + str(tools['ninja']), '-DCMAKE_C_COMPILER=' + str(tools['cc']),
             '-DCMAKE_CXX_COMPILER=' + str(tools['cxx']), '-DCMAKE_AR=' + actual['compiler_and_binutils']['ar'],
             '-DCMAKE_RANLIB=' + actual['compiler_and_binutils']['ranlib'], '-DCMAKE_LINKER=' + actual['compiler_and_binutils']['ld'],
             *['-D%s=%s' % item for item in values.items()]], log, env=env, timeout=1800)
    db = load(build / 'compile_commands.json')
    prefixes = {'core': str(out / 'layout/takkernel/engine/src/main/cpp') + '/', 'jni': str(out / 'layout/takkernel/engine/src/main/jni') + '/',
                'cesium': str(out / 'cesium-source') + '/'}
    counts = {name: len({row['file'] for row in db if row['file'].startswith(prefix)}) for name, prefix in prefixes.items()}
    require(counts == TU, 'translation-unit graph differs: %s' % counts)
    dump(out / 'configured.json', {'translation_units': counts, 'cmake_values': {k: str(v) for k, v in values.items()}})
    for target in ('takengine', 'takenginejni'):
        run([tools['ninja'], '-C', build, '-j' + str(args.jobs), target], log, env=env, timeout=args.build_seconds)
    rooted = {k: p.root_of(paths, k) for k in paths}
    utilities = {name: shutil.which(name) for name in ('nm', 'readelf')}

    def audit_run(argv, label, timeout):
        argv = list(map(str, argv))
        argv[0] = utilities.get(argv[0], argv[0])
        return capture(argv, label, timeout)
    elf = audit.elf_audit(out, rooted, audit_run)
    again, errors = p.verify_all(paths)
    require(not errors and again == results, 'provider inputs changed during the build')
    require(tree(out / 'layout') == composed['layout'], 'layout changed during the build')
    libs = {name: {'path': str(out / 'build' / rel), 'sha256': sha(out / 'build' / rel), 'bytes': (out / 'build' / rel).stat().st_size}
            for name, rel in (('libtakengine.so', 'kernel-cpp/libtakengine.so'), ('libtakenginejni.so', 'kernel-jni/libtakenginejni.so'))}
    architecture = architecture_record()
    dump(out / 'composed-native.json', {
        'label': LABEL, 'official_revision': REVISION, 'composed_sources_sha256': sha(args.sources / 'composed-sources.json'),
        'composed_java_sha256': sha(args.java / 'composed-java.json'), 'cmake_parent': cmake_parent,
        'native_translation_units': counts,
        'providers_file': str(args.providers), 'providers': results, 'tools': identities, 'actual_toolchain': actual,
        'elf_audit': elf, 'libraries': libs, 'jobs': args.jobs, 'resumed': bool(args.resume),
        'seconds': round(time.time() - started, 1),
        'kernel_native_recipe_build_py_used': False, 'runtime_acceptance': False, 'sdk_acceptance': False,
        **({'architecture': architecture} if architecture else {})})
    print('native: %s; ELF audit passed; %s' % (counts, {k: v['sha256'][:12] for k, v in libs.items()}))


# ---------------------------------------------------------------- package
def phase_package(args):
    out = fresh(args.output)
    log = out / 'package.log'
    sdk = module('proto_sdk_package_build', TAKX / 'sdk-package/build.py', TAKX / 'sdk-package')
    p, resource_inputs = sdk.p, sdk.resource_inputs
    lock = p.load(TAKX / 'sdk-package/inputs.lock.json')
    paths = p.load(args.providers)
    roots = {k: p.root_of(paths, k) for k in paths}
    java = load(args.java / 'composed-java.json')
    native = load(args.native / 'composed-native.json')
    composed = load(args.sources / 'composed-sources.json')
    require(native['composed_java_sha256'] == sha(args.java / 'composed-java.json') and
            native['composed_sources_sha256'] == sha(args.sources / 'composed-sources.json'),
            'native libraries were not built from this Java/source composition')
    require(tree(args.java / 'classes') == java['classes'], 'composed classes changed')
    libraries = sdk.native_files(args.native, roots)
    require(list(libraries) == lock['native_order'], 'native load order differs from the sdk-package lock')
    for name in ('takengine', 'takenginejni'):
        require(sha(libraries[name]) == native['libraries']['lib' + name + '.so']['sha256'], 'native library changed: ' + name)
    resource_lock = p.load(TAKX / 'sdk-package/runtime-resources.lock.json')
    require(resource_lock['official_revision'] == REVISION, 'resource lock revision differs')

    def git(*argv):
        return subprocess.check_output([args.git, '--no-lazy-fetch', '-C', str(args.source), *argv])
    mapping = resource_lock['mapping_recipe']
    data = git('show', REVISION + ':' + mapping['path'])
    require(len(data) == mapping['bytes'] and hashlib.sha256(data).hexdigest() == mapping['sha256'], 'official Gradle mapping differs')
    resource_tree = {}
    for row in git('ls-tree', '-r', '--long', REVISION, '--', *[root.rstrip('/') for root in resource_inputs.ROOTS]).decode().splitlines():
        metadata, name = row.split('\t')
        mode, kind, blob, size = metadata.split()
        require(kind == 'blob', 'non-blob runtime resource')
        resource_tree[name] = (mode, blob, int(size))

    def payload(digest):
        path = args.cache / digest
        require(path.is_file() and sha(path) == digest, 'resource payload missing or different: ' + digest)
        return path.read_bytes()
    resources = resource_inputs.collect(resource_lock, resource_tree, lambda name: git('show', REVISION + ':' + name), payload)
    (out / 'runtime-resources.sha256').write_text(''.join(
        item.get('lfs_payload', item)['sha256'] + ' ' + item['kind'] + ' ' + item['destination'] + '\n' for item in resource_lock['files']))
    classes = {name: (args.java / 'classes' / name).read_bytes() for name in java['classes']}
    natives = {'lib' + name + '.so': Path(path).read_bytes() for name, path in libraries.items()}
    entries = resource_inputs.merge(classes, {'libs/linux64/' + name: data for name, data in natives.items()}, resources)
    entries['META-INF/MANIFEST.MF'] = ('Manifest-Version: 1.0\nImplementation-Title: ' + LABEL +
                                       '\nImplementation-Version: 8.30.5\n\n').encode()
    native_manifest = {name: hashlib.sha256(data).hexdigest() for name, data in natives.items()}
    (out / 'native-resources.sha256').write_text(''.join(digest + ' ' + name + '\n' for name, digest in native_manifest.items()))
    package = out / 'package'
    package.mkdir()
    names = arch_names()
    artifact = package / names['kernel_jar']
    sdk.jar_write(artifact, entries)
    third = package / 'third-party'
    third.mkdir()
    dependencies = []
    for row in java['classpath']:
        source = Path(row['path'])
        require(sha(source) == row['sha256'] and not (third / source.name).exists(), 'dependency JAR changed or duplicated')
        shutil.copyfile(source, third / source.name)
        dependencies.append({'path': 'third-party/' + source.name, 'sha256': row['sha256']})
    headers = package / 'headers/jni'
    shutil.copytree(args.java / 'jni-headers', headers)
    lib_dir = out / 'lib/linux64'
    lib_dir.mkdir(parents=True)
    for name, data in natives.items():
        (lib_dir / name).write_bytes(data)
    provider_receipts = {}
    for family, root in roots.items():
        receipt = root / ('inputs.json' if family == 'kernel_java' else 'complete.json')
        provider_receipts[family] = {'root': str(root), 'receipt': receipt.name, 'sha256': sha(receipt)}
    patches = [{'patch': 'takx/' + name, 'sha256': digest} for name, digest in sorted(composed['producers'].items()) if name.endswith('.patch')]
    patches += [{'patch': row['recipe'] + '/' + row['patch'], 'sha256': row['sha256'], 'applied': row['applied']} for row in composed['jni_layers']]
    patches.append({'patch': native['cmake_parent']['override_patch'], 'sha256': native['cmake_parent']['override_patch_sha256'],
                    'target': native['cmake_parent']['base'], 'kind': 'build-graph override (include roots and link of the JNI target), not a Kernel source patch'})
    patches += [{'patch': row['patch'], 'sha256': row['sha256'], 'target': row['target'],
                 'applied': row['applied'], 'locked_preimage': row['locked_preimage'],
                 'locked_postimage': row['locked_postimage'], **({'reason': row['reason']} if 'reason' in row else {})}
                for row in java['notes']['sdk_package_patches']]
    patches += composed.get('architecture_layout_overrides', [])
    architecture = architecture_record()
    receipt = {
        'label': LABEL, 'actual_kernel_version': '8.30.5', 'private_kernel_8_57_11': False, 'qualified': False,
        'source': {'repository': 'https://github.com/TAK-Product-Center/atak-civ', 'revision': REVISION},
        'patches': patches,
        'composition': {'composed_sources_sha256': sha(args.sources / 'composed-sources.json'),
                        'composed_java_sha256': sha(args.java / 'composed-java.json'),
                        'composed_native_sha256': sha(args.native / 'composed-native.json'),
                        'java': {k: java[k] for k in ('source_count', 'class_count', 'jni_header_count')},
                        'native_translation_units': native['native_translation_units'],
                        'client_interop_java_patched': java['notes']['client_interop_java_patched']},
        'provider_proofs': provider_receipts,
        'toolchain': {'tools': native['tools'], 'javac': java['javac'],
                      'compiler_and_binutils': native['actual_toolchain'].get('compiler_and_binutils')},
        'packaging': {'layout': 'takx/sdk-package (stock EngineLibrary resource loader, libs/linux64, locked runtime resources)',
                      'sdk_package_recipe_used': False,
                      'reason': 'takx/sdk-package/build.py accepts only the baseline kernel-native proof',
                      'native_resource_order': lock['native_order'], 'native_resources': native_manifest,
                      'runtime_resource_entries': len(resources), 'class_count': len(classes),
                      'stock_loader_java_library_path': '${java.io.tmpdir}/takkernel/engine/' +
                          hashlib.md5(artifact.read_bytes()).hexdigest() + '/lib/linux64'},
        'outputs': {'jar': {'path': str(artifact), 'sha256': sha(artifact), 'bytes': artifact.stat().st_size},
                    'third_party': dependencies, 'libraries': {name: {'path': str(lib_dir / name), 'sha256': digest}
                                                              for name, digest in native_manifest.items()},
                    'jni_headers': len(java['jni_headers'])},
        'elf_audit': native['elf_audit'],
        'acceptance': {'runtime': False, 'renderer': False, 'sdk': False, 'client': False,
                       'note': 'controls are recorded separately in controls.json by kernel/controls.py'},
    }
    if architecture:  # absent on amd64, whose receipt keeps its shape
        receipt['architecture'] = architecture
    dump(out / 'receipt.json', receipt)
    print('package: %s sha256 %s (%d classes, %d natives, %d resources)' % (
        artifact, receipt['outputs']['jar']['sha256'], len(classes), len(natives), len(resources)))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('phase', choices=('sources', 'java', 'native', 'package'))
    for name in ('output', 'source', 'sources', 'kernel-java', 'public-jars', 'java-home', 'java', 'native', 'providers', 'cache', 'overrides'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--git', default='/usr/bin/git')
    parser.add_argument('--cmake', default='/workspace/takx-native-tools/venv/bin/cmake')
    parser.add_argument('--ninja', default='/workspace/takx-native-tools/venv/bin/ninja')
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--build-seconds', type=int, default=3 * 3600)
    parser.add_argument('--resume', action='store_true', help='native: continue an interrupted ninja build in --output')
    args = parser.parse_args()
    require(args.output, '--output is required')
    {'sources': phase_sources, 'java': phase_java, 'native': phase_native, 'package': phase_package}[args.phase](args)


if __name__ == '__main__':
    main()
