#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX 5.8 elevation-download-compat."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE_FILE = HERE / "source-profile.json"
KERNEL_PATCH_FILE = HERE / "kernel.patch"
TAKX58_PATCH_FILE = HERE / "takx58.patch"
CONTROLS_JAVA_FILE = HERE / "ElevationDownloadControls.java"

SCHEMA = "uds-takx-elevation-download-compat-hosted-v1"
EXPECTED_PROFILE_SHA = (
    "b7627fdda6f97481422be0ffec12b2fe3c206a35f44ce8030cbdee198da4a3b0"
)
EXPECTED_CONTROLS_SHA = (
    "8edd6dfefdc0a3a40471a11ac1787a024195932de087124dea3b46a39d119b67"
)
EXPECTED_ASSERTIONS = 53

STRICT_JNI_AND_STARTUP_MARKERS = (
    "WARNING in native method",
    "WARNING: JNI",
    "FATAL ERROR",
    "JNI DETECTED ERROR",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
    "EngineLibrary: Failed to load",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_source_text(path: Path) -> str:
    raw = path.read_bytes()
    # Windows core.autocrlf=true expands LF->CRLF on checkout; Linux CI verifies raw bytes.
    if os.name == "nt":
        raw = raw.replace(b"\r\n", b"\n")
    return _sha256_bytes(raw)


def verify_offline() -> dict:
    """Validate pinned source-profile.json, patches, and ElevationDownloadControls.java."""
    for path in (
        PROFILE_FILE,
        KERNEL_PATCH_FILE,
        TAKX58_PATCH_FILE,
        CONTROLS_JAVA_FILE,
    ):
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError(
                f"missing required elevation-download-compat input: {path}"
            )

    profile_sha = _sha256_source_text(PROFILE_FILE)
    if profile_sha != EXPECTED_PROFILE_SHA:
        raise ValueError(
            f"source-profile.json digest mismatch: {profile_sha} != {EXPECTED_PROFILE_SHA}"
        )

    profile = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    kernel_entry = profile["sources"]["kernel"]
    takx58_entry = profile["sources"]["takx58"]

    kernel_patch_sha = _sha256_source_text(KERNEL_PATCH_FILE)
    if kernel_patch_sha != kernel_entry["patchSHA256"]:
        raise ValueError(
            f"kernel.patch digest mismatch: {kernel_patch_sha} != {kernel_entry['patchSHA256']}"
        )

    takx58_patch_sha = _sha256_source_text(TAKX58_PATCH_FILE)
    if takx58_patch_sha != takx58_entry["patchSHA256"]:
        raise ValueError(
            f"takx58.patch digest mismatch: {takx58_patch_sha} != {takx58_entry['patchSHA256']}"
        )

    controls_sha = _sha256_source_text(CONTROLS_JAVA_FILE)
    if controls_sha != EXPECTED_CONTROLS_SHA:
        raise ValueError(
            f"ElevationDownloadControls.java digest mismatch: {controls_sha} != {EXPECTED_CONTROLS_SHA}"
        )

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "kernel_revision": profile["kernelRevision"],
        "takx_revision": profile["takxRevision"],
        "source_profile_sha256": profile_sha,
        "kernel_patch_sha256": kernel_patch_sha,
        "takx58_patch_sha256": takx58_patch_sha,
        "controls_java_sha256": controls_sha,
        "expected_assertions": EXPECTED_ASSERTIONS,
    }


