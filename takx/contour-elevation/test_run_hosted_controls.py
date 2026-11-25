# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/contour-elevation/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "contour_elevation_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class HostedContourElevationControlsTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["profile"], runner.EXPECTED_PROFILE)
        self.assertEqual(info["inputs_lock_sha256"], runner.EXPECTED_LOCK_SHA)
        self.assertEqual(len(info["owned"]), 5)
        self.assertEqual(len(info["experimental_controls"]), 1)
        self.assertEqual(info["elevation_profile_files"], 11)
        self.assertEqual(info["expected_checks"], 615)

    def test_audit_controls_output_accepts_valid_pass(self) -> None:
        stdout = (
            "CONTOUR_VISIBILITY_CONTROLS_PASS checks=16\n"
            "ELEVATION_CONTOUR_CONTROLS_PASS checks=615\n"
        )
        result = runner.audit_controls_output(stdout, "", 0)
        self.assertEqual(
            result,
            {
                "visibility_checks": 16,
                "total_checks": 615,
                "strict_jni_diagnostics": 0,
            },
        )

    def test_audit_controls_output_rejects_failures(self) -> None:
        valid = (
            "CONTOUR_VISIBILITY_CONTROLS_PASS checks=16\n"
            "ELEVATION_CONTOUR_CONTROLS_PASS checks=615\n"
        )
        with self.assertRaisesRegex(RuntimeError, "exited with code 1"):
            runner.audit_controls_output(valid, "boom", 1)

        with self.assertRaisesRegex(ValueError, "strict -Xcheck:jni"):
            runner.audit_controls_output(
                valid,
                "WARNING in native method: JNI call made without checking exceptions\n",
                0,
            )

        with self.assertRaisesRegex(
            ValueError, "missing ELEVATION_CONTOUR_CONTROLS_PASS"
        ):
            runner.audit_controls_output(
                "CONTOUR_VISIBILITY_CONTROLS_PASS checks=16\n",
                "",
                0,
            )

        with self.assertRaisesRegex(
            ValueError, "missing CONTOUR_VISIBILITY_CONTROLS_PASS"
        ):
            runner.audit_controls_output(
                "ELEVATION_CONTOUR_CONTROLS_PASS checks=615\n",
                "",
                0,
            )


if __name__ == "__main__":
    unittest.main()
