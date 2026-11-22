#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted CI qualification runner for takx/kernel-render-context CPU/JVM controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
PORT_CLASSPATH_LOCK = REPO_ROOT / "takx" / "compat" / "port-classpath.lock.json"
SLF4J_JAR_NAME = "slf4j-api-1.7.30.jar"
SCHEMA = "uds-takx-render-context-hosted-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
EXPECTED_BINDING_ASSERTIONS = 88


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    required = (
        HERE / "prepare.py",
        HERE / "inputs.lock.json",
        HERE / "render-context-control.patch",
        HERE / "worker_retirement_control.py",
        HERE / "tests" / "BindingStateTest.java",
        HERE / "tests" / "WorkerRetirementControl.cpp",
    )
    hashes = {}
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f"missing required render-context file: {path}")
        hashes[str(path.relative_to(HERE)).replace("\\", "/")] = sha256_file(path)
    lock = json.loads((HERE / "inputs.lock.json").read_text(encoding="utf-8"))
    if lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(
            f"unexpected inputs.lock.json official_revision: {lock.get('official_revision')}"
        )
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": EXPECTED_REVISION,
        "expected_binding_assertions": EXPECTED_BINDING_ASSERTIONS,
        "files": hashes,
    }


def parse_binding_assertions(stdout: str) -> int:
    for line in stdout.splitlines():
        prefix = "binding state assertions="
        if line.strip().startswith(prefix):
            return int(line.strip()[len(prefix):])
    raise ValueError(f"missing binding state assertions marker in output: {stdout!r}")


def validate_receipt(receipt: dict) -> None:
    if receipt.get("schema") != SCHEMA:
        raise ValueError("unexpected hosted render-context receipt schema")
    if receipt.get("revision") != EXPECTED_REVISION:
        raise ValueError("unexpected official source revision in receipt")
    controls = receipt.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("missing controls object")
    if controls.get("prepare_verified") is not True:
        raise ValueError("prepare_verified must be true")
    if controls.get("binding_state_assertions") != EXPECTED_BINDING_ASSERTIONS:
        raise ValueError(
            f"expected {EXPECTED_BINDING_ASSERTIONS} binding state assertions, "
            f"got {controls.get('binding_state_assertions')}"
        )
    if controls.get("worker_retirement_control") != "pass":
        raise ValueError("worker_retirement_control must pass")
    scope = receipt.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("missing scope object")
    if scope.get("hosted_cpu_jvm_controls") is not True:
        raise ValueError("scope.hosted_cpu_jvm_controls must be true")
    for key in (
        "gl_context_recreation_accepted",
        "sdk_accepted",
        "security_accepted",
        "client_accepted",
    ):
        if scope.get(key) is not False:
            raise ValueError(f"scope.{key} must remain false")


def run_cmd(argv: list[str], *, cwd: Path | None = None, timeout: int = 180) -> str:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc.stdout


