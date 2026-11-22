#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Verify style sources, or explicitly package and exercise the pinned Java slice."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
API = ('java/gov/tak/api/contour/ContourFunction.java', 'java/gov/tak/api/contour/ContourStyle.java')
OWNED = (*API, 'tests/ContourStyleControls.java', 'tests/PinnedContourStyleCaller.java.in')
UI = 'Client/src/main/java/gov/takx/core/view/gui/maptools/elevationtools/contour/'
SPAN = 'takkernel/engine/src/main/java/com/atakmap/coremap/conversions/Span.java'
ANNOTATION = 'takkernel/annotation/src/main/java/gov/tak/api/annotation/NonNull.java'
TAKX_EVIDENCE = ('LICENSE.md', *(UI + name + '.java' for name in ('ContourListPanel', 'ContourUnits', 'ContourMapRenderer',
    'ContourPanel', 'GenerationDialog', 'ContourProgressPanel')),
    *(('Platform/src/main/java/gov/tak/platform/contour/' + name + '.java') for name in
      ('IContourLinesService', 'ContourLineGroup', 'ContourGroupListener')),
    'Client/src/main/java/gov/takx/core/contour/ContourLinesService.java',
    'Client/src/main/java/gov/takx/api/layer/IRaptorLayerManager.java',
    'Client/src/main/java/gov/takx/core/layer/RaptorLayerManager.java',
    'Client/src/main/java/gov/takx/core/globe/tak/TakLayerManager.java',
    'Client/src/main/java/gov/tak/core/map/layer/feature/datastore/memory/InMemoryFeatureDataStore.java')
JDK_FILES = ('bin/java', 'bin/javac', 'release', 'lib/modules')
COMPONENTS = tuple('takx/contour-elevation/java/gov/tak/project/contour/' + name + '.java'
                   for name in ('ElevationContours', 'ContourFeatures', 'ContourLayer'))
