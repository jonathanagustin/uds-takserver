#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, Kernel lane: the composed native Kernel the TAKX 5.7
# client is compiled against and booted with.
#
#   kernel-sources     P/kernel-composed-src     unmodified kernel-texture,
#                                                kernel-render-context producers,
#                                                plus the kernel-feature-jni and
#                                                kernel-elevation-jni layers
#   kernel-java-ready  P/kernel-composed-java    whole composed Java, JNI headers
#                                                and a classes-only JAR (what the
#                                                client compile waits for)
#   kernel-native      P/kernel-composed-native  takx/kernel-native CMake parent
#                                                over the composed layout against
#                                                the 14 local providers (-j3)
#   kernel-composed    P/kernel-composed         sdk-package-style JAR + receipt,
#                                                then real JVM controls in the
#                                                runtime image
#   kernel-baseline    P/kernel-native           optional fallback evidence: the
#                                                unmodified kernel-native/build.py
#                                                --phase build (not part of 'all')
#
# The composition is Kernel 8.30.5 (atak-civ 9f6893dd) + project patches
# (texture, render-context), unqualified. It is not the private Kernel 8.57.11
# and is never labelled as such. kernel/compose.py and kernel/controls.py do
# the work; this script only runs them in the images, moves failed outputs
# aside and writes status files. No recipe or lock under takx/ is edited.
# On arm64 the stage runs from the patched copy of the tree (../arch.sh,
# ../arch_tree.py) and the composed layout gets ../overrides/arm64/layout/.
#
# Usage: kernel.sh [all|kernel-sources|kernel-java-ready|kernel-native|
#                   kernel-composed|kernel-controls|kernel-baseline]...
#
# Environment:
#   TAKX_PROTO_ROOT   work root, default /var/lib/takx-proto
#   TAKX_KERNEL_JOBS  ninja jobs for the composed native build, default 3
#
# A stage whose status file says "ok" and whose marker exists is skipped. An
# interrupted kernel-native build (build.ninja present, no receipt) is resumed.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Architecture hand-over (../arch.sh): on a host whose architecture has override
# patches under ../overrides/<arch>/ (arm64) this script continues from the
# patched copy of the tree; on amd64 it does nothing and the tracked recipes run.
# shellcheck source=takx/desktop-prototype/arch.sh
. "${here}/../arch.sh"
takx_arch_enter "${BASH_SOURCE[0]}" "$@"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
compose="${repo}/takx/desktop-prototype/kernel/compose.py"
controls="${repo}/takx/desktop-prototype/kernel/controls.py"
jdk=/tools/temurin-17.0.21_9/jdk-17.0.21+9
atak="${repo}/upstream/atak-civ"
jobs="${TAKX_KERNEL_JOBS:-3}"
javapath="PATH=${jdk}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
providers="${root}/kernel-providers.json"
label='Kernel 8.30.5 + project patches (texture, render-context), unqualified'
# File name of the runtime JAR: it carries the architecture (arch.sh). The key
# ...linux-amd64-public-cpu.jar further down is a reference into the
# takx/client-compiler lock, not a file name, and is the same everywhere.
kernel_jar="$(takx_arch_get kernel_jar)"

mkdir -p "${root}"/{P,failed,logs,status}

