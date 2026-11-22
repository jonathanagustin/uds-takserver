# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests for takx/ui-java/run_hosted_build.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "run_hosted_build.py"
SPEC = importlib.util.spec_from_file_location("ui_java_hosted", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class UiJavaHostedVerifyTests(unittest.TestCase):
    def test_verify_offline_passes(self) -> None:
        result = MOD.verify_offline("original")
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["profile"], "original")
        self.assertEqual(result["artifact_count"], 5)
        self.assertEqual(result["archive_count"], 4)
        self.assertEqual(result["jar_count"], 4)


if __name__ == "__main__":
    unittest.main()
