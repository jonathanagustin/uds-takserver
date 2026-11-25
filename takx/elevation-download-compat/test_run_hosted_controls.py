# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/elevation-download-compat/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "elevation_download_compat_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class HostedElevationDownloadCompatControlsTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["source_profile_sha256"], runner.EXPECTED_PROFILE_SHA)
        self.assertEqual(info["controls_java_sha256"], runner.EXPECTED_CONTROLS_SHA)
        self.assertEqual(info["expected_assertions"], 53)

    def test_audit_controls_output_accepts_valid_pass(self) -> None:
        stdout = "PASS_REAL_ELEVATION_DOWNLOAD_CONTROLS assertions=53\n"
        result = runner.audit_controls_output(stdout, "", 0)
        self.assertEqual(result, {"assertions": 53, "strict_jni_diagnostics": 0})

    def test_audit_controls_output_rejects_failures(self) -> None:
        valid = "PASS_REAL_ELEVATION_DOWNLOAD_CONTROLS assertions=53\n"
        with self.assertRaisesRegex(RuntimeError, "exited with code 1"):
            runner.audit_controls_output(valid, "error", 1)

        with self.assertRaisesRegex(ValueError, "strict -Xcheck:jni"):
            runner.audit_controls_output(
                valid,
                "WARNING in native method: JNI call made without checking exceptions\n",
                0,
            )

        with self.assertRaisesRegex(
            ValueError, "missing PASS_REAL_ELEVATION_DOWNLOAD_CONTROLS"
        ):
            runner.audit_controls_output("wrong output\n", "", 0)


if __name__ == "__main__":
    unittest.main()
