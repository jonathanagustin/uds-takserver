#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux GCC 14 runner for the joined TAK Kernel spatial database controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "inputs.lock.json"
CONSUMER_SRC = HERE / "KernelSpatialDatabase.cpp"

SCHEMA = "uds-takx-kernel-spatial-db-hosted-v1"
REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
KERNEL_SUBDIR = Path("takkernel/engine/src/main/cpp")

KERNEL_SOURCES = (
    "db/SpatiaLiteDB.cpp",
    "db/Database.cpp",
    "db/Cursor.cpp",
    "db/Statement.cpp",
    "port/String.cpp",
    "port/StringBuilder.cpp",
    "port/Platform.cpp",
    "thread/Lock.cpp",
    "thread/Mutex.cpp",
    "thread/Cond.cpp",
    "thread/Monitor.cpp",
    "thread/impl/ThreadImpl_common.cpp",
    "thread/impl/ThreadImpl_libpthread.cpp",
    "util/Blob.cpp",
    "util/Logging2.cpp",
)

STATIC_LIB_NAMES = (
    "libspatialite.a",
    "libxml2.a",
    "libiconv.a",
    "libz.a",
    "libgeos_c.a",
    "libgeos.a",
    "libproj.a",
    "libsqlite3.a",
    "libcrypto.a",
)

FORBIDDEN_RUNTIME_LIBS = (
    "spatialite",
    "sqlite",
    "geos",
    "proj",
    "crypto",
    "ssl",
    "xml2",
    "iconv",
    "charset",
    "libz.so",
)

EXPECTED_PASS_MARKER = "real_kernel_spatial_database_geometry_transform_typed_transaction_reopen=PASS"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("revision") != REVISION:
        raise ValueError(f"unexpected kernel-spatial-db revision: {lock.get('revision')}")
    if not CONSUMER_SRC.is_file():
        raise FileNotFoundError(f"missing {CONSUMER_SRC}")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": REVISION,
        "consumer_sha256": sha256_file(CONSUMER_SRC),
        "lock_sha256": sha256_file(LOCK_PATH),
        "kernel_source_count": len(KERNEL_SOURCES),
    }


def check_runtime_closure(ldd_text: str) -> None:
    if "not found" in ldd_text:
        raise ValueError(f"unresolved runtime library in consumer:\n{ldd_text}")
    lowered = ldd_text.lower()
    for word in FORBIDDEN_RUNTIME_LIBS:
        if word in lowered:
            raise ValueError(f"forbidden host third-party runtime fallback ({word}):\n{ldd_text}")


def check_no_textrel(readelf_dynamic_text: str) -> None:
    if "TEXTREL" in readelf_dynamic_text:
        raise ValueError("unexpected TEXTREL in kernel-spatial-pic.so")


def run_cmd(argv: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
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
    kernel_root = atak_source / KERNEL_SUBDIR
    if not kernel_root.is_dir():
        raise FileNotFoundError(f"missing ATAK Kernel C++ root: {kernel_root}")

    output.mkdir(parents=True, exist_ok=True)
    include_flags = [
        f"-I{deps_prefix / 'include'}",
        f"-I{deps_prefix / 'include' / 'libxml2'}",
        f"-I{kernel_root}",
    ]

    static_libs = [deps_prefix / "lib" / name for name in STATIC_LIB_NAMES]
    for lib in static_libs:
        if not lib.is_file():
            raise FileNotFoundError(f"missing prebuilt static dependency: {lib}")

    objects: list[Path] = []
    source_digests: dict[str, str] = {}
    for index, rel in enumerate(KERNEL_SOURCES):
        src = kernel_root / rel
        if not src.is_file():
            raise FileNotFoundError(f"missing Kernel source unit: {src}")
        source_digests[rel] = sha256_file(src)
        obj = output / f"kernel-{index}.o"
        run_cmd(
            [
                "g++",
                "-std=c++17",
                "-O2",
                "-fPIC",
                "-DSQLITE_HAS_CODEC",
                "-DRTTI_ENABLED",
                *include_flags,
                "-c",
                str(src),
                "-o",
                str(obj),
            ],
            timeout=90,
        )
        objects.append(obj)

    consumer = output / "kernel-spatial-consumer"
    run_cmd(
        [
            "g++",
            "-std=c++17",
            "-O2",
            "-DSQLITE_HAS_CODEC",
            *include_flags,
            str(CONSUMER_SRC),
            *[str(o) for o in objects],
            "-Wl,--start-group",
            *[str(p) for p in static_libs],
            "-Wl,--end-group",
            "-Wl,--no-undefined",
            "-ldl",
            "-pthread",
            "-lm",
            "-o",
            str(consumer),
        ],
        timeout=120,
    )

    db_path = output / "owned-database.sqlite"
    if db_path.exists():
        db_path.unlink()
    consumer_out = run_cmd([str(consumer), str(db_path)], timeout=30)
    if EXPECTED_PASS_MARKER not in consumer_out:
        raise RuntimeError(f"Kernel spatial database control did not pass:\n{consumer_out}")

    pic_so = output / "kernel-spatial-pic.so"
    run_cmd(
        [
            "g++",
            "-shared",
            "-Wl,--whole-archive",
            *[str(o) for o in objects],
            *[str(p) for p in static_libs],
            "-Wl,--no-whole-archive",
            "-Wl,--no-undefined",
            "-ldl",
            "-pthread",
            "-lm",
            "-o",
            str(pic_so),
        ],
        timeout=120,
    )
    check_no_textrel(run_cmd(["readelf", "-d", str(pic_so)]))
    check_runtime_closure(run_cmd(["ldd", str(consumer)]))

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "revision": REVISION,
        "verify": verified,
        "kernel_sources": source_digests,
        "artifacts": {
            "kernel-spatial-consumer": sha256_file(consumer),
            "kernel-spatial-pic.so": sha256_file(pic_so),
        },
        "controls": {
            "real_kernel_spatial_database_control_passed": True,
            "whole_archive_pic_no_undefined": True,
            "no_textrel": True,
            "no_host_third_party_runtime_fallback": True,
        },
        "scope": {
            "linux_gcc14_static_pic_host_abi": True,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    receipt_path = output / "hosted-kernel-spatial-db-receipt.json"
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
