#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Plan Test's cluster work and verify intentional acceptance-mode skips."""

import json
import os
import re
import sys

REPO = "jonathanagustin/uds-takserver"
SHA40 = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_INSTALL_SCENARIOS = frozenset({
    ("install-online", "amd64"),
    ("install-online", "arm64"),
    ("install-airgap", "amd64"),
})
REUSED_SCENARIO_KEYS = frozenset({
    "scenario",
    "architecture",
    "source_run",
    "attempt",
    "job_id",
    "artifact_id",
    "tested_commit",
    "tested_tree",
    "target_commit",
    "evidence_url",
})


def _unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_reused_install_evidence(event, requested, selected, evidence):
    """Strictly validate opt-in reused install evidence; fail closed on any drift."""
    if evidence is None or evidence == "":
        return None
    if event != "push" or trust_only(event, requested) or selected != "none":
        raise ValueError("Install reuse is only permitted for ordinary push runs")
    if isinstance(evidence, str):
        try:
            payload = json.loads(evidence, object_pairs_hook=_unique_json_pairs)
        except (ValueError, RecursionError) as err:
            raise ValueError("Invalid reused install evidence JSON") from err
    elif isinstance(evidence, dict):
        payload = evidence
    else:
        raise ValueError("Reused install evidence must be a JSON object")
    if not isinstance(payload, dict) or set(payload.keys()) != {"schema_version", "reuse_authorized", "scenarios"}:
        raise ValueError("Unexpected reused install evidence schema")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise ValueError("Unsupported reused install evidence schema version")
    if payload["reuse_authorized"] is not True:
        raise ValueError("Reused install evidence is not authorized")
    scenarios = payload["scenarios"]
    if not isinstance(scenarios, list) or not (1 <= len(scenarios) <= len(REQUIRED_INSTALL_SCENARIOS)):
        raise ValueError("Reused install evidence must list 1 to 3 scenarios")
    seen = set()
    normalized = []
    for item in scenarios:
        if not isinstance(item, dict) or set(item.keys()) != REUSED_SCENARIO_KEYS:
            raise ValueError("Invalid reused install scenario entry")
        pair = (item["scenario"], item["architecture"])
        if pair not in REQUIRED_INSTALL_SCENARIOS or pair in seen:
            raise ValueError(f"Invalid or duplicate reused install scenario: {pair}")
        seen.add(pair)
        for field in ("source_run", "attempt", "job_id", "artifact_id"):
            val = item[field]
            if type(val) is not int or val <= 0:
                raise ValueError(f"Invalid positive integer for {field}")
        for field in ("tested_commit", "tested_tree", "target_commit"):
            val = item[field]
            if not isinstance(val, str) or not SHA40.fullmatch(val):
                raise ValueError(f"Invalid commit/tree SHA for {field}")
        allowed_urls = {
            f"https://github.com/{REPO}/actions/runs/{item['source_run']}/job/{item['job_id']}",
            f"https://github.com/{REPO}/actions/runs/{item['source_run']}/attempts/{item['attempt']}",
        }
        if item["evidence_url"] not in allowed_urls:
            raise ValueError("Reused install evidence URL does not match repository run/attempt/job")
        normalized.append(item)
    return sorted(normalized, key=lambda row: (row["scenario"], row["architecture"]))


def format_reused_summary(reused, install_result):
    fresh_installs = len(REQUIRED_INSTALL_SCENARIOS) - len(reused)
    lines = [
        f"Push verification passed with install reuse ({len(reused)} of 3 installs reused, "
        f"{fresh_installs} installs executed fresh [install={install_result}]; "
        "regular and PostgreSQL 15 upgrades executed fresh)—not seven newly executed paths:",
    ]
    for item in reused:
        lines.append(
            f"- {item['scenario']} ({item['architecture']}): run {item['source_run']} "
            f"attempt {item['attempt']} job {item['job_id']} artifact {item['artifact_id']} "
            f"{item['evidence_url']}"
        )
    return "\n".join(lines)


def trust_only(event, requested):
    return event == "workflow_dispatch" and requested == "true"


def diagnostic_case(event, requested, selected="none"):
    # Inputs from non-manual events cannot reduce required coverage.
    if event != "workflow_dispatch":
        return "none"
    selected = selected or "none"
    if selected not in {"none", "upgrade-amd64", "pg15-amd64", "pg15-both", "airgap-amd64"}:
        raise ValueError("Unknown diagnostic case")
    if selected != "none" and trust_only(event, requested):
        raise ValueError("Diagnostic case and trust_bundle cannot be combined")
    return selected


def plan(event, requested, amd64_runner, selected="none", profile="temporary"):
    if event != "workflow_dispatch":
        profile = "temporary"
    if profile not in {"temporary", "ubuntu-24.04", "ubuntu-26.04"}:
        raise ValueError("Unknown runner profile")
    if profile != "temporary":
        amd64_runner = profile
    arm64_runner = profile + "-arm" if profile != "temporary" else "ubuntu-22.04-arm"
    selected = diagnostic_case(event, requested, selected)
    upgrades = {"include": [{"arch": "amd64", "runner": amd64_runner},
                             {"arch": "arm64", "runner": arm64_runner}]}
    single = {"include": [{"arch": "amd64", "runner": amd64_runner}]}
    return {"install-matrix": install_matrix(event, requested, amd64_runner, selected, arm64_runner),
            "upgrade-matrix": single if selected == "upgrade-amd64" else upgrades,
            "pg15-matrix": single if selected == "pg15-amd64" else upgrades,
            "diagnostic-case": selected}


