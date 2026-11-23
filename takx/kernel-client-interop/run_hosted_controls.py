#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX Kernel 8.30.5 client interop profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCHEMA = "uds-takx-kernel-client-interop-hosted-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"

INPUTS_LOCK = HERE / "inputs.lock.json"
PATCH_FILE = HERE / "client-contracts.patch"
BUILD_SCRIPT = HERE / "build.py"
VIEWPORT_JAVA = HERE / "java/PublicViewportFit.java"
HEADLESS_CONTROLS_JAVA = HERE / "HeadlessControls.java"

GOOGLE_MAVEN_MIRROR = "https://maven-central.storage-download.googleapis.com/maven2/"
MAVEN_CENTRAL_PREFIXES = (
    "https://repo.maven.apache.org/maven2/",
    "https://repo1.maven.org/maven2/",
)

STARTUP_AND_FATAL_MARKERS = (
    "FATAL ERROR",
    "JNI DETECTED ERROR",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
    "EngineLibrary: Failed",
)

REQUIRED_MARKERS = (
    "PASS_PUBLIC_VIEWPORT_FIT_V1_POLICY",
    "PASS_REAL_ROLE_PUBLIC_BITMAP_CLIENT_MARSHAL_PIXEL_CACHE_AND_ISOLATION_CONTROLS",
    "PASS_REAL_CDE_FONT_FORWARDING_DEFAULT_AND_CONCURRENT_LOCK",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def candidate_urls(url: str) -> list[str]:
    """Return Google Maven Central mirror ahead of upstream Maven Central to avoid 429s."""
    urls: list[str] = []
    for prefix in MAVEN_CENTRAL_PREFIXES:
        if url.startswith(prefix):
            rel = url[len(prefix) :]
            urls.append(f"{GOOGLE_MAVEN_MIRROR}{rel}")
            break
    urls.append(url)
    return urls


def verify_offline() -> dict:
    """Validate frozen kernel-client-interop lockfile, patch, and owned Java sources."""
    for path in (
        INPUTS_LOCK,
        PATCH_FILE,
        BUILD_SCRIPT,
        VIEWPORT_JAVA,
        HEADLESS_CONTROLS_JAVA,
    ):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(
                f"missing or empty required kernel-client-interop input: {path}"
            )

    lock = json.loads(INPUTS_LOCK.read_text(encoding="utf-8"))
    if lock.get("schema") != 1:
        raise ValueError(f"unexpected inputs.lock.json schema: {lock.get('schema')}")
    if lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(
            f"unexpected official_revision: {lock.get('official_revision')}"
        )
    patch_sha = _sha256_file(PATCH_FILE)
    if patch_sha != lock.get("patch_sha256"):
        raise ValueError(f"client-contracts.patch SHA-256 mismatch: {patch_sha}")

    viewport_sha = _sha256_file(VIEWPORT_JAVA)
    expected_viewport = lock.get("owned_java", {}).get("java/PublicViewportFit.java")
    if viewport_sha != expected_viewport:
        raise ValueError(f"PublicViewportFit.java SHA-256 mismatch: {viewport_sha}")

    files = lock.get("files", {})
    if len(files) != 9:
        raise ValueError(f"expected 9 locked upstream files, found {len(files)}")

    public_jars = lock.get("public_runtime_jars", {})
    if not public_jars:
        raise ValueError("public_runtime_jars missing in inputs.lock.json")

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": EXPECTED_REVISION,
        "profile": lock.get("profile"),
        "viewport_policy": lock.get("viewport_policy"),
        "locked_upstream_files": len(files),
        "public_runtime_jars": sorted(public_jars),
        "inputs_lock_sha256": _sha256_file(INPUTS_LOCK),
        "patch_sha256": patch_sha,
        "viewport_java_sha256": viewport_sha,
        "headless_controls_sha256": _sha256_file(HEADLESS_CONTROLS_JAVA),
        "build_py_sha256": _sha256_file(BUILD_SCRIPT),
    }


def download_verified_jar(name: str, spec: dict, dest_dir: Path) -> Path:
    """Fetch a locked Maven JAR with Google mirror fallback and exact byte/SHA-256 verification."""
    dest = dest_dir / name
    expected_sha = spec["sha256"]
    expected_bytes = int(spec["bytes"])

    if (
        dest.is_file()
        and _sha256_file(dest) == expected_sha
        and dest.stat().st_size == expected_bytes
    ):
        return dest

    last_err: Exception | None = None
    for url in candidate_urls(spec["url"]):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "uds-takserver-takx-ci/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            if len(data) != expected_bytes:
                raise ValueError(
                    f"size mismatch for {name} from {url}: {len(data)} != {expected_bytes}"
                )
            got_sha = hashlib.sha256(data).hexdigest()
            if got_sha != expected_sha:
                raise ValueError(
                    f"SHA-256 mismatch for {name} from {url}: {got_sha} != {expected_sha}"
                )
            dest.write_bytes(data)
            return dest
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(f"failed to fetch verified {name}: {last_err}")


