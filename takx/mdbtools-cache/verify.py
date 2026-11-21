#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline guard and restored-prefix verifier for the TAKX MDBTools 1.0.0 OCI cache."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA = "uds-takx-mdbtools-cache-v1"
EXPECTED_ARCHIVE_SHA256 = "3446e1d71abdeb98d41e252777e67e1909b186496fda59f98f67032f7fbcd955"
EXPECTED_PATCH_SHA256 = "8c5e6faf6c95b026d834b7094d4f7a4bafc0f55222312a3bc7aac691309c3839"
EXPECTED_COPYING_SHA256 = "8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643"
EXPECTED_COPYING_LIB_SHA256 = "b7993225104d90ddd8024fd838faf300bea5e83d91203eab98e29512acebd69c"
REQUIRED_CONTROLS = (
    "mdb_static_pic_control",
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
    """Verify Dockerfile pins and C control source match takx/native-mdbtools/inputs.lock.json."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    control = (ROOT / "mdb_control.c").read_text(encoding="utf-8")
    for label, digest in (
        ("archive", EXPECTED_ARCHIVE_SHA256),
        ("patch", EXPECTED_PATCH_SHA256),
        ("COPYING", EXPECTED_COPYING_SHA256),
        ("COPYING.LIB", EXPECTED_COPYING_LIB_SHA256),
    ):
        if digest not in dockerfile:
            raise ValueError(f"Dockerfile missing locked MDBTools {label} SHA-256")
    if "mdb_static_pic_control=true" not in control:
        raise ValueError("mdb_control.c missing mdb_static_pic_control marker")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "dockerfile_sha256": sha256_file(ROOT / "Dockerfile"),
        "control_source_sha256": sha256_file(ROOT / "mdb_control.c"),
        "locked_archive_sha256": EXPECTED_ARCHIVE_SHA256,
        "locked_patch_sha256": EXPECTED_PATCH_SHA256,
    }


def validate_receipt(receipt: dict, prefix: Path | None = None) -> None:
    """Fail-closed validation of mdbtools-receipt.json and optional extracted prefix."""
    if receipt.get("schema") != SCHEMA:
        raise ValueError("unexpected MDBTools cache receipt schema")
    if receipt.get("component") != "mdbtools" or receipt.get("version") != "1.0.0":
        raise ValueError("unexpected component/version in MDBTools receipt")
    if receipt.get("arch") not in ("x86_64", "amd64"):
        raise ValueError("MDBTools cache requires native x86_64")
    if receipt.get("source_archive_sha256") != EXPECTED_ARCHIVE_SHA256:
        raise ValueError("source_archive_sha256 mismatch")
    if receipt.get("patch_sha256") != EXPECTED_PATCH_SHA256:
        raise ValueError("patch_sha256 mismatch")
    if receipt.get("copying_sha256") != EXPECTED_COPYING_SHA256:
        raise ValueError("copying_sha256 mismatch")
    if receipt.get("copying_lib_sha256") != EXPECTED_COPYING_LIB_SHA256:
        raise ValueError("copying_lib_sha256 mismatch")
    controls = receipt.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("missing controls object")
    for name in REQUIRED_CONTROLS:
        if controls.get(name) is not True:
            raise ValueError(f"required MDBTools control did not pass: {name}")
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
            "libmdb_a_sha256": prefix / "lib" / "libmdb.a",
            "mdbtools_h_sha256": prefix / "include" / "mdbtools.h",
            "copying_sha256": prefix / "COPYING",
            "copying_lib_sha256": prefix / "COPYING.LIB",
        }
        for key, path in checks.items():
            if not path.is_file():
                raise FileNotFoundError(f"missing restored MDBTools artifact: {path}")
            actual = sha256_file(path)
            if actual != receipt.get(key):
                raise ValueError(f"hash mismatch for {path.name}: {actual} != {receipt.get(key)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "check-prefix"), default="verify")
    parser.add_argument("--prefix", type=Path, help="extracted /mdbtools directory to validate")
    args = parser.parse_args(argv)
    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2, sort_keys=True))
        return 0
    if args.prefix is None:
        parser.error("--prefix is required for --phase check-prefix")
    receipt = json.loads((args.prefix / "mdbtools-receipt.json").read_text(encoding="utf-8"))
    validate_receipt(receipt, prefix=args.prefix)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
