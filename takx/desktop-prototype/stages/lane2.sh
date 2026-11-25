#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, lane 2: the provider chain that ends in the patched
# CommonCommo 1.17.2 JAR and JNI library.
#
#   proj -> geos -> assimp (also the zlib 1.3.1 provider)
#        -> spatial (kernel-spatial-db; waits for lane 1's P/sqlcipher)
#        -> crypto (native-crypto: quictls 3.0.14 next to the sqlcipher proof)
#        -> commoncommo (native-commoncommo: jcommoncommo.jar + libcommoncommojni.so)
#
# Every provider is produced by the unmodified recipe under takx/, run inside
# the takx-desktop-proto:toolchain image through run-in-toolchain.sh. This
# script only seeds a dedicated source cache per recipe, runs the recipe,
# replays the recipe's own proof test and records the result. It never edits a
# recipe, a lock or a receipt.
#
# Usage:
#   lane2.sh [all|proj|geos|assimp|spatial|crypto|commoncommo]...
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto):
#   cache/<sha256>              flat verified source cache written by fetch.sh
#   cache-lane2/<provider>/     dedicated per-recipe cache, hard links into cache/
#                               (the recipes bound output+cache size, so they
#                               cannot be given the whole flat cache)
#   P/<provider>/               the recipe's --output (proof directory)
#   failed/<provider>-<n>/      a failed or stale output, moved aside, never reused
#   logs/<provider>.log         recipe output; logs/<provider>-verify.log the replay
#   status/<provider>.json      {"stage","status","outputs","seconds","notes"}
#
# Environment:
#   TAKX_PROTO_ROOT              work root
#   TAKX_LANE2_SQLCIPHER_WAIT_S  how long to wait for status/sqlcipher.json
#                                (default 14400)
#   TAKX_LANE2_FETCH             1 (default) passes --fetch-dependencies so an
#                                entry missing from the cache is fetched from the
#                                exact locked URL and checked against the lock;
#                                0 runs strictly offline
#   TAKX_PROTO_CACHE             provider cache registry prefix; 'off' neither
#                                restores nor pushes (also implied by
#                                TAKX_LANE2_FETCH=0)
#
# A provider whose status file already says "ok" and whose complete.json exists
# is skipped. Recipes run with one build job (they hard-code -j1).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="$repo/takx/desktop-prototype/run-in-toolchain.sh"
jdk=/tools/temurin-17.0.21_9/jdk-17.0.21+9
cmake=/workspace/takx-native-tools/venv/bin/cmake
ninja=/workspace/takx-native-tools/venv/bin/ninja
atak="$repo/upstream/atak-civ"
sqlcipher_wait="${TAKX_LANE2_SQLCIPHER_WAIT_S:-14400}"
fetch=()
if [[ "${TAKX_LANE2_FETCH:-1}" == 1 ]]; then fetch=(--fetch-dependencies); fi
# providers.json describes the default invocation; any other one is not cached.
[[ "${TAKX_LANE2_FETCH:-1}" == 1 ]] || TAKX_PROTO_CACHE=off

mkdir -p "$root"/{P,failed,logs,status,cache-lane2}