def qualify(official_source: Path, output: Path, cxx: str) -> dict:
    official = official_source.resolve(strict=True)
    out = output.resolve()
    if out.exists():
        raise FileExistsError(f"output directory must be fresh: {out}")
    out.mkdir(parents=True, mode=0o700)

    head = run_cmd(["git", "-C", str(official), "rev-parse", "HEAD"], timeout=30).strip()
    if head != EXPECTED_REVISION:
        raise ValueError(f"official source HEAD {head} != {EXPECTED_REVISION}")

    prepared_root = out / "prepared"
    run_cmd(
        [
            sys.executable,
            "-B",
            str(HERE / "prepare.py"),
            "--phase",
            "prepare",
            "--source",
            str(official),
            "--baseline-profile",
            str(REPO_ROOT / "takx" / "kernel-native"),
            "--texture-profile",
            str(REPO_ROOT / "takx" / "kernel-texture"),
            "--client-profile",
            str(REPO_ROOT / "takx" / "kernel-client-interop"),
            "--output",
            str(prepared_root),
        ],
        timeout=180,
    )

    # Compile and execute BindingStateTest.java (88 JVM state assertions) against
    # patched RenderContextBinding.java and official public Kernel interfaces.
    classes_dir = out / "binding-classes"
    classes_dir.mkdir(mode=0o700)
    binding_src = (
        prepared_root
        / "source"
        / "takkernel"
        / "engine"
        / "src"
        / "main"
        / "java"
        / "com"
        / "atakmap"
        / "map"
        / "opengl"
        / "RenderContextBinding.java"
    )
    sourcepath = os.pathsep.join(
        [
            str(prepared_root / "source" / "takkernel" / "engine" / "src" / "main" / "java"),
            str(official / "takkernel" / "engine" / "src" / "main" / "java"),
            str(official / "takkernel" / "engine" / "src" / "jre" / "java"),
            str(official / "takkernel" / "port" / "src" / "main" / "java"),
            str(official / "takkernel" / "port" / "src" / "jre" / "java"),
            str(official / "takkernel" / "annotation" / "src" / "main" / "java"),
            str(REPO_ROOT / "takx" / "compat" / "src" / "main" / "java"),
        ]
    )
    java_home = os.environ.get("JAVA_HOME_17_X64") or os.environ.get("JAVA_HOME")
    if java_home and (Path(java_home) / "bin" / "javac").is_file():
        javac = str(Path(java_home) / "bin" / "javac")
        java = str(Path(java_home) / "bin" / "java")
    else:
        javac = shutil.which("javac") or "javac"
        java = shutil.which("java") or "java"
    # Fetch the SHA-256-pinned slf4j-api jar required by takkernel's JRE Log.java.
    cp_lock = json.loads(PORT_CLASSPATH_LOCK.read_text(encoding="utf-8"))
    slf4j_entry = cp_lock[SLF4J_JAR_NAME]
    slf4j_jar = out / SLF4J_JAR_NAME
    urllib.request.urlretrieve(slf4j_entry["url"], slf4j_jar)
    actual_slf4j_sha = sha256_file(slf4j_jar)
    if actual_slf4j_sha != slf4j_entry["sha256"]:
        raise ValueError(
            f"{SLF4J_JAR_NAME} SHA-256 mismatch: expected {slf4j_entry['sha256']}, got {actual_slf4j_sha}"
        )

    run_cmd(
        [
            javac,
            "-encoding",
            "UTF-8",
            "-cp",
            str(slf4j_jar),
            "-sourcepath",
            sourcepath,
            "-d",
            str(classes_dir),
            str(binding_src),
            str(HERE / "tests" / "BindingStateTest.java"),
        ],
        timeout=120,
    )
    binding_stdout = run_cmd(
        [
            java,
            "-ea",
            "-cp",
            os.pathsep.join([str(classes_dir), str(slf4j_jar)]),
            "com.atakmap.map.opengl.BindingStateTest",
        ],
        timeout=60,
    )
    assertions = parse_binding_assertions(binding_stdout)
    if assertions != EXPECTED_BINDING_ASSERTIONS:
        raise ValueError(
            f"expected {EXPECTED_BINDING_ASSERTIONS} binding state assertions, got {assertions}"
        )

    # Compile and execute production RetirableWorker against genuine Kernel C++ Work units.
    worker_out = out / "worker-control"
    run_cmd(
        [
            sys.executable,
            "-B",
            str(HERE / "worker_retirement_control.py"),
            "--official-source",
            str(official),
            "--prepared-source",
            str(prepared_root / "source"),
            "--output",
            str(worker_out),
            "--cxx",
            cxx,
        ],
        timeout=180,
    )
    worker_receipt = json.loads((worker_out / "receipt.json").read_text(encoding="utf-8"))
    if worker_receipt.get("accepted") is not True:
        raise ValueError(f"worker_retirement_control did not pass: {worker_receipt}")

    java_ver = run_cmd([java, "-version"], timeout=30)
    gxx_ver = run_cmd([cxx, "--version"], timeout=30).splitlines()[0]
    receipt = {
        "schema": SCHEMA,
        "revision": EXPECTED_REVISION,
        "toolchain": {
            "cxx": gxx_ver,
            "java": java_ver.strip() or "jdk17",
        },
        "controls": {
            "prepare_verified": True,
            "binding_state_assertions": assertions,
            "worker_retirement_control": "pass",
        },
        "digests": {
            "prepare_receipt_sha256": sha256_file(prepared_root / "receipt.json"),
            "worker_receipt_sha256": sha256_file(worker_out / "receipt.json"),
            "binding_source_sha256": sha256_file(binding_src),
        },
        "scope": {
            "hosted_cpu_jvm_controls": True,
            "gl_context_recreation_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    validate_receipt(receipt)
    receipt_path = out / "hosted-render-context-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--official-source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args(argv)
    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2, sort_keys=True))
        return 0
    if not args.official_source or not args.output:
        parser.error("--official-source and --output are required for --phase qualify")
    receipt = qualify(args.official_source, args.output, args.cxx)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
