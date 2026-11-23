#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX Kernel 8.30.5 native libraries."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import audit
import providers
import sources

SCHEMA = "uds-takx-kernel-native-hosted-v1"
EXPECTED_LOCK_SHA = "92aa9ad3cb9674f9d18166bca1878eb12ed227eb706e63d05526113d981dbade"
HOSTED_CMAKE = HERE / "hosted_CMakeLists.txt"
LOCK_FILE = HERE / "inputs.lock.json"
SQLCIPHER_MAP = HERE / "core-sqlcipher.map"
CONTROL_JAVA_FILES = (
    HERE / "KernelRuntimeSmoke.java",
    HERE / "NativeDatabaseCandidate.java",
    HERE / "KernelThrowingLoggerCandidate.java",
)

CESIUM_ARCHIVES = (
    "libCesiumUtility.a",
    "libCesiumGltf.a",
    "libCesiumGeometry.a",
    "libCesiumGeospatial.a",
    "libCesiumJsonReader.a",
    "libCesiumJsonWriter.a",
    "libCesiumGltfContent.a",
    "libCesiumGltfReader.a",
    "libCesiumGltfWriter.a",
    "libCesiumAsync.a",
    "libCesium3DTiles.a",
    "libCesium3DTilesReader.a",
    "libCesium3DTilesWriter.a",
    "libCesium3DTilesContent.a",
    "libCesiumRasterOverlays.a",
    "libCesium3DTilesSelection.a",
    "libCesiumIonClient.a",
    "libCesiumQuantizedMeshTerrain.a",
)

RUNTIME_PHASES = (
    ("basic", ("PASS_KERNEL_JNI_MATRIX_POINT_WKB_NATIVE_PROJECTION",)),
    (
        "database",
        (
            "PASS_PREPARED_NATIVE_DATABASE2_ENCRYPTED_OPEN_REOPEN_WRONG_KEY_CONTROL",
            "PASS_REAL_GDAL_MEM_EXACT_WRITE_READ",
        ),
    ),
    ("logger", ("PASS_REAL_THROWING_LOGGER_BASELINE_REJECTION_MATCH_AND_CORRECT_KEY_RECOVERY",)),
)
REQUIRED_RUNTIME_FOOTER = "PASS_SCOPED_KERNEL_NATIVE_CALLS_NO_RENDERER_OR_CLIENT_CLAIM"
REQUIRED_SQLITE_BRIDGE = {"sqlite3_config", "sqlite3_errstr"}

CORE_REQUIRED_NEEDED = {"libGLESv2.so.2", "libgdal.so", "liblas_c.so.3"}
CORE_ALLOWED_NEEDED = CORE_REQUIRED_NEEDED | {"liblas.so.3"}
JNI_REQUIRED_NEEDED = {"libtakengine.so", "libGLESv2.so.2", "libgdal.so"}
JNI_ALLOWED_NEEDED = JNI_REQUIRED_NEEDED


def verify_offline() -> dict:
    """Validate frozen Kernel native recipe files and hosted qualification inputs."""
    required = [
        HOSTED_CMAKE,
        LOCK_FILE,
        SQLCIPHER_MAP,
        *CONTROL_JAVA_FILES,
    ]
    for path in required:
        if not path.is_file():
            raise FileNotFoundError(f"missing required kernel-native hosted input: {path}")

    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    lock_digest = hashlib.sha256(json.dumps(lock, sort_keys=True).encode()).hexdigest()
    if lock_digest != EXPECTED_LOCK_SHA:
        raise ValueError(f"unexpected inputs.lock.json canonical digest: {lock_digest}")

    for name, sha in lock["locks"].items():
        if providers.digest(providers.regular(HERE / name)) != sha:
            raise ValueError(f"locked source inventory mismatch: {name}")
    for name, entry in lock["patches"].items():
        if providers.digest(providers.regular(HERE / "patches" / name)) != entry["sha256"]:
            raise ValueError(f"locked patch mismatch: {name}")
    if providers.digest(providers.regular(SQLCIPHER_MAP)) != lock["sqlcipher_map_sha256"]:
        raise ValueError("core-sqlcipher.map digest mismatch")

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": lock["revision"],
        "source_counts": lock["source_counts"],
        "hosted_cmake_sha256": providers.digest(HOSTED_CMAKE),
        "inputs_lock_sha256": lock_digest,
        "sqlcipher_map_sha256": lock["sqlcipher_map_sha256"],
    }


