#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Stage 1 of the desktop prototype: fetch every public input and verify it
# against the pin of the recipe that consumes it. See fetch_inputs.py for the
# layout. Runs inside the toolchain image (Git 2.47, no proxy variables, CA
# bundle mounted) so that reconstructed archives do not depend on host tools.
#
#   takx/desktop-prototype/fetch.sh            # full run, writes status/fetch.json
#   takx/desktop-prototype/fetch.sh --only jars
#   TAKX_FETCH_ON_HOST=1 takx/desktop-prototype/fetch.sh   # use host git/python
#
# Exit status is non-zero when any required input is missing or differs from
# its pin. Pins and locks are never changed by this stage.
#
# On an architecture with override patches (arm64, see arch.sh) the fetch
# continues from the patched tree, so that it reads the locks the stages of
# that architecture verify. On amd64 nothing changes.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=takx/desktop-prototype/arch.sh
. "${here}/arch.sh"
takx_arch_enter "${BASH_SOURCE[0]}" "$@"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
jobs="${TAKX_FETCH_JOBS:-4}"
mkdir -p "$root/logs" "$root/status"
log="$root/logs/fetch.log"

if [[ "${TAKX_FETCH_ON_HOST:-0}" == 1 ]]; then
  python3 -I -B "$here/fetch_inputs.py" --root "$root" --jobs "$jobs" "$@" 2>&1 | tee -a "$log"
  exit "${PIPESTATUS[0]}"
fi
docker image inspect takx-desktop-proto:toolchain >/dev/null 2>&1 || {
  echo "fetch.sh: build takx-desktop-proto:toolchain first (see Dockerfile)" >&2; exit 2; }
# The Java trust store is not needed by this stage, but every later stage that
# lets a JVM reach the network expects it, so create it here once.
"$here/run-in-toolchain.sh" --java-truststore >/dev/null
set +e
"$here/run-in-toolchain.sh" -- python3 -I -B "$here/fetch_inputs.py" --root "$root" --jobs "$jobs" "$@" 2>&1 | tee -a "$log"
status="${PIPESTATUS[0]}"
set -e
exit "$status"
