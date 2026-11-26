#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Desktop prototype: real JVM controls for the composed Kernel package.

Runs in the takx-desktop-proto:runtime image against P/kernel-composed:

  stock-basic / stock-database / stock-logger
      takx/sdk-package StockLoaderSmoke with the kernel-native smokes, the same
      arguments and markers takx/sdk-package/build.py --phase qualify uses, under
      -Xcheck:jni. Natives are loaded only by the stock EngineLibrary loader.
  composed
      kernel/ComposedControls.java: takx/kernel-texture TextureStyleControl plus
      the three render-context GLMapView natives, under -Xcheck:jni.
  binding-state
      takx/kernel-render-context/tests/BindingStateTest.java (JVM state machine).
  jni-symbols
      every 'native' method javap reports in the Kernel JAR and its dependency
      JARs, cross-checked against the exported symbols of the nine packaged
      libraries. Unresolved names are counted and listed, never hidden.
  coexist-sdk-first / coexist-common-first
      takx/sdk-coexist JoinedSdkCommo: Kernel + CommonCommo 1.17.2 in one JVM.

A control passes only if its JVM exits 0, every required marker is present and
there is no strict-JNI diagnostic, UnsatisfiedLinkError or NoClassDefFoundError.
Nothing here qualifies the renderer, the SDK or the client.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
TAKX = HERE.parents[1]
FORBIDDEN = ('EngineLibrary: Failed to load', 'UnsatisfiedLinkError', 'NoClassDefFoundError')
STRICT = ('WARNING in native method', 'WARNING: JNI', 'FATAL ERROR')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stock_loader_directory_name(jar):
    """Name of the directory below ${java.io.tmpdir}/takkernel/engine/ that the stock loader extracts into.

    EngineLibrary.getRuntimeDirectory() names it HashingUtils.md5sum(jar), and
    that method of Kernel 8.30.5 removes one leading zero from the MD5 hex
    string ("mimic the previous implementation of the md5sum which used
    BigInteger"). A JAR whose MD5 starts with 0, one in sixteen, is therefore
    extracted into a 31-character directory. Predicting the full digest instead
    points java.library.path at a directory that does not exist; the natives
    still load, by absolute path, but the GDAL binding's own
    System.loadLibrary("gdalalljni") fails and gdalJNI.isAvailable() is false.
    """
    value = hashlib.md5(Path(jar).read_bytes()).hexdigest()
    return value[1:] if value.startswith('0') else value


def mangle(text):
    out = []
    for ch in text:
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        elif ch in './':
            out.append('_')
        elif ch == '_':
            out.append('_1')
        elif ch == ';':
            out.append('_2')
        elif ch == '[':
            out.append('_3')
        else:
            out.append('_0%04x' % ord(ch))
    return ''.join(out)


