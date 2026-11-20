# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Check manual runner selection and non-manual coverage boundaries."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/benchmark-matrix.py"
SPEC = importlib.util.spec_from_file_location("benchmark_matrix", SCRIPT)
MODE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODE)


class BenchmarkMatrix(unittest.TestCase):
    def test_nonmanual_inputs_cannot_reduce_coverage_or_change_runners(self):
        for event in ("schedule", "pull_request", "push"):
            for architecture in ("amd64", "arm64", "invalid"):
                for profile in ("ubuntu-26.04", "untrusted-runner"):
                    with self.subTest(event=event, architecture=architecture, profile=profile):
                        self.assertEqual(MODE.plan(event, architecture, profile, "custom-amd64"),
                                         {"include": [{"arch": "amd64", "runner": "custom-amd64"},
                                                      {"arch": "arm64", "runner": "ubuntu-22.04-arm"}]})

    def test_manual_defaults_preserve_override_and_native_arm(self):
        for architecture, profile in (("both", "temporary"), ("", "")):
            self.assertEqual(MODE.plan("workflow_dispatch", architecture, profile)["include"],
                             [{"arch": "amd64", "runner": "ubuntu-22.04"},
                              {"arch": "arm64", "runner": "ubuntu-22.04-arm"}])
            self.assertEqual(MODE.plan("workflow_dispatch", architecture, profile, "custom")["include"][0]["runner"],
                             "custom")

    def test_each_manual_selection_uses_the_requested_native_labels(self):
        for architecture in ("both", "amd64", "arm64"):
            for profile in ("ubuntu-24.04", "ubuntu-26.04"):
                expected = [{"arch": arch, "runner": profile + ("-arm" if arch == "arm64" else "")}
                            for arch in ("amd64", "arm64") if architecture in ("both", arch)]
                self.assertEqual(MODE.plan("workflow_dispatch", architecture, profile, "custom")["include"], expected)

    def test_unknown_manual_inputs_fail_without_writing_a_matrix(self):
        for key, value in (("ARCHITECTURE", "ARM64"), ("ARCHITECTURE", "amd64\ninjected=true"),
                           ("RUNNER_PROFILE", "self-hosted"), ("RUNNER_PROFILE", "ubuntu-latest")):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "output"
                output.write_text("existing=value\n")
                env = {**os.environ, "EVENT_NAME": "workflow_dispatch", "ARCHITECTURE": "both",
                       "RUNNER_PROFILE": "temporary", "GITHUB_OUTPUT": str(output), key: value}
                result = subprocess.run([sys.executable, "-B", str(SCRIPT)], env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(output.read_text(), "existing=value\n")

    def test_cli_output_is_an_actions_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            env = {**os.environ, "EVENT_NAME": "workflow_dispatch", "ARCHITECTURE": "arm64",
                   "RUNNER_PROFILE": "ubuntu-26.04", "TEST_RUNNER": "custom", "GITHUB_OUTPUT": str(output)}
            subprocess.run([sys.executable, "-B", str(SCRIPT)], env=env, check=True)
            key, value = output.read_text().strip().split("=", 1)
            self.assertEqual(key, "matrix")
            self.assertEqual(json.loads(value), {"include": [{"arch": "arm64", "runner": "ubuntu-26.04-arm"}]})

    def test_workflow_retains_label_gate_and_consumes_validated_matrix(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/benchmark.yaml").read_text())
        planner, benchmark = workflow["jobs"]["plan"], workflow["jobs"]["benchmark"]
        self.assertIn("github.event_name == 'schedule'", planner["if"])
        self.assertIn("github.event_name == 'workflow_dispatch'", planner["if"])
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'benchmark')", planner["if"])
        self.assertIn("github.event.label.name == 'benchmark'", planner["if"])
        self.assertEqual(benchmark["needs"], ["plan"])
        self.assertEqual(benchmark["strategy"]["matrix"], "${{ fromJSON(needs.plan.outputs.matrix) }}")
        self.assertFalse(benchmark["strategy"]["fail-fast"])
        self.assertEqual(benchmark["runs-on"], "${{ matrix.runner }}")


if __name__ == "__main__":
    unittest.main()
