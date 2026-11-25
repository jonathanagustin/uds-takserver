#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, lane 3: the small native providers plus the GDAL chain.
#
#   draco    takx/native-deps      Draco + tinygltf + Khronos headers
#   las      takx/native-las       libLAS/LASzip/Boost subset
#   mdb      takx/native-mdbtools  MDBTools 1.0.0 (--with-upstream-fixture)
#   gles     takx/native-gles      libglvnd dispatch over the image's Mesa
#                                  (needs P/draco for the Khronos headers)
#   scene    takx/kernel-scene     minizip + reduced GDAL 2.4.4 + KernelScene
#                                  (waits for lane 2's P/proj and P/assimp)
#   gdal-jni takx/native-gdal-jni  libgdal.so + libgdalalljni.so, default
#                                  (non-KML) profile, which is the one
#                                  kernel-native/providers.py accepts
#                                  (waits for lane 4's status/gis-java.json)
#   curl     takx/native-curl      curl 8.23.0 (waits for lane 1's P/sqlcipher
#                                  and lane 2's P/assimp)
#
# Every provider is produced by the unmodified recipe under takx/, run inside
# the takx-desktop-proto:toolchain image through run-in-toolchain.sh. This
# script only seeds a dedicated source cache per recipe, runs the recipe,
# replays the recipe's own guard/proof tests and records the result. It never
# edits a recipe, a lock or a receipt.
#
# Usage:
#   lane3.sh [all|draco|las|mdb|gles|scene|gdal-jni|curl]...
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto):
#   cache/<sha256>              flat verified source cache written by fetch.sh
#   cache-lane3/<provider>/     dedicated per-recipe cache, hard links into cache/
#   src/atak-civ-lfs/...        real bytes of the atak-civ LFS archives (fetch.sh)
#   inputs/native-curl/         curl-8.23.0.tar.xz by name (fetch.sh)
#   P/<provider>/               the recipe's --output (proof directory)
#   failed/<provider>-<n>/      a failed or stale output, moved aside, never reused
#   logs/<provider>.log         recipe output; logs/<provider>-verify.log the tests
#   status/<provider>.json      {"stage","status","outputs","seconds","notes"}
#
# Environment:
#   TAKX_PROTO_ROOT       work root
#   TAKX_LANE3_WAIT_S     how long to wait for another lane's status file
#                         (default 14400)
#   TAKX_LANE3_FETCH      1 (default) passes --fetch-dependencies where the
#                         recipe has that flag; 0 runs strictly offline
#   TAKX_LANE3_GIS_BUILD  gis-java build directory (holds build-state.json);
#                         default: found from status/gis-java.json outputs
#   TAKX_PROTO_CACHE      provider cache registry prefix; 'off' neither restores
#                         nor pushes (also implied by TAKX_LANE3_FETCH=0)
#
# A provider whose status file already says "ok" and whose complete.json exists
# is skipped. Recipes run with one build job.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Architecture hand-over (../arch.sh): on a host whose architecture has override
# patches under ../overrides/<arch>/ (arm64) this script continues from the
# patched copy of the tree; on amd64 it does nothing and the tracked recipes run.
if [[ -f "${here}/../arch.sh" ]]; then
  # shellcheck source=takx/desktop-prototype/arch.sh
  . "${here}/../arch.sh"
  takx_arch_enter "${BASH_SOURCE[0]}" "$@"
fi
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
jdk=/tools/temurin-17.0.21_9/jdk-17.0.21+9
cmake=/workspace/takx-native-tools/venv/bin/cmake
ninja=/workspace/takx-native-tools/venv/bin/ninja
atak="${repo}/upstream/atak-civ"
wait_s="${TAKX_LANE3_WAIT_S:-14400}"
fetch=()
if [[ "${TAKX_LANE3_FETCH:-1}" == 1 ]]; then fetch=(--fetch-dependencies); fi
# providers.json describes the default invocation; any other one is not cached.
[[ "${TAKX_LANE3_FETCH:-1}" == 1 ]] || TAKX_PROTO_CACHE=off

mkdir -p "${root}"/{P,failed,logs,status,cache-lane3}

