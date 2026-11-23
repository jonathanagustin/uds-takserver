#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX 8.30.5 elevation contours."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONTOUR_GRID_JAVA = (
    REPO / "takx/contour-grid/java/gov/tak/project/contour/ContourGrid.java"
)
CONTOUR_CLIP_JAVA = (
    REPO / "takx/contour-grid/java/gov/tak/project/contour/ContourClip.java"
)
ELEVATION_JNI_DIR = REPO / "takx/kernel-elevation-jni"

BUILD_SCRIPT = HERE / "build.py"
LOCK_FILE = HERE / "inputs.lock.json"

SCHEMA = "uds-takx-contour-elevation-hosted-v1"
EXPECTED_LOCK_SHA = "752e9b05ed874c0c53022ee5d1b624a7c9a30401da84ce0692f21ed244c159f5"
EXPECTED_PROFILE = "public-elevation-contours-v2"
EXPECTED_CHECKS = 615

STRICT_JNI_MARKERS = (
    "WARNING in native method",
    "WARNING: JNI",
    "FATAL ERROR",
    "JNI DETECTED ERROR",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
)


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
    # Windows core.autocrlf=true expands LF->CRLF on checkout; Linux CI verifies raw bytes.
    if os.name == "nt":
        raw = raw.replace(b"\r\n", b"\n")
    return _sha256_bytes(raw)


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "contour_elevation_build", BUILD_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module from {BUILD_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_offline() -> dict:
    """Validate locked contour-elevation sources, contour-grid dependency, and elevation profile."""
    for path in (BUILD_SCRIPT, LOCK_FILE, CONTOUR_GRID_JAVA, CONTOUR_CLIP_JAVA):
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(f"missing required contour-elevation input: {path}")

    lock_sha = _sha256_source_text(LOCK_FILE)
    if lock_sha != EXPECTED_LOCK_SHA:
        raise ValueError(
            f"inputs.lock.json digest mismatch: {lock_sha} != {EXPECTED_LOCK_SHA}"
        )

    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    if lock.get("profile") != EXPECTED_PROFILE:
        raise ValueError(f"unexpected profile: {lock.get('profile')}")
    if lock.get("checks") != EXPECTED_CHECKS:
        raise ValueError(f"unexpected checks expectation: {lock.get('checks')}")

    for flag in (
        "contour_api_implemented",
        "layer_runtime_accepted",
        "full_client_accepted",
    ):
        if lock.get(flag) is not False:
            raise ValueError(f"acceptance flag {flag} must remain False")

    owned_hashes: dict[str, str] = {}
    for rel_path, expected_sha in sorted(lock["owned"].items()):
        src = HERE / rel_path
        if src.is_symlink() or not src.is_file():
            raise FileNotFoundError(f"missing owned source: {src}")
        actual_sha = _sha256_source_text(src)
        if actual_sha != expected_sha:
            raise ValueError(
                f"owned source mismatch for {rel_path}: {actual_sha} != {expected_sha}"
            )
        owned_hashes[rel_path] = actual_sha

    experimental_hashes: dict[str, str] = {}
    for rel_path, expected_sha in sorted(lock["experimental_controls"].items()):
        src = HERE / rel_path
        if src.is_symlink() or not src.is_file():
            raise FileNotFoundError(f"missing experimental control: {src}")
        actual_sha = _sha256_source_text(src)
        if actual_sha != expected_sha:
            raise ValueError(
                f"experimental control mismatch for {rel_path}: {actual_sha} != {expected_sha}"
            )
        experimental_hashes[rel_path] = actual_sha

    grid_sha = _sha256_source_text(CONTOUR_GRID_JAVA)
    if grid_sha != lock["grid_sha256"]:
        raise ValueError(
            f"ContourGrid.java digest mismatch: {grid_sha} != {lock['grid_sha256']}"
        )

    clip_sha = _sha256_source_text(CONTOUR_CLIP_JAVA)
    if clip_sha != lock["clip_sha256"]:
        raise ValueError(
            f"ContourClip.java digest mismatch: {clip_sha} != {lock['clip_sha256']}"
        )

    elevation_profile_hashes: dict[str, str] = {}
    for rel_path, expected_sha in sorted(lock["elevation_profile"].items()):
        src = ELEVATION_JNI_DIR / rel_path
        if src.is_symlink() or not src.is_file():
            raise FileNotFoundError(f"missing locked elevation profile file: {src}")
        actual_sha = _sha256_source_text(src)
        if actual_sha != expected_sha:
            raise ValueError(
                f"elevation profile mismatch for {rel_path}: {actual_sha} != {expected_sha}"
            )
        elevation_profile_hashes[rel_path] = actual_sha

    if os.name != "nt":
        build_mod = _load_build_module()
        build_mod.contract()

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profile": EXPECTED_PROFILE,
        "inputs_lock_sha256": lock_sha,
        "build_py_sha256": _sha256_source_text(BUILD_SCRIPT),
        "grid_sha256": grid_sha,
        "clip_sha256": clip_sha,
        "owned": owned_hashes,
        "experimental_controls": experimental_hashes,
        "elevation_profile_files": len(elevation_profile_hashes),
        "expected_checks": EXPECTED_CHECKS,
    }


