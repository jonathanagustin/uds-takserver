#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Mesa llvmpipe + Xvfb OpenGL ES 3.0 qualification harness for TAKX."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
CONTROL_SRC = ROOT / "gl_controls.c"
JNI_JAVA_SRC = ROOT / "GlJniSmoke.java"
JNI_C_SRC = ROOT / "gl_jni_bridge.c"
SCHEMA = "uds-takx-gl-qualification-v1"
REQUIRED_CONTROLS = (
    "xvfb_es3_clear_readback",
    "textured_polygon_atlas_readback",
    "context_destroy_recreate_invalidation",
    "layer_visibility_clear_redraw",
    "jni_strict_gles3_bridge_readback",
)
MAX_OUTPUT_BYTES = 1024 * 1024
COMMAND_TIMEOUT_S = 120


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_control_output(text: str) -> dict:
    """Parse key=value pairs and provider ELF paths from gl_controls stdout."""
    fields: dict[str, str] = {}
    providers: list[str] = []
    in_providers = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "providers_begin":
            in_providers = True
            continue
        if line == "providers_end":
            in_providers = False
            continue
        if in_providers:
            providers.append(line)
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    if in_providers:
        raise ValueError("unterminated providers_begin block in control output")
    return {"fields": fields, "providers": sorted(set(providers))}


def hash_providers(paths: list[str]) -> list[dict[str, object]]:
    """Hash each existing loaded ELF provider path reported by dl_iterate_phdr."""
    records: list[dict[str, object]] = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute() or not path.is_file():
            continue
        resolved = path.resolve()
        records.append({
            "path": raw,
            "resolved": str(resolved),
            "bytes": resolved.stat().st_size,
            "sha256": sha256_file(resolved),
        })
    return records


def validate_receipt(receipt: dict) -> None:
    """Fail-closed validation of a Mesa/Xvfb qualification receipt."""
    if receipt.get("schema") != SCHEMA:
        raise ValueError("unexpected receipt schema")
    if receipt.get("arch") not in ("x86_64", "amd64"):
        raise ValueError("Mesa/Xvfb qualification requires native x86_64")
    renderer = str(receipt.get("renderer", ""))
    version = str(receipt.get("version", ""))
    if "llvmpipe" not in renderer or "OpenGL ES 3." not in version:
        raise ValueError("receipt must prove Mesa llvmpipe OpenGL ES 3.x rendering")
    controls = receipt.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("missing controls object")
    for name in REQUIRED_CONTROLS:
        if controls.get(name) is not True:
            raise ValueError(f"required GL control did not pass: {name}")
    if receipt.get("jni_warnings") != 0:
        raise ValueError("receipt must prove zero strict -Xcheck:jni warnings")
    providers = receipt.get("providers")
    if not isinstance(providers, list) or not providers:
        raise ValueError("receipt must record hashed ELF providers")
    for item in providers:
        if not isinstance(item, dict) or len(str(item.get("sha256", ""))) != 64:
            raise ValueError("invalid provider hash entry")
    # Never over-claim hardware GPU, full SDK, or TAKX client acceptance.
    scope = receipt.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("missing scope object")
    if scope.get("software_mesa_xvfb") is not True:
        raise ValueError("scope.software_mesa_xvfb must be true")
    for key in ("hardware_gpu_accepted", "sdk_accepted", "client_accepted"):
        if scope.get(key) is not False:
            raise ValueError(f"scope.{key} must remain false")


def run_bounded(
    argv: list[str], env: dict[str, str] | None = None, *, forbid_jni_warnings: bool = False
) -> str:
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_S,
        env=env,
        check=False,
    )
    if len(result.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise RuntimeError("control stdout exceeded 1 MiB bound")
    if result.returncode != 0:
        raise RuntimeError(
            f"command failed (exit={result.returncode}): {argv[0]}: {result.stderr.strip()[:400]}"
        )
    if forbid_jni_warnings:
        combined = f"{result.stdout}\n{result.stderr}"
        if "WARNING" in combined or "JNI" in result.stderr:
            raise RuntimeError(
                f"strict -Xcheck:jni diagnostic detected: {result.stderr.strip()[:400]}"
            )
    return result.stdout


def resolve_java_home() -> Path:
    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        candidate = Path(env_home)
        if (candidate / "include" / "jni.h").is_file():
            return candidate
    javac_bin = shutil.which("javac")
    if javac_bin:
        candidate = Path(javac_bin).resolve().parent.parent
        if (candidate / "include" / "jni.h").is_file():
            return candidate
    raise RuntimeError("JDK with include/jni.h is required for JNI GL qualification")


def verify_offline() -> dict:
    """Offline guard: verify source presence and deterministic hashes without running GL."""
    for src in (CONTROL_SRC, JNI_JAVA_SRC, JNI_C_SRC):
        if not src.is_file():
            raise FileNotFoundError(f"missing control source: {src}")
    combined = (
        CONTROL_SRC.read_text(encoding="utf-8")
        + "\n"
        + JNI_JAVA_SRC.read_text(encoding="utf-8")
        + "\n"
        + JNI_C_SRC.read_text(encoding="utf-8")
    )
    for name in REQUIRED_CONTROLS:
        if name not in combined:
            raise ValueError(f"control sources missing assertion marker: {name}")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "control_source": CONTROL_SRC.name,
        "control_source_sha256": sha256_file(CONTROL_SRC),
        "jni_java_source_sha256": sha256_file(JNI_JAVA_SRC),
        "jni_c_source_sha256": sha256_file(JNI_C_SRC),
        "required_controls": list(REQUIRED_CONTROLS),
    }


