#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for takx/kernel-render-context JNI controls.

Rebuilds patched Kernel 8.30.5 libtakenginejni.so with ManagedRenderContext/Surface
exception-cleanup fixes and client-interop feature/SceneLayer bridges, then runs all
four headless JNI suites under JDK 17 -Xcheck:jni with zero modifications to any frozen
file.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAKX_ROOT = HERE.parent
NATIVE_DIR = TAKX_ROOT / "kernel-native"
CLIENT_INTEROP_DIR = TAKX_ROOT / "kernel-client-interop"
ELEVATION_JNI_DIR = TAKX_ROOT / "kernel-elevation-jni"

SCHEMA = "uds-takx-render-context-jni-hosted-v1"
EXPECTED_PROFILE = "public-kernel-render-context-experimental-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
EXPECTED_KERNEL_VERSION = "8.30.5"

RENDER_CONTEXT_LOCK = HERE / "inputs.lock.json"
RENDER_CONTEXT_SCOPE = HERE / "source-scope.json"
RENDER_CONTEXT_PATCH = HERE / "render-context-control.patch"
CLIENT_INTEROP_LOCK = CLIENT_INTEROP_DIR / "inputs.lock.json"
CLIENT_CONTRACTS_PATCH = CLIENT_INTEROP_DIR / "client-contracts.patch"
HOSTED_CMAKE = ELEVATION_JNI_DIR / "hosted_CMakeLists.txt"

RENDER_CONTEXT_JNI_FILES = (
    "takkernel/engine/src/main/jni/interop/core/ManagedRenderContext.cpp",
    "takkernel/engine/src/main/jni/interop/core/ManagedRenderSurface.cpp",
)
CLIENT_INTEROP_PATCH_FILES = (
    "takkernel/engine/src/main/java/com/atakmap/map/layer/feature/FeatureLayer3.java",
    "takkernel/engine/src/main/java/com/atakmap/map/layer/model/SceneLayer.java",
    "takkernel/engine/src/main/jni/interop/feature/Interop.cpp",
    "takkernel/engine/src/main/jni/jscenelayer.cpp",
)
OWNED_TEST_FILES = (
    "tests/ManagedBridgeProbe.java",
    "tests/ManagedBridgeProbe.cpp",
    "tests/SurfaceModesProbe.java",
    "tests/SurfaceModesProbe.cpp",
    "tests/FeatureLookupProbe.java",
    "tests/FeatureLookupProbe.cpp",
    "tests/SceneJNIControls.java",
)
PROBE_CPP_NAMES = (
    "ManagedBridgeProbe",
    "SurfaceModesProbe",
    "FeatureLookupProbe",
)

