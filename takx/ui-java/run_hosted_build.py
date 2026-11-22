#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 runner for takx/ui-java (builds all 5 original UI JARs).

Downloads the SHA-256-pinned public source archives and Maven Central classpath
JARs with retry on HTTP 429/5xx, invokes takx/ui-java/build.py --profile original
on Linux JDK 17, and emits a scoped qualification receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "inputs.lock.json"
BUILD_PATH = HERE / "build.py"
SCHEMA = "uds-takx-ui-java-hosted-v1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def required_inputs_for_profile(lock: dict, profile: str = "original") -> tuple[list[dict], list[tuple[str, dict]]]:
    artifact_names = lock["profiles"][profile]
    archive_keys = sorted({lock["artifacts"][name]["source"] for name in artifact_names})
    archives = [lock["archives"][key] for key in archive_keys]
    jars = sorted(lock["public_jars"].items())
    return archives, jars


def verify_offline(profile: str = "original") -> dict:
    lock = load_lock()
    if lock.get("schema") != 1 or lock.get("java_major") != 17:
        raise ValueError("unexpected ui-java inputs.lock.json schema or java_major")
    if profile not in lock.get("profiles", {}):
        raise ValueError(f"unknown ui-java profile: {profile}")
    archives, jars = required_inputs_for_profile(lock, profile)
    if not BUILD_PATH.is_file():
        raise ValueError("missing takx/ui-java/build.py")
    for entry in archives:
        if len(entry["sha256"]) != 64:
            raise ValueError(f"invalid archive sha256 for {entry['file']}")
    for name, entry in jars:
        if len(entry["sha256"]) != 64:
            raise ValueError(f"invalid jar sha256 for {name}")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profile": profile,
        "artifact_count": len(lock["profiles"][profile]),
        "archive_count": len(archives),
        "jar_count": len(jars),
        "inputs_lock_sha256": sha256_file(LOCK_PATH),
        "build_py_sha256": sha256_file(BUILD_PATH),
    }


def _download_verified(url: str, expected_bytes: int, expected_sha256: str, dest: Path) -> None:
    last_err: Exception | None = None
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "uds-takserver-ci/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = resp.read()
            if len(data) != expected_bytes:
                raise ValueError(
                    f"size mismatch for {dest.name}: got {len(data)}, expected {expected_bytes}"
                )
            got = sha256_bytes(data)
            if got != expected_sha256:
                raise ValueError(
                    f"SHA-256 mismatch for {dest.name}: got {got}, expected {expected_sha256}"
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as err:
            last_err = err
            if isinstance(err, urllib.error.HTTPError) and err.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(min(30, 2**attempt))
    raise RuntimeError(f"failed to download {url}: {last_err}")


def _resolve_java_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        return Path(env_home).resolve()
    javac = shutil.which("javac")
    if not javac:
        raise RuntimeError("javac not found; pass --java-home or set JAVA_HOME")
    return Path(javac).resolve().parents[1]


def run_hosted_build(java_home: Path | None, receipt_path: Path | None, profile: str = "original") -> dict:
    offline = verify_offline(profile)
    lock = load_lock()
    archives, jars = required_inputs_for_profile(lock, profile)
    java = _resolve_java_home(java_home)

    with tempfile.TemporaryDirectory(prefix="takx-ui-java-hosted-") as tmp:
        work = Path(tmp)
        sources_dir = work / "sources"
        classpath_dir = work / "classpath"
        output_dir = work / "output"

        for entry in archives:
            _download_verified(
                entry["url"],
                entry["bytes"],
                entry["sha256"],
                sources_dir / entry["file"],
            )
        for jar_name, entry in jars:
            _download_verified(
                entry["url"],
                entry["bytes"],
                entry["sha256"],
                classpath_dir / jar_name,
            )

        proc = subprocess.run(
            [
                sys.executable,
                "-B",
                str(BUILD_PATH),
                "--profile",
                profile,
                "--sources-dir",
                str(sources_dir),
                "--classpath-dir",
                str(classpath_dir),
                "--output",
                str(output_dir),
                "--java-home",
                str(java),
                "--timeout-seconds",
                "300",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ui-java build.py failed (rc={proc.returncode}): "
                f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
            )

        built_jars = sorted(output_dir.glob("*.jar"))
        jar_digests = {jar.name: sha256_file(jar) for jar in built_jars}
        if len(jar_digests) != len(lock["profiles"][profile]):
            raise RuntimeError(
                f"expected {len(lock['profiles'][profile])} built JARs, got {len(jar_digests)}"
            )

    receipt = {
        "schema": SCHEMA,
        "phase": "run",
        "profile": profile,
        "inputs_lock_sha256": offline["inputs_lock_sha256"],
        "build_py_sha256": offline["build_py_sha256"],
        "java_home": str(java),
        "built_jars": jar_digests,
        "build_summary": json.loads(proc.stdout.strip()),
        "scope": {
            "hosted_linux_jdk17_compile_and_package": True,
            "complete_client_or_runtime_acceptance": False,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    if receipt_path is not None:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="TAKX ui-java hosted Linux JDK 17 qualification")
    parser.add_argument("--phase", choices=("verify", "run"), required=True)
    parser.add_argument("--profile", choices=("original",), default="original")
    parser.add_argument("--java-home", type=Path, default=None)
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args()

    if args.phase == "verify":
        result = verify_offline(args.profile)
    else:
        result = run_hosted_build(args.java_home, args.receipt, args.profile)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