log() { printf '%s lane2: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

write_status() { # stage status seconds notes outputs...
  local stage="$1" status="$2" seconds="$3" notes="$4"; shift 4
  python3 -I - "$root/status/$stage.json" "$stage" "$status" "$seconds" "$notes" "$@" <<'PY'
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
  python3 -I - "$root/status/$1.json" <<'PY' 2>/dev/null || echo missing
import json, sys
print(json.load(open(sys.argv[1]))['status'])
PY
}

set_aside() { # provider: move an existing output dir to failed/<provider>-<n>
  local name="$1" n=1
  [[ -e "$root/P/$name" ]] || return 0
  while [[ -e "$root/failed/$name-$n" ]]; do n=$((n + 1)); done
  mv "$root/P/$name" "$root/failed/$name-$n"
  log "moved previous P/$name to failed/$name-$n"
}

seed_cache() { # provider recipe-dir [extra lock files...]
  # Hard-link every cache/<sha256> whose digest is named by the recipe's lock or
  # entrypoint. The recipe itself re-verifies size and digest of each entry.
  local name="$1"; shift
  local dir="$root/cache-lane2/$name" sha
  mkdir -p "$dir"
  while read -r sha; do
    if [[ -f "$root/cache/$sha" && ! -e "$dir/$sha" ]]; then ln "$root/cache/$sha" "$dir/$sha"; fi
  done < <(cat "$@" | grep -oE '\b[0-9a-f]{64}\b' | sort -u)
  log "cache-lane2/$name holds $(find "$dir" -maxdepth 1 -type f | wc -l) entries"
}

need() { # stage dependency...
  local stage="$1" dep; shift
  for dep in "$@"; do
    if [[ "$(status_of "$dep")" != ok || ! -s "$root/P/$dep/complete.json" ]]; then
      log "$stage is blocked: prerequisite '$dep' is not ok"
      write_status "$stage" failed 0 "blocked: prerequisite $dep is not ok; recipe not run"
      return 1
    fi
  done
}

wait_for() { # stage dependency seconds
  local stage="$1" dep="$2" left="$3" state
  while :; do
    state="$(status_of "$dep")"
    if [[ "$state" == ok && -s "$root/P/$dep/complete.json" ]]; then return 0; fi
    if [[ "$state" == failed ]]; then break; fi
    if ((left <= 0)); then state="not ready after ${3}s"; break; fi
    sleep 60; left=$((left - 60))
  done
  log "$stage is blocked: prerequisite '$dep' is $state"
  write_status "$stage" failed 0 "blocked: prerequisite $dep is $state; recipe not run"
  return 1
}

# Input-keyed provider cache (provider_cache.py, providers.json). A provider
# first asks the cache for a verified copy of its output and is built only when
# there is none; a fresh ok output is offered to the cache. Neither can fail a
# provider, and TAKX_PROTO_CACHE=off skips both.
cache="$repo/takx/desktop-prototype/provider_cache.py"

cache_restored() { # provider marker: 0 when the cache installed a verified P/<provider>
  [[ "${TAKX_PROTO_CACHE:-}" != off ]] || return 1
  python3 -I "$cache" restore "$1" >>"$root/logs/provider-cache.log" 2>&1 || return 1
  if [[ "$(status_of "$1")" == ok && -s "$root/P/$1/$2" ]]; then return 0; fi
  set_aside "$1"; return 1
}

cache_push() { # provider: best effort
  [[ "${TAKX_PROTO_CACHE:-}" != off ]] || return 0
  python3 -I "$cache" push "$1" >>"$root/logs/provider-cache.log" 2>&1 \
    || log "$1 was not pushed to the provider cache; see logs/provider-cache.log"
  return 0
}

# provider <name> <recipe dir> <proof env assignments or ''> <outputs csv> -- recipe args...
# Runs takx/<recipe>/build.py --output P/<name> ..., then the recipe's
# test_build.py (with the proof variables set, which replays its real controls).
provider() {
  local name="$1" recipe="$2" proofenv="$3" outs="$4"; shift 5
  local out="$root/P/$name" logf="$root/logs/$name.log" vlog="$root/logs/$name-verify.log"
  local start rc seconds sha envargs=() item outputs=()
  if [[ "$(status_of "$name")" == ok && -s "$out/complete.json" ]]; then
    log "$name already ok, skipping"; return 0
  fi
  set_aside "$name"
  if cache_restored "$name" complete.json; then
    log "$name restored from the provider cache, build skipped"; return 0
  fi
  docker rm -f "takx-$name" >/dev/null 2>&1 || true
  start=$(date +%s)
  printf '\n===== %s lane2 %s: build.py %s\n' "$(date -u +%FT%TZ)" "$name" "$*" >>"$logf"
  log "$name: running takx/$recipe/build.py (log $logf)"
  "$run" -d --name "takx-$name" --log "$logf" -- \
    python3 -B "$repo/takx/$recipe/build.py" --output "$out" "$@" >/dev/null
  rc="$(docker wait "takx-$name")"
  docker rm "takx-$name" >/dev/null 2>&1 || true
  seconds=$(($(date +%s) - start))
  if [[ "$rc" != 0 || ! -s "$out/complete.json" ]]; then
    log "$name: recipe failed (exit $rc) after ${seconds}s"
    write_status "$name" failed "$seconds" "takx/$recipe/build.py exit $rc; see logs/$name.log and the retained output directory"
    return 1
  fi
  for item in $proofenv; do envargs+=(-e "$item"); done
  printf '\n===== %s lane2 %s: test_build.py %s\n' "$(date -u +%FT%TZ)" "$name" "$proofenv" >>"$vlog"
  if ! "$run" "${envargs[@]}" -- python3 -B -m unittest discover \
      -s "$repo/takx/$recipe" -p test_build.py -v >>"$vlog" 2>&1; then
    seconds=$(($(date +%s) - start))
    log "$name: recipe passed but its test_build.py failed"
    write_status "$name" failed "$seconds" "build.py exit 0 but test_build.py failed; see logs/$name-verify.log"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  sha="$(sha256sum "$out/complete.json" | cut -d' ' -f1)"
  IFS=, read -r -a outputs <<<"$outs"
  for item in "${!outputs[@]}"; do
    [[ -e "$out/${outputs[$item]}" ]] || {
      write_status "$name" failed "$seconds" "expected output ${outputs[$item]} is missing"; return 1; }
    outputs[$item]="$out/${outputs[$item]}"
  done
  write_status "$name" ok "$seconds" \
    "takx/$recipe/build.py exit 0; its test_build.py passed ($(tail -n 3 "$vlog" | tr '\n' ' ' | sed 's/  */ /g')); complete.json sha256 $sha${extra_note:+; $extra_note}" \
    "${outputs[@]}"
  log "$name: ok in ${seconds}s"
  cache_push "$name"
}

stage_proj() {
  seed_cache proj "$repo/takx/native-proj/build.py" "$repo/takx/native-proj/inputs.lock.json"
  provider proj native-proj "TAKX_NATIVE_PROJ_PROOF=$root/P/proj" \
    complete.json,install/lib/libproj.a -- \
    --cache "$root/cache-lane2/proj" --cmake "$cmake" "${fetch[@]}"
}

stage_geos() {
  seed_cache geos "$repo/takx/native-geos/build.py" "$repo/takx/native-geos/inputs.lock.json" \
    "$repo/takx/native-geos/patch.json"
  provider geos native-geos "TAKX_NATIVE_GEOS_PROOF=$root/P/geos" \
    complete.json,install/lib/libgeos.a,install/lib/libgeos_c.a -- \
    --cache "$root/cache-lane2/geos" "${fetch[@]}"
}

stage_assimp() {
  seed_cache assimp "$repo/takx/native-assimp/build.py" "$repo/takx/native-assimp/inputs.lock.json"
  provider assimp native-assimp "TAKX_NATIVE_ASSIMP_PROOF=$root/P/assimp" \
    complete.json,zlib-install/lib/libz.a -- \
    --cache "$root/cache-lane2/assimp" --jdk-home "$jdk" --cmake "$cmake" --ninja "$ninja" "${fetch[@]}"
}

stage_spatial() {
  need spatial proj geos assimp || return 1
  wait_for spatial sqlcipher "$sqlcipher_wait" || return 1
  seed_cache spatial "$repo/takx/kernel-spatial-db/build.py" "$repo/takx/kernel-spatial-db/inputs.lock.json"
  provider spatial kernel-spatial-db "TAKX_KERNEL_SPATIAL_DB_PROOF=$root/P/spatial" \
    complete.json,libiconv-install,libxml2-install -- \
    --cache "$root/cache-lane2/spatial" --source "$atak" \
    --proj-proof "$root/P/proj" --geos-proof "$root/P/geos" \
    --sqlcipher-proof "$root/P/sqlcipher" --zlib-proof "$root/P/assimp" "${fetch[@]}"
}

stage_crypto() {
  wait_for crypto sqlcipher "$sqlcipher_wait" || return 1
  seed_cache crypto "$repo/takx/native-crypto/build.py" "$repo/takx/native-crypto/inputs.lock.json"
  provider crypto native-crypto \
    "TAKX_NATIVE_CRYPTO_PROOF=$root/P/crypto TAKX_NATIVE_CRYPTO_MODERN=$root/P/sqlcipher" \
    complete.json,quictls-install/lib/libssl.a,quictls-install/lib/libcrypto.a -- \
    --cache "$root/cache-lane2/crypto" --modern-proof "$root/P/sqlcipher" "${fetch[@]}"
}

stage_commoncommo() {
  need commoncommo assimp spatial crypto || return 1
  local tools="$root/cache-commoncommo-tools"
  seed_cache commoncommo "$repo/takx/native-commoncommo/build.py" \
    "$repo/takx/native-commoncommo/inputs.lock.json" "$repo/takx/sources.lock.json"
  # native-commoncommo has no proof-replay test: its acceptance is the five
  # -Xcheck:jni smokes it runs before writing complete.json. test_build.py holds
  # its offline guards only.
  local extra_note="test_build.py here is offline guards only; acceptance is the recipe's five -Xcheck:jni smokes recorded under 'outcomes' in complete.json (CommonCommo 1.17.2 with project JNI patch 0102, static hidden quictls)"
  provider commoncommo native-commoncommo "" \
    complete.json,install/java/jcommoncommo.jar,install/lib/libcommoncommojni.so -- \
    --cache "$root/cache-lane2/commoncommo" --source "$atak" \
    --spatial-proof "$root/P/spatial" --zlib-proof "$root/P/assimp" --quictls-proof "$root/P/crypto" \
    --tools-root "$tools/root" --tool-metadata "$tools/metadata" --java-home "$jdk" "${fetch[@]}"
}

(($#)) || set -- all
extra_note=""
failed=0
for target in "$@"; do
  case "$target" in
    all)
      # Later stages check their own prerequisites, so one failure blocks only
      # the providers that depend on it.
      for stage in proj geos assimp spatial crypto commoncommo; do
        "stage_$stage" || failed=1
      done;;
    proj|geos|assimp|spatial|crypto|commoncommo) "stage_$target" || failed=1;;
    *) echo "lane2.sh: unknown stage '$target'" >&2; exit 2;;
  esac
done
exit "$failed"
