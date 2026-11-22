#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 runner for takx/mission-package conformance and admission controls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import urllib.error
import urllib.request

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMA = "uds-takx-mission-package-hosted-v1"
DOWNLOAD_ATTEMPTS = 4


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path}")
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


def maven_central_url(locked_name: str) -> str:
    require(locked_name.endswith(".jar") and "--" in locked_name, f"unexpected dependency filename: {locked_name}")
    group, rest = locked_name.split("--", 1)
    stem = rest[:-4]
    require("-" in stem, f"missing version separator in {locked_name}")
    artifact, version = stem.rsplit("-", 1)
    group_path = group.replace(".", "/")
    return f"https://repo1.maven.org/maven2/{group_path}/{artifact}/{version}/{rest}"


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
    projector = _load_module("mp_projector", HERE / "project_sources.py")
    lock = json.loads((HERE / "inputs.lock.json").read_text(encoding="utf-8"))
    profile = projector.profile()
    deps = {name: maven_central_url(name) for name in sorted(lock["dependencies"])}
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "takx_revision": lock["takx"]["revision"],
        "admission_profile_id": profile["profileId"],
        "dependency_count": len(deps),
        "dependencies": deps,
        "inputs_lock_sha256": sha256_file(HERE / "inputs.lock.json"),
        "admission_profile_sha256": sha256_file(HERE / "admission-profile.json"),
        "qualify_py_sha256": sha256_file(HERE / "qualify.py"),
        "scope": {
            "hosted_linux_jdk17_mission_package": False,
            "full_client_accepted": False,
            "native_transfer_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }


def _download_with_retry(url: str, dest: Path, expected_bytes: int, expected_sha256: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "uds-takserver-takx-mission-package/1"})
            with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
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


def qualify(takx_source: Path, output: Path, java_home: Path | None = None) -> dict:
    qualify_mod = _load_module("mp_qualify", HERE / "qualify.py")
    projector = _load_module("mp_projector", HERE / "project_sources.py")
    lock = json.loads((HERE / "inputs.lock.json").read_text(encoding="utf-8"))
    projector.profile()

    takx = takx_source.resolve()
    out = output.absolute()
    require(not any(p.is_symlink() for p in (out, *out.parents)), "symlink output refused")
    require(not out.exists() and out.parent.is_dir(), "fresh output directory required")
    for root in (REPO, takx):
        require(out != root and not out.is_relative_to(root) and not root.is_relative_to(out), "output overlaps inputs")

    java = resolve_java_home(java_home)
    require((java / "bin/javac").is_file() and (java / "bin/java").is_file(), f"missing JDK 17 tools under {java}")

    out.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="takx-mp-maven-") as tmp:
        maven_dir = Path(tmp) / "maven"
        maven_dir.mkdir(mode=0o700)
        for name, item in sorted(lock["dependencies"].items()):
            _download_with_retry(
                maven_central_url(name),
                maven_dir / name,
                item["bytes"],
                item["sha256"],
            )

        conformance_out = out / "conformance"
        conformance_proof = qualify_mod.qualify(takx, maven_dir, java, conformance_out)

        projection_out = out / "projection"
        projection_receipt = projector.export(takx, projection_out)

        admission_out = out / "admission"
        admission_proof = qualify_mod.qualify(
            takx,
            maven_dir,
            java,
            admission_out,
            admission_projection=projection_out,
        )

    conformance_checks = len(conformance_proof["observed"]["checks"])
    admission_checks = len(admission_proof["observed"]["checks"])
    require(conformance_checks > 0 and admission_checks > 0, "expected positive check counts")

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "takx_revision": lock["takx"]["revision"],
        "admission_profile_id": projection_receipt["profileId"],
        "java_home": str(java),
        "conformance_checks": conformance_checks,
        "admission_checks": admission_checks,
        "conformance_qualification_sha256": sha256_file(conformance_out / "qualification.json"),
        "projection_receipt_sha256": sha256_file(projection_out / "projection.json"),
        "admission_qualification_sha256": sha256_file(admission_out / "qualification.json"),
        "scope": {
            "hosted_linux_jdk17_mission_package": True,
            "full_client_accepted": False,
            "native_transfer_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }
    (out / "hosted-mission-package-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--takx-source", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.phase == "verify":
        require(
            args.takx_source is None and args.output is None and args.java_home is None,
            "qualify paths require explicit --phase qualify",
        )
        result = verify_only()
    else:
        require(args.takx_source is not None and args.output is not None, "--takx-source and --output are required for --phase qualify")
        result = qualify(args.takx_source, args.output, java_home=args.java_home)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
