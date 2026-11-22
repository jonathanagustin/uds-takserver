#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline guard and restored-prefix verifier for the TAKX libglvnd 1.7.0 OCI cache."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA = "uds-takx-gles-cache-v1"
EXPECTED_GLVND_SHA256 = "073e7292788d4d3eeb45ea6c7bdcce9bfdb3b3eef8d7dbd47f2f30dce046ef98"
EXPECTED_MESON_SHA256 = "82c6818dc81743c96de3a458f06175776ebfde4081195ea31ea6971838f25e38"
REQUIRED_LIBRARIES = ("libEGL.so.1", "libGLESv2.so.2", "libGLdispatch.so.0")
REQUIRED_CONTROLS = (
    "real_surfaceless_es3_clear_readback",
    "built_glvnd_dispatch_loaded",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    """Verify Dockerfile pins and C control source match takx/native-gles/inputs.lock.json."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    control = (ROOT / "surfaceless_es3.c").read_text(encoding="utf-8")
    if EXPECTED_GLVND_SHA256 not in dockerfile:
        raise ValueError("Dockerfile missing locked libglvnd 1.7.0 archive SHA-256")
    if EXPECTED_MESON_SHA256 not in dockerfile:
        raise ValueError("Dockerfile missing locked meson 1.7.2 wheel SHA-256")
    if "real_surfaceless_es3_clear_readback=true" not in control:
        raise ValueError("surfaceless_es3.c missing control marker")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "dockerfile_sha256": sha256_file(ROOT / "Dockerfile"),
        "control_source_sha256": sha256_file(ROOT / "surfaceless_es3.c"),
        "locked_glvnd_sha256": EXPECTED_GLVND_SHA256,
        "locked_meson_sha256": EXPECTED_MESON_SHA256,
    }


def validate_receipt(receipt: dict, prefix: Path | None = None) -> None:
    """Fail-closed validation of gles-receipt.json and optional extracted prefix."""
    if receipt.get("schema") != SCHEMA:
        raise ValueError("unexpected GLES cache receipt schema")
    if receipt.get("component") != "libglvnd" or receipt.get("version") != "1.7.0":
        raise ValueError("unexpected component/version in GLES receipt")
    if receipt.get("arch") not in ("x86_64", "amd64"):
        raise ValueError("GLES cache requires native x86_64")
    if receipt.get("source_archive_sha256") != EXPECTED_GLVND_SHA256:
        raise ValueError("source_archive_sha256 mismatch")
    if receipt.get("meson_wheel_sha256") != EXPECTED_MESON_SHA256:
        raise ValueError("meson_wheel_sha256 mismatch")
    controls = receipt.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("missing controls object")
    for name in REQUIRED_CONTROLS:
        if controls.get(name) is not True:
            raise ValueError(f"required GLES control did not pass: {name}")
    libraries = receipt.get("libraries")
    if not isinstance(libraries, dict):
        raise ValueError("missing libraries object")
    for lib in REQUIRED_LIBRARIES:
        if len(str(libraries.get(lib, ""))) != 64:
            raise ValueError(f"missing or invalid hash for {lib}")
    scope = receipt.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("missing scope object")
    if scope.get("software_mesa_llvmpipe") is not True:
        raise ValueError("scope.software_mesa_llvmpipe must be true")
    for key in ("hardware_gpu_accepted", "sdk_accepted", "security_accepted"):
        if scope.get(key) is not False:
            raise ValueError(f"scope.{key} must remain false")

    if prefix is not None:
        for lib in REQUIRED_LIBRARIES:
            path = (prefix / "lib" / lib).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"missing restored GLES library: {lib}")
            actual = sha256_file(path)
            if actual != libraries[lib]:
                raise ValueError(f"hash mismatch for {lib}: {actual} != {libraries[lib]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "check-prefix"), default="verify")
    parser.add_argument("--prefix", type=Path, help="extracted /glvnd directory to validate")
    args = parser.parse_args(argv)
    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2, sort_keys=True))
        return 0
    if args.prefix is None:
        parser.error("--prefix is required for --phase check-prefix")
    receipt = json.loads((args.prefix / "gles-receipt.json").read_text(encoding="utf-8"))
    validate_receipt(receipt, prefix=args.prefix)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
