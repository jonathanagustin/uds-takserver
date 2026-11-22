#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline unit tests for takx/compat/run_hosted_controls.py."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


def load_runner():
    spec = importlib.util.spec_from_file_location("compat_hosted_controls", HERE / "run_hosted_controls.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HostedCompatJavaControlsTests(unittest.TestCase):
    def test_verify_phase_checks_all_seven_suites_without_subprocess(self):
        runner = load_runner()
        with mock.patch("subprocess.run", side_effect=AssertionError("verify must not invoke subprocess")):
            manifest = runner.verify_only()
        self.assertEqual(manifest["suite_count"], 7)
        self.assertEqual(
            set(manifest["suites"]),
            {"pair", "uri", "context", "canvas", "region-storage", "bitmap", "font"},
        )
        self.assertFalse(manifest["scope"]["sdk_accepted"])
        self.assertFalse(manifest["scope"]["security_accepted"])
        self.assertFalse(manifest["scope"]["client_accepted"])

    def test_verify_phase_rejects_qualify_paths(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, "explicit --phase qualify"):
            runner.main(["--phase", "verify", "--output", "/tmp/unused"])

    def test_qualify_phase_requires_explicit_inputs(self):
        runner = load_runner()
        with self.assertRaisesRegex(ValueError, "required for --phase qualify"):
            runner.main(["--phase", "qualify"])

    def test_clean_env_strips_java_and_loader_vars(self):
        runner = load_runner()
        with mock.patch.dict(
            os.environ,
            {
                "CLASSPATH": "/bad",
                "JAVA_TOOL_OPTIONS": "-Xmx1g",
                "_JAVA_OPTIONS": "-Dfoo=bar",
                "LD_PRELOAD": "/evil.so",
                "TAKX_COMPAT_EXTRA_SOURCE_DIR": "/evil",
                "PATH": "/usr/bin",
            },
            clear=True,
        ):
            cleaned = runner.clean_env(Path("/opt/atak"))
        self.assertEqual(cleaned["LC_ALL"], "C")
        self.assertEqual(cleaned["ATAK_SOURCE_DIR"], "/opt/atak")
        for forbidden in ("CLASSPATH", "JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "LD_PRELOAD", "TAKX_COMPAT_EXTRA_SOURCE_DIR"):
            self.assertNotIn(forbidden, cleaned)

    def test_qualify_rejects_overlapping_output(self):
        runner = load_runner()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_atak = root / "atak"
            fake_atak.mkdir()
            with self.assertRaisesRegex(ValueError, "output overlaps inputs"):
                runner.main([
                    "--phase", "qualify",
                    "--atak-source", str(fake_atak),
                    "--output", str(fake_atak / "nested"),
                ])


if __name__ == "__main__":
    unittest.main()
