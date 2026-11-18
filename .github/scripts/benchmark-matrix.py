#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Select native Benchmark runners without changing scheduled or PR coverage."""

import json
import os
import sys


def plan(event, architecture="both", profile="temporary", amd64_runner=""):
    if event != "workflow_dispatch":
        architecture, profile = "both", "temporary"
    architecture, profile = architecture or "both", profile or "temporary"
    if architecture not in {"both", "amd64", "arm64"}:
        raise ValueError("Unknown benchmark architecture")
    if profile not in {"temporary", "ubuntu-24.04", "ubuntu-26.04"}:
        raise ValueError("Unknown benchmark runner profile")
    runners = {"amd64": amd64_runner or "ubuntu-22.04", "arm64": "ubuntu-22.04-arm"}
    if profile != "temporary":
        runners = {"amd64": profile, "arm64": profile + "-arm"}
    return {"include": [{"arch": arch, "runner": runner} for arch, runner in runners.items()
                        if architecture == "both" or architecture == arch]}


def main():
    matrix = plan(os.environ["EVENT_NAME"], os.environ.get("ARCHITECTURE", "both"),
                  os.environ.get("RUNNER_PROFILE", "temporary"), os.environ.get("TEST_RUNNER", ""))
    output = "matrix=" + json.dumps(matrix, separators=(",", ":")) + "\n"
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(output)
    else:
        print(output, end="")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError) as error:
        print(f"Benchmark planning failed: {error}", file=sys.stderr)
        sys.exit(1)
