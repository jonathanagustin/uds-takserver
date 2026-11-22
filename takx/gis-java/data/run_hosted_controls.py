#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 runner for takx/gis-java/data deterministic JAR and resource lookup controls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SCHEMA = "uds-takx-gdal-data-hosted-v1"
DOWNLOAD_ATTEMPTS = 4


def _load_build_module():
    spec = importlib.util.spec_from_file_location("gdal_data_build", HERE / "build.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {HERE / 'build.py'}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_java_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    for var in ("JAVA_HOME_17_X64", "JAVA_HOME_17_ARM64", "JAVA_HOME"):
        val = os.environ.get(var)
        if val:
            return Path(val).resolve()
    javac = shutil.which("javac")
    if not javac:
        raise ValueError("JDK 17 home required via --java-home or JAVA_HOME_17_X64/JAVA_HOME")
    return Path(javac).resolve().parents[1]


def verify_only() -> dict:
    lock = json.loads((HERE / "resources.lock.json").read_text(encoding="utf-8"))
    gis_lock = json.loads((HERE.parent / "sources.lock.json").read_text(encoding="utf-8"))
    archive = lock["archive"]
    gis_gdal = gis_lock["archives"]["gdal"]
    require(archive["url"] == gis_gdal["url"], "GDAL archive URL disagrees with takx/gis-java/sources.lock.json")
    require(archive["sha256"] == gis_gdal["sha256"], "GDAL archive SHA-256 disagrees with takx/gis-java/sources.lock.json")
    require(archive["bytes"] == gis_gdal["bytes"], "GDAL archive size disagrees with takx/gis-java/sources.lock.json")
    require(len(lock["resources"]) == 92, f"expected 92 locked GDAL data files, got {len(lock['resources'])}")
    require((HERE / "ResourceLookupProbe.java").is_file(), "missing ResourceLookupProbe.java")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "archive_url": archive["url"],
        "archive_sha256": archive["sha256"],
        "archive_bytes": archive["bytes"],
        "locked_resource_count": len(lock["resources"]),
        "resources_lock_sha256": sha256_file(HERE / "resources.lock.json"),
        "build_py_sha256": sha256_file(HERE / "build.py"),
        "probe_java_sha256": sha256_file(HERE / "ResourceLookupProbe.java"),
        "scope": {
            "hosted_linux_jdk17_gdal_data": False,
            "full_client_accepted": False,
            "native_gdal_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }


def _download_with_retry(url: str, dest: Path, expected_bytes: int, expected_sha256: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "uds-takserver-takx-gdal-data/1"})
            with urllib.request.urlopen(req, timeout=120) as resp, dest.open("wb") as out:
                shutil.copyfileobj(resp, out)
            if dest.stat().st_size != expected_bytes:
                raise ValueError(f"{dest.name} byte size mismatch: {dest.stat().st_size} != {expected_bytes}")
            actual_sha = sha256_file(dest)
            if actual_sha != expected_sha256:
                raise ValueError(f"{dest.name} SHA-256 mismatch: {actual_sha} != {expected_sha256}")
            return
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
            if dest.exists():
                dest.unlink()
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(2 * attempt)
    raise RuntimeError(f"failed to download verified {dest.name} from {url}: {last_error}")


def qualify(output: Path, java_home: Path | None = None) -> dict:
    verified = verify_only()
    build_mod = _load_build_module()
    lock = json.loads((HERE / "resources.lock.json").read_text(encoding="utf-8"))

    out = output.absolute()
    require(not any(p.is_symlink() for p in (out, *out.parents)), "symlink output refused")
    require(not out.exists() and out.parent.is_dir(), "fresh output directory required")
    require(out != REPO and not out.is_relative_to(REPO) and not REPO.is_relative_to(out), "output overlaps repository")

    java = resolve_java_home(java_home)
    javac = java / "bin/javac"
    java_bin = java / "bin/java"
    require(javac.is_file() and java_bin.is_file(), f"missing JDK 17 tools under {java}")

    out.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="takx-gdal-data-") as tmp:
        tmp_dir = Path(tmp)
        archive_path = tmp_dir / "gdal-2.4.4-mod.tar.gz"
        _download_with_retry(
            verified["archive_url"],
            archive_path,
            verified["archive_bytes"],
            verified["archive_sha256"],
        )

        first_out = out / "build-1"
        second_out = out / "build-2"
        first_receipt = build_mod.build(archive_path, first_out)
        second_receipt = build_mod.build(archive_path, second_out)
        require(first_receipt == second_receipt, "two clean gdal-data builds produced differing receipts")

        jar_name = first_receipt["artifact"]["file"]
        jar_1 = first_out / jar_name
        jar_2 = second_out / jar_name
        require(jar_1.read_bytes() == jar_2.read_bytes(), "two clean gdal-data builds produced differing JAR bytes")

        expected = {name: entry["sha256"] for name, entry in lock["resources"].items()}
        index = "\n".join(sorted([*expected, "gdaldata.files"])) + "\n"
        expected["gdaldata.files"] = hashlib.sha256(index.encode("ascii")).hexdigest()
        hashes_tsv = tmp_dir / "expected.tsv"
        hashes_tsv.write_text(
            "".join(f"{name}\t{digest}\n" for name, digest in sorted(expected.items())),
            encoding="utf-8",
        )

        classes_dir = tmp_dir / "classes"
        classes_dir.mkdir(mode=0o700)
        subprocess.run(
            [str(javac), "--release", "11", "-d", str(classes_dir), str(HERE / "ResourceLookupProbe.java")],
            check=True,
            timeout=60,
        )
        probe = subprocess.run(
            [str(java_bin), "-cp", os.pathsep.join([str(classes_dir), str(jar_1)]), "ResourceLookupProbe", str(hashes_tsv)],
            check=True,
            timeout=60,
            capture_output=True,
            text=True,
        )
        require("PASS 93 JDK classloader resource lookups" in probe.stdout, f"unexpected probe output: {probe.stdout}")

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "archive_sha256": verified["archive_sha256"],
        "java_home": str(java),
        "artifact_file": jar_name,
        "artifact_sha256": first_receipt["artifact"]["sha256"],
        "artifact_bytes": first_receipt["artifact"]["bytes"],
        "deterministic_clean_builds": True,
        "jdk_resource_lookups_verified": 93,
        "scope": {
            "hosted_linux_jdk17_gdal_data": True,
            "full_client_accepted": False,
            "native_gdal_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }
    (out / "hosted-gdal-data-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.phase == "verify":
        require(
            args.output is None and args.java_home is None,
            "qualify paths require explicit --phase qualify",
        )
        result = verify_only()
    else:
        require(args.output is not None, "--output is required for --phase qualify")
        result = qualify(args.output, java_home=args.java_home)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
