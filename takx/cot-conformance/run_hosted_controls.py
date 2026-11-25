#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 runner for CommonCommo 1.17.2 JNI smokes and CoT conformance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAKX_ROOT = HERE.parent
COMMO_LOCK_PATH = TAKX_ROOT / "native-commoncommo" / "inputs.lock.json"
JNI_PATCH_PATH = TAKX_ROOT / "native-commoncommo" / "0102-jni-exception-boundaries.patch"
PROVENANCE_PATH = HERE / "provenance.json"

SCHEMA = "uds-takx-cot-conformance-hosted-v1"
ATAK_REV = "9f6893dd657feacc35ec5de03dad721c2e44170e"

JNI_HEADERS = {
    "com_atakmap_commoncommo_Commo.h": "jcommo.h",
    "com_atakmap_commoncommo_CoTPointData.h": "jcotpointdata.h",
    "com_atakmap_commoncommo_CloudClient.h": "jcloudclient.h",
    "com_atakmap_commoncommo_IOProviderTester.h": "jnifileioprovider.h",
}

FORBIDDEN_DYNAMIC_NEEDED = (
    "libssl.so",
    "libcrypto.so",
    "libxml2.so",
    "libcurl.so",
    "libngtcp2.so",
    "libprotobuf",
    "libmicrohttpd.so",
)

SMOKE_SOURCES = (
    ("CommonCommoSmoke", TAKX_ROOT / "CommonCommoSmoke.java"),
    ("CommonCommoRoutingSmoke", TAKX_ROOT / "CommonCommoRoutingSmoke.java"),
    ("CommonCommoSignalSmoke", TAKX_ROOT / "CommonCommoSignalSmoke.java"),
    ("CommonCommoThrowingLogger", TAKX_ROOT / "native-commoncommo" / "CommonCommoThrowingLogger.java"),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    commo_lock = json.loads(COMMO_LOCK_PATH.read_text(encoding="utf-8"))
    if commo_lock.get("revision") != ATAK_REV:
        raise ValueError(f"unexpected CommonCommo revision: {commo_lock.get('revision')}")
    jni_patch_spec = commo_lock["jni_patch"]
    got_patch_sha = sha256_file(JNI_PATCH_PATH)
    if got_patch_sha != jni_patch_spec["sha256"]:
        raise ValueError(f"JNI patch SHA-256 mismatch: {got_patch_sha} != {jni_patch_spec['sha256']}")

    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    if provenance.get("revision") != ATAK_REV:
        raise ValueError(f"unexpected provenance revision: {provenance.get('revision')}")
    for rel, spec in provenance.get("files", {}).items():
        target = HERE / rel
        if not target.is_file():
            raise FileNotFoundError(f"missing pinned conformance file: {target}")
        got = sha256_file(target)
        if got != spec["sha256"]:
            raise ValueError(f"provenance SHA-256 mismatch for {rel}: {got} != {spec['sha256']}")

    smoke_digests = {}
    for name, src in SMOKE_SOURCES:
        if not src.is_file():
            raise FileNotFoundError(f"missing smoke source: {src}")
        smoke_digests[name] = sha256_file(src)

    conformance_src = HERE / "ProtocolConformance.java"
    if not conformance_src.is_file():
        raise FileNotFoundError(f"missing {conformance_src}")

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": ATAK_REV,
        "jni_patch_sha256": got_patch_sha,
        "protocol_conformance_sha256": sha256_file(conformance_src),
        "smoke_sources": smoke_digests,
        "provenance_files_verified": len(provenance.get("files", {})),
    }


