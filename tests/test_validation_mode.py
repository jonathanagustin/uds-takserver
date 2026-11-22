# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Exercise cluster matrix planning and the actual Test result verifier."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/test-validation-mode.py"
SPEC = importlib.util.spec_from_file_location("test_mode", SCRIPT)
MODE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODE)


class TestValidationMode(unittest.TestCase):
    def test_only_explicit_trust_dispatch_uses_two_native_installs(self):
        for event in ("pull_request", "push", "workflow_dispatch"):
            for requested in ("true", "false", ""):
                with self.subTest(event=event, requested=requested):
                    matrix = MODE.install_matrix(event, requested, "custom-amd64-runner")["include"]
                    focused = event == "workflow_dispatch" and requested == "true"
                    self.assertEqual(len(matrix), 2 if focused else 3)
                    self.assertEqual([item["arch"] for item in matrix[:2]], ["amd64", "arm64"])
                    self.assertEqual(matrix[0]["runner"], "custom-amd64-runner")
                    self.assertEqual(matrix[1]["runner"], "ubuntu-22.04-arm")
                    self.assertEqual(sum(item.get("airgap", False) for item in matrix), 0 if focused else 1)

    def test_cli_default_and_override_keep_both_architectures_and_airgap(self):
        for runner in ("", "custom-amd64-runner"):
            with self.subTest(runner=runner):
                env = {**os.environ, "EVENT_NAME": "pull_request", "TRUST_BUNDLE": "false", "TEST_RUNNER": runner}
                env.pop("GITHUB_OUTPUT", None)
                result = subprocess.run(["python3", str(SCRIPT), "plan"], env=env,
                                        capture_output=True, text=True, check=True)
                rows = json.loads(dict(line.split("=", 1) for line in result.stdout.splitlines())["install-matrix"])["include"]
                self.assertEqual([(row["arch"], row.get("airgap", False)) for row in rows],
                                 [("amd64", False), ("arm64", False), ("amd64", True)])
                self.assertEqual([row["runner"] for row in rows],
                                 [runner or "ubuntu-22.04", "ubuntu-22.04-arm", runner or "ubuntu-22.04"])

    def test_normal_runs_reject_a_skipped_upgrade_even_if_input_was_set(self):
        for event, requested in (("pull_request", "true"), ("push", "true"), ("workflow_dispatch", "false")):
            MODE.verify(event, requested, "success", "success", "success" if event == "push" else "skipped")
            with self.assertRaisesRegex(ValueError, "expected success"):
                MODE.verify(event, requested, "success", "skipped", "skipped")

    def test_pg15_push_and_selected_pr_cannot_pass_with_missing_work(self):
        for event, required in (("push", "false"), ("push", "true"), ("pull_request", "true")):
            MODE.verify(event, "false", "success", "success", "success", pg15_required=required)
            for result in ("skipped", "failure", "cancelled", ""):
                with self.subTest(event=event, required=required, result=result):
                    with self.assertRaisesRegex(ValueError, "PostgreSQL 15"):
                        MODE.verify(event, "false", "success", "success", result, pg15_required=required)
        for result in ("success", "skipped"):
            MODE.verify("pull_request", "false", "success", "success", result)

    def test_pg15_expectation_does_not_override_explicit_manual_modes(self):
        for required in ("true", "false"):
            MODE.verify("workflow_dispatch", "true", "success", "skipped", "skipped", pg15_required=required)
            MODE.verify("workflow_dispatch", "false", "skipped", "success", "skipped",
                        "upgrade-amd64", required)
            MODE.verify("workflow_dispatch", "false", "skipped", "skipped", "success",
                        "pg15-amd64", required)
            MODE.verify("workflow_dispatch", "false", "skipped", "skipped", "success",
                        "pg15-both", required)

    def test_cli_pg15_policy_and_invalid_expectations_fail_closed(self):
        base = {**os.environ, "EVENT_NAME": "push", "TRUST_BUNDLE": "false", "DIAGNOSTIC_CASE": "none",
                "INSTALL_RESULT": "success", "UPGRADE_RESULT": "success", "PG15_RESULT": "skipped"}
        base.pop("PG15_REQUIRED", None)
        for event, required, pg15, expected in (
                ("push", None, "skipped", False), ("push", "false", "skipped", False),
                ("push", "false", "success", True), ("pull_request", "true", "skipped", False),
                ("pull_request", "true", "success", True), ("pull_request", "false", "skipped", True)):
            env = {**base, "EVENT_NAME": event, "PG15_RESULT": pg15}
            if required is not None:
                env["PG15_REQUIRED"] = required
            result = subprocess.run(["python3", str(SCRIPT), "verify"], env=env, capture_output=True, text=True)
            with self.subTest(event=event, required=required, pg15=pg15):
                self.assertEqual(result.returncode == 0, expected, result.stderr)
        for event in ("push", "pull_request", "workflow_dispatch"):
            for invalid in ("", "True", "0", "yes"):
                env = {**base, "EVENT_NAME": event, "PG15_RESULT": "success", "PG15_REQUIRED": invalid}
                result = subprocess.run(["python3", str(SCRIPT), "verify"], env=env,
                                        capture_output=True, text=True)
                with self.subTest(event=event, invalid=invalid):
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Unknown PostgreSQL 15 expectation", result.stderr)

    def test_trust_trial_accepts_only_its_intentional_skips(self):
        MODE.verify("workflow_dispatch", "true", "success", "skipped", "skipped")
        for install in ("failure", "skipped", "cancelled", ""):
            with self.assertRaisesRegex(ValueError, "Install did not pass"):
                MODE.verify("workflow_dispatch", "true", install, "skipped", "skipped")
        for upgrade in ("failure", "cancelled", "success", ""):
            with self.assertRaisesRegex(ValueError, "expected skipped"):
                MODE.verify("workflow_dispatch", "true", "success", upgrade, "skipped")
        for pg15 in ("failure", "cancelled", "success", ""):
            with self.assertRaisesRegex(ValueError, "PostgreSQL 15"):
                MODE.verify("workflow_dispatch", "true", "success", "skipped", pg15)

    def test_cli_writes_a_runner_output_and_fails_a_false_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            env = {**os.environ, "EVENT_NAME": "workflow_dispatch", "TRUST_BUNDLE": "true", "GITHUB_OUTPUT": str(output)}
            result = subprocess.run(["python3", str(SCRIPT), "plan"], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout, "")
            matrix = json.loads(dict(line.split("=", 1) for line in output.read_text().splitlines())["install-matrix"])
            self.assertEqual(len(matrix["include"]), 2)
            env.update(INSTALL_RESULT="success", UPGRADE_RESULT="failure", PG15_RESULT="skipped")
            result = subprocess.run(["python3", str(SCRIPT), "verify"], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("expected skipped", result.stderr)

    def test_normal_seven_paths_and_forged_inputs_preserve_coverage(self):
        for event in ("pull_request", "push", "workflow_dispatch"):
            for selected in (("none",) if event == "workflow_dispatch" else
                             ("none", "upgrade-amd64", "pg15-amd64", "pg15-both", "airgap-amd64", "invalid")):
                with self.subTest(event=event, selected=selected):
                    outputs = MODE.plan(event, "false", "custom", selected)
                    self.assertEqual(outputs["diagnostic-case"], "none")
                    self.assertEqual([len(outputs[key]["include"]) for key in
                                      ("install-matrix", "upgrade-matrix", "pg15-matrix")], [3, 2, 2])
                    for key in ("upgrade-matrix", "pg15-matrix"):
                        self.assertEqual(outputs[key]["include"], [
                            {"arch": "amd64", "runner": "custom"},
                            {"arch": "arm64", "runner": "ubuntu-22.04-arm"}])
                    # Forged PR/push diagnostic inputs never authorize omissions.
                    for results in (("skipped", "success", "skipped"),
                                    ("skipped", "skipped", "success")):
                        with self.assertRaises(ValueError):
                            MODE.verify(event, "false", *results, selected)

    def test_manual_diagnostics_select_one_amd_row_and_reject_wrong_results(self):
        for selected, key, expected in (
                ("upgrade-amd64", "upgrade-matrix", ("skipped", "success", "skipped")),
                ("pg15-amd64", "pg15-matrix", ("skipped", "skipped", "success"))):
            outputs = MODE.plan("workflow_dispatch", "false", "custom", selected)
            self.assertEqual(outputs[key]["include"], [{"arch": "amd64", "runner": "custom"}])
            # Even the intentionally skipped install has a valid default matrix.
            self.assertEqual(len(outputs["install-matrix"]["include"]), 3)
            MODE.verify("workflow_dispatch", "false", *expected, selected)
            for index in range(3):
                for result in ("success", "failure", "skipped", "cancelled", ""):
                    actual = list(expected)
                    actual[index] = result
                    if tuple(actual) == expected:
                        continue
                    with self.subTest(selected=selected, actual=actual):
                        with self.assertRaises(ValueError):
                            MODE.verify("workflow_dispatch", "false", *actual, selected)

    def test_mixed_and_unknown_manual_modes_fail_plan_and_verify(self):
        for trust, selected in (("true", "upgrade-amd64"), ("true", "pg15-amd64"), ("true", "pg15-both"), ("true", "airgap-amd64"),
                                ("false", "unknown")):
            with self.assertRaises(ValueError):
                MODE.plan("workflow_dispatch", trust, "custom", selected)
            with self.assertRaises(ValueError):
                MODE.verify("workflow_dispatch", trust, "success", "success", "skipped", selected)

    def test_workflow_consumes_planned_matrices_and_preserves_pg15_trigger(self):
        workflow = (SCRIPT.parents[1] / "workflows/test.yaml").read_text()
        self.assertIn("options: [none, upgrade-amd64, pg15-amd64, pg15-both, airgap-amd64]", workflow)
        planner = workflow.split("  check-flavor:", 1)[1].split("  validate-install:", 1)[0]
        self.assertIn("DIAGNOSTIC_CASE: ${{ inputs.diagnostic_case || 'none' }}", planner)
        for key in ("install-matrix", "upgrade-matrix", "pg15-matrix", "diagnostic-case"):
            self.assertIn(f"{key}: ${{{{ steps.validation-mode.outputs.{key} }}}}", planner)
        sections = {}
        names = ("validate-install", "validate-upgrade", "validate-upgrade-pg15")
        for index, name in enumerate(names):
            end = names[index + 1] if index + 1 < len(names) else "verify-test"
            sections[name] = workflow.split(f"  {name}:\n", 1)[1].split(f"\n  {end}:\n", 1)[0]
        for name, key in zip(names, ("install-matrix", "upgrade-matrix", "pg15-matrix")):
            self.assertIn("needs: [check-flavor, images, cloudtak-index, postgis-index]", sections[name])
            self.assertIn(f"matrix: ${{{{ fromJSON(needs.check-flavor.outputs.{key}) }}}}", sections[name])
        self.assertIn("diagnostic-case == 'none'", sections["validate-install"])
        self.assertIn("diagnostic-case != 'pg15-amd64'", sections["validate-upgrade"])
        self.assertIn("diagnostic-case != 'pg15-amd64' && "
                      "needs.check-flavor.outputs.diagnostic-case != 'pg15-both'", sections["validate-upgrade"])
        pg15 = sections["validate-upgrade-pg15"]
        self.assertIn("diagnostic-case == 'pg15-amd64'", pg15)
        self.assertIn("diagnostic-case == 'pg15-amd64' || "
                      "needs.check-flavor.outputs.diagnostic-case == 'pg15-both'", pg15)
        self.assertIn("github.event_name == 'push'", pg15)
        self.assertIn("contains(github.event.pull_request.labels.*.name, 'upgrade-pg15')", pg15)
        verifier = workflow.split("  verify-test:", 1)[1]
        self.assertIn("DIAGNOSTIC_CASE: ${{ inputs.diagnostic_case || 'none' }}", verifier)
        self.assertIn("PG15_REQUIRED: ${{ github.event_name == 'push' || "
                      "contains(github.event.pull_request.labels.*.name, 'upgrade-pg15') }}", verifier)

    def test_manual_pg15_both_uses_two_native_rows_and_exact_family_results(self):
        outputs = MODE.plan("workflow_dispatch", "false", "custom-amd64", "pg15-both")
        self.assertEqual(outputs["diagnostic-case"], "pg15-both")
        self.assertEqual(outputs["pg15-matrix"]["include"], [
            {"arch": "amd64", "runner": "custom-amd64"},
            {"arch": "arm64", "runner": "ubuntu-22.04-arm"}])
        MODE.verify("workflow_dispatch", "false", "skipped", "skipped", "success", "pg15-both")
        for index in range(3):
            for result in ("success", "failure", "skipped", "cancelled", ""):
                actual = ["skipped", "skipped", "success"]
                actual[index] = result
                if actual == ["skipped", "skipped", "success"]:
                    continue
                with self.subTest(actual=actual):
                    with self.assertRaises(ValueError):
                        MODE.verify("workflow_dispatch", "false", *actual, "pg15-both")

    def test_cli_pg15_both_plan_and_diagnostic_only_success(self):
        env = {**os.environ, "EVENT_NAME": "workflow_dispatch", "TRUST_BUNDLE": "false",
               "TEST_RUNNER": "custom-amd64", "DIAGNOSTIC_CASE": "pg15-both",
               "INSTALL_RESULT": "skipped", "UPGRADE_RESULT": "skipped", "PG15_RESULT": "success",
               "PG15_REQUIRED": "false"}
        env.pop("GITHUB_OUTPUT", None)
        result = subprocess.run(["python3", str(SCRIPT), "plan"], env=env,
                                capture_output=True, text=True, check=True)
        planned = dict(line.split("=", 1) for line in result.stdout.splitlines())
        self.assertEqual(planned["diagnostic-case"], "pg15-both")
        self.assertEqual([row["arch"] for row in json.loads(planned["pg15-matrix"])["include"]],
                         ["amd64", "arm64"])
        result = subprocess.run(["python3", str(SCRIPT), "verify"], env=env,
                                capture_output=True, text=True, check=True)
        self.assertIn("Selected diagnostic case passed—not full acceptance", result.stdout)
        self.assertNotIn("All tests passed", result.stdout)
        env["PG15_RESULT"] = "skipped"
        result = subprocess.run(["python3", str(SCRIPT), "verify"], env=env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        env.update(TRUST_BUNDLE="true", PG15_RESULT="success")
        for command in ("plan", "verify"):
            result = subprocess.run(["python3", str(SCRIPT), command], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cannot be combined", result.stderr)

    def test_airgap_dispatch_selects_only_actual_offline_amd_row(self):
        for runner in ('ubuntu-22.04', 'custom-amd64'):
            outputs = MODE.plan('workflow_dispatch', 'false', runner, 'airgap-amd64')
            self.assertEqual(outputs['diagnostic-case'], 'airgap-amd64')
            self.assertEqual(outputs['install-matrix']['include'], [
                {'arch': 'amd64', 'runner': runner, 'airgap': True}])
            MODE.verify('workflow_dispatch', 'false', 'success', 'skipped', 'skipped', 'airgap-amd64')
            for index in range(3):
                for result in ('success', 'skipped', 'failure', 'cancelled', ''):
                    actual = ['success', 'skipped', 'skipped']
                    actual[index] = result
                    if actual == ['success', 'skipped', 'skipped']:
                        continue
                    with self.subTest(actual=actual), self.assertRaises(ValueError):
                        MODE.verify('workflow_dispatch', 'false', *actual, 'airgap-amd64')
        workflow = (SCRIPT.parents[1] / 'workflows/test.yaml').read_text()
        install = workflow.split('  validate-install:', 1)[1].split('    strategy:', 1)[0]
        self.assertIn("diagnostic-case == 'none' || needs.check-flavor.outputs.diagnostic-case == 'airgap-amd64'", install)
        upgrade = workflow.split('  validate-upgrade:', 1)[1].split('    strategy:', 1)[0]
        self.assertIn("diagnostic-case != 'airgap-amd64'", upgrade)
        # The existing shared step consumes matrix.airgap, so this executes
        # test-airgap and its egress restore rather than ordinary online Test.
        self.assertIn("AIRGAP_TEST: ${{ matrix.airgap && 'true' || 'false' }}", workflow)
        self.assertIn("if: env.TEST_TYPE == 'install' && env.AIRGAP_TEST == 'true'", workflow)
        self.assertIn('run: uds run test-airgap --set FLAVOR=upstream --no-progress', workflow)

    def test_airgap_cli_summary_never_claims_full_acceptance(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / 'summary'
            env = {**os.environ, 'EVENT_NAME': 'workflow_dispatch', 'TRUST_BUNDLE': 'false',
                   'DIAGNOSTIC_CASE': 'airgap-amd64', 'INSTALL_RESULT': 'success',
                   'UPGRADE_RESULT': 'skipped', 'PG15_RESULT': 'skipped',
                   'GITHUB_STEP_SUMMARY': str(summary)}
            env.pop('GITHUB_OUTPUT', None)
            plan = subprocess.run(['python3', str(SCRIPT), 'plan'], env=env, capture_output=True, text=True, check=True)
            matrix = json.loads(dict(line.split('=', 1) for line in plan.stdout.splitlines())['install-matrix'])
            self.assertEqual([(r['arch'], r.get('airgap')) for r in matrix['include']], [('amd64', True)])
            result = subprocess.run(['python3', str(SCRIPT), 'verify'], env=env, capture_output=True, text=True, check=True)
            self.assertIn('not full acceptance', summary.read_text())
            self.assertIn('only the AMD airgap install row passed', summary.read_text())
            self.assertNotIn('All tests passed', result.stdout + summary.read_text())
            summary.unlink()
            env['INSTALL_RESULT'] = 'failure'
            result = subprocess.run(['python3', str(SCRIPT), 'verify'], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(summary.exists())

    def test_cli_diagnostic_output_never_claims_full_acceptance(self):
        env = {**os.environ, "EVENT_NAME": "workflow_dispatch", "TRUST_BUNDLE": "false",
               "DIAGNOSTIC_CASE": "upgrade-amd64", "INSTALL_RESULT": "skipped",
               "UPGRADE_RESULT": "success", "PG15_RESULT": "skipped"}
        result = subprocess.run(["python3", str(SCRIPT), "verify"], env=env,
                                capture_output=True, text=True, check=True)
        self.assertIn("Selected diagnostic case passed—not full acceptance", result.stdout)
        self.assertNotIn("All tests passed", result.stdout)
        env["DIAGNOSTIC_CASE"] = "none"
        result = subprocess.run(["python3", str(SCRIPT), "verify"], env=env,
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)



class RunnerComparison(unittest.TestCase):
    def test_profiles_select_native_pairs_and_preserve_airgap(self):
        for profile in ('ubuntu-24.04', 'ubuntu-26.04'):
            result = MODE.plan('workflow_dispatch', 'false', 'custom', 'none', profile)
            self.assertEqual(result['install-matrix']['include'], [
                {'arch': 'amd64', 'runner': profile},
                {'arch': 'arm64', 'runner': profile + '-arm'},
                {'arch': 'amd64', 'runner': profile, 'airgap': True}])
            self.assertEqual(result['upgrade-matrix']['include'], result['install-matrix']['include'][:2])
            self.assertEqual(result['pg15-matrix']['include'], result['install-matrix']['include'][:2])

    def test_nonmanual_inputs_cannot_reduce_coverage(self):
        for event in ('push', 'pull_request'):
            self.assertEqual(MODE.plan(event, 'true', 'custom', 'upgrade-amd64', 'ubuntu-26.04'),
                         MODE.plan(event, 'false', 'custom'))

    def test_default_preserves_repository_override(self):
        self.assertEqual(MODE.plan('workflow_dispatch', 'false', 'custom'),
                         MODE.plan('workflow_dispatch', 'false', 'custom', 'none', 'temporary'))

    def test_unknown_profile_refused(self):
        for profile in ('self-hosted', 'arbitrary', ''):
            with self.assertRaises(ValueError):
                MODE.plan('workflow_dispatch', 'false', 'custom', 'none', profile)

    def test_nonmanual_inputs_cannot_override_the_plan(self):
        for event in ("push", "pull_request"):
            for profile in ("ubuntu-24.04", "ubuntu-26.04", "unknown"):
                self.assertEqual(MODE.plan(event, "false", "custom", profile=profile),
                             MODE.plan(event, "false", "custom"))

    def test_cli_uses_explicit_profile(self):
        env = {**os.environ, 'EVENT_NAME': 'workflow_dispatch', 'TRUST_BUNDLE': 'false',
             'DIAGNOSTIC_CASE': 'pg15-both', 'RUNNER_PROFILE': 'ubuntu-26.04',
             'TEST_RUNNER': 'custom'}
        env.pop('GITHUB_OUTPUT', None)
        result = subprocess.run(["python3", str(SCRIPT), 'plan'], env=env,
                             capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(dict(line.split('=', 1) for line in result.stdout.splitlines())['pg15-matrix']),
                         {'include': [{'arch': 'amd64', 'runner': 'ubuntu-26.04'},
                                      {'arch': 'arm64', 'runner': 'ubuntu-26.04-arm'}]})


class InstallReuseVerification(unittest.TestCase):
    @staticmethod
    def _scenario(scenario="install-online", arch="amd64", run_id=44361333753, attempt=1,
                  job_id=1001, artifact_id=2001):
        sha = "a" * 40
        return {
            "scenario": scenario,
            "architecture": arch,
            "source_run": run_id,
            "attempt": attempt,
            "job_id": job_id,
            "artifact_id": artifact_id,
            "tested_commit": sha,
            "tested_tree": "b" * 40,
            "target_commit": "c" * 40,
            "evidence_url": f"https://github.com/jonathanagustin/uds-takserver/actions/runs/{run_id}/job/{job_id}",
        }

    def _payload(self, scenarios=None, authorized=True, schema_version=1):
        if scenarios is None:
            scenarios = [
                self._scenario("install-online", "amd64", job_id=1001, artifact_id=2001),
                self._scenario("install-online", "arm64", job_id=1002, artifact_id=2002),
                self._scenario("install-airgap", "amd64", job_id=1003, artifact_id=2003),
            ]
        return {"schema_version": schema_version, "reuse_authorized": authorized, "scenarios": scenarios}

    def test_non_push_and_manual_modes_reject_reuse_evidence(self):
        evidence = self._payload()
        for event, requested, selected in (
            ("pull_request", "false", "none"),
            ("workflow_dispatch", "false", "none"),
            ("workflow_dispatch", "true", "none"),
            ("workflow_dispatch", "false", "airgap-amd64"),
            ("workflow_dispatch", "false", "upgrade-amd64"),
            ("workflow_dispatch", "false", "pg15-both"),
        ):
            with self.subTest(event=event, requested=requested, selected=selected):
                with self.assertRaisesRegex(ValueError, "ordinary push"):
                    MODE.verify(event, requested, "skipped", "success", "success",
                                selected=selected, reused_installs=evidence)

    def test_malformed_or_unauthorized_evidence_fails_closed(self):
        bad_cases = [
            "not-json",
            '{"schema_version":1,"schema_version":1,"reuse_authorized":true,"scenarios":[]}',
            self._payload(authorized=False),
            self._payload(schema_version=2),
            self._payload(scenarios=[]),
            self._payload(scenarios=[self._scenario("install-airgap", "arm64")]),
            self._payload(scenarios=[self._scenario("install-online", "amd64"),
                                     self._scenario("install-online", "amd64")]),
            {**self._payload(), "extra": True},
        ]
        bad_url = self._scenario()
        bad_url["evidence_url"] = "https://github.com/evil/fork/actions/runs/44361333753/job/1001"
        bad_cases.append(self._payload(scenarios=[bad_url]))
        bad_sha = self._scenario()
        bad_sha["tested_tree"] = "not-a-sha"
        bad_cases.append(self._payload(scenarios=[bad_sha]))
        bad_bool = self._scenario()
        bad_bool["source_run"] = True
        bad_cases.append(self._payload(scenarios=[bad_bool]))
        for case in bad_cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    MODE.verify("push", "false", "skipped", "success", "success", reused_installs=case)

    def test_push_with_reused_installs_still_requires_fresh_regular_and_pg15_upgrades(self):
        full = self._payload()
        MODE.verify("push", "false", "skipped", "success", "success", reused_installs=full)
        MODE.verify("push", "false", "success", "success", "success", reused_installs=full)
        for bad_upgrade in ("skipped", "failure", "cancelled"):
            with self.assertRaisesRegex(ValueError, "Upgrade result"):
                MODE.verify("push", "false", "skipped", bad_upgrade, "success", reused_installs=full)
        for bad_pg15 in ("skipped", "failure", "cancelled"):
            with self.assertRaisesRegex(ValueError, "PostgreSQL 15"):
                MODE.verify("push", "false", "skipped", "success", bad_pg15, reused_installs=full)

    def test_partial_install_reuse_requires_fresh_install_job_success(self):
        partial = self._payload(scenarios=[self._scenario("install-online", "amd64")])
        MODE.verify("push", "false", "success", "success", "success", reused_installs=partial)
        with self.assertRaisesRegex(ValueError, "Install result skipped"):
            MODE.verify("push", "false", "skipped", "success", "success", reused_installs=partial)

    def test_cli_reports_reused_evidence_links_and_never_claims_all_tests_passed(self):
        payload = json.dumps(self._payload())
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            env = {
                **os.environ,
                "EVENT_NAME": "push",
                "TRUST_BUNDLE": "false",
                "DIAGNOSTIC_CASE": "none",
                "INSTALL_RESULT": "skipped",
                "UPGRADE_RESULT": "success",
                "PG15_RESULT": "success",
                "REUSED_INSTALL_EVIDENCE": payload,
                "GITHUB_STEP_SUMMARY": str(summary),
            }
            result = subprocess.run([sys.executable, str(SCRIPT), "verify"], env=env,
                                    capture_output=True, text=True, check=True)
            self.assertIn("3 of 3 installs reused", result.stdout)
            self.assertIn("not seven newly executed paths", result.stdout)
            self.assertIn("actions/runs/44361333753/job/1001", result.stdout)
            self.assertIn("actions/runs/44361333753/job/1002", result.stdout)
            self.assertIn("actions/runs/44361333753/job/1003", result.stdout)
            self.assertNotIn("All tests passed", result.stdout)
            summary_text = summary.read_text(encoding="utf-8")
            self.assertIn("## Install reuse evidence", summary_text)
            self.assertIn("actions/runs/44361333753/job/1003", summary_text)
            self.assertNotIn("All tests passed", summary_text)

            # Non-push CLI invocation with REUSED_INSTALL_EVIDENCE must fail closed.
            env["EVENT_NAME"] = "pull_request"
            env["INSTALL_RESULT"] = "success"
            failed = subprocess.run([sys.executable, str(SCRIPT), "verify"], env=env,
                                    capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("ordinary push", failed.stderr)


if __name__ == "__main__":
    unittest.main()