def audit_controls_output(stdout: str, stderr: str, returncode: int) -> dict[str, int]:
    """Verify exact 53-assertion pass marker and zero strict -Xcheck:jni diagnostics."""
    if returncode != 0:
        raise RuntimeError(
            f"ElevationDownloadControls exited with code {returncode}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}"
        )

    combined = f"{stdout}\n{stderr}"
    diags = [
        line
        for line in combined.splitlines()
        if any(m in line for m in STRICT_JNI_AND_STARTUP_MARKERS)
    ]
    if diags:
        raise ValueError(
            f"strict -Xcheck:jni or startup diagnostic detected: {diags[:3]}"
        )

    match = re.search(
        rf"PASS_REAL_ELEVATION_DOWNLOAD_CONTROLS assertions={EXPECTED_ASSERTIONS}\b",
        stdout,
    )
    if not match:
        raise ValueError(
            f"missing PASS_REAL_ELEVATION_DOWNLOAD_CONTROLS assertions={EXPECTED_ASSERTIONS} marker"
        )

    return {
        "assertions": EXPECTED_ASSERTIONS,
        "strict_jni_diagnostics": 0,
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
            (
                "LD_",
                "DYLD_",
                "JAVA_",
                "JDK_",
                "CCACHE_",
                "GDAL_",
                "CPL_",
                "PROJ_",
                "OPENSSL_",
            )
        )
    }
    env.update(
        JAVA_HOME=str(java_home),
        PATH=f"{java_home / 'bin'}:{env.get('PATH', '')}",
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
    """Patch ElevationDownloader.java, compile with SDK JAR, and execute 53 behavioral assertions under -Xcheck:jni."""
    verified = verify_offline()
    profile = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    kernel_entry = profile["sources"]["kernel"]

    atak_source = atak_source.resolve()
    sdk_package_dir = sdk_package_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")

    sdk_jar = sdk_package_dir / "kernel-8.30.5-linux-amd64-public-cpu.jar"
    manifest_path = sdk_package_dir / "version-abi-manifest.json"
    third_party_dir = sdk_package_dir / "third-party"
    for req_file in (sdk_jar, manifest_path):
        if req_file.is_symlink() or not req_file.is_file():
            raise FileNotFoundError(f"missing SDK package artifact: {req_file}")
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

    rel_source = Path(kernel_entry["path"])
    upstream_downloader = atak_source / rel_source
    if upstream_downloader.is_symlink() or not upstream_downloader.is_file():
        raise FileNotFoundError(
            f"missing upstream ElevationDownloader.java: {upstream_downloader}"
        )
    if _sha256_file(upstream_downloader) != kernel_entry["preimageSHA256"]:
        raise ValueError(
            f"upstream ElevationDownloader.java preimage mismatch: {upstream_downloader}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    patched_root = output_dir / "patched-src"
    patched_file = patched_root / rel_source
    patched_file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(upstream_downloader, patched_file)

    patch_bin = shutil.which("patch")
    if not patch_bin:
        raise FileNotFoundError("patch binary not found in PATH")

    subprocess.run(
        [
            patch_bin,
            "--batch",
            "--fuzz=0",
            "--no-backup-if-mismatch",
            "-p1",
            "-d",
            str(patched_root),
            "-i",
            str(KERNEL_PATCH_FILE),
        ],
        check=True,
        timeout=30,
    )
    postimage_sha = _sha256_file(patched_file)
    if postimage_sha != kernel_entry["postimageSHA256"]:
        raise ValueError(
            f"patched ElevationDownloader.java postimage mismatch: {postimage_sha}"
        )

    classes_dir = output_dir / "classes"
    tmp_dir = output_dir / "tmp"
    home_dir = output_dir / "home"
    data_dir = output_dir / "data"
    for sub in (classes_dir, tmp_dir, home_dir, data_dir):
        sub.mkdir(parents=True, exist_ok=True)

    env = _clean_env(java_home, tmp_dir)
    sdk_cp = os.pathsep.join(str(p) for p in [sdk_jar, *dep_jars])
    subprocess.run(
        [
            str(javac_bin),
            "-J-Xmx256m",
            "--release",
            "17",
            "-proc:none",
            "-cp",
            sdk_cp,
            "-d",
            str(classes_dir),
            str(patched_file),
            str(CONTROLS_JAVA_FILE),
        ],
        env=env,
        check=True,
        timeout=120,
    )

    jar_md5 = hashlib.md5(sdk_jar.read_bytes(), usedforsecurity=False).hexdigest()
    library_path = tmp_dir / "takkernel/engine" / jar_md5 / "lib/linux64"
    dted_root = output_dir / "dted-root"
    if dted_root.exists():
        raise ValueError(
            f"non-existent dted-root required before execution: {dted_root}"
        )

    run_cp = f"{classes_dir}{os.pathsep}{sdk_cp}"
    proc = subprocess.run(
        [
            str(java_bin),
            "-Xcheck:jni",
            "-Xmx256m",
            "-XX:ActiveProcessorCount=2",
            "-Djava.awt.headless=true",
            f"-Djava.library.path={library_path}",
            f"-Djava.io.tmpdir={tmp_dir}",
            f"-Duser.home={home_dir}",
            f"-DTAK_APP_DATA_PATH={data_dir}",
            "-cp",
            run_cp,
            "gov.tak.platform.client.dted.ElevationDownloadControls",
            str(dted_root),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    controls_audit = audit_controls_output(proc.stdout, proc.stderr, proc.returncode)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native_manifest = manifest["native_resources"]
    extracted_natives: dict[str, str] = {}
    if library_path.is_dir():
        for lib_name, expected_sha in sorted(native_manifest.items()):
            lib_file = library_path / lib_name
            if not lib_file.is_file():
                raise FileNotFoundError(f"missing extracted native library: {lib_file}")
            actual_sha = _sha256_file(lib_file)
            if actual_sha != expected_sha:
                raise ValueError(
                    f"extracted native hash mismatch for {lib_name}: {actual_sha} != {expected_sha}"
                )
            extracted_natives[lib_name] = actual_sha

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "patched_downloader": {
            "path": kernel_entry["path"],
            "preimage_sha256": kernel_entry["preimageSHA256"],
            "postimage_sha256": postimage_sha,
        },
        "sdk_jar": {
            "name": sdk_jar.name,
            "sha256": _sha256_file(sdk_jar),
            "third_party_jars": len(dep_jars),
        },
        "compiled_classes": sorted(
            p.relative_to(classes_dir).as_posix() for p in classes_dir.rglob("*.class")
        ),
        "extracted_native_providers": extracted_natives,
        "controls": controls_audit,
        "scope": {
            "elevation_download_controls_passed": True,
            "xcheck_jni_clean": True,
            "platform_build_accepted": False,
            "client_accepted": False,
            "network_download_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-elevation-download-compat-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--atak-source", type=Path)
    parser.add_argument("--sdk-package-dir", type=Path)
    parser.add_argument(
        "--java-home", type=Path, default=Path("/usr/lib/jvm/java-17-openjdk-amd64")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    required = (
        ("--atak-source", args.atak_source),
        ("--sdk-package-dir", args.sdk_package_dir),
        ("--output", args.output),
    )
    missing = [flag for flag, val in required if val is None]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")

    qualify(
        atak_source=args.atak_source,
        sdk_package_dir=args.sdk_package_dir,
        java_home=args.java_home,
        output_dir=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