# Real upstream/patched JNI classes, fields, and methods checked by FeatureLookupProbe.
FEATURE_LOOKUP_ROWS: tuple[tuple[str, str, str, int], ...] = (
    ("com/atakmap/map/layer/feature/FeatureLayer3$Options", "labelsEnabled", "Z", 0),
    ("com/atakmap/map/layer/feature/FeatureLayer3$Options", "labelHints", "I", 0),
    (
        "com/atakmap/map/layer/feature/FeatureLayer3$Options",
        "filter",
        "Lcom/atakmap/map/layer/feature/FeatureDataStore2$FeatureQueryParameters;",
        0,
    ),
    ("com/atakmap/map/layer/feature/geometry/Envelope", "minX", "D", 0),
    ("com/atakmap/map/layer/feature/geometry/Envelope", "minY", "D", 0),
    ("com/atakmap/map/layer/feature/geometry/Envelope", "minZ", "D", 0),
    ("com/atakmap/map/layer/feature/geometry/Envelope", "maxX", "D", 0),
    ("com/atakmap/map/layer/feature/geometry/Envelope", "maxY", "D", 0),
    ("com/atakmap/map/layer/feature/geometry/Envelope", "maxZ", "D", 0),
    ("com/atakmap/map/layer/feature/geometry/Envelope", "<init>", "(DDDDDD)V", 1),
    ("com/atakmap/map/opengl/GLLabelManager", "HINT_XRAY", "I", 2),
    ("com/atakmap/map/opengl/GLLabelManager", "HINT_WEIGHTED_FLOAT", "I", 2),
    (
        "com/atakmap/map/opengl/GLLabelManager",
        "HINT_AUTO_SURFACE_OFFSET_ADJUST",
        "I",
        2,
    ),
    ("gov/tak/api/engine/map/RenderSurface", "getDpi", "()D", 1),
    ("gov/tak/api/engine/map/RenderSurface", "getWidth", "()I", 1),
    ("gov/tak/api/engine/map/RenderSurface", "getHeight", "()I", 1),
    ("gov/tak/api/engine/map/RenderSurface2", "getSurfaceWidth", "()I", 1),
    ("gov/tak/api/engine/map/RenderSurface2", "getSurfaceHeight", "()I", 1),
    (
        "gov/tak/api/engine/map/RenderContext",
        "getRenderSurface",
        "()Lgov/tak/api/engine/map/RenderSurface;",
        1,
    ),
    ("gov/tak/api/engine/map/RenderContext", "isRenderThread", "()Z", 1),
    ("gov/tak/api/engine/map/RenderContext", "requestRefresh", "()V", 1),
    ("gov/tak/api/engine/map/RenderContext", "setFrameRate", "(F)V", 1),
    ("gov/tak/api/engine/map/RenderContext", "getFrameRate", "()F", 1),
    ("gov/tak/api/engine/map/RenderContext", "setContinuousRenderEnabled", "(Z)V", 1),
    ("gov/tak/api/engine/map/RenderContext", "isContinuousRenderEnabled", "()Z", 1),
    ("gov/tak/api/engine/map/RenderContext", "supportsChildContext", "()Z", 1),
    (
        "gov/tak/api/engine/map/RenderContext",
        "createChildContext",
        "()Lgov/tak/api/engine/map/RenderContext;",
        1,
    ),
    (
        "gov/tak/api/engine/map/RenderContext",
        "destroyChildContext",
        "(Lgov/tak/api/engine/map/RenderContext;)V",
        1,
    ),
    ("gov/tak/api/engine/map/RenderContext", "isAttached", "()Z", 1),
    ("gov/tak/api/engine/map/RenderContext", "attach", "()Z", 1),
    ("gov/tak/api/engine/map/RenderContext", "detach", "()Z", 1),
    ("gov/tak/api/engine/map/RenderContext", "isMainContext", "()Z", 1),
    ("com/atakmap/map/layer/model/SceneLayer", "isVisible", "(J)Z", 1),
    ("com/atakmap/map/layer/model/SceneLayer", "setVisible", "(JZ)V", 1),
    (
        "com/atakmap/map/layer/model/SceneLayer",
        "query",
        "(Lcom/atakmap/map/layer/feature/FeatureDataStore2$FeatureQueryParameters;)Lcom/atakmap/map/layer/feature/FeatureCursor;",
        1,
    ),
    ("com/atakmap/map/layer/model/SceneLayer", "isItemVisible", "(JJ)Z", 3),
    ("com/atakmap/map/layer/model/SceneLayer", "setItemVisible", "(JJZ)V", 3),
    (
        "com/atakmap/map/layer/model/SceneLayer",
        "queryFeatures",
        "(JLcom/atakmap/map/layer/feature/FeatureDataStore2$FeatureQueryParameters;)Lcom/atakmap/interop/Pointer;",
        3,
    ),
)

JNI_REQUIRED_NEEDED = {"libtakengine.so", "libGLESv2.so.2", "libgdal.so"}
REQUIRED_SQLITE_BRIDGE = {"sqlite3_config", "sqlite3_errstr"}
STRICT_JNI_MARKERS = (
    "WARNING in native method",
    "WARNING: JNI",
    "FATAL ERROR",
    "JNI DETECTED ERROR",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
)
SYSTEM_NEEDED = re.compile(
    r"^(?:libc|libm|libdl|libpthread|librt|libstdc\+\+|libgcc_s|ld-linux-x86-64)\.so\.[0-9]+$"
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
    if os.name == "nt":
        raw = raw.replace(b"\r\n", b"\n")
    return _sha256_bytes(raw)


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_patch_section(patch_bytes: bytes, target_rel: str) -> bytes:
    """Extract a single file diff section from a git-extended or traditional unified patch."""
    header_re = re.compile(
        rb"(?m)^(?:diff --git [^\r\n]+\r?\n)?--- (?:a/([^\r\n]+)|(/dev/null))\r?\n"
        rb"\+\+\+ (?:b/([^\r\n]+)|(/dev/null))\r?\n"
    )
    matches = list(header_re.finditer(patch_bytes))
    selected: list[bytes] = []
    for index, match in enumerate(matches):
        before = match.group(1).decode("utf-8") if match.group(1) else None
        after = match.group(3).decode("utf-8") if match.group(3) else None
        if before == target_rel and after == target_rel:
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(patch_bytes)
            )
            selected.append(patch_bytes[match.start() : end])
    if len(selected) != 1:
        raise ValueError(
            f"patch section missing or duplicated for {target_rel}: count={len(selected)}"
        )
    return selected[0]


