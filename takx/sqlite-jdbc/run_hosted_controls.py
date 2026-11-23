#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 runner for source-built SQLite JDBC and SQLCipher load-order controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "uds-takx-sqlite-jdbc-hosted-v1"
LOCK_SHA = "ba52361fd347031e250951c93bb65eb7937920900cd02f504ead36241363faea"
EXPECTED_INPUT_COUNT = 74
EXPECTED_TOTAL_BYTES = 15814646
MAVEN_CENTRAL_PREFIX = "https://repo.maven.apache.org/maven2/"
GOOGLE_MAVEN_MIRROR_PREFIX = "https://maven-central.storage-download.googleapis.com/maven2/"

LOCK_PATH = HERE / "inputs.lock.json"
BUILD_SCRIPT = HERE / "build.py"
SMOKE_JAVA = HERE / "SQLiteSourceSmoke.java"
LOAD_ORDERS_C = HERE / "SQLiteLoadOrders.c"
EXPORTS_MAP = HERE / "exports.map"
PATCH_FILE = HERE / "patches/gcc14-extension-prototype.patch"
GROUP_C = HERE.parent / "native-crypto/Group.c"
CRYPTO_EXPORTS_MAP = HERE.parent / "native-crypto/exports.map"
SQLCIPHER_PROBE_C = HERE.parent / "native-sqlcipher/SqlcipherSourceProbe.c"

REQUIRED_FILES = (
    LOCK_PATH,
    BUILD_SCRIPT,
    SMOKE_JAVA,
    LOAD_ORDERS_C,
    EXPORTS_MAP,
    PATCH_FILE,
    GROUP_C,
    CRYPTO_EXPORTS_MAP,
    SQLCIPHER_PROBE_C,
)
FOREIGN_MAP_PREFIXES = ("libsqlite3.so", "libcrypto.so", "libssl.so")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    for path in REQUIRED_FILES:
        if not path.is_file():
            raise FileNotFoundError(f"missing required sqlite-jdbc control input: {path}")
    got_lock_sha = sha256_file(LOCK_PATH)
    if got_lock_sha != LOCK_SHA:
        raise ValueError(f"inputs.lock.json SHA-256 mismatch: {got_lock_sha} != {LOCK_SHA}")
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    inputs = lock.get("inputs", [])
    total_bytes = sum(entry["bytes"] for entry in inputs)
    if len(inputs) != EXPECTED_INPUT_COUNT or total_bytes != EXPECTED_TOTAL_BYTES:
        raise ValueError(
            f"unexpected sqlite-jdbc lock inventory: count={len(inputs)}, bytes={total_bytes}"
        )
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "lock_sha256": got_lock_sha,
        "input_count": len(inputs),
        "total_bytes": total_bytes,
    }


def candidate_urls(url: str) -> list[str]:
    urls = [url]
    if url.startswith(MAVEN_CENTRAL_PREFIX):
        suffix = url[len(MAVEN_CENTRAL_PREFIX):]
        urls.append(f"{GOOGLE_MAVEN_MIRROR_PREFIX}{suffix}")
    return urls


