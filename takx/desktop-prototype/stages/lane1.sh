#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Lane 1 of the desktop prototype (critical path of the native Kernel inputs):
#
#   sqlcipher   takx/native-sqlcipher/build.py          -> P/sqlcipher
#   cesium-acq  takx/native-cesium/acquire.py           -> P/cesium-acq   (needs P/draco)
#   cesium      takx/native-cesium/build.py             -> P/cesium       (long, one job)
#   kml         takx/native-kml/build.py                -> P/kml          (needs P/las, P/assimp, P/scene)
#
# Every step runs the unmodified recipe in the takx-desktop-proto:toolchain
# image through ../run-in-toolchain.sh, with one compile job. A step is "ok"
# only when the recipe exited 0 and its own receipt checks below pass. A failed
# output directory is never reused: the next run moves it to
# $ROOT/failed/<name>-<n> and starts in a fresh directory.
#
# Usage: lane1.sh [all|sqlcipher|cesium-acq|cesium|kml|verify-<name>]...   (default: all)
#
# Environment:
#   TAKX_PROTO_ROOT       work root, default /var/lib/takx-proto
#   TAKX_PREREQ_TIMEOUT   seconds to wait for another lane's status file, default 14400
#   TAKX_PROTO_CACHE      provider cache registry prefix; 'off' neither restores nor pushes
set -uo pipefail

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
ROOT="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${here}/../run-in-toolchain.sh"
P="${ROOT}/P"
wait_limit="${TAKX_PREREQ_TIMEOUT:-14400}"
mkdir -p "${P}" "${ROOT}/logs" "${ROOT}/status" "${ROOT}/failed"

status() { # stage status seconds notes outputs...
  local stage="$1" state="$2" seconds="$3" notes="$4"; shift 4
  python3 -I - "${ROOT}/status/${stage}.json" "${stage}" "${state}" "${seconds}" "${notes}" "$@" <<'PY'
import json, os, sys
path, stage, state, seconds, notes, *outputs = sys.argv[1:]
tmp = path + '.tmp'
with open(tmp, 'w') as stream:
    json.dump({'stage': stage, 'status': state, 'outputs': outputs,
               'seconds': int(seconds), 'notes': notes}, stream, indent=1)
    stream.write('\n')
os.replace(tmp, path)
PY
}

status_of() { # stage -> ok|failed|blocked|missing
  python3 -I -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("status","missing"))
except Exception: print("missing")' "${ROOT}/status/$1.json"
}

wait_for() { # stage...; returns 0 when all ok, 1 when one failed or timed out
  local deadline=$((SECONDS + wait_limit)) stage state pending
  while :; do
    pending=""
    for stage in "$@"; do
      state="$(status_of "${stage}")"
      case "${state}" in
        ok) ;;
        failed|blocked) echo "lane1: prerequisite ${stage} reports ${state}" >&2; return 1;;
        *) pending+=" ${stage}";;
      esac
    done
    [[ -z "${pending}" ]] && return 0
    if ((SECONDS >= deadline)); then echo "lane1: timed out waiting for${pending}" >&2; return 1; fi
    sleep 60
  done
}

set_aside() { # name: move an incomplete earlier output out of the way
  local name="$1" n=1
  [[ -e "${P}/${name}" ]] || return 0
  while [[ -e "${ROOT}/failed/${name}-${n}" ]]; do n=$((n + 1)); done
  mv "${P}/${name}" "${ROOT}/failed/${name}-${n}"
  [[ -f "${ROOT}/logs/${name}.log" ]] && mv "${ROOT}/logs/${name}.log" "${ROOT}/failed/${name}-${n}.log"
  echo "lane1: moved earlier incomplete ${name} to ${ROOT}/failed/${name}-${n}" >&2
}

already_ok() { # name marker
  [[ -f "${P}/$1/$2" && "$(status_of "$1")" == ok ]]
}

# Input-keyed provider cache (../provider_cache.py, ../providers.json). A step
# first asks the cache for a verified copy of its output and builds only when
# there is none; a fresh ok output is offered to the cache. Neither can fail a
# step, and TAKX_PROTO_CACHE=off skips both.
cache="${here}/../provider_cache.py"

cache_restored() { # name marker: 0 when the cache installed a verified P/<name>
  [[ "${TAKX_PROTO_CACHE:-}" != off ]] || return 1
  python3 -I "${cache}" restore "$1" >>"${ROOT}/logs/provider-cache.log" 2>&1 || return 1
  already_ok "$1" "$2" && return 0
  set_aside "$1"; return 1
}

cache_push() { # name: best effort
  [[ "${TAKX_PROTO_CACHE:-}" != off ]] || return 0
  python3 -I "${cache}" push "$1" >>"${ROOT}/logs/provider-cache.log" 2>&1 \
    || echo "lane1: $1 was not pushed to the provider cache; see ${ROOT}/logs/provider-cache.log" >&2
  return 0
}

