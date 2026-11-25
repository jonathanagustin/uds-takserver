# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/kernel-render-context/run_hosted_controls.py."""

import copy
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "takx_render_context_hosted", Path(__file__).with_name("run_hosted_controls.py")
)
R = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(R)


def sample_receipt() -> dict:
    return {
        "schema": R.SCHEMA,
        "revision": R.EXPECTED_REVISION,
        "toolchain": {"cxx": "g++ 12.2.0", "java": "openjdk 17"},
        "controls": {
            "prepare_verified": True,
            "binding_state_assertions": R.EXPECTED_BINDING_ASSERTIONS,
            "worker_retirement_control": "pass",
        },
        "digests": {
            "prepare_receipt_sha256": "a" * 64,
            "worker_receipt_sha256": "b" * 64,
            "binding_source_sha256": "c" * 64,
        },
        "scope": {
            "hosted_cpu_jvm_controls": True,
            "gl_context_recreation_accepted": False,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }


class HostedRenderContextControlTests(unittest.TestCase):
    def test_verify_offline_checks_all_required_files(self):
        out = R.verify_offline()
        self.assertEqual(out["schema"], R.SCHEMA)
        self.assertEqual(out["revision"], R.EXPECTED_REVISION)
        self.assertIn("tests/BindingStateTest.java", out["files"])
        self.assertIn("worker_retirement_control.py", out["files"])

    def test_parse_binding_assertions(self):
        self.assertEqual(R.parse_binding_assertions("binding state assertions=88\n"), 88)
        with self.assertRaises(ValueError):
            R.parse_binding_assertions("no marker here")

    def test_validate_receipt_enforces_controls_and_scope_bounds(self):
        base = sample_receipt()
        R.validate_receipt(base)

        for key in (
            "gl_context_recreation_accepted",
            "sdk_accepted",
            "security_accepted",
            "client_accepted",
        ):
            over = copy.deepcopy(base)
            over["scope"][key] = True
            with self.assertRaisesRegex(ValueError, key):
                R.validate_receipt(over)

        bad_count = copy.deepcopy(base)
        bad_count["controls"]["binding_state_assertions"] = 87
        with self.assertRaisesRegex(ValueError, "88"):
            R.validate_receipt(bad_count)

        bad_worker = copy.deepcopy(base)
        bad_worker["controls"]["worker_retirement_control"] = "fail"
        with self.assertRaisesRegex(ValueError, "worker_retirement_control"):
            R.validate_receipt(bad_worker)


if __name__ == "__main__":
    unittest.main()
