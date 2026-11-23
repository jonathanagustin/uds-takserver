#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX Kernel 8.30.5 SDK package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import resource_inputs
import source_families

SCHEMA = "uds-takx-sdk-package-hosted-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
EXPECTED_NATIVE_ORDER = [
    "GLdispatch",
    "GLESv2",
    "las",
    "las_c",
    "gdal",
    "gdalalljni",
    "assimp",
    "takengine",
    "takenginejni",
]
FORBIDDEN_HOST_PREFIXES = (
    "libGLdispatch.so",
    "libGLESv2.so",
    "liblas.so",
    "liblas_c.so",
    "libgdal.so",
    "libgdalalljni.so",
    "libassimp.so",
    "libsqlite3.so",
    "libsqlcipher.so",
    "libssl.so",
    "libcrypto.so",
    "libtakengine.so",
    "libtakenginejni.so",
)
STRICT_JNI_MARKERS = (
    "WARNING in native method",
    "WARNING: JNI",
    "FATAL ERROR",
    "JNI DETECTED ERROR",
)
RUNTIME_FAILURE_MARKERS = (
    "EngineLibrary: Failed to load",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
)
ALWAYS_REQUIRED_PASS_MARKERS = (
    "PASS_GENUINE_118_RESOURCES_91_ASSETS_25_CATALOG_15_STOCK_GLSL",
    "PASS_STOCK_ENGINE_RESOURCE_LOADER_SCOPED_COMPONENT",
    "PASS_ENGINE_INITIALIZER_AND_NATIVE_CONFIG_GDAL",
    "PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION",
    "PASS_REAL_REDUCED_PROFILE_GDAL_SKIP_INIT_REGISTRY_NO_SUPPRESSION",
)
DATABASE_REQUIRED_PASS_MARKERS = (
    "PASS_REAL_DEFAULT_IO_READONLY_WRITE_REJECTION_NO_CREATE_MBTILES",
    "PASS_PREPARED_NATIVE_DATABASE2_ENCRYPTED_OPEN_REOPEN_WRONG_KEY_CONTROL",
    "PASS_REAL_GDAL_MEM_EXACT_WRITE_READ",
)
LOGGER_REQUIRED_PASS_MARKER = (
    "PASS_REAL_THROWING_LOGGER_BASELINE_REJECTION_MATCH_AND_CORRECT_KEY_RECOVERY"
)

KERNEL_NATIVE_DIR = HERE.parent / "kernel-native"
RESOURCES_ADAPTER = HERE.parent / "compat/src/main/java/android/content/res/Resources.java"
CONTROL_JAVA_FILES = (
    KERNEL_NATIVE_DIR / "KernelRuntimeSmoke.java",
    KERNEL_NATIVE_DIR / "NativeDatabaseCandidate.java",
    KERNEL_NATIVE_DIR / "KernelThrowingLoggerCandidate.java",
    HERE / "StockLoaderSmoke.java",
    HERE / "RuntimeResourcesSmoke.java",
    HERE / "DefaultIoReadonlySmoke.java",
    HERE / "GdalReducedProfileSmoke.java",
)

INPUTS_LOCK = HERE / "inputs.lock.json"
RESOURCES_LOCK = HERE / "runtime-resources.lock.json"
NOTICES_LOCK = HERE / "notices.lock.json"

FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
LFS_PAYLOAD_SHA = "12e71980bdeffb9bb48db5976e9e10a0af635f7fc7834915db3baab9c195aa31"
LFS_PAYLOAD_BYTES = 1067496


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _patch_digest(path: Path) -> str:
    data = path.read_bytes()
    if os.name == "nt":
        # Windows Git checkouts convert text patches to CRLF while LF hashes are locked.
        data = data.replace(b"\r\n", b"\n")
    return _sha256_bytes(data)


def _safe_archive_name(name: str) -> str:
    pure = PurePosixPath(name)
    if not name or pure.is_absolute() or ".." in pure.parts or "\\" in name:
        raise ValueError(f"unsafe archive member name: {name}")
    return str(pure)


def _resolve_regular(candidates: tuple[Path, ...], label: str) -> Path:
    for candidate in candidates:
        if candidate.exists():
            resolved = candidate.resolve()
            if resolved.is_symlink() or not resolved.is_file():
                raise ValueError(f"resolved path for {label} is not a regular file: {resolved}")
            return resolved
    raise FileNotFoundError(f"missing {label}: tried {[str(c) for c in candidates]}")


