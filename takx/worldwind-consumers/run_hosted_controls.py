#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux runner for takx/worldwind-consumers 5.7 and 5.8 staging qualification."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMA = "uds-takx-worldwind-consumers-hosted-v1"
REQUIRED_PROFILES = ("5.7", "5.8")


def _load_stage_module():
    spec = importlib.util.spec_from_file_location("worldwind_consumers_stage", HERE / "stage.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {HERE / 'stage.py'}")
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


def verify_only() -> dict:
    lock = json.loads((HERE / "sources.lock.json").read_text(encoding="utf-8"))
    patch_path = HERE / lock["patch"]
    require(patch_path.is_file(), f"missing patch file {patch_path}")
    require(sha256_file(patch_path) == lock["patch_sha256"], "consumer patch SHA-256 mismatch")
    profiles = lock.get("profiles", {})
    require(set(profiles) == set(REQUIRED_PROFILES), f"expected profiles {REQUIRED_PROFILES}, got {sorted(profiles)}")
    summary = {}
    for name in REQUIRED_PROFILES:
        entry = profiles[name]
        require(len(entry["sources"]) == 21, f"expected 21 locked sources for profile {name}")
        summary[name] = {
            "revision": entry["revision"],
            "source_count": len(entry["sources"]),
        }
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profiles": summary,
        "patch": lock["patch"],
        "patch_sha256": lock["patch_sha256"],
        "sources_lock_sha256": sha256_file(HERE / "sources.lock.json"),
        "stage_py_sha256": sha256_file(HERE / "stage.py"),
        "scope": {
            "hosted_linux_worldwind_consumers_staging": False,
            "native_or_client_acceptance": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }


def qualify(takx_57: Path, takx_58: Path, output: Path) -> dict:
    verify_only()
    stage_mod = _load_stage_module()
    lock = json.loads((HERE / "sources.lock.json").read_text(encoding="utf-8"))
    patch_path = HERE / lock["patch"]

    src_57 = takx_57.resolve()
    src_58 = takx_58.resolve()
    out = output.absolute()
    require(not any(p.is_symlink() for p in (out, *out.parents)), "symlink output refused")
    require(not out.exists() and out.parent.is_dir(), "fresh output directory required")
    for root in (REPO, src_57, src_58):
        require(out != root and not out.is_relative_to(root) and not root.is_relative_to(out), "output overlaps inputs")

    out.mkdir(mode=0o700)
    staged_profiles = {}
    for label, source_dir in (("5.7", src_57), ("5.8", src_58)):
        profile_out = out / f"takx-{label}"
        receipt = stage_mod.stage(source_dir, profile_out, lock, patch_path)
        require(receipt["profile"] == label, f"expected profile {label}, got {receipt['profile']}")
        require(len(receipt["sources"]) == 21, f"expected 21 staged sources for {label}")
        staged_profiles[label] = {
            "revision": receipt["revision"],
            "source_count": len(receipt["sources"]),
            "staging_receipt_sha256": sha256_file(profile_out / "staging-receipt.json"),
        }

    result = {
        "schema": SCHEMA,
        "phase": "qualify",
        "patch_sha256": lock["patch_sha256"],
        "profiles": staged_profiles,
        "scope": {
            "hosted_linux_worldwind_consumers_staging": True,
            "native_or_client_acceptance": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }
    (out / "hosted-worldwind-consumers-receipt.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--takx-57", type=Path)
    parser.add_argument("--takx-58", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.phase == "verify":
        require(
            args.takx_57 is None and args.takx_58 is None and args.output is None,
            "qualify paths require explicit --phase qualify",
        )
        result = verify_only()
    else:
        require(
            args.takx_57 is not None and args.takx_58 is not None and args.output is not None,
            "--takx-57, --takx-58, and --output are required for --phase qualify",
        )
        result = qualify(args.takx_57, args.takx_58, args.output)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
