#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux GCC 14 runner for libLAS 1.8.2 / LASzip 3.4.3 decoder, seek, and ASAN/UBSAN controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "inputs.lock.json"
PROBE_SRC = HERE / "LibLASSourceProbe.cpp"
FIXTURE_HEX = HERE / "TruncatedLAZ.hex"

SCHEMA = "uds-takx-native-las-hosted-v1"
REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
EXPECTED_MARKER = "immutable_original_truncated_laz_rejected_by_cpp_and_c=true"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("revision") != REVISION:
        raise ValueError(f"unexpected native-las revision: {lock.get('revision')}")
    for path in (PROBE_SRC, FIXTURE_HEX):
        if not path.is_file():
            raise FileNotFoundError(f"missing {path}")
    fixture_bytes = bytes.fromhex(FIXTURE_HEX.read_text(encoding="utf-8"))
    got_fixture_sha = hashlib.sha256(fixture_bytes).hexdigest()
    if got_fixture_sha != lock["malformed_fixture_sha256"]:
        raise ValueError(
            f"TruncatedLAZ.hex SHA-256 mismatch: {got_fixture_sha} != {lock['malformed_fixture_sha256']}"
        )
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": REVISION,
        "probe_sha256": sha256_file(PROBE_SRC),
        "malformed_fixture_sha256": got_fixture_sha,
        "lock_sha256": sha256_file(LOCK_PATH),
    }


def parse_loaded_libraries(text: str, liblas_prefix: Path) -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    lib_root = (liblas_prefix / "lib").resolve()
    for line in text.splitlines():
        if not line.startswith("loaded_"):
            continue
        name, raw = line.split("=", 1)
        path = Path(raw).resolve()
        if name in values or name not in ("loaded_liblas", "loaded_liblas_c") or lib_root not in path.parents:
            raise ValueError(f"consumer loaded foreign or duplicate libLAS: {line}")
        values[name] = {"path": str(path), "sha256": sha256_file(path)}
    if set(values) != {"loaded_liblas", "loaded_liblas_c"}:
        raise ValueError(f"incomplete consumer library provenance: {sorted(values)}")
    return values


def parse_partial_points(text: str) -> int:
    matches = [
        line.split("=", 1)[1]
        for line in text.splitlines()
        if line.startswith("existing_truncated_las_partial_points=")
    ]
    if len(matches) != 1 or not matches[0].isdigit() or not 0 <= int(matches[0]) < 3:
        raise ValueError(f"missing or invalid truncated LAS characterization: {matches}")
    return int(matches[0])


def run_cmd(argv: list[str], env: dict[str, str] | None = None, timeout: int = 90) -> str:
    proc = subprocess.run(
        argv,
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


def qualify(liblas_prefix: Path, output: Path) -> dict:
    verified = verify_offline()
    prefix = liblas_prefix.resolve(strict=True)
    for rel in ("lib/liblas.so", "lib/liblas_c.so", "include/liblas/liblas.hpp", "include/liblas/capi/liblas.h"):
        if not (prefix / rel).is_file():
            raise FileNotFoundError(f"missing restored libLAS artifact: {prefix / rel}")

    output.mkdir(parents=True, exist_ok=True)
    bad_laz = output / "original-truncated.laz"
    bad_laz.write_bytes(bytes.fromhex(FIXTURE_HEX.read_text(encoding="utf-8")))
    if sha256_file(bad_laz) != verified["malformed_fixture_sha256"]:
        raise ValueError("written malformed LAZ fixture SHA-256 mismatch")

    base_cmd = [
        "g++",
        "-std=c++11",
        "-O1",
        f"-isystem{prefix / 'include'}",
        str(PROBE_SRC),
        f"-L{prefix / 'lib'}",
        f"-Wl,--disable-new-dtags,-rpath,{prefix / 'lib'}",
        "-llas_c",
        "-llas",
        "-pthread",
        "-ldl",
    ]

    consumer = output / "consumer"
    run_cmd([*base_cmd, "-o", str(consumer)], timeout=90)
    plain_dir = output / "plain"
    plain_dir.mkdir(parents=True, exist_ok=True)
    plain_out = run_cmd([str(consumer), str(plain_dir), str(bad_laz)], timeout=30)
    if EXPECTED_MARKER not in plain_out:
        raise RuntimeError(f"LibLASSourceProbe missing rejection marker:\n{plain_out}")
    loaded = parse_loaded_libraries(plain_out, prefix)
    partial_points = parse_partial_points(plain_out)

    consumer_asan = output / "consumer-asan"
    run_cmd(
        [*base_cmd, "-g", "-fno-omit-frame-pointer", "-fsanitize=address,undefined", "-o", str(consumer_asan)],
        timeout=90,
    )
    asan_dir = output / "asan"
    asan_dir.mkdir(parents=True, exist_ok=True)
    asan_env = dict(
        os.environ,
        ASAN_OPTIONS="detect_leaks=0:abort_on_error=1:symbolize=1",
        UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1",
    )
    asan_out = run_cmd([str(consumer_asan), str(asan_dir), str(bad_laz)], env=asan_env, timeout=45)
    if EXPECTED_MARKER not in asan_out:
        raise RuntimeError(f"ASAN/UBSAN LibLASSourceProbe missing rejection marker:\n{asan_out}")

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "revision": REVISION,
        "verify": verified,
        "loaded_libraries": loaded,
        "known_truncated_uncompressed_las_partial_points": partial_points,
        "artifacts": {
            "consumer": sha256_file(consumer),
            "consumer-asan": sha256_file(consumer_asan),
            "original-truncated.laz": sha256_file(bad_laz),
        },
        "controls": {
            "las_laz_semantics": True,
            "seek_reset_error_probes": True,
            "malformed_laz_zero_points_and_explicit_errors": True,
            "consumer_asan_ubsan": True,
        },
        "scope": {
            "linux_gcc14_host_abi": True,
            "general_malformed_input_safety": False,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    receipt_path = output / "hosted-native-las-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--liblas-prefix", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2, sort_keys=True))
        return 0
    if not args.liblas_prefix or not args.output:
        parser.error("--liblas-prefix and --output are required for --phase qualify")
    receipt = qualify(args.liblas_prefix, args.output)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