def natives_of(javap, jars, log):
    """{(class, method): count} for every native method in the given JARs."""
    result = {}
    for jar in jars:
        with zipfile.ZipFile(jar) as archive:
            names = [n[:-6].replace('/', '.') for n in archive.namelist() if n.endswith('.class') and 'module-info' not in n and not n.startswith('META-INF/')]
        for start in range(0, len(names), 400):
            text = subprocess.run([str(javap), '-p', '-cp', str(jar), *names[start:start + 400]],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            with open(log, 'a') as stream:
                stream.write(text.stderr)
            current = None
            for line in text.stdout.splitlines():
                head = re.match(r'^(?:[a-z]+ )*(?:class|interface|enum) ([\w.$]+)', line)
                if head and not line.startswith(' '):
                    current = head.group(1)
                    continue
                if current and re.search(r'\bnative\b', line.split('(')[0]) and '(' in line:
                    method = line.split('(')[0].split()[-1]
                    result.setdefault((str(jar), current, method), 0)
                    result[(str(jar), current, method)] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--package', type=Path, required=True, help='P/kernel-composed')
    parser.add_argument('--commoncommo', type=Path, required=True, help='P/commoncommo')
    parser.add_argument('--java-home', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='new directory for control outputs')
    args = parser.parse_args()
    out = args.output
    if out.exists():
        raise SystemExit('controls.py: output must not exist: %s' % out)
    out.mkdir(parents=True)
    java, javac, javap = (args.java_home / 'bin' / n for n in ('java', 'javac', 'javap'))
    receipt = json.loads((args.package / 'receipt.json').read_text())
    jar = Path(receipt['outputs']['jar']['path'])
    if sha(jar) != receipt['outputs']['jar']['sha256']:
        raise SystemExit('controls.py: packaged Kernel JAR differs from its receipt')
    deps = [args.package / 'package' / row['path'] for row in receipt['outputs']['third_party']]
    common_jar = args.commoncommo / 'install/java/jcommoncommo.jar'
    common_lib = args.commoncommo / 'install/lib'
    native_manifest = args.package / 'native-resources.sha256'
    resource_manifest = args.package / 'runtime-resources.sha256'
    env = {k: v for k, v in os.environ.items() if k not in ('CLASSPATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'DISPLAY')
           and not k.startswith(('JAVA_TOOL', '_JAVA', 'JDK_', 'GDAL_', 'PROJ_'))}
    env['LC_ALL'] = 'C'
    results, started = {}, time.time()
    record = {'label': receipt['label'], 'kernel_jar': {'path': str(jar), 'sha256': sha(jar)},
              'commoncommo_jar': {'path': str(common_jar), 'sha256': sha(common_jar)},
              'java': {'path': str(java), 'sha256': sha(java)}, 'controls': results,
              'renderer_acceptance': False, 'sdk_acceptance': False, 'client_acceptance': False}

    def write():
        record['seconds'] = round(time.time() - started, 1)
        record['all_passed'] = bool(results) and all(r['passed'] for r in results.values())
        (out / 'controls.json').write_text(json.dumps(record, indent=1, sort_keys=True) + '\n')

    def execute(name, argv, required=(), timeout=300, strict=True):
        argv = list(map(str, argv))
        log = out / (name + '.log')
        begin = time.time()
        try:
            done = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
            code, text = done.returncode, done.stdout.decode(errors='replace')
        except subprocess.TimeoutExpired as error:
            code, text = 'timeout', (error.stdout or b'').decode(errors='replace')
        log.write_text('$ ' + ' '.join(argv) + '\n' + text)
        missing = [m for m in required if m not in text]
        diagnostics = [line for line in text.splitlines() if any(m in line for m in STRICT)] if strict else []
        forbidden = [m for m in FORBIDDEN if m in text]
        results[name] = {'exit': code, 'log': str(log), 'required_markers': list(required), 'missing_markers': missing,
                         'strict_jni_diagnostics': len(diagnostics), 'forbidden': forbidden,
                         'seconds': round(time.time() - begin, 1),
                         'passed': code == 0 and not missing and not diagnostics and not forbidden}
        write()
        print('%-22s %s' % (name, 'PASS' if results[name]['passed'] else 'FAIL ' + json.dumps(results[name])), flush=True)
        return text

    cp_sdk = [jar, *deps]
    classes = out / 'classes'
    classes.mkdir()
    control_sources = [TAKX / 'kernel-native' / n for n in ('KernelRuntimeSmoke.java', 'NativeDatabaseCandidate.java', 'KernelThrowingLoggerCandidate.java')]
    control_sources += [TAKX / 'sdk-package' / n for n in ('StockLoaderSmoke.java', 'RuntimeResourcesSmoke.java', 'DefaultIoReadonlySmoke.java')]
    control_sources += [TAKX / (n + '.java') for n in ('CommonCommoSmoke', 'CommonCommoSignalSmoke', 'CommonCommoRoutingSmoke')]
    control_sources += [TAKX / 'native-commoncommo/CommonCommoThrowingLogger.java', TAKX / 'sdk-coexist/JoinedSdkCommo.java',
                        TAKX / 'kernel-texture/TextureStyleControl.java', TAKX / 'kernel-render-context/tests/BindingStateTest.java',
                        HERE / 'ComposedControls.java']
    record['control_sources'] = {str(p): sha(p) for p in control_sources}
    execute('compile-controls', [javac, '-J-Xmx256m', '--release', '17', '-proc:none', '-implicit:none', '-sourcepath', '',
                                 '-cp', os.pathsep.join(map(str, [*cp_sdk, common_jar])), '-d', classes, *control_sources], strict=False)
    if not results['compile-controls']['passed']:
        raise SystemExit(1)
    library_md5 = stock_loader_directory_name(jar)

    def jvm(app, extra_library_path=()):
        for name in ('home', 'data', 'tmp'):
            (app / name).mkdir(parents=True)
        library = app / 'tmp/takkernel/engine' / library_md5 / 'lib/linux64'
        return library, [java, '-Djava.library.path=' + os.pathsep.join(map(str, [library, *extra_library_path])), '-Xcheck:jni',
                         '-Xmx256m', '-XX:ActiveProcessorCount=2', '-Djava.awt.headless=true',
                         '-XX:ErrorFile=' + str(app / 'hs_err_%p.log'), '-Duser.home=' + str(app / 'home'),
                         '-Djava.io.tmpdir=' + str(app / 'tmp'), '-DTAK_APP_DATA_PATH=' + str(app / 'data')]

    base = ['PASS_GENUINE_118_RESOURCES_91_ASSETS_25_CATALOG_15_STOCK_GLSL', 'PASS_STOCK_ENGINE_RESOURCE_LOADER_SCOPED_COMPONENT',
            'PASS_ENGINE_INITIALIZER_AND_NATIVE_CONFIG_GDAL', 'PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION']
    extra = {'basic': [], 'database': ['PASS_REAL_DEFAULT_IO_READONLY_WRITE_REJECTION_NO_CREATE_MBTILES',
                                      'PASS_PREPARED_NATIVE_DATABASE2_ENCRYPTED_OPEN_REOPEN_WRONG_KEY_CONTROL',
                                      'PASS_REAL_GDAL_MEM_EXACT_WRITE_READ'],
             'logger': ['PASS_REAL_THROWING_LOGGER_BASELINE_REJECTION_MATCH_AND_CORRECT_KEY_RECOVERY']}
    native_pins = dict(reversed(line.split(' ', 1)) for line in native_manifest.read_text().splitlines())
    for phase in ('basic', 'database', 'logger'):
        library, argv = jvm(out / 'runtime' / phase)
        text = execute('stock-' + phase, argv + ['-cp', os.pathsep.join(map(str, [classes, *cp_sdk])), 'StockLoaderSmoke',
                                                 jar, phase, native_manifest, resource_manifest], base + extra[phase])
        row = results['stock-' + phase]
        extraction = re.findall(r'^EXTRACTION_DIRECTORY (.+)$', text, re.M)
        observed = dict((name, digest) for digest, name in re.findall(r'^PACKAGED_NATIVE ([0-9a-f]{64}) (.+)$', text, re.M))
        expected = {str(library / name): digest for name, digest in native_pins.items()}
        mapped = dict((name, digest) for digest, name in re.findall(r'^ELF ([0-9a-f]{64}) (.+)$', text, re.M))
        row['extraction_directory'] = extraction
        row['packaged_natives_extracted_and_mapped'] = (extraction == [str(library)] and observed == expected and
                                                        all(mapped.get(k) == v for k, v in expected.items()))
        row['passed'] = row['passed'] and row['packaged_natives_extracted_and_mapped']
        row['mapped_elfs'] = mapped
        write()

    empty = out / 'empty-native-manifest.txt'
    empty.write_text('')
    library, argv = jvm(out / 'runtime/composed')
    execute('composed', argv + ['-cp', os.pathsep.join(map(str, [classes, *cp_sdk])), 'ComposedControls', jar, empty],
            ['PASS_COMPOSED_STOCK_LOADER_INITIALIZED', 'PASS_REAL_TEXTURE_STYLE_JNI_COMPOSITE_CLONE_SERIALIZATION_LIFECYCLE',
             'PASS_COMPOSED_TEXTURE_JNI', 'PASS_COMPOSED_RENDER_CONTEXT_JNI_NULL_PEER_REJECTED', 'PASS_COMPOSED_CONTROLS'])
    execute('binding-state', [java, '-Xmx256m', '-Djava.awt.headless=true', '-cp', os.pathsep.join(map(str, [classes, *cp_sdk])),
                              'com.atakmap.map.opengl.BindingStateTest'])

    # javap native declarations against exported dynamic symbols.
    begin = time.time()
    exports = {}
    for name in native_pins:
        path = args.package / 'lib/linux64' / name
        if sha(path) != native_pins[name]:
            raise SystemExit('controls.py: packaged native differs: ' + name)
        text = subprocess.run(['nm', '-D', '--defined-only', str(path)], stdout=subprocess.PIPE, text=True, check=True).stdout
        for line in text.splitlines():
            symbol = line.split()[-1].split('@')[0]
            if symbol.startswith('Java_'):
                exports.setdefault(symbol, name)
    declared = natives_of(javap, cp_sdk, out / 'jni-symbols-javap.log')
    unresolved, resolved, by_jar = [], 0, {}
    for (owner, cls, method), count in sorted(declared.items()):
        short = 'Java_' + mangle(cls) + '_' + mangle(method)
        found = short in exports or any(symbol.startswith(short + '__') for symbol in exports)
        stats = by_jar.setdefault(Path(owner).name, {'declared_names': 0, 'unresolved_names': 0})
        stats['declared_names'] += 1
        if found:
            resolved += 1
        else:
            stats['unresolved_names'] += 1
            unresolved.append({'jar': Path(owner).name, 'class': cls, 'method': method, 'overloads': count, 'symbol': short})
    kernel_unresolved = [row for row in unresolved if row['jar'] == jar.name]
    needed = ['Java_com_atakmap_map_layer_feature_style_TextureFillStyle_create',
              'Java_com_atakmap_map_layer_feature_style_TextureFillStyle_nativeClass',
              'Java_com_atakmap_map_opengl_GLMapView_releaseContextCaches',
              'Java_com_atakmap_map_opengl_GLMapView_recreateContextCaches',
              'Java_com_atakmap_map_opengl_GLMapView_markContextTerminal']
    absent = [name for name in needed if name not in exports]
    (out / 'jni-symbols.json').write_text(json.dumps({
        'method': 'javap -p native declarations (by class and method name) vs nm -D --defined-only Java_* exports of the nine packaged libraries; '
                  'a name resolves if the short JNI name or an overload-qualified form is exported. Natives bound with RegisterNatives would appear unresolved here.',
        'exported_java_symbols': len(exports), 'declared_native_names': len(declared), 'resolved': resolved,
        'unresolved': unresolved, 'by_jar': by_jar}, indent=1, sort_keys=True) + '\n')
    results['jni-symbols'] = {'passed': not absent, 'log': str(out / 'jni-symbols.json'), 'exported_java_symbols': len(exports),
                              'declared_native_names': len(declared), 'resolved': resolved, 'unresolved_total': len(unresolved),
                              'unresolved_in_kernel_jar': len(kernel_unresolved), 'by_jar': by_jar,
                              'unresolved_kernel_classes': sorted({row['class'] for row in kernel_unresolved}),
                              'texture_and_render_context_symbols': {name: exports.get(name) for name in needed},
                              'seconds': round(time.time() - begin, 1)}
    write()
    print('%-22s %s unresolved=%d (kernel jar %d) of %d' % ('jni-symbols', 'PASS' if not absent else 'FAIL', len(unresolved),
                                                         len(kernel_unresolved), len(declared)), flush=True)

    for order in ('sdk-first', 'common-first'):
        library, argv = jvm(out / 'runtime' / ('coexist-' + order), [common_lib])
        argv.insert(4, '-XX:MaxMetaspaceSize=128m')
        text = execute('coexist-' + order, argv + ['-cp', os.pathsep.join(map(str, [classes, *cp_sdk, common_jar])), 'JoinedSdkCommo',
                                                   order, jar, common_jar, native_manifest, resource_manifest, 'baseline'],
                       ['PASS_JOINED_FIRST_GROUP_' + ('SDK' if order == 'sdk-first' else 'COMMONCOMMO'),
                        'PASS_JOINED_COMMON_CONSTRUCTOR_PKCS12_SIGNAL_ROUTING_LOGGER', 'PASS_JOINED_KERNEL_CONTROLS_AFTER_COMMON',
                        'PASS_JOINED_STOCK_SDK_COMMONCOMMO ' + order, 'PASS_STOCK_ENGINE_RESOURCE_LOADER_SCOPED_COMPONENT'], timeout=400)
        joined = dict((name, digest) for digest, name in re.findall(r'^JOINED_ELF ([0-9a-f]{64}) (.+)$', text, re.M))
        row = results['coexist-' + order]
        common_jni = str((common_lib / 'libcommoncommojni.so').resolve())
        row['kernel_and_commoncommo_mapped_together'] = (all(joined.get(str(library / n)) == d for n, d in native_pins.items()) and
                                                         joined.get(common_jni) == sha(common_jni))
        row['passed'] = row['passed'] and row['kernel_and_commoncommo_mapped_together']
        write()
    write()
    print('controls: %s' % ('all passed' if record['all_passed'] else 'FAILED: ' + ', '.join(k for k, v in results.items() if not v['passed'])))
    sys.exit(0 if record['all_passed'] else 1)


if __name__ == '__main__':
    main()
