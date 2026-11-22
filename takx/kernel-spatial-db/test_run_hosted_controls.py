# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "run_hosted_controls.py"
SPEC = importlib.util.spec_from_file_location("kernel_spatial_db_hosted", MODULE_PATH)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class KernelSpatialDbHostedControlsTests(unittest.TestCase):
    def test_verify_offline_validates_lock_and_consumer(self) -> None:
        result = MOD.verify_offline()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["revision"], MOD.REVISION)
        self.assertEqual(result["kernel_source_count"], 15)

    def test_check_runtime_closure_rejects_third_party_or_missing(self) -> None:
        MOD.check_runtime_closure("\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x0)\n")
        with self.assertRaises(ValueError):
            MOD.check_runtime_closure("\tlibsqlite3.so.0 => /usr/lib/libsqlite3.so.0 (0x0)\n")
        with self.assertRaises(ValueError):
            MOD.check_runtime_closure("\tlibfoo.so => not found\n")

    def test_check_no_textrel_rejects_textrel(self) -> None:
        MOD.check_no_textrel(" 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]\n")
        with self.assertRaises(ValueError):
            MOD.check_no_textrel(" 0x0000000000000016 (TEXTREL) 0x0\n")


if __name__ == "__main__":
    unittest.main()