def audit_controls_output(stdout: str, stderr: str, exit_code: int) -> dict:
    """Enforce zero-exit, clean headless startup/runtime diagnostics, and all control markers."""
    if exit_code != 0:
        raise ValueError(f"HeadlessControls exited with {exit_code}")

    combined = f"{stdout}\n{stderr}"
    diags = [
        line
        for line in combined.splitlines()
        if any(marker in line for marker in STARTUP_AND_FATAL_MARKERS)
    ]
    if diags:
        raise ValueError(
            f"startup or fatal diagnostic in HeadlessControls: {diags[:3]}"
        )

    missing = [marker for marker in REQUIRED_MARKERS if marker not in stdout]
    if missing:
        raise ValueError(f"missing required headless control markers: {missing}")

    match = re.search(r"PASS_REAL_HEADLESS_CONTROLS checks=(\d+)", stdout)
    if not match or int(match.group(1)) < 1:
        raise ValueError("missing PASS_REAL_HEADLESS_CONTROLS checks count")

    return {
        "qualified": True,
        "exit_code": 0,
        "checks": int(match.group(1)),
        "startup_and_fatal_diagnostics": 0,
        "xcheck_jni_enabled": False,
        "required_markers_count": len(REQUIRED_MARKERS) + 1,
    }


def _clean_env(java_home: Path, tmp_dir: Path) -> dict[str, str]:
    removed = {
        "CLASSPATH",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "BASH_ENV",
        "ENV",
        "PYTHONPATH",
        "_JAVA_OPTIONS",
        "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "JDK_JAVAC_OPTIONS",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in removed
        and not k.startswith(
            ("LD_", "DYLD_", "JAVA_", "JDK_", "CCACHE_", "GDAL_", "CPL_", "PROJ_")
        )
    }
    env.update(
        JAVA_HOME=str(java_home),
        TMPDIR=str(tmp_dir),
        LC_ALL="C.UTF-8",
        PYTHONNOUSERSITE="1",
    )
    return env


