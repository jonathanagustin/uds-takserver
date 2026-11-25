#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Save finished provider outputs to the provider cache. A thin wrapper over
# provider_cache.py: each provider whose status file says "ok" becomes one
# single-layer image <registry>/<provider>:<key>, where the key is derived from
# the provider's inputs (providers.json), so a changed recipe gets a new tag and
# an unchanged one is recognised as already saved. Requires a prior
# `docker login`; without it, or with TAKX_PROTO_CACHE=off, nothing is pushed.
#
# Usage: push-cache.sh [provider ...]     (default: every provider of the lanes)
# Restore: restore-cache.sh; listing: provider_cache.py status --write-index
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
rc=0
python3 -I "$here/provider_cache.py" push "$@" || rc=$?
# 3 means "skipped cleanly" (not ok yet, cache off, not logged in).
if [[ "$rc" == 3 ]]; then exit 0; fi
exit "$rc"