def qualify(output_dir: Path, cc: str = "gcc") -> dict:
    """Compile C + JNI controls and execute under isolated Xvfb + Mesa llvmpipe."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"output directory must be empty: {output_dir}")
    if shutil.which("xvfb-run") is None:
        raise RuntimeError("xvfb-run is required for --phase qualify")
    if shutil.which(cc) is None:
        raise RuntimeError(f"C compiler not found: {cc}")
    if shutil.which("javac") is None or shutil.which("java") is None:
        raise RuntimeError("javac and java are required for JNI GL qualification")
    java_home = resolve_java_home()

    output_dir.mkdir(parents=True, exist_ok=True)
    binary = output_dir / "gl_controls"
    run_bounded([
        cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-O2",
        str(CONTROL_SRC),
        "-o", str(binary),
        "-lX11", "-lEGL", "-lGLESv2",
    ])

    jni_lib = output_dir / "libtakx_gl_jni.so"
    run_bounded(["javac", "-d", str(output_dir), str(JNI_JAVA_SRC)])
    run_bounded([
        cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-O2", "-shared", "-fPIC",
        f"-I{java_home / 'include'}",
        f"-I{java_home / 'include' / 'linux'}",
        str(JNI_C_SRC),
        "-o", str(jni_lib),
        "-lX11", "-lEGL", "-lGLESv2",
    ])

    env = os.environ.copy()
    env["LIBGL_ALWAYS_SOFTWARE"] = "1"
    env["MESA_LOADER_DRIVER_OVERRIDE"] = "llvmpipe"
    env["GALLIUM_DRIVER"] = "llvmpipe"
    stdout = run_bounded([
        "xvfb-run", "--auto-servernum",
        "--server-args=-screen 0 640x480x24",
        str(binary),
    ], env=env)
    jni_stdout = run_bounded([
        "xvfb-run", "--auto-servernum",
        "--server-args=-screen 0 640x480x24",
        "java", "-Xcheck:jni", "-cp", str(output_dir), "GlJniSmoke", str(jni_lib),
    ], env=env, forbid_jni_warnings=True)

    parsed = parse_control_output(stdout)
    jni_parsed = parse_control_output(jni_stdout)
    fields = {**parsed["fields"], **jni_parsed["fields"]}
    controls = {name: fields.get(name) == "true" for name in REQUIRED_CONTROLS}
    jni_class = output_dir / "GlJniSmoke.class"
    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "arch": platform.machine(),
        "control_source": CONTROL_SRC.name,
        "control_source_sha256": sha256_file(CONTROL_SRC),
        "jni_java_source_sha256": sha256_file(JNI_JAVA_SRC),
        "jni_c_source_sha256": sha256_file(JNI_C_SRC),
        "binary_sha256": sha256_file(binary),
        "jni_library_sha256": sha256_file(jni_lib),
        "jni_class_sha256": sha256_file(jni_class),
        "jni_warnings": 0,
        "display": fields.get("display", ""),
        "egl": fields.get("egl", ""),
        "version": fields.get("version", ""),
        "renderer": fields.get("renderer", ""),
        "pixels": {
            "clear": fields.get("pixel_clear", ""),
            "atlas": fields.get("pixel_atlas", ""),
            "recreated": fields.get("pixel_recreated", ""),
            "layer_on": fields.get("pixel_layer_on", ""),
            "layer_off": fields.get("pixel_layer_off", ""),
            "jni": fields.get("pixel_jni", ""),
        },
        "controls": controls,
        "providers": hash_providers(parsed["providers"]),
        "scope": {
            "software_mesa_xvfb": True,
            "hardware_gpu_accepted": False,
            "sdk_accepted": False,
            "client_accepted": False,
        },
    }
    validate_receipt(receipt)
    receipt_path = output_dir / "gl-qualification-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--output", type=Path, help="empty output directory for --phase qualify")
    parser.add_argument("--cc", default="gcc", help="C compiler binary")
    args = parser.parse_args(argv)
    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2, sort_keys=True))
        return 0
    if args.output is None:
        parser.error("--output is required for --phase qualify")
    receipt = qualify(args.output.resolve(), cc=args.cc)
    print(json.dumps({
        "schema": receipt["schema"],
        "renderer": receipt["renderer"],
        "version": receipt["version"],
        "controls": receipt["controls"],
        "provider_count": len(receipt["providers"]),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
