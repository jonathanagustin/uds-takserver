#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Unified offline guard and restored-prefix verifier for TAKX Layer-0 native OCI caches."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA = "uds-takx-native-cache-v1"
REQUIRED_CONTROLS = (
    "static_pic_control",
    "whole_archive_no_undefined",
    "no_textrel",
)

COMPONENTS = {
    "zlib": {
        "version": "1.3.1",
        "archive_sha256": "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23",
        "license_sha256": "845efc77857d485d91fb3e0b884aaa929368c717ae8186b66fe1ed2495753243",
        "control_file": "zlib_control.c",
        "control_marker": "zlib_static_pic_control=true",
        "required_artifacts": ("lib/libz.a", "include/zlib.h", "LICENSE"),
    },
    "expat": {
        "version": "2.10.0",
        "archive_sha256": "c624ba2998fc65df1163a49641ff3f9ab73138b11641d92c86bee0fb01b1bc7f",
        "license_sha256": "31b15de82aa19a845156169a17a5488bf597e561b2c318d159ed583139b25e87",
        "control_file": "expat_control.c",
        "control_marker": "expat_static_pic_control=true",
        "required_artifacts": ("lib/libexpat.a", "include/expat.h", "COPYING"),
    },
    "quictls": {
        "version": "3.0.14+quic1",
        "archive_sha256": "75b0d9425c914913840d376388d8d8e6105ec8a03a9c33ab56fc3ac0384003a2",
        "license_sha256": "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a",
        "control_file": "quictls_control.c",
        "control_marker": "quictls_static_pic_control=true",
        "required_artifacts": ("lib/libssl.a", "lib/libcrypto.a", "LICENSE.txt"),
    },
    "openssl": {
        "version": "3.5.9",
        "archive_sha256": "603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a",
        "license_sha256": "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a",
        "control_file": "openssl_control.c",
        "control_marker": "openssl_static_pic_control=true",
        "required_artifacts": ("lib/libssl.a", "lib/libcrypto.a", "LICENSE.txt"),
    },
    "curl": {
        "version": "8.23.0",
        "archive_sha256": "8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5",
        "license_sha256": "82f2f4427d6545ee5aaac4f0b80428da6cc8ba41c2cf5da3a03680ec327b9681",
        "control_file": "curl_control.c",
        "control_marker": "curl_static_pic_control=true",
        "required_artifacts": ("lib/libcurl.a", "include/curl/curl.h", "COPYING"),
    },
    "sqlcipher": {
        "version": "4.5.7",
        "archive_sha256": "b670845f28da0a3c717e991e9f18a334e08f6bc977190bbce6be864ca229f96d",
        "license_sha256": "09e4af560ce2e3c9c2aa6b564e35947b03db7d1ae345f22a32793ed46542cc14",
        "control_file": "sqlcipher_control.c",
        "control_marker": "sqlcipher_static_pic_control=true",
        "required_artifacts": ("lib/libsqlite3.a", "include/sqlite3.h", "LICENSE.md"),
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_component_offline(name: str) -> dict:
    if name not in COMPONENTS:
        raise ValueError(f"unknown component: {name}")
    spec = COMPONENTS[name]
    comp_dir = ROOT / name
    dockerfile = (comp_dir / "Dockerfile").read_text(encoding="utf-8")
    control = (comp_dir / spec["control_file"]).read_text(encoding="utf-8")
    if spec["archive_sha256"] not in dockerfile:
        raise ValueError(f"{name}/Dockerfile missing locked archive SHA-256")
    if spec["license_sha256"] not in dockerfile:
        raise ValueError(f"{name}/Dockerfile missing locked license SHA-256")
    if spec["control_marker"] not in control:
        raise ValueError(f"{name}/{spec['control_file']} missing control marker")
    return {
        "component": name,
        "version": spec["version"],
        "dockerfile_sha256": sha256_file(comp_dir / "Dockerfile"),
        "control_source_sha256": sha256_file(comp_dir / spec["control_file"]),
    }


def verify_all_offline() -> dict:
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "components": {name: verify_component_offline(name) for name in sorted(COMPONENTS)},
    }


def validate_receipt(name: str, receipt: dict, prefix: Path | None = None) -> None:
    if name not in COMPONENTS:
        raise ValueError(f"unknown component: {name}")
    spec = COMPONENTS[name]
    if receipt.get("schema") != SCHEMA:
        raise ValueError("unexpected native cache receipt schema")
    if receipt.get("component") != name or receipt.get("version") != spec["version"]:
        raise ValueError(f"unexpected component/version for {name}")
    if receipt.get("arch") not in ("x86_64", "amd64"):
        raise ValueError("native cache requires x86_64")
    if receipt.get("source_archive_sha256") != spec["archive_sha256"]:
        raise ValueError("source_archive_sha256 mismatch")
    if receipt.get("license_sha256") != spec["license_sha256"]:
        raise ValueError("license_sha256 mismatch")
    controls = receipt.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("missing controls object")
    for ctrl in REQUIRED_CONTROLS:
        if controls.get(ctrl) is not True:
            raise ValueError(f"required control did not pass: {ctrl}")
    scope = receipt.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("missing scope object")
    if scope.get("static_pic_host_abi") is not True:
        raise ValueError("scope.static_pic_host_abi must be true")
    for key in ("sdk_accepted", "security_accepted"):
        if scope.get(key) is not False:
            raise ValueError(f"scope.{key} must remain false")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("missing artifacts object")
    for rel in spec["required_artifacts"]:
        if len(str(artifacts.get(rel, ""))) != 64:
            raise ValueError(f"missing or invalid hash for {rel}")
        if prefix is not None:
            path = prefix / rel
            if not path.is_file():
                raise FileNotFoundError(f"missing restored artifact: {rel}")
            actual = sha256_file(path)
            if actual != artifacts[rel]:
                raise ValueError(f"hash mismatch for {rel}: {actual} != {artifacts[rel]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "check-prefix"), default="verify")
    parser.add_argument("--component", choices=sorted(COMPONENTS))
    parser.add_argument("--prefix", type=Path)
    args = parser.parse_args(argv)
    if args.phase == "verify":
        out = verify_component_offline(args.component) if args.component else verify_all_offline()
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    if not args.component or not args.prefix:
        parser.error("--component and --prefix are required for --phase check-prefix")
    receipt = json.loads((args.prefix / "cache-receipt.json").read_text(encoding="utf-8"))
    validate_receipt(args.component, receipt, prefix=args.prefix)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