def _defined_nm_names(nm_output: str) -> set[str]:
    names: set[str] = set()
    for line in nm_output.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-2] not in ("U", "u"):
            names.add(parts[-1].split("@", 1)[0])
    return names


def audit_elf_outputs(
    core_so: Path,
    jni_so: Path,
    core_dynsym: str,
    core_dyn: str,
    jni_dynsym: str,
    jni_dyn: str,
    jni_nm: str,
    archive_definitions: dict[str, set[str]],
) -> dict:
    """Enforce SQLCipher ownership, hidden SSL/crypto/Expat, zero TEXTREL, and NEEDED bounds."""
    for label in ("ssl", "crypto", "expat", "sqlite"):
        if not archive_definitions.get(label):
            raise ValueError(f"empty reference archive symbol set: {label}")

    specs = (
        ("Core", core_so, core_dynsym, core_dyn, CORE_REQUIRED_NEEDED, CORE_ALLOWED_NEEDED),
        ("JNI", jni_so, jni_dynsym, jni_dyn, JNI_REQUIRED_NEEDED, JNI_ALLOWED_NEEDED),
    )
    elfs: dict[str, dict] = {}
    for name, path, dynsym, dynamic, req_needed, allow_needed in specs:
        exports, imports, all_names = audit.symbols(dynsym)
        for family in ("ssl", "crypto", "expat"):
            leaked = sorted(all_names & archive_definitions[family])
            if leaked:
                raise ValueError(f"{name} leaked {family} symbols in dynsym: {leaked[:5]}")

        sql_exports = exports & archive_definitions["sqlite"]
        sql_imports = imports & archive_definitions["sqlite"]
        if name == "Core":
            if sql_exports != REQUIRED_SQLITE_BRIDGE or sql_imports:
                raise ValueError(
                    f"Core SQLCipher boundary mismatch: exports={sorted(sql_exports)} imports={sorted(sql_imports)}"
                )
        else:
            if sql_exports or sql_imports != REQUIRED_SQLITE_BRIDGE:
                raise ValueError(
                    f"JNI SQLCipher boundary mismatch: exports={sorted(sql_exports)} imports={sorted(sql_imports)}"
                )
            jni_defined = _defined_nm_names(jni_nm)
            second_sqlite = sorted(
                sym for sym in jni_defined if sym.startswith(("sqlite3_", "sqlcipher"))
            )
            if second_sqlite:
                raise ValueError(f"second SQLite implementation in JNI: {second_sqlite[:5]}")

        if "TEXTREL" in dynamic:
            raise ValueError(f"unexpected TEXTREL in {name} ({path.name})")
        needed = set(re.findall(r"\(NEEDED\).*?\[(.*?)\]", dynamic))
        if not req_needed <= needed:
            raise ValueError(f"{name} missing required NEEDED entries: {sorted(req_needed - needed)}")
        bad_needed = [
            entry
            for entry in needed
            if entry not in allow_needed and not audit.SYSTEM.fullmatch(entry)
        ]
        if bad_needed:
            raise ValueError(f"{name} has unreviewed NEEDED entries: {sorted(bad_needed)}")

        elfs[name] = {
            "path": path.name,
            "exported_dynamic_symbols": len(exports),
            "sqlcipher_exports": sorted(sql_exports),
            "sqlcipher_imports": sorted(sql_imports),
            "needed": sorted(needed),
        }

    return {
        "elfs": elfs,
        "hidden_crypto_ssl_expat": True,
        "shared_sqlcipher_owner": True,
        "second_jni_sqlite_body": False,
    }