def preseed_maven_inputs(lock: dict, cache_dir: Path) -> list[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[dict] = []
    for entry in lock.get("inputs", []):
        url = entry["url"]
        if not url.startswith(MAVEN_CENTRAL_PREFIX):
            continue
        expected_sha = entry["sha256"]
        expected_bytes = entry["bytes"]
        dest = cache_dir / expected_sha
        if dest.is_symlink():
            raise ValueError(f"symlink cache target refused: {dest}")
        if dest.is_file() and dest.stat().st_size == expected_bytes and sha256_file(dest) == expected_sha:
            seeded.append({"path": entry["path"], "url": "cached", "sha256": expected_sha, "bytes": expected_bytes})
            continue
        last_error: Exception | None = None
        for candidate in candidate_urls(url):
            try:
                req = urllib.request.Request(
                    candidate,
                    headers={"User-Agent": "uds-takserver-hosted-sqlite-jdbc"},
                )
                with urllib.request.urlopen(req, timeout=60) as response:
                    data = response.read(expected_bytes + 1)
                if len(data) != expected_bytes:
                    raise ValueError(
                        f"byte count mismatch for {candidate}: {len(data)} != {expected_bytes}"
                    )
                got_sha = hashlib.sha256(data).hexdigest()
                if got_sha != expected_sha:
                    raise ValueError(f"SHA-256 mismatch for {candidate}: {got_sha} != {expected_sha}")
                dest.write_bytes(data)
                seeded.append({"path": entry["path"], "url": candidate, "sha256": got_sha, "bytes": len(data)})
                break
            except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
                last_error = exc
        else:
            raise RuntimeError(f"failed to preseed Maven input {entry['path']}: {last_error}")
    return seeded


def parse_load_order_maps(text: str, jdbc_native: Path, modern_group: Path) -> list[str]:
    if text.count("PASS actual source-built SQLite JDBC/native:") != 2:
        raise ValueError("missing expected dual JDBC smoke passes in load-order output")
    if "encrypt_reopen_wrong_key=PASS" not in text:
        raise ValueError("missing encrypted SQLCipher wrong-key control pass in load-order output")
    if "jdbc_plain_and_sqlcipher_encrypted_controls=PASS" not in text:
        raise ValueError("missing joined JDBC + SQLCipher load-order pass marker")

    mapped = sorted(set(re.findall(r"^map=.*?(/[^\n]+)$", text, re.MULTILINE)))
    expected_jdbc = jdbc_native.resolve().as_posix()
    expected_modern = modern_group.resolve().as_posix()
    if expected_jdbc not in mapped or expected_modern not in mapped:
        raise ValueError(
            f"expected both {expected_jdbc} and {expected_modern} in /proc/self/maps"
        )
    foreign = [p for p in mapped if Path(p).name.startswith(FOREIGN_MAP_PREFIXES)]
    if foreign:
        raise ValueError(f"unexpected shared SQLite/crypto library mapped: {foreign}")
    return mapped


def build_modern_group(sqlcipher_prefix: Path, openssl_prefix: Path, out_so: Path) -> None:
    libsqlite = sqlcipher_prefix / "lib/libsqlite3.a"
    sqlite_hdr = sqlcipher_prefix / "include/sqlite3.h"
    libcrypto = openssl_prefix / "lib/libcrypto.a"
    evp_hdr = openssl_prefix / "include/openssl/evp.h"
    for required in (libsqlite, sqlite_hdr, libcrypto, evp_hdr):
        if not required.is_file():
            raise FileNotFoundError(f"missing SQLCipher/OpenSSL cache file: {required}")

    out_so.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "gcc",
            "-std=c99",
            "-O2",
            "-fPIC",
            "-shared",
            "-DSQLITE_HAS_CODEC",
            f"-I{sqlcipher_prefix / 'include'}",
            f"-I{openssl_prefix / 'include'}",
            f"-I{SQLCIPHER_PROBE_C.parent}",
            str(GROUP_C),
            str(libsqlite),
            str(libcrypto),
            f"-Wl,--version-script={CRYPTO_EXPORTS_MAP}",
            "-Wl,--no-undefined",
            "-lpthread",
            "-ldl",
            "-lm",
            "-o",
            str(out_so),
        ],
        check=True,
    )
    dynamic = subprocess.check_output(["readelf", "-d", str(out_so)], text=True)
    if "TEXTREL" in dynamic:
        raise ValueError("unexpected TEXTREL in modern-group.so")
    for line in dynamic.splitlines():
        if "(NEEDED)" in line and any(name in line for name in FOREIGN_MAP_PREFIXES):
            raise ValueError(f"unexpected shared SQLite/crypto NEEDED entry: {line.strip()}")


def _clean_load_order_env(absent_modules_dir: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OPENSSL_", "LD_"))
    }
    env.update(
        OPENSSL_CONF="/dev/null",
        OPENSSL_MODULES=str(absent_modules_dir),
        LC_ALL="C",
    )
    return env