def install_matrix(event, requested, amd64_runner, selected="none", arm64_runner="ubuntu-22.04-arm"):
    selected = diagnostic_case(event, requested, selected)
    if selected == "airgap-amd64":
        return {"include": [{"arch": "amd64", "runner": amd64_runner, "airgap": True}]}
    include = [{"arch": "amd64", "runner": amd64_runner},
               {"arch": "arm64", "runner": arm64_runner}]
    if not trust_only(event, requested):
        include.append({"arch": "amd64", "runner": amd64_runner, "airgap": True})
    return {"include": include}


def verify(event, requested, install, upgrade, pg15, selected="none", pg15_required="false", reused_installs=None):
    if pg15_required not in {"true", "false"}:
        raise ValueError("Unknown PostgreSQL 15 expectation")
    selected = diagnostic_case(event, requested, selected)
    reused = parse_reused_install_evidence(event, requested, selected, reused_installs)
    if selected != "none":
        expected = (("success", "skipped", "skipped") if selected == "airgap-amd64"
                    else ("skipped", "success", "skipped") if selected == "upgrade-amd64"
                    else ("skipped", "skipped", "success"))
        if (install, upgrade, pg15) != expected:
            raise ValueError(f"Diagnostic results {(install, upgrade, pg15)}; expected {expected}")
        return None
    if reused is not None:
        covered = {(item["scenario"], item["architecture"]) for item in reused}
        allowed_install = {"success", "skipped"} if covered == REQUIRED_INSTALL_SCENARIOS else {"success"}
        if install not in allowed_install:
            raise ValueError(f"Install result {install}; expected one of {sorted(allowed_install)}")
        if upgrade != "success":
            raise ValueError(f"Upgrade result {upgrade}; expected fresh success")
        if pg15 != "success":
            raise ValueError(f"PostgreSQL 15 upgrade result {pg15}; expected fresh success")
        return reused
    if install != "success":
        raise ValueError(f"Install did not pass: {install}")
    expected_upgrade = "skipped" if trust_only(event, requested) else "success"
    if upgrade != expected_upgrade:
        raise ValueError(f"Upgrade result {upgrade}; expected {expected_upgrade}")
    if trust_only(event, requested):
        allowed_pg15 = {"skipped"}
    elif event == "push" or (event == "pull_request" and pg15_required == "true"):
        # A missing or false flag cannot weaken the unconditional push policy.
        # For PRs the workflow supplies its label-based selection policy,
        # independently of the job result being verified.
        allowed_pg15 = {"success"}
    else:
        allowed_pg15 = {"success", "skipped"}
    if pg15 not in allowed_pg15:
        raise ValueError(f"PostgreSQL 15 upgrade did not pass or intentionally skip: {pg15}")
    return None


def main():
    event, requested = os.environ["EVENT_NAME"], os.environ.get("TRUST_BUNDLE", "false")
    selected = diagnostic_case(event, requested, os.environ.get("DIAGNOSTIC_CASE", "none"))
    if sys.argv[1:] == ["plan"]:
        outputs = plan(event, requested, os.environ.get("TEST_RUNNER") or "ubuntu-22.04",
                     selected, os.environ.get("RUNNER_PROFILE", "temporary"))
        output = "".join(key + "=" + (value if isinstance(value, str) else
                         json.dumps(value, separators=(",", ":"))) + "\n"
                         for key, value in outputs.items())
        if path := os.environ.get("GITHUB_OUTPUT"):
            with open(path, "a", encoding="utf-8") as stream:
                stream.write(output)
        else:
            print(output, end="")
        return
    if sys.argv[1:] != ["verify"]:
        raise ValueError("expected plan or verify")
    reused = verify(
        event,
        requested,
        os.environ["INSTALL_RESULT"],
        os.environ["UPGRADE_RESULT"],
        os.environ["PG15_RESULT"],
        selected,
        os.environ.get("PG15_REQUIRED", "false"),
        os.environ.get("REUSED_INSTALL_EVIDENCE") or None,
    )
    if selected != "none":
        message = "Selected diagnostic case passed—not full acceptance"
        print(message)
        if selected == "airgap-amd64" and (path := os.environ.get("GITHUB_STEP_SUMMARY")):
            with open(path, "a", encoding="utf-8") as stream:
                stream.write("## Focused airgap diagnostic\n\n"
                             "`airgap-amd64`: only the AMD airgap install row passed. "
                             "Ordinary installs and both upgrade families were intentionally skipped.\n\n"
                             + message + ".\n")
        return
    if reused is not None:
        summary_text = format_reused_summary(reused, os.environ["INSTALL_RESULT"])
        print(summary_text)
        if path := os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(path, "a", encoding="utf-8") as stream:
                stream.write("## Install reuse evidence\n\n" + summary_text + "\n")
        return
    print("Trust-only acceptance passed." if trust_only(event, requested) else "All tests passed successfully!")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError) as error:
        print(f"Test validation failed: {error}", file=sys.stderr)
        sys.exit(1)
