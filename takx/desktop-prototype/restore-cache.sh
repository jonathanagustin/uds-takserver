#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Restore provider outputs from the provider cache. A thin wrapper over
# provider_cache.py: for each provider it derives the input key, pulls
# <registry>/<provider>:<key>, verifies the archive digest and the recorded
# per-file manifest, and only then moves the tree to $TAKX_PROTO_ROOT/P, runs
# the recipe's own check where providers.json names one, and writes its status
# file. An existing provider directory is never overwritten;
# a provider with nothing cached under its key is left for the lanes to build.
# The lanes do the same on their own before building; this script is for
# restoring ahead of time. No index file is needed (cache-index.tsv is a
# generated listing); a first argument naming one is accepted and ignored.
#
# Usage: restore-cache.sh [provider ...]  (default: every provider, prerequisites first)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Architecture hand-over (arch.sh): on a host whose architecture has override
# patches (arm64) the keys are those of the patched tree copy, so this script
# continues from there; on amd64 it does nothing.
if [[ -f "${here}/arch.sh" ]]; then
  # shellcheck source=takx/desktop-prototype/arch.sh disable=SC1091 # followed only when arch.sh is linted with it
  . "${here}/arch.sh"
  takx_arch_enter "${BASH_SOURCE[0]}" "$@"
fi
if [[ $# -gt 0 && "$1" == *.tsv ]]; then
  echo "restore-cache: the index '$1' is not needed any more and is ignored" >&2
  shift
fi
rc=0
python3 -I "${here}/provider_cache.py" restore "$@" || rc=$?
# 3 means some provider was skipped cleanly (nothing cached, already built
# prerequisites missing, cache off); the lanes build those.
if [[ "${rc}" == 3 ]]; then exit 0; fi
exit "${rc}"
