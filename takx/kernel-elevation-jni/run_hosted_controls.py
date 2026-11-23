#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX 8.30.5 elevation JNI boundary fixes."""

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
REPO = HERE.parents[1]
NATIVE_DIR = REPO / "takx/kernel-native"
JNI_DIAGNOSTICS_FILE = REPO / "takx/jni_diagnostics.py"

SCHEMA = "uds-takx-kernel-elevation-jni-hosted-v1"
EXPECTED_LOCK_SHA = "5d179495f5b26fa8d9a4bf0b2c22a81100414aa8dc9cebbe9f7390f66f525891"
EXPECTED_PROFILE = "public-elevation-jni-boundaries-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
EXPECTED_KERNEL_VERSION = "8.30.5"

HOSTED_CMAKE = HERE / "hosted_CMakeLists.txt"
LOCK_FILE = HERE / "inputs.lock.json"
PATCH_FILE = HERE / "elevation-jni-boundaries.patch"
PREPARE_FILE = HERE / "prepare.py"
CONTROLS_FILE = HERE / "controls.py"

FAULT_CASES = (
    ("source:getName", False, 4),
    ("source:query", False, 6),
    ("cursor:moveToNext", False, 7),
    ("cursor:get", False, 7),
    ("cursor:close", False, 7),
    ("chunk:getType", False, 8),
    ("chunk:getUri", False, 8),
    ("chunk:getBounds", False, 8),
    ("chunk:sample", False, 8),
    ("chunk:dispose", False, 8),
    ("cursor:get", True, 8),
    ("chunk:getType", True, 9),
    ("chunk:sample", True, 9),
)

SCALAR_CASES = (
    ("source:query", False, 4),
    ("cursor:close", False, 5),
    ("chunk:getType", False, 6),
    ("chunk:getBounds", False, 6),
    ("chunk:sample", False, 6),
    ("chunk:sample", True, 7),
)

