#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 runner for SWIG 4.3.0 GDAL and Assimp GIS Java bindings."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "uds-takx-gis-java-hosted-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
EXPECTED_SWIG_VERSION = "4.3.0"
EXPECTED_JAVA_MAJOR = 17
EXPECTED_JAVA_RELEASE = 11
EXPECTED_PROFILE_ID = "gdal244-java-pending-exceptions-v2"
EXPECTED_GDAL_JAVA_FILES = 58
EXPECTED_ASSIMP_JAVA_FILES = 42
EXPECTED_GDAL_CLASSES = 60
EXPECTED_ASSIMP_CLASSES = 47
EXPECTED_WRAPPERS = (
    "gdal_wrap.cpp",
    "gdalconst_wrap.c",
    "ogr_wrap.cpp",
    "osr_wrap.cpp",
    "gnm_wrap.cpp",
)
NATIVE_LIB_SUFFIXES = (".so", ".dll", ".dylib")

LOCK_PATH = HERE / "sources.lock.json"
BUILD_SCRIPT = HERE / "build.py"
COMPILE_PROBE_JAVA = HERE / "CompileApiProbe.java"
EXCEPTION_CONTROLS_JAVA = HERE / "GdalExceptionControls.java"
JNI_PROFILE_PATH = HERE / "jni-exceptions-profile-v2.json"
JNI_PATCH_PATH = HERE / "patches/gdal-java-pending-exceptions-v2.patch"

REQUIRED_FILES = (
    LOCK_PATH,
    BUILD_SCRIPT,
    COMPILE_PROBE_JAVA,
    EXCEPTION_CONTROLS_JAVA,
    JNI_PROFILE_PATH,
    JNI_PATCH_PATH,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    for path in REQUIRED_FILES:
        if not path.is_file():
            raise FileNotFoundError(f"missing required gis-java control input: {path}")

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("schema") != 1:
        raise ValueError(f"unexpected gis-java lock schema: {lock.get('schema')}")
    if lock.get("revision") != EXPECTED_REVISION:
        raise ValueError(f"unexpected gis-java revision: {lock.get('revision')}")
    if lock.get("swig_version") != EXPECTED_SWIG_VERSION:
        raise ValueError(f"unexpected SWIG version: {lock.get('swig_version')}")
    if lock.get("java_major") != EXPECTED_JAVA_MAJOR or lock.get("java_release") != EXPECTED_JAVA_RELEASE:
        raise ValueError(
            f"unexpected Java versions: major={lock.get('java_major')} release={lock.get('java_release')}"
        )

    archives = lock.get("archives", {})
    if set(archives) != {"gdal", "assimp"}:
        raise ValueError(f"unexpected archive keys: {sorted(archives)}")
    if archives["gdal"].get("java_files") != EXPECTED_GDAL_JAVA_FILES:
        raise ValueError(f"unexpected gdal java_files count: {archives['gdal'].get('java_files')}")
    if archives["assimp"].get("java_files") != EXPECTED_ASSIMP_JAVA_FILES:
        raise ValueError(f"unexpected assimp java_files count: {archives['assimp'].get('java_files')}")

    profile = json.loads(JNI_PROFILE_PATH.read_text(encoding="utf-8"))
    if profile.get("profileId") != EXPECTED_PROFILE_ID:
        raise ValueError(f"unexpected JNI exception profile: {profile.get('profileId')}")
    if sha256_file(JNI_PATCH_PATH) != profile.get("patchSha256"):
        raise ValueError("JNI exception patch SHA-256 does not match profile lock")

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": EXPECTED_REVISION,
        "swig_version": EXPECTED_SWIG_VERSION,
        "profile_id": EXPECTED_PROFILE_ID,
        "lock_sha256": sha256_file(LOCK_PATH),
    }


