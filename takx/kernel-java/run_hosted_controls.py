#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 runner for Kernel 8.30.5 engine-shared Java compilation and JNI headers."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "uds-takx-kernel-java-hosted-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
MAVEN_CENTRAL_PREFIX = "https://repo.maven.apache.org/maven2/"
GOOGLE_MAVEN_MIRROR_PREFIX = "https://maven-central.storage-download.googleapis.com/maven2/"

LOCK_PATH = HERE / "inputs.lock.json"
BUILD_SCRIPT = HERE / "build.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_urls(url: str) -> list[str]:
    urls = [url]
    if url.startswith(MAVEN_CENTRAL_PREFIX):
        suffix = url[len(MAVEN_CENTRAL_PREFIX):]
        urls.append(f"{GOOGLE_MAVEN_MIRROR_PREFIX}{suffix}")
    return urls


def verify_offline() -> dict:
    for required in (LOCK_PATH, BUILD_SCRIPT):
        if not required.is_file():
            raise FileNotFoundError(f"missing required kernel-java file: {required}")

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("schema") != 1:
        raise ValueError(f"unexpected kernel-java lock schema: {lock.get('schema')}")
    if lock.get("revision") != EXPECTED_REVISION:
        raise ValueError(f"unexpected kernel-java revision: {lock.get('revision')}")
    if lock.get("java_major") != 17 or lock.get("java_release") != 11:
        raise ValueError(
            f"unexpected Java target: major={lock.get('java_major')} release={lock.get('java_release')}"
        )

    base_jars = lock.get("public_jars", {})
    replacements = lock.get("patched_replacements", {})
    shared = lock.get("shared_profile", {})
    shared_jars = shared.get("public_jars", {})
    shared_replacements = shared.get("patched_replacements", {})
    if (
        len(base_jars) != 23
        or len(replacements) != 6
        or len(shared_jars) != 10
        or len(shared_replacements) != 3
    ):
        raise ValueError("incomplete engine-shared JAR or replacement inventory in inputs.lock.json")

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": EXPECTED_REVISION,
        "base_public_jars": len(base_jars),
        "shared_public_jars": len(shared_jars),
        "lock_sha256": sha256_file(LOCK_PATH),
    }


def select_patched_shared_jars(lock: dict) -> dict[str, dict]:
    selected: dict[str, dict] = {}
    selected.update(copy.deepcopy(lock["public_jars"]))
    selected.update(copy.deepcopy(lock["shared_profile"]["public_jars"]))
    replacements = dict(lock["patched_replacements"])
    replacements.update(lock["shared_profile"]["patched_replacements"])
    for old_name, replacement in replacements.items():
        if old_name not in selected:
            raise ValueError(f"replacement target missing from selected JARs: {old_name}")
        del selected[old_name]
        new_name = replacement["name"]
        selected[new_name] = {k: v for k, v in replacement.items() if k != "name"}
    if len(selected) != 33:
        raise ValueError(f"expected 33 patched engine-shared public JARs, found {len(selected)}")
    return selected


def preseed_public_jars(jars: dict[str, dict], classpath_dir: Path) -> dict[str, str]:
    classpath_dir.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name, entry in sorted(jars.items()):
        dest = classpath_dir / name
        expected_sha = entry["sha256"]
        expected_bytes = entry["bytes"]
        if dest.is_symlink():
            raise ValueError(f"symlink classpath target refused: {dest}")
        if dest.is_file() and dest.stat().st_size == expected_bytes and sha256_file(dest) == expected_sha:
            hashes[name] = expected_sha
            continue

        last_error: Exception | None = None
        for candidate in candidate_urls(entry["url"]):
            try:
                req = urllib.request.Request(
                    candidate,
                    headers={"User-Agent": "uds-takserver-hosted-kernel-java"},
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
                hashes[name] = got_sha
                break
            except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
                last_error = exc
        else:
            raise RuntimeError(f"failed to preseed public JAR {name}: {last_error}")
    return hashes


def validate_build_result(result: dict, jni_coverage: dict) -> None:
    expected_exact = {
        "exit_code": 0,
        "errors": 0,
        "classpath_profile": "patched",
        "source_scope": "engine-shared",
        "engine_sources": 862,
        "shared_sources": 231,
        "upstream_sources": 1176,
        "adapter_sources": 59,
        "all_sources": 1235,
        "exact_public_jars": 33,
        "source_rebuilt_gis_jars": 2,
        "gis_classes_identical_to_original_probe": 107,
    }
    for key, expected in expected_exact.items():
        if result.get(key) != expected:
            raise ValueError(
                f"kernel-java build summary mismatch for {key}: {result.get(key)} != {expected}"
            )
    if result.get("compiled_class_files", 0) <= 0:
        raise ValueError("kernel-java build produced zero compiled class files")
    req_headers = result.get("required_jni_headers", 0)
    gen_headers = result.get("generated_jni_headers", 0)
    if req_headers <= 0 or gen_headers < req_headers:
        raise ValueError(
            f"incomplete JNI header generation: generated={gen_headers}, required={req_headers}"
        )
    if jni_coverage.get("missing_headers") != []:
        raise ValueError(f"missing required JNI headers: {jni_coverage.get('missing_headers')}")


def qualify(
    atak_source: Path,
    gis_build: Path,
    java_home: Path,
    output_dir: Path,
) -> dict:
    verified = verify_offline()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    classpath_dir = output_dir / "public-jars"
    proof_dir = output_dir / "proof"

    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    jars = select_patched_shared_jars(lock)
    jar_hashes = preseed_public_jars(jars, classpath_dir)

    subprocess.run(
        [
            sys.executable,
            "-B",
            str(BUILD_SCRIPT),
            "--scope",
            "engine-shared",
            "--profile",
            "patched",
            "--jni-headers",
            "--source",
            str(atak_source.resolve()),
            "--classpath-dir",
            str(classpath_dir),
            "--gis-build",
            str(gis_build.resolve()),
            "--java-home",
            str(java_home.resolve()),
            "--output",
            str(proof_dir),
        ],
        check=True,
        timeout=300,
    )

    result = json.loads((proof_dir / "result.json").read_text(encoding="utf-8"))
    jni_coverage = json.loads((proof_dir / "jni-header-coverage.json").read_text(encoding="utf-8"))
    validate_build_result(result, jni_coverage)

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "public_jars_count": len(jar_hashes),
        "compile_summary": result,
        "jni_header_coverage": {
            "required_count": len(jni_coverage["required_headers"]),
            "generated_count": len(jni_coverage["generated_header_hashes"]),
            "missing_count": len(jni_coverage["missing_headers"]),
        },
        "scope": {
            "kernel_java_engine_shared_compiled": True,
            "jni_headers_complete": True,
            "native_built_or_loaded": False,
            "full_sdk_acceptance": False,
            "client_acceptance": False,
        },
    }
    receipt_path = output_dir / "hosted-kernel-java-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--atak-source", type=Path)
    parser.add_argument("--gis-build", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    missing = [
        flag
        for flag, val in (
            ("--atak-source", args.atak_source),
            ("--gis-build", args.gis_build),
            ("--java-home", args.java_home),
            ("--output", args.output),
        )
        if val is None
    ]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")
    qualify(args.atak_source, args.gis_build, args.java_home, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
