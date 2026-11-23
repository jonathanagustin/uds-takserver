#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for takx/kernel-render-context/font-jni-control."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER_CONTEXT_DIR = HERE.parent

SCHEMA = "uds-takx-font-jni-control-hosted-v1"
EXPECTED_REVISION = "e8adc958475b92b9f251145c6ce20532be16d806"
EXPECTED_TAG = "jdk-17.0.21+9"
EXPECTED_LICENSE = "GPL-2.0-only WITH Classpath-exception-2.0"

LOCK_FILE = HERE / "inputs.lock.json"
PATCH_FILE = HERE / "font-exception-checks.patch"
PREPARE_FILE = HERE / "prepare.py"
LICENSE_FILE = HERE / "OPENJDK-LICENSE"
FONT_PROBE_JAVA = RENDER_CONTEXT_DIR / "tests/FontJniProbe.java"

EXPECTED_FILES = (
    "src/java.desktop/share/native/libfontmanager/HBShaper.c",
    "src/java.desktop/share/native/libfontmanager/freetypeScaler.c",
    "src/java.desktop/share/native/libfontmanager/hb-jdk-font.cc",
)
FATAL_MARKERS = (
    "FATAL ERROR",
    "JNI DETECTED ERROR",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
)
JNI_WARNING_MARKERS = ("WARNING in native method", "WARNING: JNI")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_source_text(path: Path) -> str:
    raw = path.read_bytes()
    if os.name == "nt":
        raw = raw.replace(b"\r\n", b"\n")
    return _sha256_bytes(raw)


def _load_prepare_module():
    spec = importlib.util.spec_from_file_location("font_jni_prepare", PREPARE_FILE)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module from {PREPARE_FILE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_offline() -> dict:
    """Verify frozen lockfile, patch, license, prepare helper, and FontJniProbe.java offline."""
    for path in (LOCK_FILE, PATCH_FILE, PREPARE_FILE, LICENSE_FILE, FONT_PROBE_JAVA):
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(
                f"missing or empty required font-jni-control input: {path}"
            )

    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    if lock.get("schema") != 1:
        raise ValueError(f"unexpected inputs.lock.json schema: {lock.get('schema')}")
    if lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(
            f"unexpected official_revision: {lock.get('official_revision')}"
        )
    if lock.get("official_tag") != EXPECTED_TAG:
        raise ValueError(f"unexpected official_tag: {lock.get('official_tag')}")
    if lock.get("patch_license") != EXPECTED_LICENSE:
        raise ValueError(f"unexpected patch_license: {lock.get('patch_license')}")

    for flag in (
        "stock_source_binary_equivalence",
        "native_builder_integration",
        "runtime_accepted",
        "full_sdk_accepted",
    ):
        if lock.get(flag) is not False:
            raise ValueError(f"acceptance flag {flag} must remain False")

    patch_sha = _sha256_source_text(PATCH_FILE)
    if patch_sha != lock.get("patch_sha256"):
        raise ValueError(
            f"font-exception-checks.patch SHA mismatch: {patch_sha} != {lock.get('patch_sha256')}"
        )

    files = lock.get("files", {})
    if tuple(sorted(files)) != tuple(sorted(EXPECTED_FILES)):
        raise ValueError(f"unexpected locked file membership: {sorted(files)}")

    if os.name != "nt":
        prepare_mod = _load_prepare_module()
        prepare_mod.contract()

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "official_revision": EXPECTED_REVISION,
        "official_tag": EXPECTED_TAG,
        "inputs_lock_sha256": _sha256_source_text(LOCK_FILE),
        "patch_sha256": patch_sha,
        "prepare_py_sha256": _sha256_source_text(PREPARE_FILE),
        "license_sha256": _sha256_source_text(LICENSE_FILE),
        "font_probe_java_sha256": _sha256_source_text(FONT_PROBE_JAVA),
        "locked_files": len(files),
    }


def validate_prepared_receipt(receipt_path: Path, expected_lock: dict) -> dict:
    """Validate prepare.py's prepared.json and return its SHA-256 and parsed payload."""
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise FileNotFoundError(f"missing prepared.json receipt: {receipt_path}")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(
            f"unexpected prepared.json revision: {receipt.get('official_revision')}"
        )
    if receipt.get("patch_sha256") != expected_lock["patch_sha256"]:
        raise ValueError("prepared.json patch_sha256 mismatch")
    for flag in ("native_build_or_execution", "runtime_accepted", "full_sdk_accepted"):
        if receipt.get(flag) is not False:
            raise ValueError(f"prepared.json flag {flag} must remain False")
    return {
        "receipt": receipt,
        "sha256": _sha256_file(receipt_path),
    }