def verify_offline() -> dict:
    """Verify frozen lockfiles, patches, hosted CMakeLists, and owned probe files offline."""
    for req in (
        RENDER_CONTEXT_LOCK,
        RENDER_CONTEXT_SCOPE,
        RENDER_CONTEXT_PATCH,
        CLIENT_INTEROP_LOCK,
        CLIENT_CONTRACTS_PATCH,
        HOSTED_CMAKE,
    ):
        if req.is_symlink() or not req.is_file() or req.stat().st_size == 0:
            raise FileNotFoundError(
                f"missing or empty required render-context JNI input: {req}"
            )

    lock = json.loads(RENDER_CONTEXT_LOCK.read_text(encoding="utf-8"))
    scope = json.loads(RENDER_CONTEXT_SCOPE.read_text(encoding="utf-8"))
    client_lock = json.loads(CLIENT_INTEROP_LOCK.read_text(encoding="utf-8"))

    if lock.get("profile") != EXPECTED_PROFILE:
        raise ValueError(f"unexpected profile: {lock.get('profile')}")
    if lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(
            f"unexpected official_revision: {lock.get('official_revision')}"
        )
    if lock.get("actual_kernel_version") != EXPECTED_KERNEL_VERSION:
        raise ValueError(
            f"unexpected actual_kernel_version: {lock.get('actual_kernel_version')}"
        )
    if scope.get("upstream_commit") != EXPECTED_REVISION:
        raise ValueError(
            f"unexpected source-scope upstream_commit: {scope.get('upstream_commit')}"
        )
    if client_lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(
            f"unexpected client-interop official_revision: {client_lock.get('official_revision')}"
        )

    for flag in ("gpu_qualified", "native_builder_integration", "full_sdk_accepted"):
        if lock.get(flag) is not False:
            raise ValueError(f"acceptance flag {flag} must remain False")

    rc_patch_sha = _sha256_file(RENDER_CONTEXT_PATCH)
    if rc_patch_sha != lock.get("context_patch_sha256"):
        raise ValueError(
            f"render-context-control.patch SHA mismatch: {rc_patch_sha} != {lock.get('context_patch_sha256')}"
        )
    if rc_patch_sha != scope.get("patch_sha256"):
        raise ValueError(
            f"render-context-control.patch scope SHA mismatch: {rc_patch_sha} != {scope.get('patch_sha256')}"
        )

    client_patch_sha = _sha256_source_text(CLIENT_CONTRACTS_PATCH)
    if client_patch_sha != client_lock.get("patch_sha256"):
        raise ValueError(
            f"client-contracts.patch SHA mismatch: {client_patch_sha} != {client_lock.get('patch_sha256')}"
        )

    test_hashes: dict[str, str] = {}
    for rel in OWNED_TEST_FILES:
        path = HERE / rel
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing owned render-context test file: {path}")
        test_hashes[rel] = _sha256_source_text(path)

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profile": EXPECTED_PROFILE,
        "official_revision": EXPECTED_REVISION,
        "actual_kernel_version": EXPECTED_KERNEL_VERSION,
        "render_context_inputs_lock_sha256": _sha256_source_text(RENDER_CONTEXT_LOCK),
        "render_context_source_scope_sha256": _sha256_source_text(RENDER_CONTEXT_SCOPE),
        "render_context_patch_sha256": rc_patch_sha,
        "client_interop_inputs_lock_sha256": _sha256_source_text(CLIENT_INTEROP_LOCK),
        "client_contracts_patch_sha256": client_patch_sha,
        "hosted_cmake_sha256": _sha256_source_text(HOSTED_CMAKE),
        "owned_test_files": test_hashes,
        "feature_lookup_rows": len(FEATURE_LOOKUP_ROWS),
    }