def audit_jar(jar_path: Path, expected_classes: int, require_notice: bool) -> dict:
    if not jar_path.is_file() or jar_path.is_symlink():
        raise FileNotFoundError(f"missing JAR artifact: {jar_path}")
    with zipfile.ZipFile(jar_path) as archive:
        names = archive.namelist()
    native_entries = [name for name in names if name.lower().endswith(NATIVE_LIB_SUFFIXES)]
    if native_entries:
        raise ValueError(f"unexpected native binary entries in {jar_path.name}: {native_entries}")
    if "META-INF/LICENSE" not in names:
        raise ValueError(f"missing META-INF/LICENSE in {jar_path.name}")
    if require_notice and "META-INF/NOTICE" not in names:
        raise ValueError(f"missing META-INF/NOTICE in {jar_path.name}")
    class_count = sum(1 for name in names if name.endswith(".class"))
    if class_count != expected_classes:
        raise ValueError(
            f"unexpected class count in {jar_path.name}: {class_count} != {expected_classes}"
        )
    return {
        "path": jar_path.name,
        "sha256": sha256_file(jar_path),
        "class_count": class_count,
    }


def audit_generated_wrappers(generated_dir: Path) -> dict[str, str]:
    wrappers: dict[str, str] = {}
    for name in EXPECTED_WRAPPERS:
        wrapper = generated_dir / name
        if wrapper.is_symlink() or not wrapper.is_file():
            raise FileNotFoundError(f"missing generated SWIG wrapper: {wrapper}")
        if wrapper.stat().st_size == 0:
            raise ValueError(f"empty generated SWIG wrapper: {wrapper}")
        wrappers[name] = sha256_file(wrapper)
    return wrappers


def _child_env(
    java_home: Path,
    swig_bin: Path,
    swig_lib: Path,
    cache_dir: Path,
    build_dir: Path,
    *,
    offline: bool,
) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        PATH=f"{java_home / 'bin'}{os.pathsep}{env.get('PATH', '')}",
        GIS_JAVA_SWIG=str(swig_bin),
        SWIG_LIB=str(swig_lib),
        GIS_JAVA_CACHE_DIR=str(cache_dir),
        GIS_JAVA_BUILD_DIR=str(build_dir),
        GIS_JAVA_OFFLINE="true" if offline else "false",
    )
    return env


def qualify(
    java_home: Path,
    swig_bin: Path,
    swig_lib: Path,
    output_dir: Path,
) -> dict:
    verified = verify_offline()
    java_home = java_home.resolve()
    swig_bin = swig_bin.resolve()
    swig_lib = swig_lib.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "cache"
    build_dir = output_dir / "build"

    build_env = _child_env(
        java_home, swig_bin, swig_lib, cache_dir, build_dir, offline=False
    )
    subprocess.run(
        [sys.executable, "-B", str(BUILD_SCRIPT)],
        env=build_env,
        check=True,
        timeout=300,
    )

    gdal_jar = audit_jar(build_dir / "gdal-2.4.4-mod-java.jar", EXPECTED_GDAL_CLASSES, require_notice=False)
    assimp_jar = audit_jar(
        build_dir / "assimp-4.0.1-mod-jassimp-java.jar",
        EXPECTED_ASSIMP_CLASSES,
        require_notice=True,
    )
    wrappers = audit_generated_wrappers(build_dir / "generated")

    replay_env = _child_env(
        java_home, swig_bin, swig_lib, cache_dir, build_dir, offline=True
    )
    replay = subprocess.run(
        [sys.executable, "-B", str(BUILD_SCRIPT)],
        env=replay_env,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    if "Verified completed Java-only cache and compile API probe:" not in replay.stdout:
        raise RuntimeError(f"offline replay verification marker missing: {replay.stdout[-1000:]}")

    state = json.loads((build_dir / "build-state.json").read_text(encoding="utf-8"))
    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "input_key": state["input_key"],
        "java_source_counts": state["java_source_counts"],
        "jars": {
            "gdal": gdal_jar,
            "assimp": assimp_jar,
        },
        "generated_wrappers": wrappers,
        "scope": {
            "gis_java_build_passed": True,
            "offline_replay_passed": True,
            "native_built_or_loaded": False,
            "full_sdk_acceptance": False,
            "client_acceptance": False,
        },
    }
    receipt_path = output_dir / "hosted-gis-java-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--swig-bin", type=Path)
    parser.add_argument("--swig-lib", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    missing = [
        flag
        for flag, val in (
            ("--java-home", args.java_home),
            ("--swig-bin", args.swig_bin),
            ("--swig-lib", args.swig_lib),
            ("--output", args.output),
        )
        if val is None
    ]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")
    qualify(args.java_home, args.swig_bin, args.swig_lib, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
