#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 runner for GDAL 2.4.4 JNI and exception controls under -Xcheck:jni."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "uds-takx-native-gdal-jni-hosted-v1"
WRAPPERS = (
    "gdal_wrap.cpp",
    "gdalconst_wrap.c",
    "ogr_wrap.cpp",
    "osr_wrap.cpp",
    "gnm_wrap.cpp",
)
HEADER_SUBDIRS = ("apps", "gnm", "gcore", "alg", "ogr", "ogr/ogrsf_frmts", "port")
STATIC_DEP_ARCHIVES = (
    "libproj.a",
    "libkmlengine.a",
    "libkmldom.a",
    "libkmlbase.a",
    "libkmlconvenience.a",
    "libkmlregionator.a",
    "libkmlxsd.a",
    "libminizip.a",
    "liburiparser.a",
    "libexpat.a",
    "libz.a",
)

PROBE_JAVA = HERE / "GdalJniProbe.java"
EXCEPTION_CONTROLS_JAVA = HERE.parent / "gis-java/GdalExceptionControls.java"
BUILD_SCRIPT = HERE / "build.py"

PROBE_MARKER = "GDAL_JNI_VERSION_REGISTER_MEM_EXACT_WGS84_INTERRUPT_MISSING_FILE=true"
EXCEPTION_MARKER = "PASS_REAL_GDAL_ENUMERATION_PROGRESS_ORIGINAL_THROWABLE_CONTROLS"
JNI_WARNING_MARKERS = ("WARNING in native method:", "FATAL ERROR in native method:")

REQUIRED_FILES = (BUILD_SCRIPT, PROBE_JAVA, EXCEPTION_CONTROLS_JAVA)
GENERATED_EXCEPTION_MAIN = (
    "import org.gdal.gdal.gdal;\n"
    "public final class GdalExceptionMain {\n"
    "  public static void main(String[] args) {\n"
    "    gdal.AllRegister();\n"
    "    GdalExceptionControls.run();\n"
    "  }\n"
    "}\n"
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
            raise FileNotFoundError(f"missing required native-gdal-jni control input: {path}")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "probe_java_sha256": sha256_file(PROBE_JAVA),
        "exception_controls_sha256": sha256_file(EXCEPTION_CONTROLS_JAVA),
        "build_script_sha256": sha256_file(BUILD_SCRIPT),
    }


def _defined_symbol_names(nm_output: str) -> set[str]:
    names: set[str] = set()
    for line in nm_output.splitlines():
        parts = line.split()
        if parts:
            names.add(parts[-1].split("@", 1)[0])
    return names


def audit_elf_shared_provider(
    libgdal: Path,
    libjni: Path,
    gdal_dyn: str,
    gdal_syms: str,
    jni_dyn: str,
    jni_syms: str,
) -> None:
    if "TEXTREL" in gdal_dyn:
        raise ValueError(f"unexpected TEXTREL in {libgdal.name}")
    if "TEXTREL" in jni_dyn:
        raise ValueError(f"unexpected TEXTREL in {libjni.name}")
    needed_lines = [line for line in jni_dyn.splitlines() if "(NEEDED)" in line]
    if not any("libgdal.so" in line for line in needed_lines):
        raise ValueError(f"{libjni.name} does not declare NEEDED libgdal.so")

    expat_exports = sorted(name for name in _defined_symbol_names(gdal_syms) if name.startswith("XML_"))
    if expat_exports:
        raise ValueError(f"private Expat symbols escaped {libgdal.name}: {expat_exports[:5]}")
    if "GDALVersionInfo" in _defined_symbol_names(jni_syms):
        raise ValueError(f"{libjni.name} defines GDALVersionInfo instead of using shared {libgdal.name}")


def _check_clean_jni_output(combined: str) -> None:
    for marker in JNI_WARNING_MARKERS:
        if marker in combined:
            raise ValueError(f"strict -Xcheck:jni violation ({marker})")