log() { printf '%s kernel: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

write_status() { # stage status seconds notes outputs...
  local stage="$1" status="$2" seconds="$3" notes="$4"; shift 4
  python3 -I - "${root}/status/${stage}.json" "${stage}" "${status}" "${seconds}" "${notes}" "$@" <<'PY'
import json, os, sys
path, stage, status, seconds, notes, *outputs = sys.argv[1:]
record = {'stage': stage, 'status': status, 'outputs': outputs, 'seconds': int(seconds), 'notes': notes}
extra = os.environ.get('KERNEL_STATUS_EXTRA')
if extra:
    record.update(json.load(open(extra)))
tmp = path + '.tmp.%d' % os.getpid()
with open(tmp, 'w') as stream:
    json.dump(record, stream, indent=1)
    stream.write('\n')
os.replace(tmp, path)
PY
}

status_of() {
  python3 -I - "${root}/status/$1.json" <<'PY' 2>/dev/null || echo missing
import json, sys
print(json.load(open(sys.argv[1]))['status'])
PY
}

set_aside() { # P-relative name
  local name="$1" n=1
  [[ -e "${root}/P/${name}" ]] || return 0
  while [[ -e "${root}/failed/${name//\//-}-${n}" ]]; do n=$((n + 1)); done
  mv "${root}/P/${name}" "${root}/failed/${name//\//-}-${n}"
  log "moved previous P/${name} to failed/${name//\//-}-${n}"
}

done_already() { # stage marker
  [[ "$(status_of "$1")" == ok && -s "$2" ]] && { log "$1 already ok, skipping"; return 0; }
  return 1
}

need() { # stage dependency marker
  if [[ "$(status_of "$2")" != ok || ! -s "$3" ]]; then
    log "$1 is blocked: prerequisite '$2' is not ok"
    write_status "$1" failed 0 "blocked: prerequisite $2 is not ok; not run"
    return 1
  fi
}

# detached <stage> <container> <log> [run-in-toolchain options...] -- command...
detached() {
  local stage="$1" name="$2" logf="$3" rc; shift 3
  docker rm -f "${name}" >/dev/null 2>&1 || true
  printf '\n===== %s kernel %s\n' "$(date -u +%FT%TZ)" "${stage}" >>"${logf}"
  "${run}" -d --name "${name}" --log "${logf}" "$@" >/dev/null
  rc="$(docker wait "${name}")"
  docker rm "${name}" >/dev/null 2>&1 || true
  [[ "${rc}" == 0 ]]
}

write_providers() {
  # The 14 provider families kernel-native/providers.py verifies, as built by
  # lanes 1-4. Fresh Cesium: root + acquisition root only. GDAL: kernel-scene
  # is both the dependency root and its proof.
  local P="${root}/P"
  cat >"${providers}.tmp" <<EOF
{"sqlcipher":"${P}/sqlcipher","geos":"${P}/geos","proj":"${P}/proj","assimp":"${P}/assimp","las":"${P}/las","mdb":"${P}/mdb","draco":"${P}/draco","spatial":"${P}/spatial",
 "cesium":{"root":"${P}/cesium","acquisition_root":"${P}/cesium-acq"},
 "gdal_jni":{"root":"${P}/gdal-jni","dependency_root":"${P}/scene","dependency_proof":"${P}/scene"},
 "gles":"${P}/gles","kml":"${P}/kml","curl":"${P}/curl","kernel_java":"${P}/kernel-java"}
EOF
  mv "${providers}.tmp" "${providers}"
}

stage_kernel_sources() {
  local out="${root}/P/kernel-composed-src" start seconds
  done_already kernel-sources "${out}/composed-sources.json" && return 0
  set_aside kernel-composed-src
  start=$(date +%s)
  if ! detached kernel-sources takx-kernel-sources "${root}/logs/kernel-sources.log" -- \
      python3 -B "${compose}" sources --output "${out}" --source "${atak}"; then
    write_status kernel-sources failed $(($(date +%s) - start)) "compose.py sources failed; see logs/kernel-sources.log and ${out}/sources.log"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  write_status kernel-sources ok "${seconds}" \
    "unmodified kernel-texture/prepare.py, kernel-render-context/prepare.py and native_sources.py; $(tail -n 1 "${root}/logs/kernel-sources.log"); source preparation only" \
    "${out}/layout" "${out}/context/source" "${out}/composed-sources.json"
}

stage_kernel_java_ready() {
  local out="${root}/P/kernel-composed-java" start seconds extra
  done_already kernel-java-ready "${out}/composed-java.json" && return 0
  need kernel-java-ready kernel-sources "${root}/P/kernel-composed-src/composed-sources.json" || return 1
  need kernel-java-ready kernel-java "${root}/P/kernel-java/result.json" || return 1
  set_aside kernel-composed-java
  start=$(date +%s)
  if ! detached kernel-java-ready takx-kernel-java-ready "${root}/logs/kernel-java-ready.log" -e "${javapath}" -- \
      python3 -B "${compose}" java --output "${out}" --sources "${root}/P/kernel-composed-src" \
      --kernel-java "${root}/P/kernel-java" --public-jars "${root}/inputs/kernel-client-interop" --java-home "${jdk}"; then
    write_status kernel-java-ready failed $(($(date +%s) - start)) "compose.py java failed; see logs/kernel-java-ready.log and ${out}/java.log"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  extra="$(mktemp)"
  python3 -I - "${out}" >"${extra}" <<'PY'
import json, sys
out = sys.argv[1]
d = json.load(open(out + '/composed-java.json'))
print(json.dumps({'jar': d['jar'], 'class_dir': out + '/classes', 'jni_headers': out + '/jni-headers',
                  'classpath_file': out + '/classpath.txt',
                  'extra_public_jars': [c['path'] for c in d['classpath'][-2:]],
                  'counts': {k: d[k] for k in ('source_count', 'class_count', 'jni_header_count')},
                  'replaces_client_compiler_entries': [
                      'kernel-client-interop-public-bitmap-controls-01:classes',
                      'takx-client-compiler-closure:texture-compile-only-overlay-families/classes',
                      'render-context-java-client-frozen-01:classes',
                      'sdk-package-bounded-stock-qualification:package/kernel-8.30.5-linux-amd64-public-cpu.jar']}))
PY
  KERNEL_STATUS_EXTRA="${extra}" write_status kernel-java-ready ok "${seconds}" \
    "Composed Kernel 8.30.5 Java: kernel-java sources + texture + render-context + kernel-client-interop Java (incl. PublicViewportFit) + sdk-package stock-loader/read-only-IO/OBJ patches. Classes only: no native libraries or runtime resources in this JAR; it is for compiling. $(tail -n 1 "${root}/logs/kernel-java-ready.log"). The runtime JAR with natives is P/kernel-composed/package/${kernel_jar} (status/kernel-composed.json). ${label}; not private 8.57.11." \
    "${out}/kernel-8.30.5-composed-classes.jar" "${out}/classes" "${out}/jni-headers" "${out}/classpath.txt" "${out}/composed-java.json"
  rm -f "${extra}"
}

stage_kernel_native() {
  local out="${root}/P/kernel-composed-native" start seconds resume=()
  done_already kernel-native "${out}/composed-native.json" && return 0
  need kernel-native kernel-java-ready "${root}/P/kernel-composed-java/composed-java.json" || return 1
  write_providers
  if [[ -s "${out}/build/build.ninja" && ! -e "${out}/composed-native.json" ]]; then
    resume=(--resume); log "kernel-native: resuming the interrupted build in ${out}"
  else
    set_aside kernel-composed-native
  fi
  start=$(date +%s)
  if ! detached kernel-native takx-kernel-native "${root}/logs/kernel-native.log" -e "${javapath}" -- \
      python3 -B "${compose}" native --output "${out}" --sources "${root}/P/kernel-composed-src" \
      --java "${root}/P/kernel-composed-java" --providers "${providers}" --cache "${root}/cache" \
      --java-home "${jdk}" --jobs "${jobs}" --overrides "${root}/overrides" "${resume[@]}"; then
    write_status kernel-native failed $(($(date +%s) - start)) "compose.py native failed; see logs/kernel-native.log and ${out}/native.log (output kept; rerun resumes a configured build)"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  write_status kernel-native ok "${seconds}" \
    "takx/kernel-native/CMakeLists.txt + overrides/kernel-native-cmake.patch (copy under overrides/kernel-native) over the composed layout, ninja -j${jobs}, 14 providers verified by kernel-native/providers.py before and after; $(tail -n 1 "${root}/logs/kernel-native.log"); no runtime control in this stage" \
    "${out}/build/kernel-cpp/libtakengine.so" "${out}/build/kernel-jni/libtakenginejni.so" "${out}/composed-native.json"
}

run_controls() { # -> exit status of controls.py; output P/kernel-composed/controls
  local out="${root}/P/kernel-composed/controls"
  set_aside kernel-composed/controls
  detached kernel-controls takx-kernel-controls "${root}/logs/kernel-controls.log" --runtime -e "${javapath}" -- \
    python3 -B "${controls}" --package "${root}/P/kernel-composed" --commoncommo "${root}/P/commoncommo" \
    --java-home "${jdk}" --output "${out}"
}

finish_composed() { # start-epoch
  local out="${root}/P/kernel-composed" extra seconds=$(($(date +%s) - $1)) state=ok
  run_controls || state=failed
  seconds=$(($(date +%s) - $1))
  extra="$(mktemp)"
  python3 -I - "${out}" >"${extra}" <<'PY'
import json, sys
out = sys.argv[1]
r = json.load(open(out + '/receipt.json'))
try:
    c = json.load(open(out + '/controls/controls.json'))
    controls = {k: v['passed'] for k, v in c['controls'].items()}
    symbols = {k: c['controls'].get('jni-symbols', {}).get(k) for k in
               ('declared_native_names', 'resolved', 'unresolved_total', 'unresolved_in_kernel_jar')}
except (OSError, ValueError):
    controls, symbols = {}, {}
print(json.dumps({'label': r['label'], 'jar': r['outputs']['jar'], 'receipt': out + '/receipt.json',
                  'third_party_dir': out + '/package/third-party', 'native_lib_dir': out + '/lib/linux64',
                  'stock_loader_java_library_path': r['packaging']['stock_loader_java_library_path'],
                  'controls': controls, 'jni_symbol_cross_check': symbols}))
PY
  KERNEL_STATUS_EXTRA="${extra}" write_status kernel-composed "${state}" "${seconds}" \
    "${label}. sdk-package layout (stock EngineLibrary loader, nine natives under libs/linux64, locked runtime resources) with receipt.json; controls in the runtime image: $(tail -n 1 "${root}/logs/kernel-controls.log"). CPU/JNI controls only: renderer, SDK and client acceptance remain false." \
    "${out}/package/${kernel_jar}" "${out}/receipt.json" "${out}/controls/controls.json" "${out}/lib/linux64" "${out}/package/third-party"
  rm -f "${extra}"
  [[ "${state}" == ok ]]
}

stage_kernel_composed() {
  local out="${root}/P/kernel-composed" start
  done_already kernel-composed "${out}/controls/controls.json" && return 0
  need kernel-composed kernel-native "${root}/P/kernel-composed-native/composed-native.json" || return 1
  set_aside kernel-composed
  start=$(date +%s)
  if ! detached kernel-composed takx-kernel-package "${root}/logs/kernel-package.log" -e "${javapath}" -- \
      python3 -B "${compose}" package --output "${out}" --sources "${root}/P/kernel-composed-src" \
      --java "${root}/P/kernel-composed-java" --native "${root}/P/kernel-composed-native" \
      --providers "${providers}" --cache "${root}/cache" --source "${atak}"; then
    write_status kernel-composed failed $(($(date +%s) - start)) "compose.py package failed; see logs/kernel-package.log"
    return 1
  fi
  finish_composed "${start}"
}

stage_kernel_controls() { # rerun only the controls against the existing package
  [[ -s "${root}/P/kernel-composed/receipt.json" ]] || { log "kernel-controls: no package yet"; return 1; }
  finish_composed "$(date +%s)"
}

stage_kernel_baseline() {
  # Fallback evidence only: the unmodified recipe, baseline Kernel 8.30.5
  # without the texture and render-context patches. One compiler job (recipe).
  local out="${root}/P/kernel-native" start seconds
  done_already kernel-baseline "${out}/complete.json" && return 0
  write_providers
  set_aside kernel-native
  start=$(date +%s)
  if ! detached kernel-baseline takx-kernel-baseline "${root}/logs/kernel-baseline.log" -e "${javapath}" -- \
      python3 -B "${repo}/takx/kernel-native/build.py" --phase build --providers "${providers}" --source "${atak}" \
      --cache "${root}/cache" --output "${out}" --java-home "${jdk}" \
      --cmake /workspace/takx-native-tools/venv/bin/cmake --ninja /workspace/takx-native-tools/venv/bin/ninja; then
    write_status kernel-baseline failed $(($(date +%s) - start)) "takx/kernel-native/build.py --phase build failed; see logs/kernel-baseline.log"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  write_status kernel-baseline ok "${seconds}" \
    "unmodified takx/kernel-native/build.py --phase build: baseline Kernel 8.30.5 (no texture, no render-context), its own strict JNI smokes passed; complete.json sha256 $(sha256sum "${out}/complete.json" | cut -d' ' -f1)" \
    "${out}/build/kernel-cpp/libtakengine.so" "${out}/build/kernel-jni/libtakenginejni.so" "${out}/complete.json"
}

(($#)) || set -- all
rc=0
for target in "$@"; do
  case "${target}" in
    all) stage_kernel_sources && stage_kernel_java_ready && stage_kernel_native && stage_kernel_composed || rc=1;;
    kernel-sources) stage_kernel_sources || rc=1;;
    kernel-java-ready) stage_kernel_java_ready || rc=1;;
    kernel-native) stage_kernel_native || rc=1;;
    kernel-composed) stage_kernel_composed || rc=1;;
    kernel-controls) stage_kernel_controls || rc=1;;
    kernel-baseline) stage_kernel_baseline || rc=1;;
    -h|--help) sed -n '5,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "kernel.sh: unknown target '${target}'" >&2; exit 2;;
  esac
done
exit "${rc}"
