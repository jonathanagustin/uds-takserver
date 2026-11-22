#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Run takx/client58-public-api vehicle metadata packaging and 34 CoT controls on hosted Linux JDK 17."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
RECEIPT_NAME = 'hosted-client58-public-api-receipt.json'
SLF4J_SHA256 = 'cdba07964d1bb40a0761485c6b1e8c2f8fd9eb1d19c53928ac0d7f9510105c57'
JAKARTA_ANN_SHA256 = '5f65fdaf424eee2b55e1d882ba9bb376be93fb09b37b808be6e22e8851c909fe'
KERNEL_API_SOURCES = (
    'gov/tak/api/cot/detail/ICotDetailHandler.java',
    'gov/tak/api/cot/detail/ICotDetailHandler2.java',
    'gov/tak/api/cot/event/CotDetail.java',
    'gov/tak/api/cot/event/CotEvent.java',
    'gov/tak/api/util/AttributeSet.java',
)
BROAD_FLAGS = (
    'private_sdk_equivalence',
    'vehicle_mesh_runtime_accepted',
    'contours_implemented',
    'full_client_accepted',
)


def load_build():
    spec = importlib.util.spec_from_file_location('client58_public_api_build', HERE / 'build.py')
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


def run_cmd(argv, env, timeout=60):
    proc = subprocess.run(
        [str(x) for x in argv],
        env=env,
        capture_output=True,
        text=False,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def discover_kernel_roots(kernel_root):
    candidates = [
        kernel_root / 'takkernel' / 'shared' / 'src' / 'main' / 'java',
        kernel_root / 'takkernel' / 'shared' / 'src' / 'jre' / 'java',
        kernel_root / 'takkernel' / 'engine' / 'src' / 'main' / 'java',
        kernel_root / 'takkernel' / 'engine' / 'src' / 'jre' / 'java',
        kernel_root / 'takkernel' / 'port' / 'src' / 'main' / 'java',
        kernel_root / 'takkernel' / 'port' / 'src' / 'jre' / 'java',
        kernel_root / 'takkernel' / 'annotation' / 'src' / 'main' / 'java',
        REPO / 'takx' / 'compat' / 'src' / 'main' / 'java',
    ]
    return [p for p in candidates if p.is_dir()]


def locate_kernel_sources(roots):
    found = []
    for rel in KERNEL_API_SOURCES:
        matches = [r / rel for r in roots if (r / rel).is_file()]
        require(len(matches) >= 1, f'missing pinned Kernel API source: {rel}')
        found.append(matches[0])
    return found


def verify_only():
    build = load_build()
    lock = build.contract()
    require(all(lock[key] is False for key in BROAD_FLAGS), 'broad acceptance flags must remain false')
    return lock


def qualify(args):
    build = load_build()
    lock = build.contract()
    java_home = resolve_java_home(args.java_home)
    kernel = args.kernel.resolve()
    annotations = args.annotations.resolve()
    slf4j = args.slf4j.resolve()
    output = args.output.absolute()

    for root in (REPO, java_home, kernel, annotations, slf4j):
        require(
            output != root and not output.is_relative_to(root) and not root.is_relative_to(output),
            'output overlaps inputs',
        )
    require(not any(p.is_symlink() for p in (output, *output.parents)), 'symlink output refused')
    require(not output.exists() and output.parent.is_dir(), 'fresh output directory required')

    wire_source = kernel / lock['wire_source_path']
    require(build.sha(wire_source) == lock['wire_source_sha256'], 'pinned Android wire source SHA-256 differs')
    require(build.sha(annotations) == JAKARTA_ANN_SHA256, 'jakarta.annotation-api SHA-256 differs')
    require(build.sha(slf4j) == SLF4J_SHA256, 'slf4j-api SHA-256 differs')

    kernel_roots = discover_kernel_roots(kernel)
    kernel_api_files = locate_kernel_sources(kernel_roots)

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
        'type': 'hosted-public-vehicle-metadata-v2',
        'profile': lock['profile'],
        'scope': lock['scope'],
        'wire_source_revision': lock['wire_source_revision'],
        'wire_source_sha256': lock['wire_source_sha256'],
        'host': {
            'system': platform.system(),
            'machine': platform.machine(),
            'java_version': java_version_line,
        },
        'commands': [],
        'completed': False,
        'vehicle_metadata_artifact_accepted': False,
        **{key: False for key in BROAD_FLAGS},
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
        kernel_classes = output / 'kernel-classes'
        library_classes = output / 'library-classes'
        control_classes = output / 'control-classes'
        for d in (kernel_classes, library_classes, control_classes):
            d.mkdir()

        deps_cp = os.pathsep.join(map(str, (annotations, slf4j)))
        sourcepath = os.pathsep.join(map(str, kernel_roots))
        javac = [javac_bin, '--release', '17', '-proc:none', '-encoding', 'UTF-8']

        exec_step(
            'compile-kernel-cot-api',
            [*javac, '-classpath', deps_cp, '-sourcepath', sourcepath, '-d', kernel_classes, *kernel_api_files],
        )

        lib_cp = os.pathsep.join(map(str, (kernel_classes, deps_cp)))
        exec_step(
            'compile-vehicle-library',
            [*javac, '-classpath', lib_cp, '-d', library_classes, HERE / build.SOURCE],
        )

        jar = output / 'vehicle-metadata.jar'
        artifact = build.package_library(library_classes, jar, lock)

        ctrl_cp = os.pathsep.join(map(str, (jar, kernel_classes, deps_cp)))
        exec_step(
            'compile-vehicle-controls',
            [
                *javac,
                '-classpath',
                ctrl_cp,
                '-d',
                control_classes,
                HERE / 'tests/VehicleModelControls.java',
                HERE / build.ARTIFACT_CONTROL,
            ],
        )

        run_cp = os.pathsep.join(map(str, (control_classes, jar, kernel_classes, deps_cp)))
        ctrl_out = exec_step(
            'run-vehicle-artifact-controls',
            [java_bin, '-Xcheck:jni', '-Xmx256m', '-cp', run_cp, 'VehicleArtifactControls', jar],
        )
        lines = ctrl_out.decode('utf-8').replace('\r\n', '\n').splitlines()
        require(
            lines == ['VEHICLE_MODEL_CONTROLS_PASS checks=34', 'VEHICLE_METADATA_ARTIFACT_PASS classes=2'],
            f'unexpected control output: {lines!r}',
        )

        record.update(
            completed=True,
            vehicle_metadata_artifact_accepted=True,
            controls_passed=34,
            artifact_classes_verified=2,
            artifact=artifact,
            **{key: False for key in BROAD_FLAGS},
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
    parser.add_argument('--kernel', type=Path)
    parser.add_argument('--annotations', type=Path)
    parser.add_argument('--slf4j', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.phase == 'verify':
        require(
            all(x is None for x in (args.java_home, args.kernel, args.annotations, args.slf4j, args.output)),
            'paths require explicit --phase qualify',
        )
        verify_only()
    else:
        require(
            all(x is not None for x in (args.kernel, args.annotations, args.slf4j, args.output)),
            '--kernel, --annotations, --slf4j, and --output are required for --phase qualify',
        )
        qualify(args)


if __name__ == '__main__':
    main()