def parse_probe_output(stdout: str, stderr: str, libgdal: Path, libjni: Path) -> list[str]:
    _check_clean_jni_output(f"{stdout}\n{stderr}")
    if PROBE_MARKER not in stdout:
        raise ValueError("missing GdalJniProbe pass marker in stdout")
    mapped = sorted(set(re.findall(r"^ELF (/[^\n]+)$", stdout, re.MULTILINE)))
    expected_gdal = libgdal.resolve().as_posix()
    expected_jni = libjni.resolve().as_posix()
    if expected_gdal not in mapped or expected_jni not in mapped:
        raise ValueError(f"expected both {expected_gdal} and {expected_jni} in mapped ELFs")
    return mapped


def parse_exception_output(stdout: str, stderr: str) -> None:
    _check_clean_jni_output(f"{stdout}\n{stderr}")
    if EXCEPTION_MARKER not in stdout:
        raise ValueError("missing GdalExceptionControls pass marker in stdout")


def _clean_jvm_env(gdal_data: Path) -> dict[str, str]:
    removed = {"JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "CLASSPATH"}
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in removed and not k.startswith(("LD_", "GDAL_", "PROJ_"))
    }
    env["GDAL_DATA"] = str(gdal_data)
    env["LC_ALL"] = "C.UTF-8"
    return env


