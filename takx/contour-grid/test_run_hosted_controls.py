# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline tests for takx/contour-grid/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent / "run_hosted_controls.py"
SPEC = importlib.util.spec_from_file_location("contour_grid_hosted", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ContourGridHostedVerifyTests(unittest.TestCase):
    def test_verify_offline_passes(self) -> None:
        result = MOD.verify_offline()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["profile"], "bounded-contour-grid-v1")
        self.assertEqual(result["checks"], 1258)
        self.assertEqual(
            result["control_inventory"],
            {"ContourGridControls": 878, "ContourClipControls": 380},
        )


if __name__ == "__main__":
    unittest.main()