def parse_runtime_phase_output(
    phase: str,
    stdout: str,
    stderr: str,
    required_tokens: tuple[str, ...],
    bound_hashes: dict[str, str],
) -> dict:
    """Validate -Xcheck:jni cleanliness, phase markers, logger bounds, and /proc/self/maps ELFs."""
    combined = f"{stdout}\n{stderr}"
    diags = audit.strict_jni_diagnostics(combined)
    if diags or "UnsatisfiedLinkError" in combined:
        raise ValueError(f"strict -Xcheck:jni diagnostic in phase {phase}: {diags[:3]}")
    for token in (*required_tokens, REQUIRED_RUNTIME_FOOTER):
        if token not in stdout:
            raise ValueError(f"missing runtime marker {token} in phase {phase}")

    logger_callbacks: int | None = None
    if phase == "logger":
        match = re.search(
            r"LOGGER_CALLBACKS=(\d+) BASELINE_WRONG_KEY=(\S+) LISTENER_WRONG_KEY=(\S+)",
            stdout,
        )
        if not match or int(match.group(1)) < 1 or match.group(2) != match.group(3):
            raise ValueError("throwing logger boundary mismatch")
        logger_callbacks = int(match.group(1))

    mapped = {
        path_str: sha
        for sha, path_str in re.findall(r"^ELF ([0-9a-f]{64}) (/[^\r\n]+)$", stdout, re.MULTILINE)
    }
    for mapped_path in mapped:
        basename = Path(mapped_path).name
        if basename.startswith(("libcrypto.so", "libssl.so", "libsqlite3.so")):
            raise ValueError(f"forbidden shared crypto/sqlite library mapped: {mapped_path}")
    for expected_path, expected_sha in bound_hashes.items():
        if mapped.get(expected_path) != expected_sha:
            raise ValueError(
                f"expected mapped ELF {expected_path} ({expected_sha[:12]}) absent or hash-mismatched"
            )

    result: dict = {
        "required_controls": list(required_tokens),
        "strict_jni_warnings": 0,
        "mapped_elf_count": len(mapped),
    }
    if logger_callbacks is not None:
        result["logger_callbacks"] = logger_callbacks
    return result


