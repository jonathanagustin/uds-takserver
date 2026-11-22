#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline guard and restored-prefix verifier for the TAKX PROJ 4.9.1 OCI cache."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA = "uds-takx-proj-cache-v1"
EXPECTED_ARCHIVE_SHA256 = "fca0388f3f8bc5a1a803d2f6ff30017532367992b30cf144f2d39be88f36c319"
EXPECTED_LICENSE_SHA256 = "2dcac4af6990093ef48b896bfb81452d46a31b08d46cce34b27e83f93b2e37f6"
REQUIRED_CONTROLS = (
    "proj_static_pic_control",
    "whole_archive_no_undefined",
    "no_textrel",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    """Verify Dockerfile pins and C control source match takx/native-proj/inputs.lock.json."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    control = (ROOT / "proj_control.c").read_text(encoding="utf-8")
    if EXPECTED_ARCHIVE_SHA256 not in dockerfile:
        raise ValueError("Dockerfile missing locked PROJ 4.9.1 archive SHA-256")
    if EXPECTED_LICENSE_SHA256 not in dockerfile:
        raise ValueError("Dockerfile missing locked PROJ 4.9.1 COPYING SHA-256")
    if "proj_static_pic_control=true" not in control:
        raise ValueError("proj_control.c missing proj_static_pic_control marker")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "dockerfile_sha256": sha256_file(ROOT / "Dockerfile"),
        "control_source_sha256": sha256_file(ROOT / "proj_control.c"),
        "locked_archive_sha256": EXPECTED_ARCHIVE_SHA256,
        "locked_license_sha256": EXPECTED_LICENSE_SHA256,
    }


def validate_receipt(receipt: dict, prefix: Path | None = None) -> None:
    """Fail-closed validation of proj-receipt.json and optional extracted prefix."""
    if receipt.get("schema") != SCHEMA:
        raise ValueError("unexpected PROJ cache receipt schema")
    if receipt.get("component") != "proj" or receipt.get("version") != "4.9.1":
        raise ValueError("unexpected component/version in PROJ receipt")
    if receipt.get("arch") not in ("x86_64", "amd64"):
        raise ValueError("PROJ cache requires native x86_64")
    if receipt.get("source_archive_sha256") != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("source_archive_sha256 mismatch")
    if receipt.get("license_sha256") != EXPECTED_LICENSE_SHA256:
        raise ValueError("license_sha256 mismatch")
    controls = receipt.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("missing controls object")
    for name in REQUIRED_CONTROLS:
        if controls.get(name) is not True:
            raise ValueError(f"required PROJ control did not pass: {name}")
    scope = receipt.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("missing scope object")
    if scope.get("static_pic_host_abi") is not True:
        raise ValueError("scope.static_pic_host_abi must be true")
    for key in ("sdk_accepted", "security_accepted"):
        if scope.get(key) is not False:
            raise ValueError(f"scope.{key} must remain false")

    if prefix is not None:
        checks = {
            "libproj_a_sha256": prefix / "lib" / "libproj.a",
            "proj_api_h_sha256": prefix / "include" / "proj_api.h",
            "license_sha256": prefix / "COPYING",
        }
        for key, path in checks.items():
            if not path.is_file():
                raise FileNotFoundError(f"missing restored PROJ artifact: {path}")
            actual = sha256_file(path)
            if actual != receipt.get(key):
                raise ValueError(f"hash mismatch for {path.name}: {actual} != {receipt.get(key)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "check-prefix"), default="verify")
    parser.add_argument("--prefix", type=Path, help="extracted /proj directory to validate")
    args = parser.parse_args(argv)
    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2, sort_keys=True))
        return 0
    if args.prefix is None:
        parser.error("--prefix is required for --phase check-prefix")
    receipt_path = args.prefix / "proj-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    validate_receipt(receipt, prefix=args.prefix)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
