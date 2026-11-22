#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Mesa llvmpipe + source-built GLES qualification runner for kernel-texture.

Verifies pinned upstream TAK Kernel 8.30.5 inputs, applies texture-fill.patch
with fuzz 0, compiles and runs uv-test.cpp and pixel-test.cpp against real
upstream multi-ring Tessellate and prebuilt libglvnd GLES/EGL under Mesa
llvmpipe, and emits a scoped qualification receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_LOCK_PATH = ROOT / "source.lock.json"
HOSTED_LOCK_PATH = ROOT / "hosted_gl_inputs.lock.json"
PATCH_PATH = ROOT / "texture-fill.patch"
UV_TEST_PATH = ROOT / "uv-test.cpp"
PIXEL_TEST_PATH = ROOT / "pixel-test.cpp"
SCHEMA = "uds-takx-kernel-texture-hosted-gl-v1"
RAW_BASE_TEMPLATE = "https://raw.githubusercontent.com/TAK-Product-Center/atak-civ/{commit}/{rel}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_locks() -> tuple[dict, dict]:
    source_lock = json.loads(SOURCE_LOCK_PATH.read_text(encoding="utf-8"))
    hosted_lock = json.loads(HOSTED_LOCK_PATH.read_text(encoding="utf-8"))
    return source_lock, hosted_lock


def verify_offline() -> dict:
    source_lock, hosted_lock = load_locks()
    patch_sha = sha256_file(PATCH_PATH)
    if patch_sha != source_lock["patch_sha256"]:
        raise ValueError(
            f"texture-fill.patch SHA-256 mismatch: {patch_sha} != {source_lock['patch_sha256']}"
        )
    if hosted_lock["source_commit"] != source_lock["source_commit"]:
        raise ValueError("hosted_gl_inputs.lock.json source_commit mismatch with source.lock.json")
    if hosted_lock["actual_kernel_version"] != source_lock["actual_kernel_version"]:
        raise ValueError("hosted_gl_inputs.lock.json kernel version mismatch with source.lock.json")
    overlap = set(hosted_lock["extra_files"]) & set(source_lock["files"])
    if overlap:
        raise ValueError(f"extra_files overlaps source.lock.json: {sorted(overlap)}")
    for unit in hosted_lock["compile_units"]:
        if unit not in hosted_lock["extra_files"]:
            raise ValueError(f"compile unit missing from extra_files: {unit}")
    for required in (UV_TEST_PATH, PIXEL_TEST_PATH):
        if not required.is_file():
            raise ValueError(f"missing test source: {required.name}")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "source_commit": source_lock["source_commit"],
        "actual_kernel_version": source_lock["actual_kernel_version"],
        "patch_sha256": patch_sha,
        "source_lock_sha256": sha256_file(SOURCE_LOCK_PATH),
        "hosted_lock_sha256": sha256_file(HOSTED_LOCK_PATH),
        "patched_file_count": len(source_lock["files"]),
        "extra_file_count": len(hosted_lock["extra_files"]),
        "compile_unit_count": len(hosted_lock["compile_units"]),
    }