def check_exported_jni_symbols(dynsym_text: str) -> list[str]:
    exported: list[str] = []
    for line in dynsym_text.splitlines():
        parts = line.split()
        if len(parts) < 8 or not parts[0].endswith(":"):
            continue
        bind, vis, ndx, name = parts[4], parts[5], parts[6], parts[7]
        if ndx == "UND" or bind not in ("GLOBAL", "WEAK") or vis != "DEFAULT":
            continue
        sym = name.split("@", 1)[0]
        if sym in ("_init", "_fini", "__bss_start", "_edata", "_end"):
            continue
        exported.append(sym)
    if not exported:
        raise ValueError("no exported JNI symbols found in libcommoncommojni.so")
    disallowed = [
        sym for sym in exported
        if sym not in ("JNI_OnLoad", "JNI_OnUnload") and not sym.startswith("Java_com_atakmap_commoncommo_")
    ]
    if disallowed:
        raise ValueError(f"unexpected exported JNI symbols: {disallowed[:10]}")
    return sorted(set(exported))


def check_dynamic_section(dynamic_text: str) -> None:
    if "TEXTREL" in dynamic_text:
        raise ValueError("unexpected TEXTREL in libcommoncommojni.so")
    for forbidden in FORBIDDEN_DYNAMIC_NEEDED:
        if forbidden in dynamic_text:
            raise ValueError(f"forbidden dynamic dependency in libcommoncommojni.so: {forbidden}")


def run_cmd(argv: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: int = 120) -> str:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"stdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc.stdout + proc.stderr