def audit_font_probe_output(
    stdout: str,
    stderr: str,
    returncode: int,
    *,
    xcheck_jni: bool,
) -> dict:
    """Validate FontJniProbe exit status, pass marker, and count stock -Xcheck:jni warnings."""
    if returncode != 0:
        raise RuntimeError(
            f"FontJniProbe exited with {returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    combined = f"{stdout}\n{stderr}"
    fatal = [
        line for line in combined.splitlines() if any(m in line for m in FATAL_MARKERS)
    ]
    if fatal:
        raise ValueError(f"fatal diagnostic in FontJniProbe: {fatal[:3]}")
    if "FONT_PROBE_COMPLETED" not in stdout:
        raise ValueError("missing FONT_PROBE_COMPLETED marker in FontJniProbe output")

    jni_warnings = [
        line
        for line in combined.splitlines()
        if any(m in line for m in JNI_WARNING_MARKERS)
    ]
    if not xcheck_jni and jni_warnings:
        raise ValueError(f"unexpected JNI warning in normal mode: {jni_warnings[:3]}")
    return {
        "exit_code": 0,
        "marker": "FONT_PROBE_COMPLETED",
        "xcheck_jni": xcheck_jni,
        "jni_warning_count": len(jni_warnings),
    }


def _clean_env(java_home: Path) -> dict[str, str]:
    removed = {
        "BASH_ENV",
        "ENV",
        "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "JDK_JAVAC_OPTIONS",
        "_JAVA_OPTIONS",
        "CLASSPATH",
        "PYTHONPATH",
        "PYTHONHOME",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in removed and not k.startswith(("LD_", "DYLD_", "BASH_FUNC_"))
    }
    env.update(
        JAVA_HOME=str(java_home),
        PATH=f"{java_home / 'bin'}:{env.get('PATH', '')}",
        LC_ALL="C.UTF-8",
        PYTHONNOUSERSITE="1",
    )
    return env


def qualify(
    jdk_source: Path,
    java_home: Path,
    output_dir: Path,
) -> dict:
    """Run prepare.py against jdk17u and execute FontJniProbe normally and with -Xcheck:jni."""
    verified = verify_offline()
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))

    jdk_source = jdk_source.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")

    java_bin = java_home / "bin/java"
    javac_bin = java_home / "bin/javac"
    for tool in (java_bin, javac_bin):
        if not tool.is_file():
            raise FileNotFoundError(f"missing JDK tool: {tool}")

    output_dir.mkdir(parents=True, exist_ok=True)
    prepared_dir = output_dir / "prepared"
    classes_dir = output_dir / "classes"
    classes_dir.mkdir(parents=True, exist_ok=True)
    env = _clean_env(java_home)

    # 1. Invoke frozen prepare.py --phase prepare directly (checks preimages & postimages).
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(PREPARE_FILE),
            "--phase",
            "prepare",
            "--source",
            str(jdk_source),
            "--output",
            str(prepared_dir),
        ],
        env=env,
        check=True,
        timeout=60,
    )
    prepared_info = validate_prepared_receipt(prepared_dir / "prepared.json", lock)
    prepared_hashes = {rel: _sha256_file(prepared_dir / rel) for rel in EXPECTED_FILES}
    for rel, spec in lock["files"].items():
        if prepared_hashes[rel] != spec["after"]:
            raise ValueError(f"prepared postimage SHA mismatch for {rel}")

    # 2. Compile and run frozen FontJniProbe.java normally and with -Xcheck:jni.
    subprocess.run(
        [
            str(javac_bin),
            "--release",
            "17",
            "-proc:none",
            "-d",
            str(classes_dir),
            str(FONT_PROBE_JAVA),
        ],
        env=env,
        check=True,
        timeout=60,
    )

    version_proc = subprocess.run(
        [str(java_bin), "-version"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    java_version_lines = [
        line.strip()
        for line in (version_proc.stderr or version_proc.stdout).splitlines()
        if line.strip()
    ]

    normal_proc = subprocess.run(
        [
            str(java_bin),
            "-Djava.awt.headless=true",
            "-Xmx256m",
            "-cp",
            str(classes_dir),
            "FontJniProbe",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    normal_audit = audit_font_probe_output(
        normal_proc.stdout,
        normal_proc.stderr,
        normal_proc.returncode,
        xcheck_jni=False,
    )

    xcheck_proc = subprocess.run(
        [
            str(java_bin),
            "-Xcheck:jni",
            "-Djava.awt.headless=true",
            "-Xmx256m",
            "-cp",
            str(classes_dir),
            "FontJniProbe",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    xcheck_audit = audit_font_probe_output(
        xcheck_proc.stdout,
        xcheck_proc.stderr,
        xcheck_proc.returncode,
        xcheck_jni=True,
    )

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "prepared_receipt_sha256": prepared_info["sha256"],
        "prepared_files": prepared_hashes,
        "java_version": java_version_lines,
        "font_probe": {
            "normal": normal_audit,
            "xcheck_jni_baseline": xcheck_audit,
            "stock_xcheck_jni_warnings": xcheck_audit["jni_warning_count"],
        },
        "scope": {
            "source_prepare_verified": True,
            "font_probe_executed": True,
            "stock_source_binary_equivalence": False,
            "native_builder_integration": False,
            "runtime_accepted": False,
            "full_sdk_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-font-jni-control-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--jdk-source", type=Path)
    parser.add_argument(
        "--java-home", type=Path, default=Path("/usr/lib/jvm/java-17-openjdk-amd64")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    if not args.jdk_source or not args.output:
        parser.error("--phase qualify requires --jdk-source and --output")

    qualify(
        jdk_source=args.jdk_source,
        java_home=args.java_home,
        output_dir=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
