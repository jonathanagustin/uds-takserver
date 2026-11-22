#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux JDK 17 strict -Xcheck:jni control runner for takx/contour-grid.

Reuses build.py's source contract, bounded runner, and JNI diagnostic checks to
compile and run all 1,258 ContourGridControls (878) + ContourClipControls (380)
assertions inside the prebuilt takx-gl-runner Linux JDK 17 container.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCHEMA = "uds-takx-contour-grid-hosted-v1"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module {name} from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def verify_offline() -> dict:
    build_mod = _load_module("contour_grid_build", HERE / "build.py")
    lock = build_mod.contract()
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "profile": lock["profile"],
        "checks": lock["checks"],
        "control_inventory": lock["control_inventory"],
        "inputs_lock_sha256": build_mod.sha(HERE / "inputs.lock.json"),
        "build_py_sha256": build_mod.sha(HERE / "build.py"),
    }


def _resolve_java_home(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.resolve()
    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        return Path(env_home).resolve()
    javac = shutil.which("javac")
    if not javac:
        raise RuntimeError("javac not found; pass --java-home or set JAVA_HOME")
    return Path(javac).resolve().parents[1]


def run_hosted_controls(java_home: Path | None, receipt_path: Path | None) -> dict:
    build_mod = _load_module("contour_grid_build", HERE / "build.py")
    runner = _load_module("contour_bounded_runner", REPO / "takx/native-commoncommo/runner.py")
    diagnostics = _load_module("contour_jni_diagnostics", REPO / "takx/jni_diagnostics.py")

    lock = build_mod.contract()
    java = _resolve_java_home(java_home)
    javac_bin = java / "bin/javac"
    java_bin = java / "bin/java"
    for required in (javac_bin, java_bin, java / "release"):
        if not required.is_file():
            raise RuntimeError(f"missing JDK artifact: {required}")

    jdk_identity = {
        "java_home": str(java),
        "javac_sha256": build_mod.sha(javac_bin),
        "java_sha256": build_mod.sha(java_bin),
        "release_sha256": build_mod.sha(java / "release"),
    }

    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLASSPATH", "BASH_ENV", "ENV")
        and not k.startswith(("LD_", "DYLD_", "JAVA_", "_JAVA_", "JDK_"))
    }
    env["LC_ALL"] = "C"

    with tempfile.TemporaryDirectory(prefix="takx-contour-grid-hosted-") as tmp:
        output = Path(tmp) / "out"
        output.mkdir(mode=0o700)
        classes = output / "classes"
        classes.mkdir()

        commands = [
            (
                "compile",
                [
                    javac_bin,
                    "--release",
                    "17",
                    "-proc:none",
                    "-classpath",
                    classes,
                    "-d",
                    classes,
                    *(HERE / name for name in build_mod.SOURCES),
                ],
            ),
            *[
                (
                    label,
                    [
                        java_bin,
                        "-Xcheck:jni",
                        "-Xmx256m",
                        "-Djava.awt.headless=true",
                        "-cp",
                        classes,
                        name,
                    ],
                )
                for name, (label, _marker, _checks) in build_mod.CONTROLS.items()
            ],
        ]

        command_records = []
        for label, command in commands:
            argv = [str(part) for part in command]
            runner.execute(
                argv,
                output,
                label,
                60,
                env,
                capture=True,
                disk_limit=16 * 1024**2,
                log_limit=2 * 1024**2,
            )
            command_records.append(
                {
                    "label": label,
                    "passed": True,
                    "log_sha256": build_mod.sha(output / f"{label}.log"),
                }
            )

        control_outputs = {}
        for name, (label, marker, checks) in build_mod.CONTROLS.items():
            text = (output / f"{label}.log").read_text(encoding="utf-8")
            expected = f"{marker} passed checks={checks}"
            if text.strip() != expected:
                raise RuntimeError(f"{name} output mismatch: got {text.strip()!r}, expected {expected!r}")
            if diagnostics.strict_jni_failures(text):
                raise RuntimeError(f"{name} emitted strict JNI diagnostics: {text!r}")
            control_outputs[name] = text.strip()

        if build_mod.contract() != lock:
            raise RuntimeError("source contract changed during execution")

        class_hashes = {
            str(p.relative_to(classes)): build_mod.sha(p)
            for p in sorted(classes.rglob("*.class"))
        }

    receipt = {
        "schema": SCHEMA,
        "phase": "run",
        "profile": lock["profile"],
        "inputs_lock_sha256": build_mod.sha(HERE / "inputs.lock.json"),
        "jdk_identity": jdk_identity,
        "commands": command_records,
        "controls": control_outputs,
        "checks": lock["checks"],
        "control_inventory": lock["control_inventory"],
        "strict_jni_diagnostics": 0,
        "class_count": len(class_hashes),
        "scope": {
            "hosted_linux_jdk17_strict_jni": True,
            **{flag: False for flag in build_mod.FLAGS},
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    if receipt_path is not None:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="TAKX contour-grid hosted Linux JDK 17 controls")
    parser.add_argument("--phase", choices=("verify", "run"), required=True)
    parser.add_argument("--java-home", type=Path, default=None)
    parser.add_argument("--receipt", type=Path, default=None)
    args = parser.parse_args()

    if args.phase == "verify":
        result = verify_offline()
    else:
        result = run_hosted_controls(args.java_home, args.receipt)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
