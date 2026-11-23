#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 runner for takx/client58-public-api vehicle-metadata.jar and 34 CoT controls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMA = "uds-takx-client58-public-api-hosted-v1"


def _load_build_module():
    spec = importlib.util.spec_from_file_location("client58_public_api_build", HERE / "build.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {HERE / 'build.py'}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_java_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    for var in ("JAVA_HOME_17_X64", "JAVA_HOME_17_ARM64", "JAVA_HOME"):
        val = os.environ.get(var)
        if val:
            return Path(val).resolve()
    javac = shutil.which("javac")
    if not javac:
        raise ValueError("JDK 17 home required via --java-home or JAVA_HOME_17_X64/JAVA_HOME")
    return Path(javac).resolve().parents[1]


def clean_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLASSPATH", "BASH_ENV", "ENV")
        and not k.startswith(("LD_", "DYLD_", "JAVA_", "_JAVA_", "JDK_"))
    }
    env["LC_ALL"] = "C"
    return env


def verify_only() -> dict:
    build_mod = _load_build_module()
    lock = build_mod.contract()
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profile": lock["profile"],
        "caller_revision": lock["caller_revision"],
        "owned_files": sorted(lock["owned"]),
        "inputs_lock_sha256": sha256_file(HERE / "inputs.lock.json"),
        "build_py_sha256": sha256_file(HERE / "build.py"),
        "scope": {
            "hosted_linux_jdk17_client58_public_api": False,
            "private_sdk_equivalence": False,
            "vehicle_mesh_runtime_accepted": False,
            "contours_implemented": False,
            "full_client_accepted": False,
            "security_accepted": False,
        },
    }


def _build_deterministic_jar(
    build_mod,
    lock: dict,
    javac: Path,
    kernel_cp: str,
    build_dir: Path,
    env: dict[str, str],
) -> tuple[Path, dict]:
    classes_dir = build_dir / "lib-classes"
    classes_dir.mkdir(parents=True, mode=0o700)
    subprocess.run(
        [
            str(javac),
            "--release",
            "17",
            "-encoding",
            "UTF-8",
            "-proc:none",
            "-cp",
            kernel_cp,
            "-d",
            str(classes_dir),
            str(HERE / build_mod.SOURCE),
        ],
        env=env,
        check=True,
        timeout=60,
    )
    archive = build_dir / "vehicle-metadata.jar"
    meta = build_mod.package_library(classes_dir, archive, lock)
    return archive, meta


def qualify(
    kernel_classes: Path,
    output: Path,
    public_jars: Path | None = None,
    java_home: Path | None = None,
) -> dict:
    build_mod = _load_build_module()
    lock = build_mod.contract()

    kclasses = kernel_classes.resolve()
    require(kclasses.is_dir(), f"kernel classes directory missing: {kclasses}")
    require((kclasses / "gov/tak/api/cot/event/CotEvent.class").is_file(), "CotEvent.class missing in kernel classes")
    require((kclasses / "gov/tak/api/cot/detail/ICotDetailHandler2.class").is_file(), "ICotDetailHandler2.class missing")

    out = output.absolute()
    require(not any(p.is_symlink() for p in (out, *out.parents)), "symlink output refused")
    require(not out.exists() and out.parent.is_dir(), "fresh output directory required")
    roots = [REPO, kclasses]
    if public_jars is not None:
        pj = public_jars.resolve()
        require(pj.is_dir(), f"public jars directory missing: {pj}")
        roots.append(pj)
    else:
        pj = None
    for root in roots:
        require(out != root and not out.is_relative_to(root) and not root.is_relative_to(out), "output overlaps inputs")

    java = resolve_java_home(java_home)
    javac = java / "bin/javac"
    java_bin = java / "bin/java"
    require(javac.is_file() and java_bin.is_file(), f"missing JDK 17 tools under {java}")

    cp_entries = [str(kclasses)]
    if pj is not None:
        cp_entries.extend(str(jar) for jar in sorted(pj.glob("*.jar")))
    kernel_cp = os.pathsep.join(cp_entries)

    env = clean_env()
    out.mkdir(mode=0o700)

    jar1, meta1 = _build_deterministic_jar(build_mod, lock, javac, kernel_cp, out / "build-1", env)
    jar2, meta2 = _build_deterministic_jar(build_mod, lock, javac, kernel_cp, out / "build-2", env)
    require(meta1 == meta2, "two clean vehicle-metadata.jar builds produced differing metadata")
    require(jar1.read_bytes() == jar2.read_bytes(), "two clean vehicle-metadata.jar builds produced differing JAR bytes")

    test_classes = out / "test-classes"
    test_classes.mkdir(mode=0o700)
    control_cp = os.pathsep.join([str(jar1), kernel_cp])
    subprocess.run(
        [
            str(javac),
            "--release",
            "17",
            "-encoding",
            "UTF-8",
            "-proc:none",
            "-cp",
            control_cp,
            "-d",
            str(test_classes),
            str(HERE / "tests/VehicleModelControls.java"),
            str(HERE / build_mod.ARTIFACT_CONTROL),
        ],
        env=env,
        check=True,
        timeout=60,
    )

    run_cp = os.pathsep.join([str(test_classes), str(jar1), kernel_cp])
    proc = subprocess.run(
        [
            str(java_bin),
            "-Xcheck:jni",
            "-Xmx256m",
            "-cp",
            run_cp,
            "VehicleArtifactControls",
            str(jar1),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    if proc.returncode != 0:
        raise RuntimeError(f"VehicleArtifactControls failed (exit={proc.returncode}): {combined[-1500:]}")
    require("VEHICLE_MODEL_CONTROLS_PASS checks=34" in proc.stdout, f"missing 34-check marker: {proc.stdout}")
    require("VEHICLE_METADATA_ARTIFACT_PASS classes=2" in proc.stdout, f"missing artifact marker: {proc.stdout}")
    require("WARNING" not in combined and "FATAL ERROR" not in combined, f"unexpected JNI diagnostics: {combined}")

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "profile": lock["profile"],
        "java_home": str(java),
        "artifact": meta1,
        "deterministic_clean_builds": True,
        "controls_passed": 34,
        "artifact_origin_classes_verified": 2,
        "strict_jni_warnings": 0,
        "scope": {
            "hosted_linux_jdk17_client58_public_api": True,
            "private_sdk_equivalence": False,
            "vehicle_mesh_runtime_accepted": False,
            "contours_implemented": False,
            "full_client_accepted": False,
            "security_accepted": False,
        },
    }
    (out / "hosted-client58-public-api-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--kernel-classes", type=Path)
    parser.add_argument("--public-jars", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.phase == "verify":
        require(
            args.kernel_classes is None
            and args.public_jars is None
            and args.output is None
            and args.java_home is None,
            "qualify paths require explicit --phase qualify",
        )
        result = verify_only()
    else:
        require(
            args.kernel_classes is not None and args.output is not None,
            "--kernel-classes and --output are required for --phase qualify",
        )
        result = qualify(
            args.kernel_classes,
            args.output,
            public_jars=args.public_jars,
            java_home=args.java_home,
        )

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
