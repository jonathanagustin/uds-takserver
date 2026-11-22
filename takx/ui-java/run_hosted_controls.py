#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 verifier and live source builder for takx/ui-java."""

from __future__ import annotations

import argparse
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
SCHEMA = "uds-takx-ui-java-hosted-v1"
DOWNLOAD_ATTEMPTS = 4


def _load_build():
    spec = importlib.util.spec_from_file_location("ui_java_build", HERE / "build.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load takx/ui-java/build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def require(value: object, message: str) -> None:
    if not value:
        raise ValueError(message)


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


def verify_only(profile: str = "original") -> dict:
    build_mod = _load_build()
    lock = json.loads((HERE / "inputs.lock.json").read_text(encoding="utf-8"))
    artifacts = build_mod.selected_artifacts(lock, profile)
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profile": profile,
        "java_major": lock["java_major"],
        "java_release": lock["java_release"],
        "artifact_count": len(artifacts),
        "artifacts": sorted(artifacts),
        "inputs_lock_sha256": build_mod.sha256(HERE / "inputs.lock.json"),
        "build_py_sha256": build_mod.sha256(HERE / "build.py"),
        "scope": {
            "hosted_linux_jdk17_ui_java": False,
            "complete_client_or_runtime_acceptance": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }


MAVEN_CENTRAL_PREFIXES = (
    "https://repo.maven.apache.org/maven2/",
    "https://repo1.maven.org/maven2/",
)
MAVEN_CENTRAL_MIRRORS = (
    "https://maven-central.storage-download.googleapis.com/maven2/",
    "https://repo1.maven.org/maven2/",
    "https://repo.maven.apache.org/maven2/",
)


def candidate_urls(url: str) -> list[str]:
    for prefix in MAVEN_CENTRAL_PREFIXES:
        if url.startswith(prefix):
            suffix = url[len(prefix):]
            return [mirror + suffix for mirror in MAVEN_CENTRAL_MIRRORS]
    return [url]


def _download_with_retry(url: str, dest: Path, expected_bytes: int, expected_sha256: str, build_mod) -> None:
    urls = candidate_urls(url)
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        for candidate in urls:
            try:
                req = urllib.request.Request(candidate, headers={"User-Agent": "uds-takserver-takx-ui-java/1"})
                with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
                    shutil.copyfileobj(resp, out)
                build_mod.checked_file(dest, {"bytes": expected_bytes, "sha256": expected_sha256}, dest.name)
                return
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                last_error = exc
                if dest.exists():
                    dest.unlink()
        if attempt < DOWNLOAD_ATTEMPTS:
            time.sleep(2 * attempt)
    raise RuntimeError(f"failed to download verified {dest.name} from {urls}: {last_error}")


def qualify(output: Path, java_home: Path | None = None, profile: str = "original", timeout: int = 240) -> dict:
    build_mod = _load_build()
    lock = json.loads((HERE / "inputs.lock.json").read_text(encoding="utf-8"))
    artifacts = build_mod.selected_artifacts(lock, profile)

    out = output.absolute()
    require(not any(p.is_symlink() for p in (out, *out.parents)), "symlink output refused")
    require(not out.exists() and out.parent.is_dir(), "fresh output directory required")
    require(out != REPO and not out.is_relative_to(REPO) and not REPO.is_relative_to(out), "output overlaps repository")

    java = resolve_java_home(java_home)
    require((java / "bin/javac").is_file() and (java / "bin/java").is_file(), f"missing JDK 17 tools under {java}")

    source_names = {artifact["source"] for artifact in artifacts.values()}
    source_names.update(
        notice["source"]
        for artifact in artifacts.values()
        for notice in artifact["notices"]
        if notice["source"] != "license"
    )
    required_jars = {
        entry
        for artifact in artifacts.values()
        for entry in artifact["classpath"]
        if not entry.startswith("artifact:")
    }
    required_jars.update(artifact["generator"]["tool"] for artifact in artifacts.values() if "generator" in artifact)
    license_required = any(
        notice["source"] == "license"
        for artifact in artifacts.values()
        for notice in artifact["notices"]
    )

    out.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="takx-ui-java-inputs-") as tmp:
        tmp_root = Path(tmp)
        sources_dir = tmp_root / "sources"
        classpath_dir = tmp_root / "classpath"
        sources_dir.mkdir(mode=0o700)
        classpath_dir.mkdir(mode=0o700)

        for name in sorted(source_names):
            item = lock["archives"][name]
            _download_with_retry(
                item["url"],
                sources_dir / item["file"],
                item["bytes"],
                item["sha256"],
                build_mod,
            )
        if license_required:
            lic = lock["license_text"]
            _download_with_retry(
                lic["url"],
                sources_dir / lic["file"],
                lic["bytes"],
                lic["sha256"],
                build_mod,
            )
        for jar_name in sorted(required_jars):
            jar = lock["public_jars"][jar_name]
            _download_with_retry(
                jar["url"],
                classpath_dir / jar_name,
                jar["bytes"],
                jar["sha256"],
                build_mod,
            )

        build_out = out / "product"
        build_receipt = build_mod.build(sources_dir, classpath_dir, build_out, java, profile=profile, timeout=timeout)

    require(len(build_receipt["artifacts"]) == len(artifacts), "unexpected built artifact count")
    require(build_receipt["complete_client_or_runtime_acceptance"] is False, "broad acceptance flag must remain false")

    summary = {
        "schema": SCHEMA,
        "phase": "qualify",
        "profile": profile,
        "inputs_lock_sha256": build_mod.sha256(HERE / "inputs.lock.json"),
        "build_py_sha256": build_mod.sha256(HERE / "build.py"),
        "toolchain": build_receipt["toolchain"],
        "artifact_count": len(build_receipt["artifacts"]),
        "artifacts": {
            row["project_artifact"]: {
                "jar_sha256": row["artifact"]["sha256"],
                "jar_bytes": row["artifact"]["bytes"],
                "sources_jar_sha256": row["source_archive"]["sha256"],
                "class_count": len(row["class_files"]),
            }
            for row in build_receipt["artifacts"]
        },
        "build_receipt_sha256": build_mod.sha256(out / "product/receipt.json"),
        "scope": {
            "hosted_linux_jdk17_ui_java": True,
            "complete_client_or_runtime_acceptance": False,
            "sdk_accepted": False,
            "security_accepted": False,
        },
    }
    (out / "hosted-ui-java-receipt.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--profile", choices=("original", "wizard-reference"), default="original")
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if args.phase == "verify":
        require(args.output is None and args.java_home is None, "qualify paths require explicit --phase qualify")
        result = verify_only(profile=args.profile)
    else:
        require(args.output is not None, "--output is required for --phase qualify")
        result = qualify(args.output, java_home=args.java_home, profile=args.profile)

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
