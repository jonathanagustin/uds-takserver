#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Build step of a job of the opt-in workflow: run one group of groups.json.
#
#   ci/run-group.sh GROUP
#
# Runs pipeline.py run GROUP as root from the repository copy ci/enter.sh made
# (TAKX_CI_REPO): it restores the group's prerequisites from the provider
# cache, runs the group's stage scripts and writes status/ci-<group>.json. The
# exit status is non-zero when a prerequisite is missing or a stage failed.
# Uses the docker login of the calling user; no other credential.
set -euo pipefail

if ((EUID != 0)); then
  exec sudo -E env "PATH=${PATH}" "DOCKER_CONFIG=${DOCKER_CONFIG:-${HOME}/.docker}" "$0" "$@"
fi
[[ $# -eq 1 ]] || { echo "usage: run-group.sh GROUP" >&2; exit 2; }
: "${TAKX_CI_REPO:?run ci/enter.sh first}"
: "${TAKX_PROTO_TOOLCHAIN_ID:?run ci/enter.sh first}"
exec python3 -I -B "${TAKX_CI_REPO}/takx/desktop-prototype/ci/pipeline.py" run "$1"