def audit_controls_output(stdout: str, stderr: str, returncode: int) -> dict[str, int]:
    """Verify exact pass markers and zero strict -Xcheck:jni diagnostics."""
    if returncode != 0:
        raise RuntimeError(
            f"ElevationContourControls exited with code {returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
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

    vis_match = re.search(r"CONTOUR_VISIBILITY_CONTROLS_PASS checks=(\d+)\b", stdout)
    if not vis_match:
        raise ValueError("missing CONTOUR_VISIBILITY_CONTROLS_PASS checks marker")
    contour_match = re.search(
        rf"ELEVATION_CONTOUR_CONTROLS_PASS checks={EXPECTED_CHECKS}\b", stdout
    )
    if not contour_match:
        raise ValueError(
            f"missing ELEVATION_CONTOUR_CONTROLS_PASS checks={EXPECTED_CHECKS} marker"
        )

    return {
        "visibility_checks": int(vis_match.group(1)),
        "total_checks": EXPECTED_CHECKS,
        "strict_jni_diagnostics": 0,
    }


def _clean_jvm_env(java_home: Path, gdal_data: Path) -> dict[str, str]:
    removed = {
        "CPATH",
        "C_INCLUDE_PATH",
        "CPLUS_INCLUDE_PATH",
        "LIBRARY_PATH",
        "COMPILER_PATH",
        "GCC_EXEC_PREFIX",
        "CFLAGS",
        "CXXFLAGS",
        "CPPFLAGS",
        "LDFLAGS",
        "CC",
        "CXX",
        "MAKEFLAGS",
        "GNUMAKEFLAGS",
        "MFLAGS",
        "MAKEFILES",
        "BASH_ENV",
        "ENV",
        "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "JDK_JAVAC_OPTIONS",
        "_JAVA_OPTIONS",
        "CLASSPATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONUSERBASE",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in removed
        and not k.startswith(
            ("LD_", "CMAKE_", "PKG_CONFIG", "BASH_FUNC_", "GDAL_", "PROJ_")
        )
    }
    env.update(
        JAVA_HOME=str(java_home),
        PATH=f"{java_home / 'bin'}:{env.get('PATH', '')}",
        LC_ALL="C.UTF-8",
        GDAL_DATA=str(gdal_data),
    )
    return env


def _resolve_regular(candidates: tuple[Path, ...], label: str) -> Path:
    for cand in candidates:
        if cand.exists():
            resolved = cand.resolve()
            if resolved.is_symlink() or not resolved.is_file():
                raise ValueError(
                    f"resolved path for {label} is not a regular file: {resolved}"
                )
            return resolved
    raise FileNotFoundError(f"missing {label}: tried {[str(c) for c in candidates]}")


def qualify(
    caches_root: Path,
    gdal_jni_dir: Path,
    kernel_java_dir: Path,
    kernel_native_dir: Path,
    kernel_elevation_jni_dir: Path,
    java_home: Path,
    output_dir: Path,
) -> dict:
    """Compile and execute all 615 ElevationContourControls assertions against patched libtakenginejni.so."""
    verified = verify_offline()

    caches_root = caches_root.resolve()
    gdal_jni_dir = gdal_jni_dir.resolve()
    kernel_java_dir = kernel_java_dir.resolve()
    kernel_native_dir = kernel_native_dir.resolve()
    kernel_elevation_jni_dir = kernel_elevation_jni_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")

    classes_root = kernel_java_dir / "classes"
    classpath_dir = kernel_java_dir / "classpath"
    for req_dir in (classes_root, classpath_dir):
        if not req_dir.is_dir():
            raise FileNotFoundError(f"missing kernel-java directory: {req_dir}")
    jars = sorted(classpath_dir.glob("*.jar"))
    if not jars:
        raise FileNotFoundError(f"no dependency JARs found under {classpath_dir}")

    java_bin = java_home / "bin/java"
    javac_bin = java_home / "bin/javac"
    for tool in (java_bin, javac_bin):
        if not tool.is_file():
            raise FileNotFoundError(f"missing JDK tool: {tool}")

    gdal_data = caches_root / "gdal/share/gdal"
    if not gdal_data.is_dir():
        raise FileNotFoundError(f"missing GDAL_DATA directory: {gdal_data}")

    gldispatch_so = _resolve_regular(
        (
            caches_root / "gles/lib/libGLdispatch.so.0.0.0",
            caches_root / "gles/lib/libGLdispatch.so.0",
        ),
        "libGLdispatch.so",
    )
    glesv2_so = _resolve_regular(
        (
            caches_root / "gles/lib/libGLESv2.so.2.1.0",
            caches_root / "gles/lib/libGLESv2.so.2",
        ),
        "libGLESv2.so",
    )
    las_so = _resolve_regular(
        (
            caches_root / "liblas/lib/liblas.so.2.4.0",
            caches_root / "liblas/lib/liblas.so.3",
            caches_root / "liblas/lib/liblas.so",
        ),
        "liblas.so",
    )
    las_c_so = _resolve_regular(
        (
            caches_root / "liblas/lib/liblas_c.so.2.4.0",
            caches_root / "liblas/lib/liblas_c.so.3",
            caches_root / "liblas/lib/liblas_c.so",
        ),
        "liblas_c.so",
    )
    gdal_so = _resolve_regular((gdal_jni_dir / "libgdal.so",), "libgdal.so")
    gdal_jni_so = _resolve_regular(
        (gdal_jni_dir / "libgdalalljni.so",), "libgdalalljni.so"
    )
    core_so = _resolve_regular(
        (
            kernel_native_dir / "build/kernel-cpp/libtakengine.so",
            kernel_native_dir / "libtakengine.so",
        ),
        "libtakengine.so",
    )
    jni_so = _resolve_regular(
        (
            kernel_elevation_jni_dir / "libtakenginejni.so",
            kernel_elevation_jni_dir / "build/kernel-jni/libtakenginejni.so",
        ),
        "patched libtakenginejni.so",
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    loads = [
        gldispatch_so,
        glesv2_so,
        las_so,
        las_c_so,
        gdal_so,
        gdal_jni_so,
        core_so,
        jni_so,
    ]
    bound_hashes = {path.as_posix(): _sha256_file(path) for path in loads}
    manifest = output_dir / "runtime-native.sha256"
    manifest.write_text(
        "".join(f"{bound_hashes[p.as_posix()]} {p.as_posix()}\n" for p in loads),
        encoding="utf-8",
    )

    jvm_env = _clean_jvm_env(java_home, gdal_data)
    base_cp = os.pathsep.join(str(p) for p in [classes_root, *jars])
    compiled_classes = output_dir / "classes"
    compiled_classes.mkdir(parents=True, exist_ok=True)

    java_sources = [
        CONTOUR_GRID_JAVA,
        CONTOUR_CLIP_JAVA,
        HERE / "java/gov/tak/project/contour/ElevationContours.java",
        HERE / "java/gov/tak/project/contour/ContourFeatures.java",
        HERE / "java/gov/tak/project/contour/ContourLayer.java",
        HERE / "tests/ElevationContourControls.java",
        HERE / "tests/ContourVisibilityControls.java",
    ]
    subprocess.run(
        [
            str(javac_bin),
            "--release",
            "17",
            "-proc:none",
            "-cp",
            base_cp,
            "-d",
            str(compiled_classes),
            *[str(src) for src in java_sources],
        ],
        env=jvm_env,
        check=True,
        timeout=120,
    )

    sqlite_out = output_dir / "owned-contours.sqlite"
    full_cp = os.pathsep.join(str(p) for p in [compiled_classes, classes_root, *jars])
    proc = subprocess.run(
        [
            str(java_bin),
            "-Xcheck:jni",
            "-Xmx256m",
            "-Djava.awt.headless=true",
            "-cp",
            full_cp,
            "ElevationContourControls",
            str(manifest),
            str(sqlite_out),
        ],
        env=jvm_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    controls_audit = audit_controls_output(proc.stdout, proc.stderr, proc.returncode)

    if not sqlite_out.is_file() or sqlite_out.stat().st_size == 0:
        raise FileNotFoundError(
            f"missing or empty persistent contour database: {sqlite_out}"
        )

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "bound_native_providers": bound_hashes,
        "compiled_class_count": len(list(compiled_classes.rglob("*.class"))),
        "controls": controls_audit,
        "artifacts": {
            sqlite_out.name: {
                "sha256": _sha256_file(sqlite_out),
                "bytes": sqlite_out.stat().st_size,
            }
        },
        "scope": {
            "contour_elevation_controls_passed": True,
            "xcheck_jni_clean": True,
            "contour_api_implemented": False,
            "layer_runtime_accepted": False,
            "full_client_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-contour-elevation-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--caches-root", type=Path)
    parser.add_argument("--gdal-jni-dir", type=Path)
    parser.add_argument("--kernel-java-dir", type=Path)
    parser.add_argument("--kernel-native-dir", type=Path)
    parser.add_argument("--kernel-elevation-jni-dir", type=Path)
    parser.add_argument(
        "--java-home", type=Path, default=Path("/usr/lib/jvm/java-17-openjdk-amd64")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    required = (
        ("--caches-root", args.caches_root),
        ("--gdal-jni-dir", args.gdal_jni_dir),
        ("--kernel-java-dir", args.kernel_java_dir),
        ("--kernel-native-dir", args.kernel_native_dir),
        ("--kernel-elevation-jni-dir", args.kernel_elevation_jni_dir),
        ("--output", args.output),
    )
    missing = [flag for flag, val in required if val is None]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")

    qualify(
        caches_root=args.caches_root,
        gdal_jni_dir=args.gdal_jni_dir,
        kernel_java_dir=args.kernel_java_dir,
        kernel_native_dir=args.kernel_native_dir,
        kernel_elevation_jni_dir=args.kernel_elevation_jni_dir,
        java_home=args.java_home,
        output_dir=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
