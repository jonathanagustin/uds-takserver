# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/worldwind-consumers/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("worldwind_consumers_hosted", HERE / "run_hosted_controls.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class HostedWorldWindConsumersControlsTests(unittest.TestCase):
    def test_verify_phase_validates_both_profiles_without_network(self):
        result = MOD.verify_only()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(set(result["profiles"]), {"5.7", "5.8"})
        self.assertEqual(result["profiles"]["5.7"]["source_count"], 21)
        self.assertEqual(result["profiles"]["5.8"]["source_count"], 21)
        self.assertFalse(result["scope"]["hosted_linux_worldwind_consumers_staging"])

    def test_verify_phase_rejects_qualify_paths(self):
        with self.assertRaisesRegex(ValueError, "explicit --phase qualify"):
            MOD.main(["--phase", "verify", "--output", "/tmp/unused"])

    def test_qualify_phase_requires_inputs(self):
        with self.assertRaisesRegex(ValueError, "--takx-57, --takx-58, and --output are required"):
            MOD.main(["--phase", "qualify"])

    def test_qualify_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "fresh output directory required"):
                MOD.qualify(root / "57", root / "58", root)

    def test_qualify_rejects_repo_overlapping_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(ValueError, "output overlaps inputs"):
                MOD.qualify(root / "57", root / "58", MOD.REPO / "nonexistent-worldwind-consumers-out")


if __name__ == "__main__":
    unittest.main()
