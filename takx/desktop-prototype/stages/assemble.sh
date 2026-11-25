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
#   P/kernel-overlay/       overlay JAR and receipt (assemble/kernel_overlay.py)
#   failed/dist-takx57-<n>/ a replaced or failed tree
#   logs/assemble.log
#   P/compat-system-clock/  libuds_desktop_clock.so (takx/compat recipe)
#   status/kernel-overlay.json, status/compat-system-clock.json, status/assemble.json
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
out="${root}/dist/takx57"
logf="${root}/logs/assemble.log"

mkdir -p "${root}"/{dist,failed,logs,status}

stage_status() { # stage status seconds notes outputs...
  python3 -I - "${root}/status/$1.json" "$@" <<'PY'
import json, os, sys
path, stage, status, seconds, notes, *outputs = sys.argv[1:]
tmp = path + '.tmp.%d' % os.getpid()
with open(tmp, 'w') as stream:
    json.dump({'stage': stage, 'status': status, 'outputs': outputs,
               'seconds': int(seconds), 'notes': notes}, stream, indent=1)
    stream.write('\n')
os.replace(tmp, path)
PY
}
status() { stage_status assemble "$@"; }

retire() {
  [[ -e "${out}" ]] || return 0
  local n=1
  while [[ -e "${root}/failed/dist-takx57-${n}" ]]; do n=$((n + 1)); done
  mv "${out}" "${root}/failed/dist-takx57-${n}"
}

if [[ "${TAKX_ASSEMBLE_FORCE:-0}" != 1 && -s "${out}/manifest.json" ]] &&
   grep -q '"status": "ok"' "${root}/status/assemble.json" 2>/dev/null; then
  echo "assemble: already ok, skipping" >&2
  exit 0
fi

# Kernel Java overlay: Kernel classes rebuilt with the recorded prototype
# patches (assemble/kernel-overlay/*.patch). Rebuilt on every assembly.
overlay="${root}/P/kernel-overlay"
if [[ -e "${overlay}" ]]; then
  n=1; while [[ -e "${root}/failed/kernel-overlay-${n}" ]]; do n=$((n + 1)); done
  mv "${overlay}" "${root}/failed/kernel-overlay-${n}"
fi
start=$(date +%s)
printf '\n===== %s kernel-overlay\n' "$(date -u +%FT%TZ)" >>"${logf}"
rc=0
"${run}" -- python3 -I -B "${repo}/takx/desktop-prototype/assemble/kernel_overlay.py" \
  --root "${root}" --output "${overlay}" >>"${logf}" 2>&1 || rc=$?
if [[ "${rc}" != 0 || ! -s "${overlay}/receipt.json" ]]; then
  stage_status kernel-overlay failed "$(($(date +%s) - start))" "kernel_overlay.py exit ${rc}: $(tail -n 1 "${logf}" | cut -c1-300)"
  echo "assemble: kernel overlay failed (exit ${rc}); see ${logf}" >&2
  exit 1
fi
stage_status kernel-overlay ok "$(($(date +%s) - start))" \
  "Kernel 8.30.5 + project patches, desktop-prototype Java overlay: $(tail -n 1 "${logf}"); Kernel classes rebuilt from their own sources with assemble/kernel-overlay/*.patch (fuzz 0); the Kernel JAR is unchanged and its controls were not rerun with the overlay" \
  "${overlay}/kernel-8.30.5-prototype-java-overlay.jar" "${overlay}/receipt.json"

# The Kernel build's android.os.SystemClock (takx/compat) loads the JNI
# library uds_desktop_clock; the unmodified compat recipe builds it. The
# source it compiles must be the one inside the composed Kernel.
clock="${root}/P/compat-system-clock"
if [[ ! -s "${clock}/libuds_desktop_clock.so" || "${TAKX_ASSEMBLE_FORCE:-0}" == 1 ]]; then
  if [[ -e "${clock}" ]]; then
    n=1; while [[ -e "${root}/failed/compat-system-clock-${n}" ]]; do n=$((n + 1)); done
    mv "${clock}" "${root}/failed/compat-system-clock-${n}"
  fi
  start=$(date +%s)
  printf '\n===== %s compat-system-clock\n' "$(date -u +%FT%TZ)" >>"${logf}"
  rc=0
  cmp -s "${repo}/takx/compat/src/main/java/android/os/SystemClock.java" \
    "${root}/P/kernel-composed-java/stage/compat/android/os/SystemClock.java" || rc=90
  if [[ "${rc}" == 0 ]]; then
    "${run}" -- python3 -I -B "${repo}/takx/compat/build-system-clock.py" --output "${clock}" >>"${logf}" 2>&1 || rc=$?
  fi
  if [[ "${rc}" != 0 || ! -s "${clock}/libuds_desktop_clock.so" ]]; then
    stage_status compat-system-clock failed "$(($(date +%s) - start))" "takx/compat/build-system-clock.py exit ${rc} (90: SystemClock.java differs from the composed Kernel source): $(tail -n 1 "${logf}" | cut -c1-300)"
    echo "assemble: compat-system-clock failed (exit ${rc}); see ${logf}" >&2
    exit 1
  fi
  stage_status compat-system-clock ok "$(($(date +%s) - start))" \
    "unmodified takx/compat/build-system-clock.py; libuds_desktop_clock.so sha256 $(sha256sum "${clock}/libuds_desktop_clock.so" | cut -d' ' -f1); SystemClock.java equals the source compiled into the composed Kernel; recipe test-system-clock.py not run here" \
    "${clock}/libuds_desktop_clock.so"
fi

retire
start=$(date +%s)
printf '\n===== %s assemble\n' "$(date -u +%FT%TZ)" >>"${logf}"
rc=0
"${run}" -- python3 -I -B "${repo}/takx/desktop-prototype/assemble/assemble.py" \
  --root "${root}" --output "${out}" >>"${logf}" 2>&1 || rc=$?
seconds=$(($(date +%s) - start))
if [[ "${rc}" != 0 || ! -s "${out}/manifest.json" ]]; then
  retire
  status failed "${seconds}" "assemble.py exit ${rc}: $(tail -n 1 "${logf}" | cut -c1-300)"
  echo "assemble: failed (exit ${rc}); see ${logf}" >&2
  exit 1
fi
status ok "${seconds}" \
  "runnable tree mirroring official deployment/bin: $(tail -n 1 "${logf}"); Kernel 8.30.5 + project patches, CommonCommo 1.17.2 patched, NASA WorldWind interop, takx/ui-java rebuilds; hand-written JBoss Modules descriptors; public tak-assets subset only; manifest sha256 $(sha256sum "${out}/manifest.json" | cut -d' ' -f1); nothing launched" \
  "${out}" "${out}/manifest.json"
echo "assemble: ok in ${seconds}s" >&2
