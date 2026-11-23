#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX 5.8 public vehicle CoT metadata API."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMA = "uds-takx-client58-public-api-hosted-v1"
EXPECTED_PROFILE = "public-vehicle-metadata-v2"
STRICT_JNI_MARKERS = (
    "WARNING in native method",
    "WARNING: JNI",
    "FATAL ERROR",
    "JNI DETECTED ERROR",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
)
REQUIRED_MODEL_PASS = "VEHICLE_MODEL_CONTROLS_PASS checks=34"
REQUIRED_ARTIFACT_PASS = "VEHICLE_METADATA_ARTIFACT_PASS classes=2"

INPUTS_LOCK = HERE / "inputs.lock.json"
BUILD_SCRIPT = HERE / "build.py"
SOURCE_JAVA = HERE / "java/gov/tak/api/cot/detail/VehicleModelDetailHandler.java"
MODEL_CONTROLS_JAVA = HERE / "tests/VehicleModelControls.java"
ARTIFACT_CONTROLS_JAVA = HERE / "tests/VehicleArtifactControls.java"
LICENSE_FILE = REPO / "LICENSE"


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "client58_public_api_build", BUILD_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {BUILD_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_digest(path: Path) -> str:
    data = path.read_bytes()
    if os.name == "nt":
        # Windows Git checkouts convert LF text sources to CRLF while LF hashes are locked.
        data = data.replace(b"\r\n", b"\n")
    return _sha256_bytes(data)


def verify_offline() -> dict:
    """Validate frozen client58-public-api lockfile, build script, LICENSE, and Java sources."""
    required = (
        INPUTS_LOCK,
        BUILD_SCRIPT,
        SOURCE_JAVA,
        MODEL_CONTROLS_JAVA,
        ARTIFACT_CONTROLS_JAVA,
        LICENSE_FILE,
    )
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing or empty required client58 input: {path}")

    lock = json.loads(INPUTS_LOCK.read_text(encoding="utf-8"))
    if lock.get("profile") != EXPECTED_PROFILE:
        raise ValueError(f"unexpected inputs.lock.json profile: {lock.get('profile')}")

    expected_owned = {
        "java/gov/tak/api/cot/detail/VehicleModelDetailHandler.java",
        "tests/VehicleModelControls.java",
        "tests/VehicleArtifactControls.java",
    }
    owned = lock.get("owned", {})
    if set(owned) != expected_owned:
        raise ValueError(f"unexpected owned source set: {sorted(owned)}")

    for key in (
        "private_sdk_equivalence",
        "vehicle_mesh_runtime_accepted",
        "contours_implemented",
        "full_client_accepted",
    ):
        if lock.get(key) is not False:
            raise ValueError(f"source lock cannot pre-grant acceptance flag: {key}")

    source_hashes: dict[str, str] = {}
    for rel_path, expected_sha in sorted(owned.items()):
        actual_sha = _source_digest(HERE / rel_path)
        if actual_sha != expected_sha:
            raise ValueError(
                f"owned source SHA-256 mismatch for {rel_path}: {actual_sha} != {expected_sha}"
            )
        source_hashes[rel_path] = actual_sha

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profile": lock["profile"],
        "actual_kernel": lock["actual_kernel"],
        "caller_revision": lock["caller_revision"],
        "wire_source_revision": lock["wire_source_revision"],
        "inputs_lock_sha256": _sha256_file(INPUTS_LOCK),
        "build_py_sha256": _sha256_file(BUILD_SCRIPT),
        "owned_source_hashes": source_hashes,
    }


def audit_controls_output(stdout: str, stderr: str, exit_code: int) -> dict:
    """Enforce zero-exit, strict -Xcheck:jni cleanliness, and exact CoT + artifact markers."""
    if exit_code != 0:
        raise ValueError(f"VehicleArtifactControls exited with {exit_code}")

    combined = f"{stdout}\n{stderr}"
    diags = [
        line
        for line in combined.splitlines()
        if any(m in line for m in STRICT_JNI_MARKERS)
    ]
    if diags:
        raise ValueError(
            f"strict -Xcheck:jni or linkage diagnostic detected: {diags[:3]}"
        )

    if REQUIRED_MODEL_PASS not in stdout:
        raise ValueError(f"missing CoT controls marker {REQUIRED_MODEL_PASS!r}")
    if REQUIRED_ARTIFACT_PASS not in stdout:
        raise ValueError(f"missing artifact origin marker {REQUIRED_ARTIFACT_PASS!r}")

    return {
        "checks": 34,
        "artifact_classes_verified": 2,
        "strict_jni_warnings": 0,
    }


def _clean_env(java_home: Path) -> dict[str, str]:
    removed = {
        "CLASSPATH",
        "BASH_ENV",
        "ENV",
        "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "JDK_JAVAC_OPTIONS",
        "_JAVA_OPTIONS",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in removed
        and not k.startswith(("LD_", "DYLD_", "JAVA_", "_JAVA_", "JDK_"))
    }
    env.update(
        JAVA_HOME=str(java_home),
        LC_ALL="C.UTF-8",
        PYTHONNOUSERSITE="1",
    )
    return env