START = '    private record GradientColorFunction('
END = '\n    private void initComponents()'
FLAGS = ('contour_api_implemented', 'elevation_source_integrated', 'layer_runtime_accepted', 'full_client_accepted')
CLASSES = {
    'dependencies': {'com/atakmap/coremap/conversions/Span.class', 'gov/tak/api/annotation/NonNull.class'},
    'library': {'gov/tak/api/contour/ContourFunction.class', 'gov/tak/api/contour/ContourStyle.class',
                'gov/tak/api/contour/ContourStyle$1.class'},
    'controls': {'gov/tak/api/contour/ContourStyleControls.class',
                 *(('gov/takx/core/view/gui/maptools/elevationtools/contour/' + name + '.class') for name in
                   ('ContourUnits', 'ContourUnits$1', 'PinnedContourStyleCaller', 'PinnedContourStyleCaller$GradientColorFunction'))},
}


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    path = Path(path).absolute()
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'symlink input refused')
    require(stat.S_ISREG(path.stat().st_mode), 'regular input required')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def contract():
    path = HERE / 'inputs.lock.json'
    sha(path)
    require(path.stat().st_size <= 65536, 'contract size bound')
    lock = json.loads(path.read_text())
    require(lock['schema'] == 1 and lock['profile'] == 'observable-contour-style-java-v1' and
            set(lock['owned']) == set(OWNED), 'style source profile differs')
    require(all(lock[key] is False for key in FLAGS), 'style slice cannot grant client acceptance')
    require(lock['takx_revision'] == 'af54a3e1fe8796b6e109e2e5925dbef524ea7e31' and
            lock['kernel_revision'] == '9f6893dd657feacc35ec5de03dad721c2e44170e', 'official source revision differs')
    require(set(lock['takx']) == set(TAKX_EVIDENCE) and set(lock['kernel']) == {SPAN, ANNOTATION, 'LICENSE.md'} and
            set(lock['jdk']) == set(JDK_FILES) and set(lock['components']) == set(COMPONENTS), 'external source inventory differs')
    require(lock['control_inventory'] == {'ContourStyleControls': 311, 'PinnedContourStyleCaller': 23},
            'control inventory differs')
    for name, pin in lock['owned'].items():
        require(sha(HERE / name) == pin, 'owned source bytes differ')
    for name, pin in lock['components'].items():
        require(sha(REPO / name) == pin, 'traced project component differs')
    return lock


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def controls(args):
    lock = contract()
    java, takx, kernel, annotations, output = (path.absolute() for path in
            (args.java_home, args.takx, args.kernel, args.annotations, args.output))
    for root in (REPO, java, takx, kernel, annotations):
        require(output != root and not output.is_relative_to(root) and not root.is_relative_to(output), 'output overlaps inputs')
    require(not any(p.is_symlink() for p in (output, *output.parents)), 'symlink output refused')
    require(not output.exists() and output.parent.is_dir(), 'fresh output required')
    inputs = {}
    for root, group in ((HERE, 'owned'), (REPO, 'components'), (takx, 'takx'), (kernel, 'kernel'), (java, 'jdk')):
        for name, pin in lock[group].items():
            require(sha(root / name) == pin, group + ' input identity differs')
            inputs[str(root / name)] = pin
    require(sha(annotations) == lock['annotations_sha256'], 'annotations identity differs')
    inputs[str(annotations)] = lock['annotations_sha256']
    for path in (HERE / 'inputs.lock.json', HERE / 'build.py', REPO / 'LICENSE',
                 REPO / 'takx/native-commoncommo/runner.py'):
        inputs[str(path)] = sha(path)
    source = (takx / (UI + 'ContourListPanel.java')).read_text()
    require(source.count(START) == 1 and source.count(END) == 1, 'pinned gradient markers differ')
    gradient = source[source.index(START):source.index(END)].rstrip()
    require(hashlib.sha256(gradient.encode()).hexdigest() == lock['gradient_sha256'], 'caller slice differs')
    template = (HERE / OWNED[-1]).read_text()
    marker = '    // PINNED_GRADIENT_RECORD'
    require(template.count(marker) == 1, 'caller template marker differs')
    runner = module('contour_style_runner', REPO / 'takx/native-commoncommo/runner.py')
    env = {k: v for k, v in os.environ.items() if k not in ('CLASSPATH', 'BASH_ENV', 'ENV')
           and not k.startswith(('LD_', 'DYLD_', 'JAVA_', '_JAVA_', 'JDK_'))}
    env['LC_ALL'] = 'C'
    output.mkdir(mode=0o700)
    record = {'schema': 1, 'type': 'project-contour-style-caller-slice', 'scope': lock['scope'],
              'host': {'system': os.uname().sysname, 'machine': os.uname().machine},
              'inputs': inputs, 'commands': [], 'completed': False, **{key: False for key in FLAGS}}

    def write():
        (output / 'receipt.json').write_text(json.dumps(record, indent=2, sort_keys=True) + '\n')

    def unchanged():
        for name, pin in inputs.items():
            require(sha(name) == pin, 'consumed input changed')
        require(contract() == lock, 'source contract changed')

    def run(label, command):
        unchanged()
        row = {'label': label, 'argv': list(map(str, command))}
        record['commands'].append(row)
        write()
        runner.execute(row['argv'], output, label, 30, env, disk_limit=16 * 1024**2, log_limit=1024**2)
        row.update(passed=True, log_sha256=sha(output / (label + '.log')))
        write()

    write()
    try:
        for name in CLASSES:
            (output / name).mkdir()
        generated = output / 'PinnedContourStyleCaller.java'
        generated.write_text(template.replace(marker, gradient))
        for name, path in (('TAKX-LICENSE.md', takx / 'LICENSE.md'), ('KERNEL-LICENSE.md', kernel / 'LICENSE.md')):
            (output / name).write_bytes(path.read_bytes())
        deps, library, tests = (output / name for name in ('dependencies', 'library', 'controls'))
        javac = [java / 'bin/javac', '--release', '17', '-proc:none', '-encoding', 'UTF-8']
        run('compile-dependencies', [*javac, '-classpath', annotations, '-d', deps, kernel / SPAN, kernel / ANNOTATION])
        run('compile-library', [*javac, '-classpath', deps, '-d', library, *(HERE / name for name in API)])
        for label in ('compile-dependencies', 'compile-library'):
            require((output / (label + '.log')).read_bytes() == b'', 'compiler diagnostics require review')
        for name in ('dependencies', 'library'):
            require({str(p.relative_to(output / name)) for p in (output / name).rglob('*') if p.is_file()} == CLASSES[name],
                    'compiled inventory differs')
        jar = output / 'contour-style-project.jar'
        entries = {name: (library / name).read_bytes() for name in CLASSES['library']}
        entries.update({'META-INF/sources/' + name.removeprefix('java/'): (HERE / name).read_bytes() for name in API})
        entries['META-INF/LICENSE'] = (REPO / 'LICENSE').read_bytes()
        entries['META-INF/project-contour-style.json'] = (json.dumps(lock, indent=2, sort_keys=True) + '\n').encode()
        with zipfile.ZipFile(jar, 'x', compression=zipfile.ZIP_STORED) as archive:
            for name, value in sorted(entries.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                archive.writestr(info, value)
        with zipfile.ZipFile(jar) as archive:
            require(set(archive.namelist()) == set(entries) and all(archive.read(name) == data for name, data in entries.items()),
                    'packaged inventory differs')
        classpath = os.pathsep.join(map(str, (jar, deps, annotations)))
        run('compile-callers', [*javac, '-classpath', classpath, '-d', tests, HERE / OWNED[-2], generated,
                                takx / (UI + 'ContourUnits.java')])
        require((output / 'compile-callers.log').read_bytes() == b'', 'caller compiler diagnostics require review')
        require({str(p.relative_to(tests)) for p in tests.rglob('*') if p.is_file()} == CLASSES['controls'],
                'caller class inventory differs')
        classpath = os.pathsep.join((str(tests), classpath))
        for name, prefix, label, marker in (
                ('ContourStyleControls', 'gov.tak.api.contour.', 'style-controls', 'contour-style-controls'),
                ('PinnedContourStyleCaller', 'gov.takx.core.view.gui.maptools.elevationtools.contour.', 'caller-controls', 'pinned-contour-style-caller')):
            run(label, [java / 'bin/java', '-Xcheck:jni', '-Xmx256m', '-Djava.awt.headless=true', '-cp', classpath, prefix + name, jar])
            expected = marker + ' passed checks=' + str(lock['control_inventory'][name]) + '\n'
            require((output / (label + '.log')).read_text() == expected, 'caller controls did not pass cleanly')
        unchanged()
        record.update(completed=True, control_inventory=lock['control_inventory'],
                      project_java_style_implemented=True, project_native_libraries_supplied=False,
                      project_jni_exercised=False, full_panel_compiled=False,
                      jar_sha256=sha(jar), jar_entries={name: hashlib.sha256(data).hexdigest() for name, data in sorted(entries.items())},
                      outputs={str(p.relative_to(output)): sha(p) for p in output.rglob('*') if p.is_file() and p.name != 'receipt.json'})
        write()
    except BaseException:
        record['failed'] = True
        write()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('verify', 'controls'), default='verify')
    for name in ('java-home', 'takx', 'kernel', 'annotations', 'output'):
        parser.add_argument('--' + name, type=Path)
    args = parser.parse_args(argv)
    paths = (args.java_home, args.takx, args.kernel, args.annotations, args.output)
    if args.phase == 'verify':
        require(all(path is None for path in paths), 'paths require explicit controls phase')
        contract()
    else:
        require(all(path is not None for path in paths), 'explicit JDK, pinned sources, annotations and fresh output required')
        controls(args)


if __name__ == '__main__':
    main()
