# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/kernel-render-context/font-jni-control/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "font_jni_control_hosted",
    Path(__file__).with_name("run_hosted_controls.py"),
)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class HostedFontJniControlTests(unittest.TestCase):
    def test_verify_offline_succeeds(self) -> None:
        info = runner.verify_offline()
        self.assertEqual(info["schema"], runner.SCHEMA)
        self.assertEqual(info["phase"], "verify")
        self.assertEqual(info["official_revision"], runner.EXPECTED_REVISION)
        self.assertEqual(info["official_tag"], runner.EXPECTED_TAG)
        self.assertEqual(info["locked_files"], 3)

    def test_audit_font_probe_output_handles_normal_and_xcheck_baseline(self) -> None:
        normal = runner.audit_font_probe_output(
            "FONT_PROBE_COMPLETED\n", "", 0, xcheck_jni=False
        )
        self.assertEqual(normal["jni_warning_count"], 0)

        baseline = runner.audit_font_probe_output(
            "FONT_PROBE_COMPLETED\n",
            "WARNING in native method: JNI call made with exception pending\n"
            "WARNING in native method: JNI call made with exception pending\n",
            0,
            xcheck_jni=True,
        )
        self.assertEqual(baseline["jni_warning_count"], 2)

        with self.assertRaisesRegex(ValueError, "missing FONT_PROBE_COMPLETED"):
            runner.audit_font_probe_output("", "", 0, xcheck_jni=False)

        with self.assertRaisesRegex(ValueError, "fatal diagnostic"):
            runner.audit_font_probe_output(
                "FONT_PROBE_COMPLETED\n", "FATAL ERROR in native", 0, xcheck_jni=True
            )

        with self.assertRaisesRegex(ValueError, "unexpected JNI warning"):
            runner.audit_font_probe_output(
                "FONT_PROBE_COMPLETED\n",
                "WARNING in native method: bad",
                0,
                xcheck_jni=False,
            )

    def test_validate_prepared_receipt_enforces_revision_and_scope(self) -> None:
        lock = json.loads(runner.LOCK_FILE.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prepared.json"
            valid = {
                "schema": 1,
                "official_revision": runner.EXPECTED_REVISION,
                "patch_sha256": lock["patch_sha256"],
                "native_build_or_execution": False,
                "runtime_accepted": False,
                "full_sdk_accepted": False,
            }
            path.write_text(json.dumps(valid), encoding="utf-8")
            res = runner.validate_prepared_receipt(path, lock)
            self.assertEqual(len(res["sha256"]), 64)

            bad_rev = dict(valid, official_revision="0" * 40)
            path.write_text(json.dumps(bad_rev), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "revision"):
                runner.validate_prepared_receipt(path, lock)

            bad_flag = dict(valid, runtime_accepted=True)
            path.write_text(json.dumps(bad_flag), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "runtime_accepted"):
                runner.validate_prepared_receipt(path, lock)


if __name__ == "__main__":
    unittest.main()
