#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Run takx/contour-style 311 model and 23 pinned TAKX caller controls on hosted Linux JDK 17."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
TEMPLATE_MARKER = '    // PINNED_GRADIENT_RECORD'
RECEIPT_NAME = 'hosted-contour-style-receipt.json'


def load_build():
    spec = importlib.util.spec_from_file_location('contour_style_build', HERE / 'build.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def require(value, message):
    if not value:
        raise ValueError(message)


def clean_env():
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ('CLASSPATH', 'BASH_ENV', 'ENV')
        and not k.startswith(('LD_', 'DYLD_', 'JAVA_', '_JAVA_', 'JDK_'))
    }
    env['LC_ALL'] = 'C'
    return env


def resolve_java_home(explicit):
    if explicit is not None:
        return Path(explicit).resolve()
    for var in ('JAVA_HOME_17_X64', 'JAVA_HOME_17_ARM64', 'JAVA_HOME'):
        val = os.environ.get(var)
        if val:
            return Path(val).resolve()
    raise ValueError('JDK 17 home required via --java-home or JAVA_HOME_17_X64/JAVA_HOME')


def run_cmd(argv, env, timeout=30):
    proc = subprocess.run(
        [str(x) for x in argv],
        env=env,
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def verify_only():
    build = load_build()
    lock = build.contract()
    require(all(lock[key] is False for key in build.FLAGS), 'broad acceptance flags must remain false')
    return lock


def qualify(args):
    build = load_build()
    lock = build.contract()
    java_home = resolve_java_home(args.java_home)
    takx = args.takx.resolve()
    kernel = args.kernel.resolve()
    annotations = args.annotations.resolve()
    output = args.output.absolute()

    for root in (REPO, java_home, takx, kernel, annotations):
        require(
            output != root and not output.is_relative_to(root) and not root.is_relative_to(output),
            'output overlaps inputs',
        )
    require(not any(p.is_symlink() for p in (output, *output.parents)), 'symlink output refused')
    require(not output.exists() and output.parent.is_dir(), 'fresh output directory required')

    inputs = {}
    for root, group in ((HERE, 'owned'), (REPO, 'components'), (takx, 'takx'), (kernel, 'kernel')):
        for name, pin in lock[group].items():
            require(build.sha(root / name) == pin, f'{group} input identity differs: {name}')
            inputs[f'{group}:{name}'] = pin
    require(build.sha(annotations) == lock['annotations_sha256'], 'annotations identity differs')
    inputs['annotations'] = lock['annotations_sha256']

    source = (takx / (build.UI + 'ContourListPanel.java')).read_text(encoding='utf-8')
    require(source.count(build.START) == 1 and source.count(build.END) == 1, 'pinned gradient markers differ')
    gradient = source[source.index(build.START):source.index(build.END)].rstrip()
    require(hashlib.sha256(gradient.encode('utf-8')).hexdigest() == lock['gradient_sha256'], 'caller slice differs')

    template = (HERE / build.OWNED[-1]).read_text(encoding='utf-8')
    require(template.count(TEMPLATE_MARKER) == 1, 'caller template marker differs')

    env = clean_env()
    javac_bin = java_home / 'bin' / 'javac'
    java_bin = java_home / 'bin' / 'java'
    require(javac_bin.is_file() and java_bin.is_file(), 'javac/java binaries missing under java_home')

    code, out, err = run_cmd([java_bin, '-version'], env)
    require(code == 0, 'java -version failed')
    java_version_line = (err or out).decode('utf-8', errors='replace').splitlines()[0].strip()

    output.mkdir(mode=0o700)
    record = {
        'schema': 1,
        'type': 'hosted-project-contour-style-caller-slice',
        'profile': lock['profile'],
        'scope': lock['scope'],
        'host': {
            'system': platform.system(),
            'machine': platform.machine(),
            'java_version': java_version_line,
        },
        'takx_revision': lock['takx_revision'],
        'kernel_revision': lock['kernel_revision'],
        'inputs': inputs,
        'commands': [],
        'completed': False,
        'project_java_style_implemented': False,
        'project_native_libraries_supplied': False,
        'project_jni_exercised': False,
        'full_panel_compiled': False,
        **{key: False for key in build.FLAGS},
    }

    receipt_path = output / RECEIPT_NAME

    def write_receipt():
        receipt_path.write_text(json.dumps(record, indent=2, sort_keys=True) + '\n', encoding='utf-8')

    def exec_step(label, command):
        row = {'label': label, 'argv': [str(x) for x in command]}
        record['commands'].append(row)
        write_receipt()
        rc, stdout_bytes, stderr_bytes = run_cmd(command, env)
        combined = stdout_bytes + stderr_bytes
        log_path = output / f'{label}.log'
        log_path.write_bytes(combined)
        row['returncode'] = rc
        row['log_sha256'] = build.sha(log_path)
        write_receipt()
        require(rc == 0, f'{label} failed with exit code {rc}: {combined.decode("utf-8", errors="replace")}')
        return combined

    write_receipt()
    try:
        for name in build.CLASSES:
            (output / name).mkdir()
        generated = output / 'PinnedContourStyleCaller.java'
        generated.write_text(template.replace(TEMPLATE_MARKER, gradient), encoding='utf-8')

        deps, library, tests = (output / name for name in ('dependencies', 'library', 'controls'))
        javac = [javac_bin, '--release', '17', '-proc:none', '-encoding', 'UTF-8']

        dep_log = exec_step(
            'compile-dependencies',
            [*javac, '-classpath', annotations, '-d', deps, kernel / build.SPAN, kernel / build.ANNOTATION],
        )
        require(dep_log == b'', 'compile-dependencies emitted unexpected diagnostics')
        lib_log = exec_step(
            'compile-library',
            [*javac, '-classpath', deps, '-d', library, *(HERE / name for name in build.API)],
        )
        require(lib_log == b'', 'compile-library emitted unexpected diagnostics')

        for name in ('dependencies', 'library'):
            actual = {p.relative_to(output / name).as_posix() for p in (output / name).rglob('*') if p.is_file()}
            require(actual == build.CLASSES[name], f'{name} compiled class inventory differs')

        jar = output / 'contour-style-project.jar'
        entries = {name: (library / name).read_bytes() for name in build.CLASSES['library']}
        entries.update(
            {'META-INF/sources/' + name.removeprefix('java/'): (HERE / name).read_bytes() for name in build.API}
        )
        entries['META-INF/LICENSE'] = (REPO / 'LICENSE').read_bytes()
        entries['META-INF/project-contour-style.json'] = (
            json.dumps(lock, indent=2, sort_keys=True) + '\n'
        ).encode('utf-8')
        with zipfile.ZipFile(jar, 'x', compression=zipfile.ZIP_STORED) as archive:
            for name, value in sorted(entries.items()):
                info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                archive.writestr(info, value)
        with zipfile.ZipFile(jar) as archive:
            require(
                set(archive.namelist()) == set(entries)
                and all(archive.read(name) == data for name, data in entries.items()),
                'packaged JAR inventory differs',
            )

        classpath = os.pathsep.join(map(str, (jar, deps, annotations)))
        caller_log = exec_step(
            'compile-callers',
            [
                *javac,
                '-classpath',
                classpath,
                '-d',
                tests,
                HERE / build.OWNED[-2],
                generated,
                takx / (build.UI + 'ContourUnits.java'),
            ],
        )
        require(caller_log == b'', 'compile-callers emitted unexpected diagnostics')
        actual_controls = {p.relative_to(tests).as_posix() for p in tests.rglob('*') if p.is_file()}
        require(actual_controls == build.CLASSES['controls'], 'caller class inventory differs')

        run_cp = os.pathsep.join((str(tests), classpath))
        for name, prefix, label, marker in (
            ('ContourStyleControls', 'gov.tak.api.contour.', 'style-controls', 'contour-style-controls'),
            (
                'PinnedContourStyleCaller',
                'gov.takx.core.view.gui.maptools.elevationtools.contour.',
                'caller-controls',
                'pinned-contour-style-caller',
            ),
        ):
            ctrl_log = exec_step(
                label,
                [java_bin, '-Xcheck:jni', '-Xmx256m', '-Djava.awt.headless=true', '-cp', run_cp, prefix + name, jar],
            )
            expected = f'{marker} passed checks={lock["control_inventory"][name]}\n'
            require(
                ctrl_log.decode('utf-8').replace('\r\n', '\n') == expected,
                f'{label} output did not match expected pass line',
            )

        record.update(
            completed=True,
            control_inventory=lock['control_inventory'],
            project_java_style_implemented=True,
            project_native_libraries_supplied=False,
            project_jni_exercised=False,
            full_panel_compiled=False,
            jar_sha256=build.sha(jar),
        )
        write_receipt()
        return record
    except BaseException:
        record['failed'] = True
        write_receipt()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('verify', 'qualify'), default='verify')
    parser.add_argument('--java-home', type=Path)
    parser.add_argument('--takx', type=Path)
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--annotations', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.phase == 'verify':
        require(
            all(x is None for x in (args.java_home, args.takx, args.kernel, args.annotations, args.output)),
            'paths require explicit --phase qualify',
        )
        verify_only()
    else:
        require(
            all(x is not None for x in (args.takx, args.kernel, args.annotations, args.output)),
            '--takx, --kernel, --annotations, and --output are required for --phase qualify',
        )
        qualify(args)


if __name__ == '__main__':
    main()