def _clean_build_env(java_home: Path) -> dict[str, str]:
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
        if k not in removed and not k.startswith(("LD_", "CMAKE_", "PKG_CONFIG", "BASH_FUNC_", "GDAL_", "PROJ_"))
    }
    env.update(
        JAVA_HOME=str(java_home),
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
    return env


def _clean_jvm_env(java_home: Path, gdal_data: Path) -> dict[str, str]:
    env = _clean_build_env(java_home)
    env["GDAL_DATA"] = str(gdal_data)
    return env


def normalize_source_command(argv: list[str]) -> list[str]:
    """Translate --no-lazy-fetch for Git versions expecting GIT_NO_LAZY_FETCH=1."""
    return [arg for arg in argv if arg != "--no-lazy-fetch"]


def _resolve_regular(candidates: tuple[Path, ...], label: str) -> Path:
    for cand in candidates:
        if cand.exists():
            resolved = cand.resolve()
            if resolved.is_symlink() or not resolved.is_file():
                raise ValueError(f"resolved path for {label} is not a regular file: {resolved}")
            return resolved
    raise FileNotFoundError(f"missing {label}: tried {[str(c) for c in candidates]}")


def _collect_archive_definitions(caches_root: Path) -> dict[str, set[str]]:
    archive_paths = {
        "ssl": caches_root / "openssl/lib/libssl.a",
        "crypto": caches_root / "openssl/lib/libcrypto.a",
        "sqlite": caches_root / "sqlcipher/lib/libsqlite3.a",
        "expat": caches_root / "expat/lib/libexpat.a",
    }
    defs: dict[str, set[str]] = {}
    for key, archive in archive_paths.items():
        if not archive.is_file():
            raise FileNotFoundError(f"missing reference archive {archive}")
        nm_out = subprocess.check_output(["nm", "--defined-only", str(archive)], text=True)
        defs[key] = _defined_nm_names(nm_out)
    return defs


def qualify(
    atak_source: Path,
    gltf_cache: Path,
    minizip_include: Path,
    caches_root: Path,
    gdal_jni_dir: Path,
    kernel_java_dir: Path,
    java_home: Path,
    output_dir: Path,
    jobs: int,
) -> dict:
    """Stage locked 8.30.5 sources, compile Core+JNI against GHCR caches, audit ELFs, run -Xcheck:jni."""
    verify_info = verify_offline()
    lock = json.loads(LOCK_FILE.read_text(encoding="utf-8"))

    atak_source = atak_source.resolve()
    gltf_cache = gltf_cache.resolve()
    minizip_include = minizip_include.resolve()
    caches_root = caches_root.resolve()
    gdal_jni_dir = gdal_jni_dir.resolve()
    kernel_java_dir = kernel_java_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    cesium_lib_dir = caches_root / "cesium/lib"
    for archive_name in CESIUM_ARCHIVES:
        if not (cesium_lib_dir / archive_name).is_file():
            raise FileNotFoundError(f"missing Cesium Layer-2 archive: {cesium_lib_dir / archive_name}")

    build_env = _clean_build_env(java_home)

    def run_git_step(argv: list[str], _label: str, timeout: int) -> bytes:
        return subprocess.check_output(normalize_source_command(argv), env=build_env, timeout=timeout)

    sources.project(atak_source, output_dir, lock, run_git_step, git="git")
    sources.gltf_headers(gltf_cache, output_dir)

    cmake_parent = output_dir / "cmake-parent"
    cmake_parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HOSTED_CMAKE, cmake_parent / "CMakeLists.txt")

    jni_headers = kernel_java_dir / "jni-headers"
    if not jni_headers.is_dir():
        raise FileNotFoundError(f"missing generated JNI headers directory: {jni_headers}")

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
            f"-DKERNEL_LAYOUT={output_dir / 'layout'}",
            f"-DJNI_HEADERS={jni_headers}",
            f"-DGLTF={output_dir / 'gltf-headers/tinygltf'}",
            f"-DGLTFLOADER={output_dir / 'gltf-headers/tinygltfloader'}",
            f"-DGLES_EXTRA={output_dir / 'gles1-declarations'}",
            f"-DMINIZIP_INCLUDE={minizip_include}",
            f"-DCORE_SQLCIPHER_MAP={SQLCIPHER_MAP}",
        ],
        env=build_env,
        check=True,
        timeout=300,
    )

    compile_db = json.loads((build_dir / "compile_commands.json").read_text(encoding="utf-8"))
    core_prefix = f"{(output_dir / 'layout/takkernel/engine/src/main/cpp').as_posix()}/"
    jni_prefix = f"{(output_dir / 'layout/takkernel/engine/src/main/jni').as_posix()}/"
    compiled_counts = {
        "core": len({entry["file"] for entry in compile_db if entry["file"].startswith(core_prefix)}),
        "jni": len({entry["file"] for entry in compile_db if entry["file"].startswith(jni_prefix)}),
    }
    expected_counts = {"core": lock["source_counts"]["core"], "jni": lock["source_counts"]["jni"]}
    if compiled_counts != expected_counts:
        raise ValueError(f"Kernel translation unit count mismatch: {compiled_counts} != {expected_counts}")

    subprocess.run(
        ["ninja", "-C", str(build_dir), f"-j{jobs}", "takengine", "takenginejni"],
        env=build_env,
        check=True,
        timeout=1500,
    )

    core_so = _resolve_regular((build_dir / "kernel-cpp/libtakengine.so",), "libtakengine.so")
    jni_so = _resolve_regular((build_dir / "kernel-jni/libtakenginejni.so",), "libtakenginejni.so")

    archive_defs = _collect_archive_definitions(caches_root)
    elf_audit = audit_elf_outputs(
        core_so=core_so,
        jni_so=jni_so,
        core_dynsym=subprocess.check_output(["readelf", "--dyn-syms", "--wide", str(core_so)], text=True),
        core_dyn=subprocess.check_output(["readelf", "-d", str(core_so)], text=True),
        jni_dynsym=subprocess.check_output(["readelf", "--dyn-syms", "--wide", str(jni_so)], text=True),
        jni_dyn=subprocess.check_output(["readelf", "-d", str(jni_so)], text=True),
        jni_nm=subprocess.check_output(["nm", "--defined-only", str(jni_so)], text=True),
        archive_definitions=archive_defs,
    )
    elf_audit["elfs"]["Core"]["sha256"] = providers.digest(core_so)
    elf_audit["elfs"]["Core"]["bytes"] = core_so.stat().st_size
    elf_audit["elfs"]["JNI"]["sha256"] = providers.digest(jni_so)
    elf_audit["elfs"]["JNI"]["bytes"] = jni_so.stat().st_size

    classes_root = kernel_java_dir / "classes"
    classpath_dir = kernel_java_dir / "classpath"
    if not classes_root.is_dir() or not classpath_dir.is_dir():
        raise FileNotFoundError(f"expected kernel-java classes/ and classpath/ under {kernel_java_dir}")
    jars = sorted(classpath_dir.glob("*.jar"))
    if not jars:
        raise FileNotFoundError(f"no JARs found under {classpath_dir}")

    base_classpath = [classes_root, *jars]
    runtime_classes = output_dir / "runtime-classes"
    runtime_classes.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            str(java_home / "bin/javac"),
            "--release",
            "17",
            "-proc:none",
            "-cp",
            os.pathsep.join(str(p) for p in base_classpath),
            "-d",
            str(runtime_classes),
            *[str(p) for p in CONTROL_JAVA_FILES],
        ],
        check=True,
        timeout=120,
    )

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
    gdal_jni_so = _resolve_regular((gdal_jni_dir / "libgdalalljni.so",), "libgdalalljni.so")

    loads = [gldispatch_so, glesv2_so, las_so, las_c_so, gdal_so, gdal_jni_so, core_so, jni_so]
    bound_hashes = {path.as_posix(): providers.digest(path) for path in loads}
    manifest = output_dir / "runtime-native.sha256"
    manifest.write_text(
        "".join(f"{bound_hashes[p.as_posix()]} {p.as_posix()}\n" for p in loads),
        encoding="utf-8",
    )

    gdal_data = caches_root / "gdal/share/gdal"
    if not gdal_data.is_dir():
        raise FileNotFoundError(f"missing GDAL_DATA directory: {gdal_data}")
    jvm_env = _clean_jvm_env(java_home, gdal_data)
    full_classpath = os.pathsep.join(str(p) for p in [runtime_classes, *base_classpath])

    phase_results: dict[str, dict] = {}
    for phase, required_tokens in RUNTIME_PHASES:
        app_dir = output_dir / "runtime" / phase
        (app_dir / "home").mkdir(parents=True, exist_ok=True)
        (app_dir / "data").mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            [
                str(java_home / "bin/java"),
                "-Xcheck:jni",
                "-Xmx256m",
                "-XX:ActiveProcessorCount=2",
                "-Djava.awt.headless=true",
                f"-Duser.home={app_dir / 'home'}",
                f"-Djava.io.tmpdir={app_dir}",
                f"-DTAK_APP_DATA_PATH={app_dir / 'data'}",
                "-cp",
                full_classpath,
                "KernelRuntimeSmoke",
                str(manifest),
                phase,
            ],
            check=False,
            env=jvm_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"KernelRuntimeSmoke {phase} failed ({proc.returncode}):\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        phase_results[phase] = parse_runtime_phase_output(
            phase, proc.stdout, proc.stderr, required_tokens, bound_hashes
        )

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verify_info,
        "translation_units": {
            **compiled_counts,
            "cesium_prebuilt_archives": len(CESIUM_ARCHIVES),
            "cesium_layer2_tus": lock["source_counts"]["cesium"],
            "total_covered": sum(compiled_counts.values()) + lock["source_counts"]["cesium"],
        },
        "elf_audit": elf_audit,
        "runtime": {
            "xcheck_jni_clean": True,
            "bound_native_providers": bound_hashes,
            "phases": phase_results,
        },
        "scope": {
            "kernel_native_core_jni_built": True,
            "elf_boundary_audit_passed": True,
            "xcheck_jni_controls_passed": True,
            "stock_engine_resource_loader_acceptance": False,
            "renderer_acceptance": False,
            "commoncommo_acceptance": False,
            "full_sdk_acceptance": False,
            "client_acceptance": False,
            "security_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-kernel-native-receipt.json"
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
    parser.add_argument("--java-home", type=Path)
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
        ("--java-home", args.java_home),
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
        java_home=args.java_home,
        output_dir=args.output,
        jobs=args.jobs,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