def _defined_nm_names(nm_out: str) -> set[str]:
    names: set[str] = set()
    for line in nm_out.splitlines():
        parts = line.split()
        if (
            len(parts) >= 3
            and len(parts[1]) == 1
            and parts[1].isalpha()
            and parts[1] != "U"
        ):
            names.add(parts[2].split("@", 1)[0])
    return names


def _dynsym_entries(readelf_out: str) -> tuple[set[str], set[str]]:
    exports: set[str] = set()
    imports: set[str] = set()
    for line in readelf_out.splitlines():
        parts = line.split()
        if len(parts) < 8 or not parts[0].endswith(":") or not parts[0][:-1].isdigit():
            continue
        bind = parts[4]
        ndx = parts[6]
        sym = parts[7].split("@", 1)[0]
        if bind not in ("GLOBAL", "WEAK"):
            continue
        if ndx == "UND":
            imports.add(sym)
        elif ndx.isdigit() or ndx == "ABS":
            exports.add(sym)
    return exports, imports


def audit_patched_jni_elf(
    jni_dynsym: str,
    jni_dyn: str,
    jni_nm: str,
    sqlite_symbols: set[str],
) -> dict:
    """Verify patched libtakenginejni.so preserves NEEDED and SQLCipher single-owner invariants."""
    exports, imports = _dynsym_entries(jni_dynsym)
    defined = _defined_nm_names(jni_nm)

    sql_exports = exports & sqlite_symbols
    sql_imports = imports & sqlite_symbols
    second_sqlite = sorted(
        sym for sym in defined if sym.startswith(("sqlite3_", "sqlcipher"))
    )
    if sql_exports or sql_imports != REQUIRED_SQLITE_BRIDGE or second_sqlite:
        raise ValueError(
            "JNI SQLCipher boundary mismatch: "
            f"exports={sorted(sql_exports)} imports={sorted(sql_imports)} defined={second_sqlite[:5]}"
        )

    if "TEXTREL" in jni_dyn:
        raise ValueError("unexpected TEXTREL in patched libtakenginejni.so")

    needed = set(re.findall(r"\(NEEDED\).*?\[(.*?)\]", jni_dyn))
    if not JNI_REQUIRED_NEEDED <= needed:
        raise ValueError(
            f"patched JNI missing required NEEDED entries: {sorted(JNI_REQUIRED_NEEDED - needed)}"
        )
    bad_needed = [
        entry
        for entry in needed
        if entry not in JNI_REQUIRED_NEEDED and not SYSTEM_NEEDED.fullmatch(entry)
    ]
    if bad_needed:
        raise ValueError(
            f"patched JNI has unreviewed NEEDED entries: {sorted(bad_needed)}"
        )

    return {
        "exported_dynamic_symbols": len(exports),
        "sqlcipher_exports": sorted(sql_exports),
        "sqlcipher_imports": sorted(sql_imports),
        "second_jni_sqlite_body": False,
        "needed": sorted(needed),
    }


def audit_control_output(
    label: str,
    stdout: str,
    stderr: str,
    returncode: int,
    required_substrings: tuple[str, ...],
) -> dict[str, int | str]:
    """Verify zero exit, zero strict -Xcheck:jni diagnostics, and expected markers."""
    if returncode != 0:
        raise RuntimeError(
            f"{label} exited with code {returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    combined = f"{stdout}\n{stderr}"
    diags = [
        line
        for line in combined.splitlines()
        if any(m in line for m in STRICT_JNI_MARKERS)
    ]
    if diags:
        raise ValueError(
            f"strict -Xcheck:jni or linkage diagnostic in {label}: {diags[:3]}"
        )
    for token in required_substrings:
        if token not in stdout:
            raise ValueError(f"missing expected marker {token!r} in {label}")
    return {"label": label, "strict_jni_diagnostics": 0}


def _clean_env(java_home: Path, gdal_data: Path | None = None) -> dict[str, str]:
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
        CC="gcc",
        CXX="g++",
        LC_ALL="C.UTF-8",
        PKG_CONFIG_PATH="",
        PYTHONNOUSERSITE="1",
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="safe.directory",
        GIT_CONFIG_VALUE_0="*",
        GIT_NO_LAZY_FETCH="1",
    )
    if gdal_data is not None:
        env["GDAL_DATA"] = str(gdal_data)
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


