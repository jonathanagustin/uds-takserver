#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, assembly stage: build the runnable TAKX 5.7 tree
# dist/takx57 from the outputs of the earlier stages. The work is done by
# ../assemble/assemble.py inside the takx-desktop-proto:toolchain image.
#
# The tree mirrors the official deployment/bin directory. It contains the
# public Kernel 8.30.5 plus project patches, CommonCommo 1.17.2 patched, the
# NASA WorldWind interop build and the takx/ui-java rebuilds, never the
# private artifacts TAKX 5.7 declares; manifest.json names the source and
# SHA-256 of every file. Nothing is launched here.
#
# Usage:
#   assemble.sh            assemble unless status/assemble.json says ok
#   TAKX_ASSEMBLE_FORCE=1  move the existing tree to failed/ and assemble again
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto):
#   dist/takx57/            the tree (boot, load, lib, modules, conf, resources, native)
#   dist/takx57/manifest.json
#   failed/dist-takx57-<n>/ a replaced or failed tree
#   logs/assemble.log
#   status/assemble.json
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="$repo/takx/desktop-prototype/run-in-toolchain.sh"
out="$root/dist/takx57"
logf="$root/logs/assemble.log"

mkdir -p "$root"/{dist,failed,logs,status}

status() { # status seconds notes outputs...
  python3 -I - "$root/status/assemble.json" "$@" <<'PY'
import json, os, sys
path, status, seconds, notes, *outputs = sys.argv[1:]
tmp = path + '.tmp.%d' % os.getpid()
with open(tmp, 'w') as stream:
    json.dump({'stage': 'assemble', 'status': status, 'outputs': outputs,
               'seconds': int(seconds), 'notes': notes}, stream, indent=1)
    stream.write('\n')
os.replace(tmp, path)
PY
}

retire() {
  [[ -e "$out" ]] || return 0
  local n=1
  while [[ -e "$root/failed/dist-takx57-$n" ]]; do n=$((n + 1)); done
  mv "$out" "$root/failed/dist-takx57-$n"
}

if [[ "${TAKX_ASSEMBLE_FORCE:-0}" != 1 && -s "$out/manifest.json" ]] &&
   grep -q '"status": "ok"' "$root/status/assemble.json" 2>/dev/null; then
  echo "assemble: already ok, skipping" >&2
  exit 0
fi

retire
start=$(date +%s)
printf '\n===== %s assemble\n' "$(date -u +%FT%TZ)" >>"$logf"
rc=0
"$run" -- python3 -I -B "$repo/takx/desktop-prototype/assemble/assemble.py" \
  --root "$root" --output "$out" >>"$logf" 2>&1 || rc=$?
seconds=$(($(date +%s) - start))
if [[ "$rc" != 0 || ! -s "$out/manifest.json" ]]; then
  retire
  status failed "$seconds" "assemble.py exit $rc: $(tail -n 1 "$logf" | cut -c1-300)"
  echo "assemble: failed (exit $rc); see $logf" >&2
  exit 1
fi
status ok "$seconds" \
  "runnable tree mirroring official deployment/bin: $(tail -n 1 "$logf"); Kernel 8.30.5 + project patches, CommonCommo 1.17.2 patched, NASA WorldWind interop, takx/ui-java rebuilds; hand-written JBoss Modules descriptors; public tak-assets subset only; manifest sha256 $(sha256sum "$out/manifest.json" | cut -d' ' -f1); nothing launched" \
  "$out" "$out/manifest.json"
echo "assemble: ok in ${seconds}s" >&2