def fetch_pinned_file(commit: str, rel: str, expected_sha256: str, dest: Path) -> None:
    url = RAW_BASE_TEMPLATE.format(commit=commit, rel=rel)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read()
    got = sha256_bytes(data)
    if got != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for {rel}: got {got}, expected {expected_sha256}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def run_hosted_qualification(gles_prefix: Path, receipt_path: Path | None) -> dict:
    offline = verify_offline()
    source_lock, hosted_lock = load_locks()
    commit = source_lock["source_commit"]

    gles_include = gles_prefix / "include"
    gles_lib = gles_prefix / "lib"
    for required in (
        gles_include / "EGL/egl.h",
        gles_include / "GLES3/gl3.h",
        gles_lib / "libEGL.so",
        gles_lib / "libGLESv2.so",
    ):
        if not required.exists():
            raise ValueError(f"missing GLES provider artifact: {required}")

    with tempfile.TemporaryDirectory(prefix="takx-kernel-texture-gl-") as tmp:
        work = Path(tmp)
        src_root = work / "src"

        for rel, meta in source_lock["files"].items():
            before = meta["before_sha256"]
            if before is None:
                continue
            fetch_pinned_file(commit, rel, before, src_root / rel)

        subprocess.run(
            ["patch", "-p1", "--fuzz=0", "--batch", "-i", str(PATCH_PATH)],
            cwd=src_root,
            check=True,
        )

        for rel, meta in source_lock["files"].items():
            got = sha256_file(src_root / rel)
            if got != meta["after_sha256"]:
                raise ValueError(
                    f"post-patch SHA-256 mismatch for {rel}: got {got}, expected {meta['after_sha256']}"
                )

        for rel, expected in hosted_lock["extra_files"].items():
            fetch_pinned_file(commit, rel, expected, src_root / rel)

        cpp_root = src_root / "takkernel/engine/src/main/cpp"

        uv_bin = work / "uv-test"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-O2",
                f"-I{cpp_root}",
                str(UV_TEST_PATH),
                "-o",
                str(uv_bin),
            ],
            check=True,
        )
        subprocess.run([str(uv_bin)], check=True)

        objs: list[str] = []
        for idx, unit in enumerate(hosted_lock["compile_units"]):
            obj = work / f"unit_{idx}.o"
            subprocess.run(
                [
                    "g++",
                    "-std=c++17",
                    "-O2",
                    "-ffunction-sections",
                    "-fdata-sections",
                    f"-I{cpp_root}",
                    "-c",
                    str(src_root / unit),
                    "-o",
                    str(obj),
                ],
                check=True,
            )
            objs.append(str(obj))

        pixel_bin = work / "pixel-test"
        subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-O2",
                "-Wl,--gc-sections",
                f"-I{cpp_root}",
                f"-isystem{gles_include}",
                str(PIXEL_TEST_PATH),
                *objs,
                f"-L{gles_lib}",
                f"-Wl,-rpath,{gles_lib}",
                "-lEGL",
                "-lGLESv2",
                "-lpthread",
                "-o",
                str(pixel_bin),
            ],
            check=True,
        )

        env = os.environ.copy()
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        env["GALLIUM_DRIVER"] = "llvmpipe"
        env["LD_LIBRARY_PATH"] = f"{gles_lib}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
        vendor_dir = Path("/usr/share/glvnd/egl_vendor.d")
        if vendor_dir.is_dir():
            env.setdefault("__EGL_VENDOR_LIBRARY_DIRS", str(vendor_dir))
            mesa_json = vendor_dir / "50_mesa.json"
            if mesa_json.is_file():
                env.setdefault("__EGL_VENDOR_LIBRARY_FILENAMES", str(mesa_json))
        pixel_proc = subprocess.run(
            [str(pixel_bin)],
            env=env,
            capture_output=True,
            text=True,
        )
        if pixel_proc.returncode != 0:
            raise RuntimeError(
                f"pixel-test failed (rc={pixel_proc.returncode}): "
                f"stdout={pixel_proc.stdout!r} stderr={pixel_proc.stderr!r}"
            )

    receipt = {
        "schema": SCHEMA,
        "phase": "run",
        "source_commit": commit,
        "actual_kernel_version": source_lock["actual_kernel_version"],
        "patch_sha256": offline["patch_sha256"],
        "source_lock_sha256": offline["source_lock_sha256"],
        "hosted_lock_sha256": offline["hosted_lock_sha256"],
        "gles_prefix": str(gles_prefix),
        "controls": {
            "uv_math_control": True,
            "pixel_shader_multi_ring_3_cycles_24576_pixels": True,
            "pixel_stdout": pixel_proc.stdout.strip(),
        },
        "scope": {
            "hosted_mesa_llvmpipe_surfaceless_egl": True,
            "feature_acceptance": False,
            "private_sdk_equivalence": False,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    if receipt_path is not None:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="TAKX kernel-texture hosted GL qualification")
    parser.add_argument("--phase", choices=("verify", "run"), required=True)
    parser.add_argument("--gles-prefix", type=Path, default=Path("/opt/gles"))
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args()

    if args.phase == "verify":
        result = verify_offline()
    else:
        result = run_hosted_qualification(args.gles_prefix, args.receipt)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