def _apply_section(target_dir: Path, patch_section: bytes, env: dict[str, str]) -> None:
    subprocess.run(
        [
            "patch",
            "--batch",
            "--fuzz=0",
            "--no-backup-if-mismatch",
            "-p1",
            "-d",
            str(target_dir),
        ],
        input=patch_section,
        env=env,
        check=True,
        timeout=30,
    )


def _run_java_control(
    label: str,
    java_bin: Path,
    classpath: str,
    main_class: str,
    args: list[str],
    required_substrings: tuple[str, ...],
    env: dict[str, str],
) -> dict:
    proc = subprocess.run(
        [
            str(java_bin),
            "-Xcheck:jni",
            "-Xmx256m",
            "-Djava.awt.headless=true",
            "-cp",
            classpath,
            main_class,
            *args,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return audit_control_output(
        label,
        proc.stdout,
        proc.stderr,
        proc.returncode,
        required_substrings,
    )


def qualify(
    atak_source: Path,
    gltf_cache: Path,
    minizip_include: Path,
    caches_root: Path,
    gdal_jni_dir: Path,
    kernel_java_dir: Path,
    kernel_native_dir: Path,
    java_home: Path,
    output_dir: Path,
    jobs: int,
) -> dict:
    """Patch, compile, and run all 4 render-context/client-interop headless JNI control suites."""
    verified = verify_offline()
    scope_doc = json.loads(RENDER_CONTEXT_SCOPE.read_text(encoding="utf-8"))
    client_lock = json.loads(CLIENT_INTEROP_LOCK.read_text(encoding="utf-8"))

    atak_source = atak_source.resolve()
    gltf_cache = gltf_cache.resolve()
    minizip_include = minizip_include.resolve()
    caches_root = caches_root.resolve()
    gdal_jni_dir = gdal_jni_dir.resolve()
    kernel_java_dir = kernel_java_dir.resolve()
    kernel_native_dir = kernel_native_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")

    core_so = _resolve_regular(
        (
            kernel_native_dir / "build/kernel-cpp/libtakengine.so",
            kernel_native_dir / "libtakengine.so",
        ),
        "libtakengine.so",
    )
    jni_headers = kernel_java_dir / "jni-headers"
    classes_root = kernel_java_dir / "classes"
    classpath_dir = kernel_java_dir / "classpath"
    for req_dir in (jni_headers, classes_root, classpath_dir):
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

    output_dir.mkdir(parents=True, exist_ok=True)
    build_env = _clean_env(java_home)

    # 1. Stage patched FeatureLayer3.java and SceneLayer.java first and emit updated JNI headers.
    java_overlay = output_dir / "java-overlay"
    client_patch_bytes = CLIENT_CONTRACTS_PATCH.read_bytes()
    patched_java_hashes: dict[str, str] = {}
    staged_java_files: list[Path] = []
    for rel in (
        "takkernel/engine/src/main/java/com/atakmap/map/layer/feature/FeatureLayer3.java",
        "takkernel/engine/src/main/java/com/atakmap/map/layer/model/SceneLayer.java",
    ):
        expected = client_lock["files"][rel]
        src = atak_source / rel
        if src.is_symlink() or not src.is_file():
            raise FileNotFoundError(f"missing upstream Java file: {src}")
        if _sha256_file(src) != expected["before"]:
            raise ValueError(f"preimage SHA mismatch for {rel}")
        dst = java_overlay / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        _apply_section(
            java_overlay, _extract_patch_section(client_patch_bytes, rel), build_env
        )
        actual = _sha256_file(dst)
        if actual != expected["after"]:
            raise ValueError(
                f"postimage SHA mismatch for {rel}: {actual} != {expected['after']}"
            )
        patched_java_hashes[rel] = actual
        staged_java_files.append(dst)

    control_classes = output_dir / "control-classes"
    overlay_jni_headers = output_dir / "overlay-jni-headers"
    control_classes.mkdir(parents=True, exist_ok=True)
    overlay_jni_headers.mkdir(parents=True, exist_ok=True)
    base_cp = os.pathsep.join(str(p) for p in [classes_root, *jars])
    subprocess.run(
        [
            str(javac_bin),
            "--release",
            "17",
            "-proc:none",
            "-cp",
            base_cp,
            "-d",
            str(control_classes),
            "-h",
            str(overlay_jni_headers),
            *(str(p) for p in staged_java_files),
        ],
        env=build_env,
        check=True,
        timeout=120,
    )
    scene_header = overlay_jni_headers / "com_atakmap_map_layer_model_SceneLayer.h"
    if not scene_header.is_file():
        raise FileNotFoundError(
            f"missing generated SceneLayer JNI header: {scene_header}"
        )

    merged_jni_headers = output_dir / "merged-jni-headers"
    shutil.copytree(jni_headers, merged_jni_headers)
    for hdr in overlay_jni_headers.glob("*.h"):
        shutil.copyfile(hdr, merged_jni_headers / hdr.name)

    # 2. Project locked upstream Kernel native tree and apply the 2 render-context JNI diffs
    #    plus the 2 client-interop JNI diffs (Interop.cpp + jscenelayer.cpp).
    if str(NATIVE_DIR) not in sys.path:
        sys.path.insert(0, str(NATIVE_DIR))
    sources = _load_module("kernel_native_sources", NATIVE_DIR / "sources.py")
    native_lock = json.loads(
        (NATIVE_DIR / "inputs.lock.json").read_text(encoding="utf-8")
    )

    def run_git_step(argv: list[str], _label: str, timeout: int) -> bytes:
        clean_argv = [arg for arg in argv if arg != "--no-lazy-fetch"]
        return subprocess.check_output(clean_argv, env=build_env, timeout=timeout)

    sources.project(atak_source, output_dir, native_lock, run_git_step, git="git")
    sources.gltf_headers(gltf_cache, output_dir)

    layout_dir = output_dir / "layout"
    rc_patch_bytes = RENDER_CONTEXT_PATCH.read_bytes()
    scope_by_path = {entry["path"]: entry for entry in scope_doc["files"]}
    patched_jni_hashes: dict[str, str] = {}

    for rel in RENDER_CONTEXT_JNI_FILES:
        spec = scope_by_path[rel]
        target = layout_dir / rel
        if _sha256_file(target) != spec["preimage_sha256"]:
            raise ValueError(f"preimage SHA mismatch for {rel}")
        _apply_section(
            layout_dir, _extract_patch_section(rc_patch_bytes, rel), build_env
        )
        actual = _sha256_file(target)
        if actual != spec["postimage_sha256"]:
            raise ValueError(
                f"postimage SHA mismatch for {rel}: {actual} != {spec['postimage_sha256']}"
            )
        patched_jni_hashes[rel] = actual

    for rel in (
        "takkernel/engine/src/main/jni/interop/feature/Interop.cpp",
        "takkernel/engine/src/main/jni/jscenelayer.cpp",
    ):
        spec = client_lock["files"][rel]
        target = layout_dir / rel
        if _sha256_file(target) != spec["before"]:
            raise ValueError(f"preimage SHA mismatch for {rel}")
        _apply_section(
            layout_dir, _extract_patch_section(client_patch_bytes, rel), build_env
        )
        actual = _sha256_file(target)
        if actual != spec["after"]:
            raise ValueError(
                f"postimage SHA mismatch for {rel}: {actual} != {spec['after']}"
            )
        patched_jni_hashes[rel] = actual

    # 3. Rebuild all 159 JNI translation units into patched libtakenginejni.so.
    cmake_parent = output_dir / "cmake-parent"
    cmake_parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HOSTED_CMAKE, cmake_parent / "CMakeLists.txt")
    build_dir = output_dir / "build"
    subprocess.run(
        [
            "cmake",
            "-S",
            str(cmake_parent),
            "-B",
            str(build_dir),
            "-G",
            "Ninja",
            "-DCMAKE_BUILD_TYPE=Release",
            f"-DCACHES_ROOT={caches_root}",
            f"-DGDAL_JNI_DIR={gdal_jni_dir}",
            f"-DKERNEL_NATIVE_DIR={core_so.parent}",
            f"-DKERNEL_LAYOUT={layout_dir}",
            f"-DJNI_HEADERS={merged_jni_headers}",
            f"-DGLTF={output_dir / 'gltf-headers'}",
            f"-DGLTFLOADER={output_dir / 'gltf-headers'}",
            f"-DGLES_EXTRA={output_dir / 'gles1-declarations'}",
            f"-DMINIZIP_INCLUDE={minizip_include}",
        ],
        env=build_env,
        check=True,
        timeout=300,
    )

    compile_db = json.loads(
        (build_dir / "compile_commands.json").read_text(encoding="utf-8")
    )
    jni_prefix = f"{(layout_dir / 'takkernel/engine/src/main/jni').as_posix()}/"
    compiled_jni_tus = len(
        {entry["file"] for entry in compile_db if entry["file"].startswith(jni_prefix)}
    )
    if compiled_jni_tus != native_lock["source_counts"]["jni"]:
        raise ValueError(
            f"JNI TU count mismatch: {compiled_jni_tus} != {native_lock['source_counts']['jni']}"
        )

    subprocess.run(
        ["ninja", "-C", str(build_dir), f"-j{jobs}", "takenginejni"],
        env=build_env,
        check=True,
        timeout=900,
    )
    jni_so = _resolve_regular(
        (build_dir / "kernel-jni/libtakenginejni.so",), "libtakenginejni.so"
    )

    sqlite_archive = caches_root / "sqlcipher/lib/libsqlite3.a"
    sqlite_symbols = _defined_nm_names(
        subprocess.check_output(
            ["nm", "--defined-only", str(sqlite_archive)], text=True
        )
    )
    elf_audit = audit_patched_jni_elf(
        jni_dynsym=subprocess.check_output(
            ["readelf", "--dyn-syms", "--wide", str(jni_so)], text=True
        ),
        jni_dyn=subprocess.check_output(["readelf", "-d", str(jni_so)], text=True),
        jni_nm=subprocess.check_output(
            ["nm", "--defined-only", str(jni_so)], text=True
        ),
        sqlite_symbols=sqlite_symbols,
    )

    # 4. Compile the 4 headless Java test harnesses against patched control_classes + base_cp.
    test_cp = os.pathsep.join(str(p) for p in [control_classes, classes_root, *jars])
    java_sources = [HERE / rel for rel in OWNED_TEST_FILES if rel.endswith(".java")]
    subprocess.run(
        [
            str(javac_bin),
            "--release",
            "17",
            "-proc:none",
            "-cp",
            test_cp,
            "-d",
            str(control_classes),
            *(str(p) for p in java_sources),
        ],
        env=build_env,
        check=True,
        timeout=120,
    )

    # 5. Compile the 3 C++ probe shared libraries linked against patched libtakenginejni.so + libtakengine.so.
    probes_dir = output_dir / "probes"
    probes_dir.mkdir(parents=True, exist_ok=True)
    rpath_dirs = os.pathsep.join(
        str(p)
        for p in (
            jni_so.parent,
            core_so.parent,
            gdal_jni_dir,
            caches_root / "gles/lib",
            caches_root / "liblas/lib",
        )
    )
    probe_libs: dict[str, Path] = {}
    for probe_name in PROBE_CPP_NAMES:
        probe_so = probes_dir / f"{probe_name}.so"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-fPIC",
                "-shared",
                "-O1",
                f"-I{layout_dir / 'takkernel/engine/src/main/cpp'}",
                f"-I{layout_dir / 'takkernel/engine/src/main/jni'}",
                f"-I{merged_jni_headers}",
                f"-I{java_home / 'include'}",
                f"-I{java_home / 'include/linux'}",
                str(HERE / "tests" / f"{probe_name}.cpp"),
                f"-L{jni_so.parent}",
                "-ltakenginejni",
                f"-L{core_so.parent}",
                "-ltakengine",
                f"-Wl,-rpath,{rpath_dirs}",
                "-Wl,-z,defs",
                "-o",
                str(probe_so),
            ],
            env=build_env,
            check=True,
            timeout=60,
        )
        probe_libs[probe_name] = probe_so

    # 6. Prepare 8-library load order manifest and lookup TSV, then run all 4 JNI suites under -Xcheck:jni.
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
    manifest = output_dir / "runtime-native.sha256"
    manifest.write_text(
        "".join(f"{_sha256_file(p)} {p.as_posix()}\n" for p in loads),
        encoding="utf-8",
    )

    lookup_tsv = output_dir / "feature-lookups.tsv"
    lookup_tsv.write_text(
        "".join(
            f"{cls}\t{member}\t{desc}\t{kind}\n"
            for cls, member, desc, kind in FEATURE_LOOKUP_ROWS
        ),
        encoding="utf-8",
    )
    scene_work = output_dir / "scene-work"
    scene_work.mkdir(parents=True, exist_ok=True)
    jvm_env = _clean_env(java_home, gdal_data=gdal_data)
    load_paths = [p.as_posix() for p in loads]

    _run_java_control(
        "ManagedBridgeProbe",
        java_bin,
        test_cp,
        "ManagedBridgeProbe",
        [*load_paths, str(probe_libs["ManagedBridgeProbe"])],
        ("BRIDGE_CALLBACKS_COMPLETED operations=22 listeners=4 children=2",),
        jvm_env,
    )
    _run_java_control(
        "SurfaceModesProbe",
        java_bin,
        test_cp,
        "SurfaceModesProbe",
        [*load_paths, str(probe_libs["SurfaceModesProbe"])],
        ("SURFACE_MODES_COMPLETED both construction orders and 100 concurrent calls",),
        jvm_env,
    )
    _run_java_control(
        "FeatureLookupProbe",
        java_bin,
        test_cp,
        "FeatureLookupProbe",
        [str(lookup_tsv), *load_paths, str(probe_libs["FeatureLookupProbe"])],
        (
            f"LOOKUP_RESULT checked={len(FEATURE_LOOKUP_ROWS)} bad=0",
            "TYPED_OPTIONS_ENVELOPE_COMPLETED defaults/filter/label-flags/null-refusal",
        ),
        jvm_env,
    )
    _run_java_control(
        "SceneJNIControls",
        java_bin,
        test_cp,
        "SceneJNIControls",
        [str(manifest), str(scene_work)],
        (
            "SCENE_QUERY_EMPTY_PASS",
            "SCENE_JNI_CONTROLS_PASS query=true idVisibility=true uriVisibility=true remove=true",
        ),
        jvm_env,
    )

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "patched_java_files": patched_java_hashes,
        "patched_jni_files": patched_jni_hashes,
        "jni_translation_units_compiled": compiled_jni_tus,
        "scene_header_generated": scene_header.name,
        "elf_audit": {
            **elf_audit,
            "sha256": _sha256_file(jni_so),
            "bytes": jni_so.stat().st_size,
        },
        "controls": {
            "suites_passed": 4,
            "managed_bridge_operations": 22,
            "managed_bridge_listeners": 4,
            "managed_bridge_children": 2,
            "surface_modes_concurrent_calls": 100,
            "feature_lookup_checked": len(FEATURE_LOOKUP_ROWS),
            "feature_lookup_bad": 0,
            "scene_jni_controls_passed": True,
            "strict_jni_diagnostics": 0,
        },
        "scope": {
            "render_context_jni_bridge_patched": True,
            "client_interop_scene_feature_jni_patched": True,
            "xcheck_jni_controls_passed": True,
            "gl_context_recreation_accepted": False,
            "headed_renderer_accepted": False,
            "mesh_rendering_accepted": False,
            "native_builder_integration": False,
            "full_sdk_accepted": False,
            "full_client_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-render-context-jni-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--atak-source", type=Path)
    parser.add_argument("--gltf-cache", type=Path)
    parser.add_argument("--minizip-include", type=Path)
    parser.add_argument("--caches-root", type=Path)
    parser.add_argument("--gdal-jni-dir", type=Path)
    parser.add_argument("--kernel-java-dir", type=Path)
    parser.add_argument("--kernel-native-dir", type=Path)
    parser.add_argument(
        "--java-home", type=Path, default=Path("/usr/lib/jvm/java-17-openjdk-amd64")
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    required = (
        ("--atak-source", args.atak_source),
        ("--gltf-cache", args.gltf_cache),
        ("--minizip-include", args.minizip_include),
        ("--caches-root", args.caches_root),
        ("--gdal-jni-dir", args.gdal_jni_dir),
        ("--kernel-java-dir", args.kernel_java_dir),
        ("--kernel-native-dir", args.kernel_native_dir),
        ("--output", args.output),
    )
    missing = [flag for flag, val in required if val is None]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")

    qualify(
        atak_source=args.atak_source,
        gltf_cache=args.gltf_cache,
        minizip_include=args.minizip_include,
        caches_root=args.caches_root,
        gdal_jni_dir=args.gdal_jni_dir,
        kernel_java_dir=args.kernel_java_dir,
        kernel_native_dir=args.kernel_native_dir,
        java_home=args.java_home,
        output_dir=args.output,
        jobs=args.jobs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