log() { printf '%s lane3: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

write_status() { # stage status seconds notes outputs...
  local stage="$1" status="$2" seconds="$3" notes="$4"; shift 4
  python3 -I - "${root}/status/${stage}.json" "${stage}" "${status}" "${seconds}" "${notes}" "$@" <<'PY'
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

status_of() { # stage -> ok|failed|missing
  python3 -I - "${root}/status/$1.json" <<'PY' 2>/dev/null || echo missing
import json, sys
print(json.load(open(sys.argv[1]))['status'])
PY
}

set_aside() { # provider: move an existing output dir to failed/<provider>-<n>
  local name="$1" n=1
  [[ -e "${root}/P/${name}" ]] || return 0
  while [[ -e "${root}/failed/${name}-${n}" ]]; do n=$((n + 1)); done
  mv "${root}/P/${name}" "${root}/failed/${name}-${n}"
  log "moved previous P/${name} to failed/${name}-${n}"
}

seed_cache() { # provider recipe-dir [extra lock files...]
  # Hard-link every cache/<sha256> whose digest is named by the recipe's lock or
  # entrypoint. The recipe itself re-verifies size and digest of each entry.
  local name="$1"; shift
  local dir="${root}/cache-lane3/${name}" sha
  mkdir -p "${dir}"
  while read -r sha; do
    if [[ -f "${root}/cache/${sha}" && ! -e "${dir}/${sha}" ]]; then ln "${root}/cache/${sha}" "${dir}/${sha}"; fi
  done < <(cat "$@" | grep -oE '\b[0-9a-f]{64}\b' | sort -u)
  log "cache-lane3/${name} holds $(find "${dir}" -maxdepth 1 -type f | wc -l) entries"
}

need() { # stage dependency...
  local stage="$1" dep; shift
  for dep in "$@"; do
    if [[ "$(status_of "${dep}")" != ok || ! -s "${root}/P/${dep}/complete.json" ]]; then
      log "${stage} is blocked: prerequisite '${dep}' is not ok"
      write_status "${stage}" failed 0 "blocked: prerequisite ${dep} is not ok; recipe not run"
      return 1
    fi
  done
}

wait_for() { # stage dependency seconds
  local stage="$1" dep="$2" left="$3" state
  while :; do
    state="$(status_of "${dep}")"
    if [[ "${state}" == ok && -s "${root}/P/${dep}/complete.json" ]]; then return 0; fi
    if [[ "${state}" == failed ]]; then break; fi
    if ((left <= 0)); then state="not ready after ${3}s"; break; fi
    sleep 60; left=$((left - 60))
  done
  log "${stage} is blocked: prerequisite '${dep}' is ${state}"
  write_status "${stage}" failed 0 "blocked: prerequisite ${dep} is ${state}; recipe not run"
  return 1
}

wait_for_status() { # stage dependency seconds: like wait_for, status file only
  local stage="$1" dep="$2" left="$3" state
  while :; do
    state="$(status_of "${dep}")"
    if [[ "${state}" == ok ]]; then return 0; fi
    if [[ "${state}" == failed ]]; then break; fi
    if ((left <= 0)); then state="not ready after ${3}s"; break; fi
    sleep 60; left=$((left - 60))
  done
  log "${stage} is blocked: prerequisite '${dep}' is ${state}"
  write_status "${stage}" failed 0 "blocked: prerequisite ${dep} is ${state}; recipe not run"
  return 1
}

# Input-keyed provider cache (provider_cache.py, providers.json). A provider
# first asks the cache for a verified copy of its output and is built only when
# there is none; a fresh ok output is offered to the cache. Neither can fail a
# provider, and TAKX_PROTO_CACHE=off skips both.
cache="${repo}/takx/desktop-prototype/provider_cache.py"

cache_restored() { # provider marker: 0 when the cache installed a verified P/<provider>
  [[ "${TAKX_PROTO_CACHE:-}" != off ]] || return 1
  python3 -I "${cache}" restore "$1" >>"${root}/logs/provider-cache.log" 2>&1 || return 1
  if [[ "$(status_of "$1")" == ok && -s "${root}/P/$1/$2" ]]; then return 0; fi
  set_aside "$1"; return 1
}

cache_push() { # provider: best effort
  [[ "${TAKX_PROTO_CACHE:-}" != off ]] || return 0
  python3 -I "${cache}" push "$1" >>"${root}/logs/provider-cache.log" 2>&1 \
    || log "$1 was not pushed to the provider cache; see logs/provider-cache.log"
  return 0
}

# provider <name> <recipe dir> <proof env assignments or ''> <outputs csv> -- recipe args...
# Runs takx/<recipe>/build.py --output P/<name> ..., then the recipe's
# test_build.py (with the proof variables set, which replays its real controls).
provider() {
  local name="$1" recipe="$2" proofenv="$3" outs="$4"; shift 5
  local out="${root}/P/${name}" logf="${root}/logs/${name}.log" vlog="${root}/logs/${name}-verify.log"
  local start rc seconds sha envargs=() item outputs=()
  if [[ "$(status_of "${name}")" == ok && -s "${out}/complete.json" ]]; then
    log "${name} already ok, skipping"; return 0
  fi
  set_aside "${name}"
  if cache_restored "${name}" complete.json; then
    log "${name} restored from the provider cache, build skipped"; return 0
  fi
  docker rm -f "takx-${name}" >/dev/null 2>&1 || true
  start=$(date +%s)
  printf '\n===== %s lane3 %s: build.py %s\n' "$(date -u +%FT%TZ)" "${name}" "$*" >>"${logf}"
  log "${name}: running takx/${recipe}/build.py (log ${logf})"
  "${run}" -d --name "takx-${name}" --log "${logf}" -- \
    python3 -B "${repo}/takx/${recipe}/build.py" --output "${out}" "$@" >/dev/null
  rc="$(docker wait "takx-${name}")"
  docker rm "takx-${name}" >/dev/null 2>&1 || true
  seconds=$(($(date +%s) - start))
  if [[ "${rc}" != 0 || ! -s "${out}/complete.json" ]]; then
    log "${name}: recipe failed (exit ${rc}) after ${seconds}s"
    write_status "${name}" failed "${seconds}" "takx/${recipe}/build.py exit ${rc}; see logs/${name}.log and the retained output directory"
    return 1
  fi
  for item in ${proofenv}; do envargs+=(-e "${item}"); done
  printf '\n===== %s lane3 %s: unittest test*.py %s\n' "$(date -u +%FT%TZ)" "${name}" "${proofenv}" >>"${vlog}"
  if ! "${run}" "${envargs[@]}" -- python3 -B -m unittest discover \
      -s "${repo}/takx/${recipe}" -p 'test*.py' -v >>"${vlog}" 2>&1; then
    seconds=$(($(date +%s) - start))
    log "${name}: recipe passed but its tests failed"
    write_status "${name}" failed "${seconds}" "build.py exit 0 but tests failed; see logs/${name}-verify.log"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  sha="$(sha256sum "${out}/complete.json" | cut -d' ' -f1)"
  IFS=, read -r -a outputs <<<"${outs}"
  for item in "${!outputs[@]}"; do
    [[ -e "${out}/${outputs[${item}]}" ]] || {
      write_status "${name}" failed "${seconds}" "expected output ${outputs[${item}]} is missing"; return 1; }
    outputs[${item}]="${out}/${outputs[${item}]}"
  done
  write_status "${name}" ok "${seconds}" \
    "takx/${recipe}/build.py exit 0; its tests (test*.py) passed ($(tail -n 3 "${vlog}" | tr '\n' ' ' | sed 's/  */ /g')); complete.json sha256 ${sha}${extra_note:+; ${extra_note}}" \
    "${outputs[@]}"
  log "${name}: ok in ${seconds}s"
  cache_push "${name}"
}


gdal_archive="${root}/src/atak-civ-lfs/depends/gdal-2.4.4-mod.tar.gz"
libkml_archive="${root}/src/atak-civ-lfs/takthirdparty/distfiles/libkml.tar.gz"

stage_draco() {
  seed_cache draco "${repo}/takx/native-deps/build.py" "${repo}/takx/native-deps/inputs.lock.json"
  provider draco native-deps "TAKX_NATIVE_DEPS_PROOF=${root}/P/draco" \
    complete.json,install/lib/libdraco.a,install/include -- \
    --source "${atak}" --cache "${root}/cache-lane3/draco" --cmake "${cmake}" --ninja "${ninja}" \
    --jobs 1 "${fetch[@]}"
}

stage_las() {
  seed_cache las "${repo}/takx/native-las/build.py" "${repo}/takx/native-las/inputs.lock.json"
  provider las native-las "TAKX_NATIVE_LAS_PROOF=${root}/P/las" complete.json -- \
    --cache "${root}/cache-lane3/las" --cmake "${cmake}" --ninja "${ninja}" "${fetch[@]}"
}

stage_mdb() {
  seed_cache mdb "${repo}/takx/native-mdbtools/build.py" "${repo}/takx/native-mdbtools/inputs.lock.json" \
    "${repo}/takx/native-mdbtools/patch.json"
  local fixture
  fixture="$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["fixture"]["sha256"])' \
    "${repo}/takx/native-mdbtools/inputs.lock.json")"
  provider mdb native-mdbtools \
    "TAKX_NATIVE_MDBTOOLS_PROOF=${root}/P/mdb TAKX_NATIVE_MDBTOOLS_FIXTURE=${root}/cache-lane3/mdb/${fixture}" \
    complete.json -- \
    --cache "${root}/cache-lane3/mdb" "${fetch[@]}" --with-upstream-fixture
}

stage_gles() {
  need gles draco || return 1
  seed_cache gles "${repo}/takx/native-gles/build.py" "${repo}/takx/native-gles/inputs.lock.json"
  provider gles native-gles "TAKX_NATIVE_GLES_PROOF=${root}/P/gles" \
    complete.json,install/lib/libEGL.so.1,install/lib/libGLESv2.so.2,install/lib/libGLdispatch.so.0 -- \
    --cache "${root}/cache-lane3/gles" --headers "${root}/P/draco/install/include" --ninja "${ninja}" "${fetch[@]}"
}

stage_scene() {
  wait_for scene proj "${wait_s}" || return 1
  wait_for scene assimp "${wait_s}" || return 1
  # kernel-scene has no proof-replay test; its acceptance is the recipe's own
  # minizip and KernelScene controls before complete.json. The test files are
  # its offline guards.
  local extra_note="tests are offline guards; acceptance is the recipe's own minizip/GDAL/KernelScene controls; reduced GDAL 2.4.4 profile (no PDF/JPEG2000/MrSID/SQLite/network drivers)"
  provider scene kernel-scene "" \
    complete.json,dependencies-built.json,producer,gdal-install/lib/libgdal.a,minizip/libminizip.a -- \
    --source "${atak}" --gdal-archive "${gdal_archive}" --libkml-archive "${libkml_archive}" \
    --proj-proof "${root}/P/proj" --assimp-proof "${root}/P/assimp"
}

gis_build_dir() {
  if [[ -n "${TAKX_LANE3_GIS_BUILD:-}" ]]; then printf '%s\n' "${TAKX_LANE3_GIS_BUILD}"; return 0; fi
  python3 -I - "${root}/status/gis-java.json" "${root}" <<'PY'
import json, os, sys
status, root = sys.argv[1:]
seen = []
for item in json.load(open(status)).get('outputs', []) + [root + '/P/gis-java', root + '/gis-java']:
    path = item
    for _ in range(3):
        if path not in seen:
            seen.append(path)
        path = os.path.dirname(path)
for path in seen:
    if os.path.isfile(os.path.join(path, 'build-state.json')):
        print(path)
        sys.exit(0)
sys.exit('no gis-java build directory with build-state.json found')
PY
}

stage_gdal_jni() {
  need gdal-jni scene || return 1
  wait_for_status gdal-jni gis-java "${wait_s}" || return 1
  wait_for gdal-jni proj 0 || return 1
  wait_for gdal-jni assimp 0 || return 1
  local gis
  gis="$(gis_build_dir)" || { write_status gdal-jni failed 0 "blocked: gis-java build directory not found"; return 1; }
  log "gdal-jni: gis-java build directory is ${gis}"
  # The cache key describes --gis-build P/gis-java only.
  [[ "${gis}" == "${root}/P/gis-java" ]] || local TAKX_PROTO_CACHE=off
  local extra_note="default (non-KML) profile; GDAL_JNI_PROOF replay reran the real -Xcheck:jni Java controls; gis-build ${gis}"
  provider gdal-jni native-gdal-jni "GDAL_JNI_PROOF=${root}/P/gdal-jni" \
    complete.json,lib/libgdal.so,lib/libgdalalljni.so -- \
    --dependency-root "${root}/P/scene" --dependency-proof "${root}/P/scene" \
    --dependency-producer "${root}/P/scene/producer" \
    --gdal-archive "${gdal_archive}" --libkml-archive "${libkml_archive}" \
    --proj-proof "${root}/P/proj" --assimp-proof "${root}/P/assimp" \
    --gis-build "${gis}" --java-home "${jdk}"
}

stage_curl() {
  wait_for curl sqlcipher "${wait_s}" || return 1
  wait_for curl assimp "${wait_s}" || return 1
  local extra_note="test_build.py is offline guards; acceptance is the recipe's own strict-link and CurlProbe controls"
  provider curl native-curl "" complete.json,install/lib/libcurl.a -- \
    --archive "${root}/inputs/native-curl/curl-8.23.0.tar.xz" \
    --openssl-proof "${root}/P/sqlcipher" --zlib-proof "${root}/P/assimp"
}

(($#)) || set -- all
extra_note=""
failed=0
for target in "$@"; do
  case "${target}" in
    all)
      # Later stages check their own prerequisites, so one failure blocks only
      # the providers that depend on it.
      for stage in draco las mdb gles scene gdal_jni curl; do
        "stage_${stage}" || failed=1
      done;;
    gdal-jni) stage_gdal_jni || failed=1;;
    draco|las|mdb|gles|scene|curl) "stage_${target}" || failed=1;;
    *) echo "lane3.sh: unknown stage '${target}'" >&2; exit 2;;
  esac
done
exit "${failed}"
