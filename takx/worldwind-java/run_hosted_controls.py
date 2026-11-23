#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 runner for takx/worldwind-java standard and interop builds."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMA = "uds-takx-worldwind-java-hosted-v1"
DOWNLOAD_ATTEMPTS = 4


def _load_build_module():
    spec = importlib.util.spec_from_file_location("worldwind_java_build", HERE / "build.py")
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
    build_mod = _load_build_module()
    lock = json.loads((HERE / "inputs.lock.json").read_text(encoding="utf-8"))
    build_mod.validate(lock)
    interop = build_mod.interop_contract()
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "worldwind_revision": lock["revision"],
        "java_count": lock["java_count"],
        "standard_profile": lock["profile"],
        "interop_profile": interop["profile"],
        "public_jars": {name: item["url"] for name, item in sorted(lock["public_jars"].items())},
        "inputs_lock_sha256": sha256_file(HERE / "inputs.lock.json"),
        "interop_lock_sha256": sha256_file(HERE / "interop.lock.json"),
        "build_py_sha256": sha256_file(HERE / "build.py"),
        "scope": {
            "hosted_linux_jdk17_worldwind_java": False,
            "native_built_or_loaded": False,
            "full_client_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }


def _download_with_retry(url: str, dest: Path, expected_bytes: int, expected_sha256: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "uds-takserver-takx-worldwind-java/1"})
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


def _run_profile_build(
    worldwind_source: Path,
    public_jars: Path,
    gis_build: Path,
    profile_out: Path,
    java_home: Path,
    interop: bool,
) -> dict:
    env = dict(os.environ)
    env["JAVA_HOME"] = str(java_home)
    env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    cmd = [
        sys.executable,
        "-B",
        str(HERE / "build.py"),
        "--source",
        str(worldwind_source),
        "--public-jars",
        str(public_jars),
        "--gis-build",
        str(gis_build),
        "--output",
        str(profile_out),
    ]
    if interop:
        cmd.append("--interop")
    subprocess.run(cmd, env=env, check=True, timeout=300)
    state = json.loads((profile_out / "build-state.json").read_text(encoding="utf-8"))
    require(state.get("status") == "complete" or state.get("artifact"), f"incomplete build state in {profile_out}")
    artifact = state["artifact"]
    jar_path = profile_out / artifact["name"]
    require(jar_path.is_file(), f"missing built artifact {jar_path}")
    require(sha256_file(jar_path) == artifact["sha256"], f"artifact SHA-256 mismatch for {jar_path}")
    return {
        "artifact_name": artifact["name"],
        "artifact_sha256": artifact["sha256"],
        "entries": artifact["entries"],
        "java_sources": state["java_sources"],
        "build_state_sha256": sha256_file(profile_out / "build-state.json"),
    }


def qualify(
    worldwind_source: Path,
    gis_build: Path,
    output: Path,
    java_home: Path | None = None,
) -> dict:
    verify_only()
    lock = json.loads((HERE / "inputs.lock.json").read_text(encoding="utf-8"))

    src = worldwind_source.resolve()
    gis = gis_build.resolve()
    out = output.absolute()
    require(not any(p.is_symlink() for p in (out, *out.parents)), "symlink output refused")
    require(not out.exists() and out.parent.is_dir(), "fresh output directory required")
    for root in (REPO, src, gis):
        require(out != root and not out.is_relative_to(root) and not root.is_relative_to(out), "output overlaps inputs")

    java = resolve_java_home(java_home)
    require((java / "bin/javac").is_file() and (java / "bin/java").is_file(), f"missing JDK 17 tools under {java}")

    out.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="takx-worldwind-jars-") as tmp:
        jars_dir = Path(tmp) / "public-jars"
        jars_dir.mkdir(mode=0o700)
        for name, item in sorted(lock["public_jars"].items()):
            _download_with_retry(
                item["url"],
                jars_dir / name,
                item["bytes"],
                item["sha256"],
            )

        standard_result = _run_profile_build(src, jars_dir, gis, out / "standard", java, interop=False)
        interop_result = _run_profile_build(src, jars_dir, gis, out / "interop", java, interop=True)

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "worldwind_revision": lock["revision"],
        "java_home": str(java),
        "standard": standard_result,
        "interop": interop_result,
        "scope": {
            "hosted_linux_jdk17_worldwind_java": True,
            "native_built_or_loaded": False,
            "full_client_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }
    (out / "hosted-worldwind-java-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--worldwind-source", type=Path)
    parser.add_argument("--gis-build", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.phase == "verify":
        require(
            args.worldwind_source is None
            and args.gis_build is None
            and args.output is None
            and args.java_home is None,
            "qualify paths require explicit --phase qualify",
        )
        result = verify_only()
    else:
        require(
            args.worldwind_source is not None and args.gis_build is not None and args.output is not None,
            "--worldwind-source, --gis-build, and --output are required for --phase qualify",
        )
        result = qualify(args.worldwind_source, args.gis_build, args.output, java_home=args.java_home)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