def qualify(kernel_java_dir: Path, java_home: Path, output_dir: Path) -> dict:
    """Build reproducible vehicle-metadata.jar twice and run all 34 CoT + origin controls under -Xcheck:jni."""
    verified = verify_offline()
    build_mod = _load_build_module()
    lock = json.loads(INPUTS_LOCK.read_text(encoding="utf-8"))

    kernel_java_dir = kernel_java_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")

    classes_root = kernel_java_dir / "classes"
    classpath_dir = kernel_java_dir / "classpath"
    if not classes_root.is_dir() or not classpath_dir.is_dir():
        raise FileNotFoundError(
            f"expected classes/ and classpath/ under {kernel_java_dir}"
        )

    dep_jars = sorted(classpath_dir.glob("*.jar"))
    if not dep_jars:
        raise FileNotFoundError(f"no dependency JARs found under {classpath_dir}")

    java_bin = java_home / "bin/java"
    javac_bin = java_home / "bin/javac"
    for tool in (java_bin, javac_bin):
        if not tool.is_file():
            raise FileNotFoundError(f"missing JDK tool: {tool}")

    output_dir.mkdir(parents=True, exist_ok=True)
    env = _clean_env(java_home)
    base_cp = os.pathsep.join(str(p) for p in [classes_root, *dep_jars])

    built_jars: list[Path] = []
    build_metas: list[dict] = []
    for idx in (1, 2):
        lib_classes = output_dir / f"build-{idx}/library-classes"
        lib_classes.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                str(javac_bin),
                "--release",
                "11",
                "-proc:none",
                "-cp",
                base_cp,
                "-d",
                str(lib_classes),
                str(SOURCE_JAVA),
            ],
            env=env,
            check=True,
            timeout=60,
        )
        jar_out = output_dir / f"build-{idx}/vehicle-metadata.jar"
        meta = build_mod.package_library(lib_classes, jar_out, lock)
        built_jars.append(jar_out)
        build_metas.append(meta)

    if (
        built_jars[0].read_bytes() != built_jars[1].read_bytes()
        or build_metas[0] != build_metas[1]
    ):
        raise ValueError(
            "two clean vehicle-metadata.jar builds produced differing bytes or metadata"
        )

    artifact = output_dir / "vehicle-metadata.jar"
    shutil.copyfile(built_jars[0], artifact)
    artifact_sha = _sha256_file(artifact)

    controls_classes = output_dir / "controls-classes"
    controls_classes.mkdir(parents=True, exist_ok=True)
    artifact_cp = os.pathsep.join(str(p) for p in [artifact, classes_root, *dep_jars])
    subprocess.run(
        [
            str(javac_bin),
            "--release",
            "11",
            "-proc:none",
            "-cp",
            artifact_cp,
            "-d",
            str(controls_classes),
            str(MODEL_CONTROLS_JAVA),
            str(ARTIFACT_CONTROLS_JAVA),
        ],
        env=env,
        check=True,
        timeout=60,
    )

    proc = subprocess.run(
        [
            str(java_bin),
            "-Xcheck:jni",
            "-Xmx128m",
            "-Djava.awt.headless=true",
            "-cp",
            f"{controls_classes}{os.pathsep}{artifact_cp}",
            "VehicleArtifactControls",
            str(artifact),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    try:
        controls_audit = audit_controls_output(
            proc.stdout, proc.stderr, proc.returncode
        )
    except ValueError as exc:
        raise RuntimeError(
            f"{exc}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        ) from exc

    if _sha256_file(artifact) != artifact_sha:
        raise ValueError(
            "vehicle-metadata.jar digest changed during controls execution"
        )

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "deterministic_clean_builds": True,
        "artifact": {
            "name": artifact.name,
            "sha256": artifact_sha,
            "bytes": artifact.stat().st_size,
            "classes": build_metas[0]["classes"],
        },
        "controls": controls_audit,
        "scope": {
            "artifact_accepted": True,
            "deterministic_clean_builds": True,
            "controls_checks_passed": controls_audit["checks"],
            "strict_jni_warnings": 0,
            "private_sdk_equivalence": False,
            "vehicle_mesh_runtime_accepted": False,
            "contours_implemented": False,
            "full_client_accepted": False,
            "security_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-client58-public-api-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--kernel-java-dir", type=Path)
    parser.add_argument(
        "--java-home", type=Path, default=Path("/usr/lib/jvm/java-17-openjdk-amd64")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    missing = [
        flag
        for flag, val in (
            ("--kernel-java-dir", args.kernel_java_dir),
            ("--output", args.output),
        )
        if val is None
    ]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")

    qualify(
        kernel_java_dir=args.kernel_java_dir,
        java_home=args.java_home,
        output_dir=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