def deterministic_jar_write(path: Path, entries: dict[str, bytes]) -> None:
    """Write deterministic deflated ZIP/JAR sorted by entry name and fixed timestamp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as jar:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(_safe_archive_name(name), FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            jar.writestr(info, data)
    with zipfile.ZipFile(path, "r") as jar:
        names = jar.namelist()
        if len(names) != len(set(names)) or set(names) != set(entries):
            raise ValueError(f"JAR allowlist or uniqueness check failed: {path}")


def verify_offline() -> dict:
    """Validate frozen sdk-package lockfiles, patches, compat Resources.java, and smoke sources."""
    required = [
        INPUTS_LOCK,
        RESOURCES_LOCK,
        NOTICES_LOCK,
        RESOURCES_ADAPTER,
        *CONTROL_JAVA_FILES,
    ]
    for path in required:
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing or empty required sdk-package input: {path}")

    lock = json.loads(INPUTS_LOCK.read_text(encoding="utf-8"))
    resource_lock = json.loads(RESOURCES_LOCK.read_text(encoding="utf-8"))
    notices_lock = json.loads(NOTICES_LOCK.read_text(encoding="utf-8"))

    if lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(f"unexpected official revision in inputs.lock.json: {lock.get('official_revision')}")
    if lock.get("native_order") != EXPECTED_NATIVE_ORDER:
        raise ValueError(f"unexpected native_order in inputs.lock.json: {lock.get('native_order')}")
    if resource_lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError("runtime-resources.lock.json official_revision disagrees with inputs.lock.json")

    patch_checks = (
        ("engine-library-public-linux.patch", lock["patch_sha256"]),
        ("default-io-readonly.patch", lock["default_io_readonly"]["patch_sha256"]),
        ("gles-null-shader-lengths.patch", lock["gles_null_shader_lengths"]["patch_sha256"]),
        ("obj-buffer-capacity.patch", lock["obj_buffer_capacity"]["patch_sha256"]),
        ("gdal-reduced-profile-skip.patch", lock["reduced_gdal"]["patch_sha256"]),
    )
    patch_hashes: dict[str, str] = {}
    for patch_name, expected_sha in patch_checks:
        patch_path = HERE / "patches" / patch_name
        if not patch_path.is_file():
            raise FileNotFoundError(f"missing frozen patch: {patch_path}")
        actual_sha = _patch_digest(patch_path)
        if actual_sha != expected_sha:
            raise ValueError(f"frozen patch digest mismatch for {patch_name}: {actual_sha} != {expected_sha}")
        patch_hashes[patch_name] = actual_sha

    files = resource_lock.get("files", [])
    if len(files) != 118:
        raise ValueError(f"expected 118 locked runtime resources, found {len(files)}")
    lfs_items = [item for item in files if item.get("lfs_payload")]
    if len(lfs_items) != 1:
        raise ValueError(f"expected exactly 1 LFS payload in runtime-resources.lock.json, found {len(lfs_items)}")
    lfs = lfs_items[0]["lfs_payload"]
    if lfs.get("sha256") != LFS_PAYLOAD_SHA or lfs.get("bytes") != LFS_PAYLOAD_BYTES:
        raise ValueError(f"unexpected LFS payload metadata: {lfs}")
    if len(notices_lock.get("official_git", [])) != 2:
        raise ValueError("expected 2 official_git notices in notices.lock.json")

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "official_revision": EXPECTED_REVISION,
        "version": lock["version"],
        "native_order": lock["native_order"],
        "locked_runtime_resources": len(files),
        "lfs_payload": lfs,
        "inputs_lock_sha256": _sha256_file(INPUTS_LOCK),
        "runtime_resources_lock_sha256": _sha256_file(RESOURCES_LOCK),
        "notices_lock_sha256": _sha256_file(NOTICES_LOCK),
        "patch_hashes": patch_hashes,
        "resources_adapter_sha256": _sha256_file(RESOURCES_ADAPTER),
    }


def parse_smoke_output(text: str) -> dict:
    """Extract structured extraction dir, packaged providers, ELFs, and logger lines from smoke output."""
    extraction_dirs = re.findall(r"^EXTRACTION_DIRECTORY (.+)$", text, re.MULTILINE)
    packaged_native = {
        path_str: sha
        for sha, path_str in re.findall(r"^PACKAGED_NATIVE ([0-9a-f]{64}) (.+)$", text, re.MULTILINE)
    }
    mapped_elfs = {
        path_str: sha
        for sha, path_str in re.findall(r"^ELF ([0-9a-f]{64}) ([^\r\n]+)$", text, re.MULTILINE)
    }
    logger_match = re.search(
        r"LOGGER_CALLBACKS=(\d+) BASELINE_WRONG_KEY=(\S+) LISTENER_WRONG_KEY=(\S+)",
        text,
    )
    return {
        "extraction_dirs": extraction_dirs,
        "packaged_native": packaged_native,
        "mapped_elfs": mapped_elfs,
        "logger_match": logger_match,
    }


def audit_phase_output(
    phase: str,
    mode: str,
    text: str,
    expected_library_path: Path,
    native_manifest: dict[str, str],
    forbidden_paths: set[str],
) -> dict:
    """Enforce PASS_* markers, zero -Xcheck:jni diagnostics, extraction isolation, and ELF allowlist."""
    diags = [line for line in text.splitlines() if any(m in line for m in STRICT_JNI_MARKERS)]
    if diags:
        raise ValueError(f"strict -Xcheck:jni diagnostic in phase {phase}: {diags[:3]}")
    for failure_marker in RUNTIME_FAILURE_MARKERS:
        if failure_marker in text:
            raise ValueError(f"runtime loader failure marker {failure_marker!r} in phase {phase}")

    required = list(ALWAYS_REQUIRED_PASS_MARKERS)
    if mode == "database":
        required.extend(DATABASE_REQUIRED_PASS_MARKERS)
    if phase == "logger":
        required.append(LOGGER_REQUIRED_PASS_MARKER)
    missing_markers = [marker for marker in required if marker not in text]
    if missing_markers:
        raise ValueError(f"missing PASS markers in phase {phase}: {missing_markers}")

    parsed = parse_smoke_output(text)
    expected_resolved = str(expected_library_path.resolve())
    if len(parsed["extraction_dirs"]) != 1:
        raise ValueError(f"expected 1 EXTRACTION_DIRECTORY in phase {phase}, got {parsed['extraction_dirs']}")
    actual_extraction = str(Path(parsed["extraction_dirs"][0]).resolve())
    if actual_extraction != expected_resolved:
        raise ValueError(
            f"extraction directory mismatch in phase {phase}: {actual_extraction} != {expected_resolved}"
        )

    expected_packaged = {
        str(Path(expected_resolved) / name): sha for name, sha in native_manifest.items()
    }
    if parsed["packaged_native"] != expected_packaged:
        raise ValueError(f"packaged native extraction inventory mismatch in phase {phase}")

    mapped_elfs = parsed["mapped_elfs"]
    for pkg_path, pkg_sha in expected_packaged.items():
        if mapped_elfs.get(pkg_path) != pkg_sha:
            raise ValueError(f"packaged provider absent or mismatched in /proc/self/maps: {pkg_path}")

    for mapped_path in mapped_elfs:
        if mapped_path in forbidden_paths:
            raise ValueError(f"original pre-packaged native path mapped at runtime: {mapped_path}")
        if mapped_path not in expected_packaged:
            name = Path(mapped_path).name
            if name.startswith(FORBIDDEN_HOST_PREFIXES):
                raise ValueError(f"unpackaged or host provider substitution mapped: {mapped_path}")

    logger_callbacks: int | None = None
    if phase == "logger":
        match = parsed["logger_match"]
        if not match or int(match.group(1)) < 1 or match.group(2) != match.group(3):
            raise ValueError("throwing logger callback count or wrong-key parity failed")
        logger_callbacks = int(match.group(1))

    result = {
        "mode": mode,
        "required_controls": required,
        "strict_jni_warnings": 0,
        "extraction_directory": actual_extraction,
        "packaged_provider_count": len(expected_packaged),
        "mapped_elf_count": len(mapped_elfs),
    }
    if logger_callbacks is not None:
        result["logger_callbacks"] = logger_callbacks
    return result


def _clean_env(java_home: Path) -> dict[str, str]:
    removed = {
        "CPATH",
        "C_INCLUDE_PATH",
        "CPLUS_INCLUDE_PATH",
        "LIBRARY_PATH",
        "CFLAGS",
        "CXXFLAGS",
        "CPPFLAGS",
        "LDFLAGS",
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
        if k not in removed
        and not k.startswith(("LD_", "GDAL_", "CPL_", "PROJ_", "CMAKE_", "PKG_CONFIG", "BASH_FUNC_"))
    }
    env.update(
        JAVA_HOME=str(java_home),
        LC_ALL="C.UTF-8",
        PYTHONNOUSERSITE="1",
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="safe.directory",
        GIT_CONFIG_VALUE_0="*",
        GIT_NO_LAZY_FETCH="1",
    )
    return env


def _build_shared_assimp(caches_root: Path, output_dir: Path, env: dict[str, str]) -> Path:
    assimp_dir = output_dir / "assimp-shared"
    assimp_dir.mkdir(parents=True, exist_ok=True)
    libassimp_so = assimp_dir / "libassimp.so"
    libassimp_a = _resolve_regular((caches_root / "assimp/lib/libassimp.a",), "libassimp.a")
    libirrxml_a = _resolve_regular((caches_root / "assimp/lib/libIrrXML.a",), "libIrrXML.a")
    libz_a = _resolve_regular((caches_root / "zlib/lib/libz.a",), "libz.a")

    subprocess.run(
        [
            "g++",
            "-shared",
            "-Wl,-soname,libassimp.so",
            "-Wl,--whole-archive",
            str(libassimp_a),
            str(libirrxml_a),
            "-Wl,--no-whole-archive",
            str(libz_a),
            "-Wl,--no-undefined",
            "-lm",
            "-o",
            str(libassimp_so),
        ],
        env=env,
        check=True,
        timeout=120,
    )
    dyn = subprocess.check_output(["readelf", "-d", str(libassimp_so)], env=env, text=True, timeout=30)
    if "TEXTREL" in dyn:
        raise ValueError(f"unexpected TEXTREL in built {libassimp_so}")
    return libassimp_so.resolve()


def _copy_notices(
    atak_source: Path,
    official_revision: str,
    notices_lock: dict,
    caches_root: Path,
    licenses_dir: Path,
    env: dict[str, str],
) -> dict[str, dict]:
    licenses_dir.mkdir(parents=True, exist_ok=True)
    for item in notices_lock["official_git"]:
        data = subprocess.check_output(
            ["git", "-C", str(atak_source), "show", f"{official_revision}:{item['path']}"],
            env=env,
            timeout=30,
        )
        if len(data) != item["bytes"] or _sha256_bytes(data) != item["sha256"]:
            raise ValueError(f"official notice differs for {item['path']}")
        (licenses_dir / f"Kernel-{item['path']}").write_bytes(data)

    cache_licenses = (
        ("gles", "gles/LICENSE-NOTICE.md"),
        ("las", "liblas/LICENSE.txt"),
        ("las", "liblas/BOOST_LICENSE_1_0.txt"),
        ("assimp", "assimp/LICENSE"),
        ("zlib", "zlib/LICENSE"),
    )
    for provider, rel_path in cache_licenses:
        src = caches_root / rel_path
        if src.is_file() and not src.is_symlink():
            dest = licenses_dir / provider / src.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

    inventory: dict[str, dict] = {}
    for file_path in sorted(licenses_dir.rglob("*")):
        if file_path.is_file():
            inventory[file_path.relative_to(licenses_dir).as_posix()] = {
                "bytes": file_path.stat().st_size,
                "sha256": _sha256_file(file_path),
            }
    return inventory


def _stage_and_patch_java_sources(
    atak_source: Path,
    official_revision: str,
    lock: dict,
    stage_dir: Path,
    env: dict[str, str],
) -> list[Path]:
    stage_dir.mkdir(parents=True, exist_ok=True)
    patch_specs = (
        ("EngineLibrary.java", lock["source_file"], lock["engine_before"], lock["engine_after"], "engine-library-public-linux.patch", lock["patch_sha256"]),
        ("DefaultIOProvider.java", lock["default_io_readonly"]["source_file"], lock["default_io_readonly"]["before"], lock["default_io_readonly"]["after"], "default-io-readonly.patch", lock["default_io_readonly"]["patch_sha256"]),
        ("GLES20.java", lock["gles_null_shader_lengths"]["source_file"], lock["gles_null_shader_lengths"]["before"], lock["gles_null_shader_lengths"]["after"], "gles-null-shader-lengths.patch", lock["gles_null_shader_lengths"]["patch_sha256"]),
        ("ObjModelSpi.java", lock["obj_buffer_capacity"]["source_file"], lock["obj_buffer_capacity"]["before"], lock["obj_buffer_capacity"]["after"], "obj-buffer-capacity.patch", lock["obj_buffer_capacity"]["patch_sha256"]),
        ("GdalLibrary.java", lock["reduced_gdal"]["source_file"], lock["reduced_gdal"]["source_before_sha256"], lock["reduced_gdal"]["source_after_sha256"], "gdal-reduced-profile-skip.patch", lock["reduced_gdal"]["patch_sha256"]),
    )
    patched_files: list[Path] = []
    for filename, repo_rel, before_sha, after_sha, patch_name, patch_sha in patch_specs:
        raw = subprocess.check_output(
            ["git", "-C", str(atak_source), "show", f"{official_revision}:{repo_rel}"],
            env=env,
            timeout=30,
        )
        if _sha256_bytes(raw) != before_sha:
            raise ValueError(f"preimage SHA-256 mismatch for {repo_rel}")
        target = stage_dir / filename
        target.write_bytes(raw)

        patch_path = HERE / "patches" / patch_name
        if _patch_digest(patch_path) != patch_sha:
            raise ValueError(f"patch SHA-256 mismatch for {patch_name}")
        subprocess.run(
            ["patch", "--batch", "--binary", "--fuzz=0", "--no-backup-if-mismatch", "-p1", "-d", str(stage_dir), "-i", str(patch_path)],
            env=env,
            check=True,
            timeout=30,
        )
        actual_after = _sha256_file(target)
        if actual_after != after_sha:
            raise ValueError(f"postimage SHA-256 mismatch for {filename}: {actual_after} != {after_sha}")
        patched_files.append(target)
    return patched_files


def _stage_third_party_jars(classpath_dir: Path, dest_dir: Path) -> list[dict]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    jars = sorted(classpath_dir.glob("*.jar"))
    if not jars:
        raise FileNotFoundError(f"no dependency JARs found in {classpath_dir}")
    records: list[dict] = []
    for jar_path in jars:
        resolved = jar_path.resolve()
        if resolved.is_symlink() or not resolved.is_file():
            raise ValueError(f"regular dependency JAR required: {jar_path}")
        with zipfile.ZipFile(resolved, "r") as jar:
            names = jar.namelist()
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate entries inside dependency JAR: {jar_path.name}")
            if any(n.lower().endswith((".so", ".dll", ".dylib", ".jnilib")) for n in names):
                raise ValueError(f"dependency JAR contains prebuilt native libraries: {jar_path.name}")
            for entry_name in names:
                _safe_archive_name(entry_name)
        dest = dest_dir / resolved.name
        shutil.copyfile(resolved, dest)
        records.append({
            "path": f"third-party/{dest.name}",
            "sha256": _sha256_file(dest),
            "bytes": dest.stat().st_size,
        })
    return records


def qualify(
    atak_source: Path,
    resource_payloads: Path,
    caches_root: Path,
    gdal_jni_dir: Path,
    kernel_java_dir: Path,
    kernel_native_dir: Path,
    java_home: Path,
    output_dir: Path,
) -> dict:
    """Assemble and qualify kernel-8.30.5-linux-amd64-public-cpu.jar on Linux x86_64."""
    verified = verify_offline()
    lock = json.loads(INPUTS_LOCK.read_text(encoding="utf-8"))
    resource_lock = json.loads(RESOURCES_LOCK.read_text(encoding="utf-8"))
    notices_lock = json.loads(NOTICES_LOCK.read_text(encoding="utf-8"))

    atak_source = atak_source.resolve()
    resource_payloads = resource_payloads.resolve()
    caches_root = caches_root.resolve()
    gdal_jni_dir = gdal_jni_dir.resolve()
    kernel_java_dir = kernel_java_dir.resolve()
    kernel_native_dir = kernel_native_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    env = _clean_env(java_home)
    java_bin = java_home / "bin/java"
    javac_bin = java_home / "bin/javac"
    for tool in (java_bin, javac_bin):
        if not tool.is_file():
            raise FileNotFoundError(f"missing JDK tool: {tool}")

    assimp_so = _build_shared_assimp(caches_root, output_dir, env)
    libraries = {
        "GLdispatch": _resolve_regular((caches_root / "gles/lib/libGLdispatch.so.0.0.0", caches_root / "gles/lib/libGLdispatch.so.0", caches_root / "gles/lib/libGLdispatch.so"), "libGLdispatch.so"),
        "GLESv2": _resolve_regular((caches_root / "gles/lib/libGLESv2.so.2.1.0", caches_root / "gles/lib/libGLESv2.so.2", caches_root / "gles/lib/libGLESv2.so"), "libGLESv2.so"),
        "las": _resolve_regular((caches_root / "liblas/lib/liblas.so.2.4.0", caches_root / "liblas/lib/liblas.so.3", caches_root / "liblas/lib/liblas.so"), "liblas.so"),
        "las_c": _resolve_regular((caches_root / "liblas/lib/liblas_c.so.2.4.0", caches_root / "liblas/lib/liblas_c.so.3", caches_root / "liblas/lib/liblas_c.so"), "liblas_c.so"),
        "gdal": _resolve_regular((gdal_jni_dir / "libgdal.so",), "libgdal.so"),
        "gdalalljni": _resolve_regular((gdal_jni_dir / "libgdalalljni.so",), "libgdalalljni.so"),
        "assimp": assimp_so,
        "takengine": _resolve_regular((kernel_native_dir / "build/kernel-cpp/libtakengine.so", kernel_native_dir / "libtakengine.so"), "libtakengine.so"),
        "takenginejni": _resolve_regular((kernel_native_dir / "build/kernel-jni/libtakenginejni.so", kernel_native_dir / "libtakenginejni.so"), "libtakenginejni.so"),
    }
    if list(libraries) != lock["native_order"]:
        raise ValueError(f"native library order mismatch: {list(libraries)} != {lock['native_order']}")
    forbidden_paths = {str(p.resolve()) for p in libraries.values()}

    package_dir = output_dir / "package"
    package_dir.mkdir(parents=True, exist_ok=True)
    notices_inventory = _copy_notices(
        atak_source, lock["official_revision"], notices_lock, caches_root, package_dir / "notices", env
    )

    stage_dir = output_dir / "patched-source"
    patched_sources = _stage_and_patch_java_sources(
        atak_source, lock["official_revision"], lock, stage_dir, env
    )

    classes_root = kernel_java_dir / "classes"
    classpath_dir = kernel_java_dir / "classpath"
    if not classes_root.is_dir() or not classpath_dir.is_dir():
        raise FileNotFoundError(f"expected kernel-java classes/ and classpath/ under {kernel_java_dir}")

    third_party_records = _stage_third_party_jars(classpath_dir, package_dir / "third-party")
    dep_jars = [package_dir / item["path"] for item in third_party_records]

    replacement_dir = output_dir / "replacement-classes"
    replacement_dir.mkdir(parents=True, exist_ok=True)
    compile_cp = os.pathsep.join(str(p) for p in [classes_root, *dep_jars])
    subprocess.run(
        [
            str(javac_bin),
            "--release",
            "17",
            "-proc:none",
            "-implicit:none",
            "-sourcepath",
            "",
            "-cp",
            compile_cp,
            "-d",
            str(replacement_dir),
            *(str(p) for p in patched_sources),
            str(RESOURCES_ADAPTER),
        ],
        env=env,
        check=True,
        timeout=120,
    )

    original_classes = {
        p.relative_to(classes_root).as_posix(): p.read_bytes()
        for p in sorted(classes_root.rglob("*.class"))
    }
    replacement_classes = {
        p.relative_to(replacement_dir).as_posix(): p.read_bytes()
        for p in sorted(replacement_dir.rglob("*.class"))
    }
    merged_classes = source_families.merge(
        original_classes,
        replacement_classes,
        ("com/atakmap/map/layer/model/obj/ObjModelSpi", "com/atakmap/map/gdal/GdalLibrary"),
    )

    mapping = resource_lock["mapping_recipe"]
    mapping_bytes = subprocess.check_output(
        ["git", "-C", str(atak_source), "show", f"{lock['official_revision']}:{mapping['path']}"],
        env=env,
        timeout=30,
    )
    if len(mapping_bytes) != mapping["bytes"] or _sha256_bytes(mapping_bytes) != mapping["sha256"]:
        raise ValueError("official Gradle buildconfig.gradle mapping differs")

    tree_out = subprocess.check_output(
        [
            "git",
            "-C",
            str(atak_source),
            "ls-tree",
            "-r",
            "--long",
            lock["official_revision"],
            "--",
            *(r.rstrip("/") for r in resource_inputs.ROOTS),
        ],
        env=env,
        timeout=30,
    ).decode("utf-8")
    resource_tree = {}
    for row in tree_out.splitlines():
        metadata, name = row.split("\t", 1)
        mode, kind, blob, size = metadata.split()
        if kind != "blob":
            raise ValueError(f"non-blob resource entry: {name}")
        resource_tree[name] = (mode, blob, int(size))

    payload_sizes = {
        item["lfs_payload"]["sha256"]: item["lfs_payload"]["bytes"]
        for item in resource_lock["files"]
        if item.get("lfs_payload")
    }

    def read_resource_blob(name: str) -> bytes:
        return subprocess.check_output(
            ["git", "-C", str(atak_source), "show", f"{lock['official_revision']}:{name}"],
            env=env,
            timeout=30,
        )

    def read_lfs_payload(sha: str) -> bytes:
        src = (resource_payloads / sha).resolve()
        if src.is_symlink() or not src.is_file() or src.stat().st_size != payload_sizes[sha]:
            raise ValueError(f"missing or sized-mismatched LFS payload: {src}")
        return src.read_bytes()

    resources = resource_inputs.collect(resource_lock, resource_tree, read_resource_blob, read_lfs_payload)
    expected_resources_file = output_dir / "runtime-resources.sha256"
    expected_resources_file.write_text(
        "".join(
            f"{item.get('lfs_payload', item)['sha256']} {item['kind']} {item['destination']}\n"
            for item in resource_lock["files"]
        ),
        encoding="utf-8",
    )

    natives = {f"lib{name}.so": path.read_bytes() for name, path in libraries.items()}
    native_manifest = {name: _sha256_bytes(data) for name, data in natives.items()}
    native_resources_file = output_dir / "native-resources.sha256"
    native_resources_file.write_text(
        "".join(f"{sha} {name}\n" for name, sha in native_manifest.items()),
        encoding="utf-8",
    )

    entries = resource_inputs.merge(
        merged_classes,
        {f"libs/linux64/{name}": data for name, data in natives.items()},
        resources,
    )
    entries["META-INF/MANIFEST.MF"] = (
        f"Manifest-Version: 1.0\n"
        f"Implementation-Title: Public-source Kernel CPU JNI component\n"
        f"Implementation-Version: {lock['version']}\n\n"
    ).encode()

    jar_name = f"kernel-{lock['version']}-linux-amd64-public-cpu.jar"
    artifact = package_dir / jar_name
    deterministic_jar_write(artifact, entries)
    jar_md5 = hashlib.md5(artifact.read_bytes(), usedforsecurity=False).hexdigest()

    manifest = {
        "version": lock["version"],
        "official_revision": lock["official_revision"],
        "abi": lock["abi"],
        "profile": lock["reduced_gdal"]["profile"],
        "class_count": len(merged_classes),
        "replaced_class_count": len(replacement_classes),
        "runtime_resource_count": len(resource_lock["files"]),
        "runtime_resource_entries_count": len(resources),
        "native_resource_order": lock["native_order"],
        "native_resources": native_manifest,
        "dependencies": third_party_records,
        "notices_count": len(notices_inventory),
        "stock_loader_java_library_path": f"${{java.io.tmpdir}}/takkernel/engine/{jar_md5}/lib/linux64",
    }
    (package_dir / "version-abi-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    runtime_classes = output_dir / "runtime-classes"
    runtime_classes.mkdir(parents=True, exist_ok=True)
    smoke_cp = os.pathsep.join(str(p) for p in [artifact, *dep_jars])
    subprocess.run(
        [
            str(javac_bin),
            "--release",
            "17",
            "-proc:none",
            "-cp",
            smoke_cp,
            "-d",
            str(runtime_classes),
            *(str(p) for p in CONTROL_JAVA_FILES),
        ],
        env=env,
        check=True,
        timeout=120,
    )

    relocated_dir = output_dir / "relocated-package"
    shutil.copytree(package_dir, relocated_dir)

    phase_results: dict[str, dict] = {}
    for phase in ("basic", "database", "logger", "relocated"):
        app = output_dir / "runtime" / phase
        (app / "home").mkdir(parents=True, exist_ok=True)
        (app / "data").mkdir(parents=True, exist_ok=True)
        selected_pkg = relocated_dir if phase == "relocated" else package_dir
        selected_jar = selected_pkg / jar_name
        selected_jars = [selected_pkg / item["path"] for item in third_party_records]
        mode = "database" if phase == "relocated" else phase
        stock_tmp = output_dir / ("stock-tmp-relocated" if phase == "relocated" else "stock-tmp-package")
        stock_tmp.mkdir(parents=True, exist_ok=True)
        library_path = stock_tmp / "takkernel/engine" / jar_md5 / "lib/linux64"

        proc = subprocess.run(
            [
                str(java_bin),
                f"-Djava.library.path={library_path}",
                "-Xcheck:jni",
                "-Xmx256m",
                "-XX:ActiveProcessorCount=2",
                "-Djava.awt.headless=true",
                f"-Duser.home={app / 'home'}",
                f"-Djava.io.tmpdir={stock_tmp}",
                f"-DTAK_APP_DATA_PATH={app / 'data'}",
                "-cp",
                os.pathsep.join(str(p) for p in [runtime_classes, selected_jar, *selected_jars]),
                "GdalReducedProfileSmoke",
                str(selected_jar),
                mode,
                str(native_resources_file),
                str(expected_resources_file),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"GdalReducedProfileSmoke {phase} failed ({proc.returncode}):\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        combined = f"{proc.stdout}\n{proc.stderr}"
        phase_results[phase] = audit_phase_output(
            phase, mode, combined, library_path, native_manifest, forbidden_paths
        )

    shader_entry = next(
        item["destination"]
        for item in resource_lock["files"]
        if Path(item["destination"]).name.startswith("gl20fp_")
    )
    omitted_shader_jar = output_dir / "negative-only-missing-shader-no-native.jar"
    negative_entries = {
        k: v
        for k, v in entries.items()
        if k != shader_entry and not k.startswith("libs/linux64/")
    }
    negative_entries["META-INF/MANIFEST.MF"] = (
        b"Manifest-Version: 1.0\n"
        b"Implementation-Title: Negative-only omitted-shader catalog fixture; no native payloads\n\n"
    )
    deterministic_jar_write(omitted_shader_jar, negative_entries)

    neg_res = output_dir / "negative-resource"
    for sub in ("home", "data", "tmp"):
        (neg_res / sub).mkdir(parents=True, exist_ok=True)
    neg_shader_proc = subprocess.run(
        [
            str(java_bin),
            "-Xmx64m",
            "-Djava.awt.headless=true",
            f"-Duser.home={neg_res / 'home'}",
            f"-Djava.io.tmpdir={neg_res / 'tmp'}",
            f"-DTAK_APP_DATA_PATH={neg_res / 'data'}",
            "-cp",
            os.pathsep.join(str(p) for p in [runtime_classes, omitted_shader_jar, *dep_jars]),
            "RuntimeResourcesSmoke",
            str(omitted_shader_jar),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if (
        neg_shader_proc.returncode != 0
        or "PASS_GENUINE_MISSING_SHADER_CATALOG_REJECTION" not in neg_shader_proc.stdout
    ):
        raise RuntimeError(
            f"missing-shader negative control failed ({neg_shader_proc.returncode}): "
            f"{neg_shader_proc.stdout}\n{neg_shader_proc.stderr}"
        )

    missing_native_jar = output_dir / "missing-resource.jar"
    deterministic_jar_write(
        missing_native_jar,
        {k: v for k, v in entries.items() if k != "libs/linux64/libGLdispatch.so"},
    )
    neg_app = output_dir / "negative"
    (neg_app / "home").mkdir(parents=True, exist_ok=True)
    (neg_app / "data").mkdir(parents=True, exist_ok=True)
    neg_native_proc = subprocess.run(
        [
            str(java_bin),
            "-Xcheck:jni",
            "-Xmx256m",
            "-Djava.awt.headless=true",
            f"-Djava.io.tmpdir={neg_app}",
            f"-Duser.home={neg_app / 'home'}",
            f"-DTAK_APP_DATA_PATH={neg_app / 'data'}",
            "-cp",
            os.pathsep.join(str(p) for p in [runtime_classes, missing_native_jar, *dep_jars]),
            "StockLoaderSmoke",
            str(missing_native_jar),
            "missing",
            str(native_resources_file),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if (
        neg_native_proc.returncode != 0
        or "PASS_REAL_STOCK_LOADER_MISSING_REQUIRED_RESOURCE" not in neg_native_proc.stdout
    ):
        raise RuntimeError(
            f"missing-resource negative control failed ({neg_native_proc.returncode}): "
            f"{neg_native_proc.stdout}\n{neg_native_proc.stderr}"
        )

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "artifact": {
            "name": jar_name,
            "sha256": _sha256_file(artifact),
            "md5": jar_md5,
            "bytes": artifact.stat().st_size,
            "class_count": len(merged_classes),
            "replaced_class_count": len(replacement_classes),
            "runtime_resource_count": len(resource_lock["files"]),
            "native_provider_count": len(native_manifest),
            "native_resources": native_manifest,
            "third_party_jar_count": len(third_party_records),
            "notices_count": len(notices_inventory),
        },
        "runtime": {
            "xcheck_jni_clean": True,
            "phases": phase_results,
            "negative_controls": {
                "missing_shader_catalog_rejection": shader_entry,
                "missing_required_native_resource": "libs/linux64/libGLdispatch.so",
            },
        },
        "scope": {
            "component_runtime_acceptance": True,
            "stock_resource_loader_controls_passed": True,
            "reduced_gdal_profile_qualified": True,
            "missing_resource_negative_control": True,
            "missing_shader_catalog_negative_control": True,
            "full_sdk_acceptance": False,
            "renderer_acceptance": False,
            "client_acceptance": False,
            "security_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-sdk-package-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--atak-source", type=Path)
    parser.add_argument("--resource-payloads", type=Path)
    parser.add_argument("--caches-root", type=Path)
    parser.add_argument("--gdal-jni-dir", type=Path)
    parser.add_argument("--kernel-java-dir", type=Path)
    parser.add_argument("--kernel-native-dir", type=Path)
    parser.add_argument("--java-home", type=Path, default=Path("/usr/lib/jvm/java-17-openjdk-amd64"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    required = (
        ("--atak-source", args.atak_source),
        ("--resource-payloads", args.resource_payloads),
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
        resource_payloads=args.resource_payloads,
        caches_root=args.caches_root,
        gdal_jni_dir=args.gdal_jni_dir,
        kernel_java_dir=args.kernel_java_dir,
        kernel_native_dir=args.kernel_native_dir,
        java_home=args.java_home,
        output_dir=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
