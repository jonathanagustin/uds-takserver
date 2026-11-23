# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/client58-public-api/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

MODULE_DIR = str(Path(__file__).resolve().parent)
if MODULE_DIR not in sys.path:
    sys.path.insert(0, MODULE_DIR)

SPEC = importlib.util.spec_from_file_location(
    "client58_public_api_hosted_controls",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class HostedClient58PublicApiControlsTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["profile"], runner.EXPECTED_PROFILE)
        self.assertEqual(len(info["owned_source_hashes"]), 3)

    def test_audit_controls_output_accepts_valid_pass(self) -> None:
        stdout = f"{runner.REQUIRED_MODEL_PASS}\n{runner.REQUIRED_ARTIFACT_PASS}\n"
        result = runner.audit_controls_output(stdout, "", 0)
        self.assertEqual(
            result,
            {"checks": 34, "artifact_classes_verified": 2, "strict_jni_warnings": 0},
        )

    def test_audit_controls_output_rejects_failures(self) -> None:
        valid = f"{runner.REQUIRED_MODEL_PASS}\n{runner.REQUIRED_ARTIFACT_PASS}\n"

        with self.assertRaisesRegex(ValueError, "exited with 1"):
            runner.audit_controls_output(valid, "", 1)

        with self.assertRaisesRegex(ValueError, "strict -Xcheck:jni"):
            runner.audit_controls_output(
                valid,
                "WARNING in native method: JNI call made without checking exceptions\n",
                0,
            )

        with self.assertRaisesRegex(ValueError, "CoT controls marker"):
            runner.audit_controls_output(f"{runner.REQUIRED_ARTIFACT_PASS}\n", "", 0)

        with self.assertRaisesRegex(ValueError, "artifact origin marker"):
            runner.audit_controls_output(f"{runner.REQUIRED_MODEL_PASS}\n", "", 0)


if __name__ == "__main__":
    unittest.main()