def qualify(atak_source: Path, deps_prefix: Path, output: Path) -> dict:
    verified = verify_offline()
    commo_lock = json.loads(COMMO_LOCK_PATH.read_text(encoding="utf-8"))
    jni_patch_spec = commo_lock["jni_patch"]

    java_home = Path(os.environ.get("JAVA_HOME", "/usr/lib/jvm/java-17-openjdk-amd64"))
    javac = java_home / "bin" / "javac"
    jar_bin = java_home / "bin" / "jar"
    java = java_home / "bin" / "java"
    for tool in (javac, jar_bin, java):
        if not tool.is_file():
            raise FileNotFoundError(f"missing JDK 17 tool: {tool}")

    output.mkdir(parents=True, exist_ok=True)
    work_commo = output / "commoncommo"
    if work_commo.exists():
        shutil.rmtree(work_commo)
    shutil.copytree(atak_source / "commoncommo", work_commo)

    sources_lock = json.loads((TAKX_ROOT / "sources.lock.json").read_text(encoding="utf-8"))
    lock_patches = {
        Path(entry["path"]).name: entry["sha256"]
        for entry in sources_lock.get("patches", [])
        if Path(entry["path"]).name.startswith(("0100-", "0101-"))
    }
    for patch_name in sorted(lock_patches):
        patch_file = TAKX_ROOT / "patches" / patch_name
        got = sha256_file(patch_file)
        if got != lock_patches[patch_name]:
            raise ValueError(f"source patch SHA-256 mismatch for {patch_name}: {got} != {lock_patches[patch_name]}")
        run_cmd(
            ["patch", "--fuzz=0", "--batch", "--no-backup-if-mismatch", "-p1", "-i", str(patch_file)],
            cwd=output,
        )

    for rel, expected in jni_patch_spec["files"].items():
        got = sha256_file(work_commo / rel)
        if got != expected["before"]:
            raise ValueError(f"pre-patch SHA-256 mismatch for {rel}: {got} != {expected['before']}")
    run_cmd(["patch", "--fuzz=0", "--batch", "--no-backup-if-mismatch", "-p1", "-i", str(JNI_PATCH_PATH)], cwd=work_commo)
    for rel, expected in jni_patch_spec["files"].items():
        got = sha256_file(work_commo / rel)
        if got != expected["after"]:
            raise ValueError(f"post-patch SHA-256 mismatch for {rel}: {got} != {expected['after']}")

    patched_commo_a = output / "libcommoncommo.a"
    shutil.copyfile(deps_prefix / "lib" / "libcommoncommo.a", patched_commo_a)
    core_impl = work_commo / "core" / "impl"
    core_cxx_flags = [
        "g++",
        "-std=c++11",
        "-O2",
        "-fPIC",
        "-Wall",
        "-Werror",
        "-D__STDC_FORMAT_MACROS",
        "-DCURL_STATICLIB",
        "-fstack-protector-all",
        f"-I{work_commo / 'core' / 'include'}",
        f"-I{deps_prefix / 'include'}",
        f"-I{deps_prefix / 'include' / 'libxml2'}",
    ]
    patched_objs: list[str] = []
    for name in ("commo.cpp", "contactmanager.cpp", "streamingsocketmanagement.cpp"):
        src = core_impl / name
        obj = src.with_suffix(".o")
        run_cmd([*core_cxx_flags, "-c", str(src), "-o", str(obj)], cwd=core_impl, timeout=90)
        patched_objs.append(str(obj))
    run_cmd(["ar", "rcs", str(patched_commo_a), *patched_objs], timeout=60)

    jni_root = work_commo / "jni"
    java_classes = output / "jcommoncommo-classes"
    jni_headers = jni_root / "native" / "headers"
    java_classes.mkdir(parents=True, exist_ok=True)
    jni_headers.mkdir(parents=True, exist_ok=True)

    java_sources = sorted(
        str(p)
        for p in (jni_root / "com" / "atakmap" / "commoncommo").rglob("*.java")
        if p.name != "ExternalNativeInitializer.java"
    )
    if not java_sources:
        raise RuntimeError("no CommonCommo Java sources found")

    run_cmd(
        [
            str(javac),
            "--release",
            "17",
            "-parameters",
            "-proc:none",
            "-h",
            str(jni_headers),
            "-d",
            str(java_classes),
            *java_sources,
        ],
        timeout=90,
    )

    jar_path = output / "jcommoncommo.jar"
    run_cmd([str(jar_bin), "cf", str(jar_path), "-C", str(java_classes), "."], timeout=60)

    jni_native = jni_root / "native"
    for generated_name, target_name in JNI_HEADERS.items():
        src_hdr = jni_headers / generated_name
        if not src_hdr.is_file():
            raise FileNotFoundError(f"missing generated JNI header: {src_hdr}")
        shutil.copyfile(src_hdr, jni_native / target_name)

    cpp_files = sorted(jni_native.glob("*.cpp"))
    obj_files: list[str] = []
    cxx_flags = [
        "g++",
        "-std=c++11",
        "-O2",
        "-fPIC",
        "-Wall",
        "-Werror",
        "-Wno-unused",
        "-D__STDC_LIMIT_MACROS",
        "-DCURL_STATICLIB",
        "-fstack-protector-all",
        f"-I{work_commo / 'core' / 'include'}",
        f"-I{deps_prefix / 'include'}",
        f"-I{deps_prefix / 'include' / 'libxml2'}",
        f"-I{java_home / 'include'}",
        f"-I{java_home / 'include' / 'linux'}",
    ]
    for cpp in cpp_files:
        obj = cpp.with_suffix(".o")
        run_cmd([*cxx_flags, "-c", str(cpp), "-o", str(obj)], cwd=jni_native, timeout=90)
        obj_files.append(str(obj))

    export_map = output / "jni-exports.map"
    export_map.write_text(
        "{ global: JNI_OnLoad; JNI_OnUnload; Java_com_atakmap_commoncommo_*; local: *; };\n",
        encoding="utf-8",
    )
    lib_dir = output / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    jni_so = lib_dir / "libcommoncommojni.so"

    static_libs = [
        patched_commo_a,
        *(
            deps_prefix / "lib" / name
            for name in (
                "libcurl.a",
                "libmicrohttpd.a",
                "libxml2.a",
                "libiconv.a",
                "libprotobuf-lite.a",
                "libngtcp2_crypto_quictls.a",
                "libngtcp2.a",
                "libnghttp2.a",
                "libssl.a",
                "libcrypto.a",
                "libz.a",
            )
        ),
    ]
    for lib in static_libs:
        if not lib.is_file():
            raise FileNotFoundError(f"missing prebuilt static dependency: {lib}")

    run_cmd(
        [
            "g++",
            "-shared",
            "-Wl,--hash-style=both",
            "-Wl,--no-undefined",
            "-Wl,--exclude-libs,ALL",
            f"-Wl,--version-script={export_map}",
            "-o",
            str(jni_so),
            *obj_files,
            *[str(p) for p in static_libs],
            "-lpthread",
            "-ldl",
            "-lm",
        ],
        cwd=jni_native,
        timeout=120,
    )

    dynsym_out = run_cmd(["readelf", "--dyn-syms", "--wide", str(jni_so)])
    exported_syms = check_exported_jni_symbols(dynsym_out)
    dynamic_out = run_cmd(["readelf", "-d", str(jni_so)])
    check_dynamic_section(dynamic_out)

    smoke_classes = output / "smoke-classes"
    smoke_classes.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            str(javac),
            "--release",
            "17",
            "-proc:none",
            "-cp",
            str(jar_path),
            "-d",
            str(smoke_classes),
            *[str(src) for _, src in SMOKE_SOURCES],
            str(HERE / "ProtocolConformance.java"),
        ],
        timeout=90,
    )

    classpath = f"{jar_path}{os.pathsep}{smoke_classes}"
    java_base = [
        str(java),
        "-Xmx128m",
        "-XX:MaxMetaspaceSize=128m",
        "-Xcheck:jni",
        f"-Djava.library.path={lib_dir}",
        "-cp",
        classpath,
    ]

    outcomes: dict[str, dict] = {}
    for name, _ in SMOKE_SOURCES:
        out_text = run_cmd([*java_base, name], timeout=90)
        if "WARNING" in out_text or "FATAL ERROR" in out_text:
            raise RuntimeError(f"JNI check warning/fatal in {name}:\n{out_text}")
        outcomes[name] = {
            "passed": True,
            "output_lines": len(out_text.splitlines()),
        }

    maps_file = output / "protocol-conformance-maps.txt"
    conf_text = run_cmd(
        [*java_base, "ProtocolConformance", str(HERE), str(jar_path), str(maps_file)],
        timeout=90,
    )
    if "WARNING" in conf_text or "FATAL ERROR" in conf_text:
        raise RuntimeError(f"JNI check warning/fatal in ProtocolConformance:\n{conf_text}")
    if "actual shutdown returned" not in conf_text:
        raise RuntimeError(f"ProtocolConformance missing completion marker:\n{conf_text}")
    outcomes["ProtocolConformance"] = {
        "passed": True,
        "output_lines": len(conf_text.splitlines()),
    }

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "revision": ATAK_REV,
        "verify": verified,
        "artifacts": {
            "jcommoncommo.jar": sha256_file(jar_path),
            "libcommoncommojni.so": sha256_file(jni_so),
        },
        "exported_jni_symbol_count": len(exported_syms),
        "controls": {
            "jni_exception_boundary_patch_verified": True,
            "strict_jni_symbol_visibility": True,
            "no_textrel_or_shared_crypto_fallback": True,
            "xcheck_jni_clean": True,
            "commoncommo_smokes_passed": True,
            "cot_protocol_conformance_passed": True,
        },
        "outcomes": outcomes,
        "scope": {
            "linux_jdk17_xcheck_jni": True,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    receipt_path = output / "hosted-cot-conformance-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--atak-source", type=Path)
    parser.add_argument("--deps-prefix", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2, sort_keys=True))
        return 0
    if not args.atak_source or not args.deps_prefix or not args.output:
        parser.error("--atak-source, --deps-prefix, and --output are required for --phase qualify")
    receipt = qualify(args.atak_source, args.deps_prefix, args.output)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