def _run_jvm(
    java_bin: Path,
    lib_dir: Path,
    classpath: str,
    main_class: str,
    env: dict[str, str],
) -> tuple[str, str]:
    proc = subprocess.run(
        [
            str(java_bin),
            "-Xcheck:jni",
            "-XX:ActiveProcessorCount=2",
            "-Xmx256m",
            "-Djava.awt.headless=true",
            f"-Djava.library.path={lib_dir}",
            "-cp",
            classpath,
            main_class,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        tail = (proc.stdout + "\n" + proc.stderr)[-2000:]
        raise RuntimeError(f"{main_class} failed (exit={proc.returncode}): {tail}")
    return proc.stdout, proc.stderr


def qualify(
    gis_build: Path,
    deps_prefix: Path,
    java_home: Path,
    output_dir: Path,
) -> dict:
    verified = verify_offline()
    gis_build = gis_build.resolve()
    deps_prefix = deps_prefix.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    libgdal_a = deps_prefix / "lib/libgdal.a"
    gdal_h = deps_prefix / "include/gdal.h"
    gdal_data = deps_prefix / "share/gdal"
    gdal_jar = gis_build / "gdal-2.4.4-mod-java.jar"
    gdal_source = gis_build / "source/gdal"
    generated_dir = gis_build / "generated"

    dep_archives = [deps_prefix / "lib" / name for name in STATIC_DEP_ARCHIVES]
    for required in (libgdal_a, gdal_h, gdal_jar, *dep_archives):
        if not required.is_file():
            raise FileNotFoundError(f"missing required GDAL JNI input file: {required}")
    for required_dir in (gdal_data, gdal_source, generated_dir):
        if not required_dir.is_dir():
            raise FileNotFoundError(f"missing required GDAL JNI directory: {required_dir}")

    wrapper_paths = [generated_dir / name for name in WRAPPERS]
    for wrapper in wrapper_paths:
        if not wrapper.is_file() or wrapper.stat().st_size == 0:
            raise FileNotFoundError(f"missing or empty SWIG wrapper: {wrapper}")

    output_dir.mkdir(parents=True, exist_ok=True)
    lib_dir = output_dir / "lib"
    obj_dir = output_dir / "obj"
    classes_dir = output_dir / "classes"
    lib_dir.mkdir(exist_ok=True)
    obj_dir.mkdir(exist_ok=True)
    classes_dir.mkdir(exist_ok=True)

    libgdal = lib_dir / "libgdal.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-Wl,--no-undefined",
            "-Wl,-soname,libgdal.so",
            "-Wl,--exclude-libs,libexpat.a",
            "-o",
            str(libgdal),
            "-Wl,--whole-archive",
            str(libgdal_a),
            "-Wl,--no-whole-archive",
            "-Wl,--start-group",
            *[str(a) for a in dep_archives],
            "-Wl,--end-group",
            "-pthread",
            "-lm",
            "-lrt",
            "-ldl",
        ],
        check=True,
    )

    include_flags = [f"-I{deps_prefix / 'include'}"]
    for sub in HEADER_SUBDIRS:
        candidate = gdal_source / sub
        if candidate.is_dir():
            include_flags.append(f"-I{candidate}")
    include_flags.extend([
        f"-I{java_home / 'include'}",
        f"-I{java_home / 'include/linux'}",
    ])

    objects: list[Path] = []
    for wrapper in wrapper_paths:
        compiler = "gcc" if wrapper.suffix == ".c" else "g++"
        obj = obj_dir / f"{wrapper.name}.o"
        objects.append(obj)
        subprocess.run(
            [compiler, "-O2", "-fPIC", *include_flags, "-c", str(wrapper), "-o", str(obj)],
            check=True,
        )

    libjni = lib_dir / "libgdalalljni.so"
    subprocess.run(
        [
            "g++",
            "-shared",
            "-Wl,--no-undefined",
            "-Wl,-soname,libgdalalljni.so",
            "-o",
            str(libjni),
            *[str(o) for o in objects],
            f"-L{lib_dir}",
            "-lgdal",
            "-Wl,-rpath,$ORIGIN",
        ],
        check=True,
    )

    gdal_dyn = subprocess.check_output(["readelf", "-d", str(libgdal)], text=True)
    gdal_syms = subprocess.check_output(["nm", "-D", "--defined-only", str(libgdal)], text=True)
    jni_dyn = subprocess.check_output(["readelf", "-d", str(libjni)], text=True)
    jni_syms = subprocess.check_output(["nm", "-D", "--defined-only", str(libjni)], text=True)
    audit_elf_shared_provider(libgdal, libjni, gdal_dyn, gdal_syms, jni_dyn, jni_syms)

    exc_main_java = output_dir / "GdalExceptionMain.java"
    exc_main_java.write_text(GENERATED_EXCEPTION_MAIN, encoding="utf-8")
    subprocess.run(
        [
            str(java_home / "bin/javac"),
            "--release",
            "17",
            "-cp",
            str(gdal_jar),
            "-d",
            str(classes_dir),
            str(PROBE_JAVA),
            str(EXCEPTION_CONTROLS_JAVA),
            str(exc_main_java),
        ],
        check=True,
    )

    jvm_env = _clean_jvm_env(gdal_data)
    classpath = os.pathsep.join([str(gdal_jar), str(classes_dir)])
    java_bin = java_home / "bin/java"

    probe_out, probe_err = _run_jvm(java_bin, lib_dir, classpath, "GdalJniProbe", jvm_env)
    mapped_elves = parse_probe_output(probe_out, probe_err, libgdal, libjni)

    exc_out, exc_err = _run_jvm(java_bin, lib_dir, classpath, "GdalExceptionMain", jvm_env)
    parse_exception_output(exc_out, exc_err)

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "libraries": {
            "libgdal.so": sha256_file(libgdal),
            "libgdalalljni.so": sha256_file(libjni),
        },
        "mapped_elf_count": len(mapped_elves),
        "scope": {
            "gdal_jni_probe_passed": True,
            "gdal_exception_controls_passed": True,
            "xcheck_jni_clean": True,
            "full_sdk_acceptance": False,
            "client_acceptance": False,
            "security_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-native-gdal-jni-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--gis-build", type=Path)
    parser.add_argument("--deps-prefix", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    missing = [
        flag
        for flag, val in (
            ("--gis-build", args.gis_build),
            ("--deps-prefix", args.deps_prefix),
            ("--java-home", args.java_home),
            ("--output", args.output),
        )
        if val is None
    ]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")
    qualify(args.gis_build, args.deps_prefix, args.java_home, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