def run_load_order_controls(
    jdbc_out: Path,
    java_home: Path,
    sqlcipher_prefix: Path,
    openssl_prefix: Path,
    work_dir: Path,
) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    modern_so = work_dir / "modern-group.so"
    build_modern_group(sqlcipher_prefix, openssl_prefix, modern_so)

    jdbc_native = jdbc_out / "native/libsqlitejdbc.so"
    if not jdbc_native.is_file():
        raise FileNotFoundError(f"missing source-built JDBC JNI library: {jdbc_native}")

    classpath = os.pathsep.join([
        str(jdbc_out / "sqlite-jdbc-host-source.jar"),
        str(jdbc_out / "slf4j-api.jar"),
    ])
    classes_dir = work_dir / "smoke-classes"
    classes_dir.mkdir(exist_ok=True)
    subprocess.run(
        [
            str(java_home / "bin/javac"),
            "--release",
            "17",
            "-cp",
            classpath,
            "-d",
            str(classes_dir),
            str(SMOKE_JAVA),
        ],
        check=True,
    )

    runner = work_dir / "orders"
    subprocess.run(
        [
            "gcc",
            "-O2",
            f"-I{java_home / 'include'}",
            f"-I{java_home / 'include/linux'}",
            str(LOAD_ORDERS_C),
            f"-L{java_home / 'lib/server'}",
            f"-Wl,-rpath,{java_home / 'lib/server'}",
            "-ljvm",
            "-ldl",
            "-o",
            str(runner),
        ],
        check=True,
    )

    env = _clean_load_order_env(work_dir / "absent-modules")
    full_cp = f"{classpath}{os.pathsep}{classes_dir}"
    orders: dict[str, dict] = {}
    for order in ("jm", "mj"):
        proc = subprocess.run(
            [
                str(runner),
                order,
                str(jdbc_native),
                str(modern_so),
                f"-Djava.class.path={full_cp}",
                f"-Dorg.sqlite.lib.path={jdbc_out / 'native'}",
                "-Dorg.sqlite.lib.name=libsqlitejdbc.so",
                str(work_dir / f"{order}.db"),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        (work_dir / f"{order}-load.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(
                f"load-order control {order} failed (exit={proc.returncode}): "
                f"{(proc.stdout + proc.stderr)[-2000:]}"
            )
        mapped = parse_load_order_maps(proc.stdout, jdbc_native, modern_so)
        orders[order] = {
            "passed": True,
            "mapped_file_count": len(mapped),
        }
    return {
        "modern_group_sha256": sha256_file(modern_so),
        "jdbc_native_sha256": sha256_file(jdbc_native),
        "orders": orders,
    }


def qualify(
    java_home: Path,
    sqlcipher_prefix: Path,
    openssl_prefix: Path,
    output_dir: Path,
) -> dict:
    verified = verify_offline()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "cache"
    jdbc_out = output_dir / "jdbc-build"
    load_orders_dir = output_dir / "load-orders"

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    seeded_maven = preseed_maven_inputs(lock, cache_dir)

    subprocess.run(
        [
            sys.executable,
            "-B",
            str(BUILD_SCRIPT),
            "--java-home",
            str(java_home.resolve()),
            "--cache",
            str(cache_dir),
            "--output",
            str(jdbc_out),
        ],
        check=True,
        timeout=600,
    )
    complete = json.loads((jdbc_out / "complete.json").read_text(encoding="utf-8"))
    isolation = run_load_order_controls(
        jdbc_out,
        java_home.resolve(),
        sqlcipher_prefix.resolve(),
        openssl_prefix.resolve(),
        load_orders_dir,
    )

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "preseeded_maven": seeded_maven,
        "jdbc_recipe_key": complete["key"],
        "jdbc_artifacts": complete["artifacts"],
        "jni_exports_count": len(complete["jni_exports"]),
        "load_order_isolation": isolation,
        "scope": {
            "jdbc_source_build_passed": True,
            "both_rtld_global_load_orders_passed": True,
            "full_sdk_acceptance": False,
            "client_acceptance": False,
            "security_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-sqlite-jdbc-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--sqlcipher-prefix", type=Path)
    parser.add_argument("--openssl-prefix", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    missing = [
        flag
        for flag, val in (
            ("--java-home", args.java_home),
            ("--sqlcipher-prefix", args.sqlcipher_prefix),
            ("--openssl-prefix", args.openssl_prefix),
            ("--output", args.output),
        )
        if val is None
    ]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")
    qualify(args.java_home, args.sqlcipher_prefix, args.openssl_prefix, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
