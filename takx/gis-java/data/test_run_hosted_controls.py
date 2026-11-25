# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/gis-java/data/run_hosted_controls.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("gdal_data_hosted", HERE / "run_hosted_controls.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class HostedGdalDataControlsTests(unittest.TestCase):
    def test_verify_phase_checks_lock_agreement_without_network(self):
        result = MOD.verify_only()
        self.assertEqual(result["schema"], MOD.SCHEMA)
        self.assertEqual(result["phase"], "verify")
        self.assertEqual(result["locked_resource_count"], 92)
        self.assertFalse(result["scope"]["hosted_linux_jdk17_gdal_data"])

    def test_verify_phase_rejects_qualify_paths(self):
        with self.assertRaisesRegex(ValueError, "explicit --phase qualify"):
            MOD.main(["--phase", "verify", "--output", "/tmp/unused"])

    def test_qualify_phase_requires_output(self):
        with self.assertRaisesRegex(ValueError, "--output is required"):
            MOD.main(["--phase", "qualify"])

    def test_qualify_rejects_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "fresh output directory required"):
                MOD.qualify(Path(tmp))

    def test_qualify_rejects_repo_overlapping_output(self):
        with self.assertRaisesRegex(ValueError, "output overlaps repository"):
            MOD.qualify(MOD.REPO / "nonexistent-gdal-data-out")


if __name__ == "__main__":
    unittest.main()