def qualify(
    atak_source: Path,
    sdk_package_dir: Path,
    java_home: Path,
    output_dir: Path,
) -> dict:
    """Apply client-contracts.patch, compile the 8 overlay Java sources, and run HeadlessControls."""
    verified = verify_offline()
    lock = json.loads(INPUTS_LOCK.read_text(encoding="utf-8"))

    atak_source = atak_source.resolve()
    sdk_package_dir = sdk_package_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")

    sdk_jar = sdk_package_dir / "kernel-8.30.5-linux-amd64-public-cpu.jar"
    third_party_dir = sdk_package_dir / "third-party"
    if not sdk_jar.is_file() or sdk_jar.is_symlink():
        raise FileNotFoundError(f"missing regular SDK JAR: {sdk_jar}")
    if not third_party_dir.is_dir():
        raise FileNotFoundError(f"missing third-party directory: {third_party_dir}")

    dep_jars = sorted(third_party_dir.glob("*.jar"))
    if not dep_jars:
        raise FileNotFoundError(f"no dependency JARs found under {third_party_dir}")

    java_bin = java_home / "bin/java"
    javac_bin = java_home / "bin/javac"
    for tool in (java_bin, javac_bin):
        if not tool.is_file():
            raise FileNotFoundError(f"missing JDK tool: {tool}")

    output_dir.mkdir(parents=True, exist_ok=True)
    stage_dir = output_dir / "source"
    jars_dir = output_dir / "public-jars"
    classes_dir = output_dir / "classes"
    headers_dir = output_dir / "jni-headers"
    controls_classes_dir = output_dir / "control-classes"
    empty_dir = output_dir / "empty"
    control_data_dir = output_dir / "control-data"
    tmp_dir = output_dir / "tmp"
    for sub in (
        stage_dir,
        jars_dir,
        classes_dir,
        headers_dir,
        controls_classes_dir,
        empty_dir,
        control_data_dir,
        tmp_dir,
    ):
        sub.mkdir(parents=True, exist_ok=True)

    for rel, spec in lock["files"].items():
        src = atak_source / rel
        if not src.is_file():
            raise FileNotFoundError(f"missing pinned upstream file: {src}")
        got_before = _sha256_file(src)
        if got_before != spec["before"]:
            raise ValueError(
                f"pre-patch SHA-256 mismatch for {rel}: {got_before} != {spec['before']}"
            )
        dst = stage_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)

    subprocess.run(
        [
            "patch",
            "--batch",
            "--fuzz=0",
            "--no-backup-if-mismatch",
            "-p1",
            "-d",
            str(stage_dir),
            "-i",
            str(PATCH_FILE),
        ],
        check=True,
        timeout=30,
    )

    for rel, spec in lock["files"].items():
        got_after = _sha256_file(stage_dir / rel)
        if got_after != spec["after"]:
            raise ValueError(
                f"post-patch SHA-256 mismatch for {rel}: {got_after} != {spec['after']}"
            )

    policy_dst = (
        stage_dir
        / "takkernel/shared/src/main/java/gov/tak/platform/symbology/milstd2525/PublicViewportFit.java"
    )
    shutil.copyfile(VIEWPORT_JAVA, policy_dst)

    runtime_jars: list[Path] = []
    runtime_jar_hashes: dict[str, str] = {}
    for jar_name, spec in sorted(lock["public_runtime_jars"].items()):
        fetched = download_verified_jar(jar_name, spec, jars_dir)
        runtime_jars.append(fetched)
        runtime_jar_hashes[jar_name] = _sha256_file(fetched)

    overlay_sources = [
        stage_dir / rel for rel in lock["files"] if rel.endswith(".java")
    ] + [policy_dst]
    base_cp = os.pathsep.join(str(p) for p in [sdk_jar, *dep_jars, *runtime_jars])
    env = _clean_env(java_home, tmp_dir)

    subprocess.run(
        [
            str(javac_bin),
            "-J-Xmx768m",
            "-J-XX:ActiveProcessorCount=2",
            "--release",
            "17",
            "-proc:none",
            "-implicit:none",
            "-sourcepath",
            str(empty_dir),
            "-cp",
            base_cp,
            "-d",
            str(classes_dir),
            "-h",
            str(headers_dir),
            *(str(p) for p in overlay_sources),
        ],
        env=env,
        check=True,
        timeout=120,
    )

    full_cp = os.pathsep.join([str(classes_dir), base_cp])
    subprocess.run(
        [
            str(javac_bin),
            "--release",
            "17",
            "-proc:none",
            "-cp",
            full_cp,
            "-d",
            str(controls_classes_dir),
            str(HEADLESS_CONTROLS_JAVA),
        ],
        env=env,
        check=True,
        timeout=120,
    )

    proc = subprocess.run(
        [
            str(java_bin),
            "-Djava.awt.headless=true",
            "-Xmx512m",
            "-XX:ActiveProcessorCount=2",
            "-cp",
            os.pathsep.join([str(controls_classes_dir), full_cp]),
            "gov.tak.platform.symbology.milstd2525.HeadlessControls",
            str(control_data_dir),
        ],
        env=env,
        cwd=output_dir,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"HeadlessControls failed ({proc.returncode}):\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    audit = audit_controls_output(proc.stdout, proc.stderr, proc.returncode)
    class_files = sorted(classes_dir.rglob("*.class"))
    header_files = sorted(headers_dir.rglob("*.h"))

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "revision": EXPECTED_REVISION,
        "profile": lock["profile"],
        "viewport_policy": lock["viewport_policy"],
        "sdk_jar_sha256": _sha256_file(sdk_jar),
        "public_runtime_jars": runtime_jar_hashes,
        "compiled_overlay_classes": len(class_files),
        "generated_jni_headers": [p.name for p in header_files],
        "verified": verified,
        "controls": audit,
        "scope": {
            "hosted_linux_jdk17_kernel_client_interop": True,
            "sdk_native_libs_rejected_in_proc_maps": True,
            "xcheck_jni_enabled": False,
            "native_object_or_link_performed": False,
            "new_jni_executed": False,
            "native_feature_acceptance": False,
            "native_execution_accepted": False,
            "multipoint_detail_acceptance": False,
            "private_sdk_equivalence": False,
            "sdk_accepted": False,
            "security_accepted": False,
            "full_client_accepted": False,
        },
    }
    (output_dir / "hosted-kernel-client-interop-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--atak-source", type=Path)
    parser.add_argument("--sdk-package-dir", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        if any(
            v is not None
            for v in (
                args.atak_source,
                args.sdk_package_dir,
                args.java_home,
                args.output,
            )
        ):
            parser.error("--phase verify does not accept qualify paths")
        print(json.dumps(verify_offline(), indent=2))
        return 0

    if not all((args.atak_source, args.sdk_package_dir, args.java_home, args.output)):
        parser.error(
            "--phase qualify requires --atak-source, --sdk-package-dir, --java-home, and --output"
        )
    receipt = qualify(
        atak_source=args.atak_source,
        sdk_package_dir=args.sdk_package_dir,
        java_home=args.java_home,
        output_dir=args.output,
    )
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
