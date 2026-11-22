# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests for takx/kernel-texture/run_hosted_gl.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "run_hosted_gl.py"
SPEC = importlib.util.spec_from_file_location("run_hosted_gl", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class HostedGlVerifyTests(unittest.TestCase):
    def test_verify_offline_passes(self) -> None:
        result = MOD.verify_offline()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["actual_kernel_version"], "8.30.5")
        self.assertGreaterEqual(result["patched_file_count"], 15)
        self.assertGreaterEqual(result["extra_file_count"], 60)
        self.assertEqual(result["compile_unit_count"], 8)

    def test_all_locked_hashes_are_sha256_hex(self) -> None:
        _, hosted = MOD.load_locks()
        for rel, digest in hosted["extra_files"].items():
            self.assertEqual(len(digest), 64, rel)
            self.assertTrue(all(c in "0123456789abcdef" for c in digest), rel)


if __name__ == "__main__":
    unittest.main()