recipe() { # name -- command...; runs in the toolchain image, log in logs/<name>.log
  local name="$1"; shift 2
  docker rm -f "takx-${name}" >/dev/null 2>&1
  "${run}" --name "takx-${name}" --log "${ROOT}/logs/${name}.log" -e PYTHONDONTWRITEBYTECODE=1 -- "$@" </dev/null
}

CHECK_PY=$(cat <<'PY'

import hashlib, json, sys
from pathlib import Path
name, root = sys.argv[1], Path(sys.argv[2])
out = root / name

def sha(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()

def need(condition, message):
    if not condition:
        sys.exit('lane1 check ' + name + ': ' + message)

need(not (out / 'failed.json').exists(), 'failed.json present')
if name == 'sqlcipher':
    data = json.loads((out / 'complete.json').read_text())
    need(data['encryption_controls'] is True and data['whole_archive_PIC'] is True, 'controls missing')
    for item, entry in data['probe_outputs'].items():
        need(sha(out / item) == entry['sha256'], 'probe changed: ' + item)
    for rel in ('openssl-install/lib/libcrypto.a', 'openssl-install/lib/libssl.a',
                'sqlcipher-install/lib/libsqlite3.a', 'sqlcipher-install/include/sqlite3.h'):
        need((out / rel).is_file(), 'missing ' + rel)
    print('sqlcipher ok: %d source files, %.0f s' % (data['source_files'], data['elapsed_seconds']))
elif name == 'cesium-acq':
    data = json.loads((out / 'acquired.json').read_text())
    need(len(data['sources']) == 25, 'expected 25 acquired sources, got %d' % len(data['sources']))
    print('cesium-acq ok: 25 sources, budget %r' % (data['budget'],))
elif name == 'cesium':
    data = json.loads((out / 'complete.json').read_text())
    seen = json.loads((out / 'observed-products.json').read_text())
    need(data['full_cesium_build_acceptance'] is True, 'no acceptance flag')
    need(data['fresh_dependency_build'] is True and data['fresh_cesium_build'] is True and data['lineage'] is None,
         'not a fresh single-pass build')
    need(len(data['actual_targets']) == 18 and len(seen['actual_cesium_archives']) == 18, 'not 18 archives')
    need(seen['complete_sha256'] == sha(out / 'complete.json'), 'complete.json changed')
    for rel, entry in seen['actual_cesium_archives'].items():
        need(sha(out / rel) == entry['sha256'], 'archive changed: ' + rel)
    need((out / 'install/lib/liburiparser.a').is_file(), 'uriparser missing')
    print('cesium ok: 18 archives, %d installed files' % len(data['installed']))
elif name == 'kml':
    data = json.loads((out / 'complete.json').read_text())
    need(data['strict_whole_archive_pic'] is True and data['dom_kmz_uri_controls'] is True
         and data['private_static_expat_dynsym_verified'] is True, 'controls missing')
    need(sha(out / 'libkml-profile.so') == data['shared']['sha256'], 'shared probe changed')
    libs = sorted(p.name for p in (out / 'install/lib').glob('libkml*.a'))
    need(len(libs) == 6, 'expected six libkml archives, got %r' % (libs,))
    print('kml ok: ' + ' '.join(libs))
else:
    sys.exit('unknown check')
PY
)

check() { # name: receipt checks, inside the same image (tool paths and hashes are image-specific)
  # The wrapper does not attach stdin, so the program is passed as an argument.
  "${run}" -e PYTHONDONTWRITEBYTECODE=1 -- /usr/bin/python3 -I -B -c "${CHECK_PY}" "$1" "${P}"
}

finish() { # name rc started notes-when-ok outputs...
  local name="$1" rc="$2" started="$3" notes="$4"; shift 4
  local seconds=$((SECONDS - started)) verdict
  if ((rc == 0)) && verdict="$(check "${name}" 2>&1)"; then
    echo "lane1: ${verdict}"
    status "${name}" ok "${seconds}" "${notes} ${verdict}" "$@"
    return 0
  fi
  echo "lane1: ${name} FAILED (recipe exit ${rc}) ${verdict-}; log ${ROOT}/logs/${name}.log" >&2
  status "${name}" failed "${seconds}" "recipe exit ${rc}; ${verdict-}; see ${ROOT}/logs/${name}.log and ${P}/${name}" "$@"
  return 1
}

do_sqlcipher() {
  already_ok sqlcipher complete.json && { echo "lane1: sqlcipher already ok"; return 0; }
  set_aside sqlcipher
  cache_restored sqlcipher complete.json && { echo "lane1: sqlcipher restored from the provider cache"; return 0; }
  local t=${SECONDS}
  recipe sqlcipher -- /usr/bin/python3 -B "${repo}/takx/native-sqlcipher/build.py" \
    --cache "${ROOT}/cache" --output "${P}/sqlcipher" --jobs 1
  finish sqlcipher $? "${t}" "native-sqlcipher/build.py --jobs 1 (Tcl 8.6.18, OpenSSL 3.5.9, SQLCipher 4.5.7)." \
    "${P}/sqlcipher" && cache_push sqlcipher
}

do_cesium_acq() {
  already_ok cesium-acq acquired.json && { echo "lane1: cesium-acq already ok"; return 0; }
  [[ "$(status_of sqlcipher)" == ok ]] || { status cesium-acq blocked 0 "sqlcipher is not ok"; return 1; }
  wait_for draco || { status cesium-acq blocked 0 "prerequisite draco (lane 3) is not ok"; return 1; }
  set_aside cesium-acq
  cache_restored cesium-acq acquired.json && { echo "lane1: cesium-acq restored from the provider cache"; return 0; }
  local t=${SECONDS}
  # The cache holds the 25 pinned archives placed by fetch.sh; acquire.py checks
  # each against its SHA-512 pin (Cesium against its Git blob tree) and would
  # only download an archive that is absent.
  recipe cesium-acq -- /usr/bin/python3 -B "${repo}/takx/native-cesium/acquire.py" \
    --output "${P}/cesium-acq" --cache "${ROOT}/cache-cesium" \
    --sqlcipher-proof "${P}/sqlcipher" --draco-proof "${P}/draco"
  finish cesium-acq $? "${t}" "native-cesium/acquire.py over the pre-verified cache-cesium (offline)." "${P}/cesium-acq" \
    && cache_push cesium-acq
}

do_cesium() {
  already_ok cesium complete.json && { echo "lane1: cesium already ok"; return 0; }
  [[ "$(status_of cesium-acq)" == ok ]] || { status cesium blocked 0 "cesium-acq is not ok"; return 1; }
  set_aside cesium
  cache_restored cesium complete.json && { echo "lane1: cesium restored from the provider cache"; return 0; }
  local t=${SECONDS}
  recipe cesium -- /usr/bin/python3 -B "${repo}/takx/native-cesium/build.py" \
    --sources "${P}/cesium-acq" --output "${P}/cesium" \
    --sqlcipher-proof "${P}/sqlcipher" --draco-proof "${P}/draco"
  finish cesium $? "${t}" "native-cesium/build.py, fresh single pass, no --resume." "${P}/cesium" "${P}/cesium-acq" \
    && cache_push cesium
}

do_kml() {
  already_ok kml complete.json && { echo "lane1: kml already ok"; return 0; }
  [[ "$(status_of cesium)" == ok ]] || { status kml blocked 0 "cesium is not ok"; return 1; }
  wait_for las assimp scene || { status kml blocked 0 "prerequisite las/assimp/scene (other lanes) is not ok"; return 1; }
  set_aside kml
  cache_restored kml complete.json && { echo "lane1: kml restored from the provider cache"; return 0; }
  local t=${SECONDS}
  recipe kml -- /usr/bin/python3 -B "${repo}/takx/native-kml/build.py" \
    --output "${P}/kml" \
    --libkml-archive "${ROOT}/src/atak-civ-lfs/takthirdparty/distfiles/libkml.tar.gz" \
    --expat-archive "${ROOT}/inputs/native-kml/expat-2.10.0.tar.xz" \
    --las-proof "${P}/las" --assimp-proof "${P}/assimp" \
    --minizip-root "${P}/scene" --minizip-proof "${P}/scene" \
    --cesium-proof "${P}/cesium"
  finish kml $? "${t}" "native-kml/build.py with a fresh Cesium producer (no stopped snapshot)." "${P}/kml" \
    && cache_push kml
}

(($#)) || set -- all
# One driver at a time: two concurrent runs would set aside each other's outputs.
exec 9>"${ROOT}/status/.lane1.lock"
flock -n 9 || { echo "lane1.sh: another lane1.sh run holds ${ROOT}/status/.lane1.lock" >&2; exit 3; }
rc=0
for step in "$@"; do
  case "${step}" in
    all) do_sqlcipher && do_cesium_acq && do_cesium; do_kml || rc=1;;
    sqlcipher) do_sqlcipher || rc=1;;
    cesium-acq) do_cesium_acq || rc=1;;
    cesium) do_cesium || rc=1;;
    kml) do_kml || rc=1;;
    verify-*) # re-run the receipt checks of a finished provider and rewrite its status
      name="${step#verify-}"
      [[ "$(status_of "${name}")" == ok ]] || { echo "lane1.sh: ${name} has no ok status to re-verify" >&2; exit 2; }
      finish "${name}" 0 "${SECONDS}" "Re-verified." "${P}/${name}" || rc=1;;
    *) echo "lane1.sh: unknown step '${step}'" >&2; exit 2;;
  esac
done
exit "${rc}"
