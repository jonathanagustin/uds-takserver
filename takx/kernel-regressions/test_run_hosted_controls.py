# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/kernel-regressions/run_hosted_controls.py."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_DIR = str(Path(__file__).resolve().parent)
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

SPEC = importlib.util.spec_from_file_location(
    "kernel_regressions_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

NATIVE_NAMES = (
    "libGLdispatch.so",
    "libGLESv2.so",
    "liblas.so",
    "liblas_c.so",
    "libgdal.so",
    "libgdalalljni.so",
    "libassimp.so",
    "libtakengine.so",
    "libtakenginejni.so",
)


def _sample_manifest() -> dict[str, str]:
    return {
        name: hashlib.sha256(f"provider-{name}".encode()).hexdigest()
        for name in NATIVE_NAMES
    }


def _sample_stdout(category: str, assertions: int, lib_dir: Path, manifest: dict[str, str]) -> str:
    lines = [
        f"PASS_REAL_FORMAT_CATEGORY name={category} assertions={assertions}",
    ]
    for name, sha in manifest.items():
        lines.append(f"ELF {sha} {lib_dir / name}")
    lines.append(f"ELF {'a' * 64} /lib/x86_64-linux-gnu/libc.so.6")
    lines.append(runner.REQUIRED_REGRESSION_FOOTER)
    return "\n".join(lines) + "\n"


class HostedKernelRegressionsControlsTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["revision"], runner.EXPECTED_REVISION)
        self.assertEqual(info["categories"], list(runner.CATEGORIES))
        self.assertEqual(info["upstream_tests_count"], 6)
        self.assertEqual(len(info["source_hashes"]), 5)

    def test_audit_category_output_accepts_all_categories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib_dir = (Path(tmp) / "lib/linux64").resolve()
            lib_dir.mkdir(parents=True)
            manifest = _sample_manifest()
            for idx, category in enumerate(runner.CATEGORIES, start=10):
                stdout = _sample_stdout(category, idx, lib_dir, manifest)
                result = runner.audit_category_output(category, stdout, "", 0, lib_dir, manifest)
                self.assertTrue(result["qualified"])
                self.assertEqual(result["assertions"], idx)
                self.assertEqual(result["strict_jni_warnings"], 0)
                self.assertEqual(result["packaged_provider_count"], 9)

    def test_audit_category_output_rejects_failures_and_substitutions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lib_dir = (Path(tmp) / "lib/linux64").resolve()
            lib_dir.mkdir(parents=True)
            manifest = _sample_manifest()
            valid = _sample_stdout("obj", 39, lib_dir, manifest)

            with self.assertRaisesRegex(ValueError, "exited with 1"):
                runner.audit_category_output("obj", valid, "", 1, lib_dir, manifest)

            with self.assertRaisesRegex(ValueError, "strict -Xcheck:jni"):
                runner.audit_category_output(
                    "obj",
                    valid,
                    "WARNING in native method: JNI call made without checking exceptions\n",
                    0,
                    lib_dir,
                    manifest,
                )

            with self.assertRaisesRegex(ValueError, "PASS_REAL_FORMAT_CATEGORY"):
                runner.audit_category_output("kml", valid, "", 0, lib_dir, manifest)

            with self.assertRaisesRegex(ValueError, "regression footer"):
                runner.audit_category_output(
                    "obj",
                    valid.replace(runner.REQUIRED_REGRESSION_FOOTER, ""),
                    "",
                    0,
                    lib_dir,
                    manifest,
                )

            with self.assertRaisesRegex(ValueError, "packaged provider absent"):
                runner.audit_category_output(
                    "obj",
                    valid.replace("libGLESv2.so", "libOther.so"),
                    "",
                    0,
                    lib_dir,
                    manifest,
                )

            with self.assertRaisesRegex(ValueError, "host provider substitution"):
                runner.audit_category_output(
                    "obj",
                    valid + f"ELF {'b' * 64} /usr/lib/x86_64-linux-gnu/libsqlite3.so.0\n",
                    "",
                    0,
                    lib_dir,
                    manifest,
                )


if __name__ == "__main__":
    unittest.main()