PROBES = (
    ("ElevationListenerProbe", "ELEVATION_LISTENER_CONTROLS_PASS checks=112", 112),
    ("ElevationChunkProbe", "ELEVATION_CHUNK_CONTROLS_PASS checks=231", 231),
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
    # Windows core.autocrlf=true expands LF->CRLF on checkout; Linux CI verifies raw bytes.
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


def verify_offline() -> dict:
    """Validate locked elevation JNI patch, prepare/controls recipes, and owned tests."""
    for path in (
        HOSTED_CMAKE,
        LOCK_FILE,
        PATCH_FILE,
        PREPARE_FILE,
        CONTROLS_FILE,
        JNI_DIAGNOSTICS_FILE,
    ):
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(
                f"missing required kernel-elevation-jni input: {path}"
            )

    lock_sha = _sha256_source_text(LOCK_FILE)
    if lock_sha != EXPECTED_LOCK_SHA:
        raise ValueError(
            f"inputs.lock.json digest mismatch: {lock_sha} != {EXPECTED_LOCK_SHA}"
        )

    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    if lock.get("profile") != EXPECTED_PROFILE:
        raise ValueError(f"unexpected profile: {lock.get('profile')}")
    if lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(f"unexpected revision: {lock.get('official_revision')}")
    if lock.get("actual_kernel_version") != EXPECTED_KERNEL_VERSION:
        raise ValueError(
            f"unexpected kernel version: {lock.get('actual_kernel_version')}"
        )

    for flag in (
        "native_builder_integration",
        "strict_elevation_accepted",
        "full_sdk_accepted",
        "full_client_accepted",
    ):
        if lock.get(flag) is not False:
            raise ValueError(f"acceptance flag {flag} must remain False")

    patch_sha = _sha256_source_text(PATCH_FILE)
    if patch_sha != lock["patch_sha256"]:
        raise ValueError(
            f"patch digest mismatch: {patch_sha} != {lock['patch_sha256']}"
        )

    control_hashes: dict[str, str] = {}
    for rel_path, expected_sha in sorted(lock["owned_controls"].items()):
        ctrl_path = HERE / rel_path
        if ctrl_path.is_symlink() or not ctrl_path.is_file():
            raise FileNotFoundError(f"missing owned control file: {ctrl_path}")
        actual_sha = _sha256_source_text(ctrl_path)
        if actual_sha != expected_sha:
            raise ValueError(
                f"control source digest mismatch for {rel_path}: {actual_sha} != {expected_sha}"
            )
        control_hashes[rel_path] = actual_sha

    if os.name != "nt":
        prepare_mod = _load_module("elevation_prepare_contract_check", PREPARE_FILE)
        prepare_mod.contract()

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profile": EXPECTED_PROFILE,
        "official_revision": EXPECTED_REVISION,
        "actual_kernel_version": EXPECTED_KERNEL_VERSION,
        "inputs_lock_sha256": lock_sha,
        "patch_sha256": patch_sha,
        "prepare_py_sha256": _sha256_source_text(PREPARE_FILE),
        "controls_py_sha256": _sha256_source_text(CONTROLS_FILE),
        "hosted_cmake_sha256": _sha256_source_text(HOSTED_CMAKE),
        "owned_controls": control_hashes,
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
        raw_name = parts[7]
        if (
            bind not in ("GLOBAL", "WEAK")
            or "@" in raw_name
            and not raw_name.endswith("@@BASE")
        ):
            sym = raw_name.split("@", 1)[0]
        else:
            sym = raw_name.split("@", 1)[0]
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
    """Verify patched libtakenginejni.so preserves NEEDED and SQLCipher ownership invariants."""
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
    label: str, stdout: str, stderr: str, returncode: int, expected_marker: str
) -> None:
    """Verify exact pass marker and zero strict -Xcheck:jni diagnostics."""
    combined = f"{stdout}\n{stderr}"
    if returncode != 0:
        raise RuntimeError(
            f"{label} exited with code {returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )
    diags = [
        line
        for line in combined.splitlines()
        if any(m in line for m in STRICT_JNI_MARKERS)
    ]
    if diags:
        raise ValueError(
            f"strict -Xcheck:jni or linkage diagnostic in {label}: {diags[:3]}"
        )
    if expected_marker not in stdout:
        raise ValueError(f"missing expected pass marker {expected_marker!r} in {label}")


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


def _run_java_control(
    label: str,
    java_bin: Path,
    classpath: str,
    main_class: str,
    args: list[str],
    expected_marker: str,
    env: dict[str, str],
) -> None:
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
    audit_control_output(
        label, proc.stdout, proc.stderr, proc.returncode, expected_marker
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
    """Prepare checked elevation JNI sources, rebuild libtakenginejni.so, and run all 5 suites under -Xcheck:jni."""
    verified = verify_offline()
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))

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

    # 1. Run exact prepare.py recipe to verify before/after hashes of all 3 patched elevation JNI files.
    prepared_dir = output_dir / "prepared-elevation"
    subprocess.run(
        [
            sys.executable,
            "-B",
            str(PREPARE_FILE),
            "--phase",
            "prepare",
            "--source",
            str(atak_source),
            "--output",
            str(prepared_dir),
        ],
        env=build_env,
        check=True,
        timeout=60,
    )

    # 2. Project locked upstream Kernel native tree via takx/kernel-native/sources.py (applies jni-exception-correctness.patch).
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

    # 3. Overlay the 3 verified elevation JNI postimages into layout/ and check digests.
    overlaid_hashes: dict[str, str] = {}
    for rel_path, expected in sorted(lock["files"].items()):
        prepared_file = prepared_dir / rel_path
        dest_file = output_dir / "layout" / rel_path
        if _sha256_file(prepared_file) != expected["after"]:
            raise ValueError(
                f"prepared elevation postimage hash mismatch for {rel_path}"
            )
        if _sha256_file(dest_file) != expected["before"]:
            raise ValueError(f"layout preimage hash mismatch for {rel_path}")
        shutil.copyfile(prepared_file, dest_file)
        overlaid_hashes[rel_path] = _sha256_file(dest_file)

    # 4. Build patched libtakenginejni.so linked against prebuilt libtakengine.so.
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
            f"-DKERNEL_LAYOUT={output_dir / 'layout'}",
            f"-DJNI_HEADERS={jni_headers}",
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
    jni_prefix = f"{(output_dir / 'layout/takkernel/engine/src/main/jni').as_posix()}/"
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

    artifact_jni = output_dir / "libtakenginejni.so"
    shutil.copyfile(jni_so, artifact_jni)

    # 5. Build runtime-native.sha256 in the exact load order expected by controls.
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
    bound_hashes = {path.as_posix(): _sha256_file(path) for path in loads}
    manifest = output_dir / "runtime-native.sha256"
    manifest.write_text(
        "".join(f"{bound_hashes[p.as_posix()]} {p.as_posix()}\n" for p in loads),
        encoding="utf-8",
    )

    # 6. Compile all 5 owned Java controls against kernel-java + classpath.
    control_classes = output_dir / "control-classes"
    control_classes.mkdir(parents=True, exist_ok=True)
    java_sources = [
        HERE / rel for rel in sorted(lock["owned_controls"]) if rel.endswith(".java")
    ]
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
            *[str(src) for src in java_sources],
        ],
        env=build_env,
        check=True,
        timeout=120,
    )

    # 7. Compile ElevationListenerProbe.cpp and ElevationChunkProbe.cpp against patched libtakenginejni.so.
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
    for probe_name, _marker, _checks in PROBES:
        probe_so = probes_dir / f"{probe_name}.so"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-fPIC",
                "-shared",
                "-O1",
                f"-I{output_dir / 'layout/takkernel/engine/src/main/cpp'}",
                f"-I{output_dir / 'layout/takkernel/engine/src/main/jni'}",
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

    jvm_env = _clean_env(java_home, gdal_data=gdal_data)
    full_cp = os.pathsep.join(str(p) for p in [control_classes, classes_root, *jars])

    # 8. Execute all 5 control suites under JDK 17 -Xcheck:jni.
    _run_java_control(
        "ElevationJNIControls",
        java_bin,
        full_cp,
        "ElevationJNIControls",
        [str(manifest)],
        "ELEVATION_JNI_CONTROLS_PASS checks=166",
        jvm_env,
    )

    fault_assertions = 0
    for fault, secondary, checks in FAULT_CASES:
        args = [str(manifest), fault]
        if secondary:
            args.append("secondary")
        _run_java_control(
            f"ElevationFaultControls:{fault}{':secondary' if secondary else ''}",
            java_bin,
            full_cp,
            "ElevationFaultControls",
            args,
            f"ELEVATION_FAULT_CONTROLS_PASS case={fault} checks={checks}",
            jvm_env,
        )
        fault_assertions += checks

    probe_assertions: dict[str, int] = {}
    for probe_name, marker, checks in PROBES:
        _run_java_control(
            probe_name,
            java_bin,
            full_cp,
            probe_name,
            [str(manifest), str(probe_libs[probe_name])],
            marker,
            jvm_env,
        )
        probe_assertions[probe_name] = checks

    scalar_assertions = 0
    for fault, secondary, checks in SCALAR_CASES:
        args = [str(manifest), fault]
        if secondary:
            args.append("secondary")
        _run_java_control(
            f"ElevationScalarControls:{fault}{':secondary' if secondary else ''}",
            java_bin,
            full_cp,
            "ElevationScalarControls",
            args,
            f"ELEVATION_SCALAR_CONTROLS_PASS case={fault} checks={checks}",
            jvm_env,
        )
        scalar_assertions += checks

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "overlaid_elevation_files": overlaid_hashes,
        "jni_translation_units_compiled": compiled_jni_tus,
        "elf_audit": {
            **elf_audit,
            "sha256": _sha256_file(artifact_jni),
            "bytes": artifact_jni.stat().st_size,
        },
        "controls": {
            "elevation_jni_checks": 166,
            "elevation_fault_cases": len(FAULT_CASES),
            "elevation_fault_assertions": fault_assertions,
            "elevation_listener_probe_checks": probe_assertions[
                "ElevationListenerProbe"
            ],
            "elevation_chunk_probe_checks": probe_assertions["ElevationChunkProbe"],
            "elevation_scalar_cases": len(SCALAR_CASES),
            "elevation_scalar_assertions": scalar_assertions,
            "total_assertions": (
                166
                + fault_assertions
                + probe_assertions["ElevationListenerProbe"]
                + probe_assertions["ElevationChunkProbe"]
                + scalar_assertions
            ),
            "strict_jni_diagnostics": 0,
        },
        "scope": {
            "elevation_jni_boundaries_patched": True,
            "xcheck_jni_controls_passed": True,
            "native_builder_integration": False,
            "strict_elevation_accepted": False,
            "full_sdk_accepted": False,
            "full_client_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-kernel-elevation-jni-receipt.json"
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
