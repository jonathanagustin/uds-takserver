#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 runner for all seven takx/compat Java compatibility suites."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import time

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMA = "uds-takx-compat-java-hosted-v1"
PINNED_ATAK_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
BITMAP_PATCH = REPO / "takx/patches/0001-kernel-remove-unused-colorfilter-import.patch"
BITMAP_PATCH_SHA256 = "dfafe9c5c670e4a2d62194ac1d6722e1b77b4e94d0d9ec494af6193c88cea2dc"

SUITES = (
    ("pair", "test-pair.sh", False),
    ("uri", "test-uri.sh", False),
    ("context", "test-context.sh", False),
    ("canvas", "test-canvas.sh", False),
    ("region-storage", "test-region-storage.sh", False),
    ("bitmap", "test-bitmap.sh", True),
    ("font", "test-font.sh", True),
)


def require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256_file(path: Path) -> str:
    resolved = Path(path).absolute()
    require(not any(p.is_symlink() for p in (resolved, *resolved.parents)), f"symlink input refused: {resolved}")
    require(stat.S_ISREG(resolved.stat().st_mode), f"regular file required: {resolved}")
    digest = hashlib.sha256()
    with resolved.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_env(atak_source: Path | None = None) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLASSPATH", "BASH_ENV", "ENV", "TAKX_COMPAT_EXTRA_SOURCE_DIR", "TAKX_COMPAT_SUPPORT_ROOT")
        and not k.startswith(("LD_", "DYLD_", "JAVA_", "_JAVA_", "JDK_"))
    }
    env["LC_ALL"] = "C.UTF-8"
    if atak_source is not None:
        env["ATAK_SOURCE_DIR"] = str(atak_source)
    return env


def verify_only() -> dict:
    require(sha256_file(BITMAP_PATCH) == BITMAP_PATCH_SHA256, "reviewed bitmap unused-import patch SHA-256 differs")
    suites = {}
    for name, script, needs_atak in SUITES:
        script_path = HERE / script
        suites[name] = {
            "script": script,
            "requires_pinned_atak": needs_atak,
            "sha256": sha256_file(script_path),
        }
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "pinned_atak_revision": PINNED_ATAK_REVISION,
        "bitmap_patch_sha256": BITMAP_PATCH_SHA256,
        "suite_count": len(SUITES),
        "suites": suites,
        "scope": {
            "hosted_linux_jdk17_compat": False,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }


def _resolve_java_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    for var in ("JAVA_HOME_17_X64", "JAVA_HOME_17_ARM64", "JAVA_HOME"):
        val = os.environ.get(var)
        if val:
            return Path(val).resolve()
    javac = shutil.which("javac")
    if not javac:
        raise ValueError("javac not found; pass --java-home or set JAVA_HOME_17_X64/JAVA_HOME")
    return Path(javac).resolve().parents[1]


def qualify(atak_source: Path, output: Path, java_home: Path | None = None) -> dict:
    contract = verify_only()
    atak = atak_source.resolve()
    out = output.absolute()
    require(not any(p.is_symlink() for p in (out, *out.parents)), "symlink output refused")
    require(not out.exists() and out.parent.is_dir(), "fresh output directory required")
    for root in (REPO, atak):
        require(out != root and not out.is_relative_to(root) and not root.is_relative_to(out), "output overlaps inputs")

    java = _resolve_java_home(java_home)
    javac_bin = java / "bin/javac"
    java_bin = java / "bin/java"
    for required in (javac_bin, java_bin):
        require(required.is_file(), f"missing JDK tool: {required}")

    head = subprocess.run(
        ["git", "-C", str(atak), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()
    require(head == PINNED_ATAK_REVISION, f"pinned ATAK revision mismatch: got {head}")

    out.mkdir(mode=0o700)
    env = clean_env(atak)
    env["PATH"] = f"{java / 'bin'}:{env.get('PATH', '/usr/bin:/bin')}"

    suite_results = {}
    for name, script, needs_atak in SUITES:
        started = time.monotonic()
        proc = subprocess.run(
            ["bash", str(HERE / script)],
            cwd=str(REPO),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
            check=False,
        )
        elapsed = round(time.monotonic() - started, 3)
        log_path = out / f"{name}.log"
        log_path.write_text(proc.stdout, encoding="utf-8")
        if proc.returncode != 0:
            raise RuntimeError(f"compat suite {name} ({script}) failed with exit {proc.returncode}:\n{proc.stdout}")
        suite_results[name] = {
            "script": script,
            "requires_pinned_atak": needs_atak,
            "passed": True,
            "elapsed_seconds": elapsed,
            "log_sha256": sha256_file(log_path),
        }

    require(verify_only()["suites"] == contract["suites"], "compat suite scripts changed during execution")

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "pinned_atak_revision": PINNED_ATAK_REVISION,
        "bitmap_patch_sha256": BITMAP_PATCH_SHA256,
        "java_home": str(java),
        "suites_passed": len(suite_results),
        "suites": suite_results,
        "scope": {
            "hosted_linux_jdk17_compat": True,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    (out / "hosted-compat-java-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--atak-source", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.phase == "verify":
        require(
            args.atak_source is None and args.output is None and args.java_home is None,
            "control paths require explicit --phase qualify",
        )
        result = verify_only()
    else:
        require(args.atak_source is not None and args.output is not None, "--atak-source and --output are required for --phase qualify")
        result = qualify(args.atak_source, args.output, args.java_home)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
