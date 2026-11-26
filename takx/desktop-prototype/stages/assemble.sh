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
# Left out for their licence: the bytedeco FFmpeg native JAR ("nonfree and
# unredistributable"), the CDDL-only wizard reference JAR, joost and JOCL. The
# Kernel JAR in the tree is a derived copy (assemble/derive_jar.py): font
# atlases without a licence removed, RUNPATH removed from two libraries.
#
# Usage:
#   assemble.sh               assemble unless status/assemble.json says ok
#   TAKX_ASSEMBLE_FORCE=1     move the existing tree to failed/ and assemble again
#   TAKX_ASSEMBLE_NAME=NAME   assemble into dist/NAME and leave dist/takx57, its
#                             overlay and its status files alone; the overlay,
#                             the derived Kernel JAR and the status files of
#                             that tree carry the suffix -NAME
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto; SUFFIX is empty for
# the default tree and -NAME otherwise):
#   dist/takx57/ or dist/NAME/     the tree (boot, load, lib, modules, conf,
#                                  resources, native, share) and manifest.json
#   P/kernel-overlay<SUFFIX>/      overlay JAR and receipt (assemble/kernel_overlay.py)
#   P/kernel-derived<SUFFIX>/      derived Kernel JAR and derive-receipt.json
#   failed/dist-<tree>-<n>/        a replaced or failed tree
#   logs/assemble.log
#   P/compat-system-clock/         libuds_desktop_clock.so (takx/compat recipe)
#   status/kernel-overlay<SUFFIX>.json, status/kernel-derived<SUFFIX>.json,
#   status/compat-system-clock.json, status/assemble<SUFFIX>.json
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
name="${TAKX_ASSEMBLE_NAME:-takx57}"
[[ "${name}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || { echo "assemble: TAKX_ASSEMBLE_NAME must be a plain directory name" >&2; exit 2; }
suffix=""
if [[ "${name}" != takx57 ]]; then suffix="-${name}"; fi
out="${root}/dist/${name}"
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
status() { stage_status "assemble${suffix}" "$@"; }

retire() {
  [[ -e "${out}" ]] || return 0
  local n=1
  while [[ -e "${root}/failed/dist-${name}-${n}" ]]; do n=$((n + 1)); done
  mv "${out}" "${root}/failed/dist-${name}-${n}"
}

if [[ "${TAKX_ASSEMBLE_FORCE:-0}" != 1 && -s "${out}/manifest.json" ]] &&
   grep -q '"status": "ok"' "${root}/status/assemble${suffix}.json" 2>/dev/null; then
  echo "assemble: already ok, skipping" >&2
  exit 0
fi

# Kernel Java overlay: Kernel classes rebuilt with the recorded prototype
# patches (assemble/kernel-overlay/*.patch). Rebuilt on every assembly.
overlay="${root}/P/kernel-overlay${suffix}"
if [[ -e "${overlay}" ]]; then
  n=1; while [[ -e "${root}/failed/kernel-overlay${suffix}-${n}" ]]; do n=$((n + 1)); done
  mv "${overlay}" "${root}/failed/kernel-overlay${suffix}-${n}"
fi
start=$(date +%s)
printf '\n===== %s kernel-overlay\n' "$(date -u +%FT%TZ)" >>"${logf}"
rc=0
"${run}" -- python3 -I -B "${repo}/takx/desktop-prototype/assemble/kernel_overlay.py" \
  --root "${root}" --output "${overlay}" >>"${logf}" 2>&1 || rc=$?
if [[ "${rc}" != 0 || ! -s "${overlay}/receipt.json" ]]; then
  stage_status "kernel-overlay${suffix}" failed "$(($(date +%s) - start))" "kernel_overlay.py exit ${rc}: $(tail -n 1 "${logf}" | cut -c1-300)"
  echo "assemble: kernel overlay failed (exit ${rc}); see ${logf}" >&2
  exit 1
fi
stage_status "kernel-overlay${suffix}" ok "$(($(date +%s) - start))" \
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

# The Kernel JAR of the tree: a derived copy of the Kernel stage's JAR
# (assemble/derive_jar.py). Derived again on every assembly.
derived="${root}/P/kernel-derived${suffix}"
kernel_jar="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["jar"]["path"])' "${root}/status/kernel-composed.json")"
if [[ -e "${derived}" ]]; then
  n=1; while [[ -e "${root}/failed/kernel-derived${suffix}-${n}" ]]; do n=$((n + 1)); done
  mv "${derived}" "${root}/failed/kernel-derived${suffix}-${n}"
fi
start=$(date +%s)
printf '\n===== %s kernel-derived\n' "$(date -u +%FT%TZ)" >>"${logf}"
rc=0
mkdir -p "${derived}"
"${run}" -- python3 -I -B "${repo}/takx/desktop-prototype/assemble/derive_jar.py" \
  --input "${kernel_jar}" --output "${derived}/$(basename "${kernel_jar}" .jar)-tree.jar" \
  --receipt "${derived}/derive-receipt.json" >>"${logf}" 2>&1 || rc=$?
if [[ "${rc}" != 0 || ! -s "${derived}/derive-receipt.json" ]]; then
  stage_status "kernel-derived${suffix}" failed "$(($(date +%s) - start))" "derive_jar.py exit ${rc}: $(tail -n 1 "${logf}" | cut -c1-300)"
  echo "assemble: deriving the Kernel JAR failed (exit ${rc}); see ${logf}" >&2
  exit 1
fi
stage_status "kernel-derived${suffix}" ok "$(($(date +%s) - start))" \
  "Kernel 8.30.5 + project patches, derived copy for the tree: $(tail -n 1 "${logf}"); font atlases without a licence removed, RUNPATH removed from libtakengine.so and libtakenginejni.so; the Kernel stage JAR, its receipt and its controls are unchanged and describe the built JAR" \
  "${derived}/$(basename "${kernel_jar}" .jar)-tree.jar" "${derived}/derive-receipt.json"

retire
start=$(date +%s)
printf '\n===== %s assemble\n' "$(date -u +%FT%TZ)" >>"${logf}"
rc=0
"${run}" -- python3 -I -B "${repo}/takx/desktop-prototype/assemble/assemble.py" \
  --root "${root}" --output "${out}" --overlay "${overlay}" --overlay-status "kernel-overlay${suffix}" \
  --kernel-derived "${derived}" >>"${logf}" 2>&1 || rc=$?
seconds=$(($(date +%s) - start))
if [[ "${rc}" != 0 || ! -s "${out}/manifest.json" ]]; then
  retire
  status failed "${seconds}" "assemble.py exit ${rc}: $(tail -n 1 "${logf}" | cut -c1-300)"
  echo "assemble: failed (exit ${rc}); see ${logf}" >&2
  exit 1
fi
status ok "${seconds}" \
  "runnable tree mirroring official deployment/bin: $(tail -n 1 "${logf}"); Kernel 8.30.5 + project patches (derived copy, see status/kernel-derived${suffix}.json), CommonCommo 1.17.2 patched, NASA WorldWind interop, takx/ui-java rebuilds; hand-written JBoss Modules descriptors; public tak-assets subset only; no FFmpeg natives, no wizard reference JAR, no joost, no JOCL; manifest sha256 $(sha256sum "${out}/manifest.json" | cut -d' ' -f1); nothing launched" \
  "${out}" "${out}/manifest.json"
echo "assemble: ok in ${seconds}s" >&2
