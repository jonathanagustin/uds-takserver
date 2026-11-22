# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "run_hosted_controls.py"
SPEC = importlib.util.spec_from_file_location("native_las_hosted", MODULE_PATH)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class NativeLasHostedControlsTests(unittest.TestCase):
    def test_verify_offline_validates_fixture_and_lock(self) -> None:
        result = MOD.verify_offline()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["revision"], MOD.REVISION)
        self.assertEqual(len(result["malformed_fixture_sha256"]), 64)

    def test_parse_partial_points(self) -> None:
        self.assertEqual(MOD.parse_partial_points("existing_truncated_las_partial_points=1\n"), 1)
        with self.assertRaises(ValueError):
            MOD.parse_partial_points("existing_truncated_las_partial_points=3\n")
        with self.assertRaises(ValueError):
            MOD.parse_partial_points("no-marker\n")


if __name__ == "__main__":
    unittest.main()
