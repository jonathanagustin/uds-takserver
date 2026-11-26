#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# The fetched inputs of one architecture as one registry object, so that a
# workflow run asks the public sources (Maven Central, GitHub, Debian, ...) at
# most once per architecture instead of once per job, and a later run with the
# same pins not at all. Maven Central answers a runner that repeats the whole
# fetch with HTTP 429; this is the reason the file exists.
#
#   ci/inputs.sh key ARCH TOOLCHAIN_ID   print the tag reference of the inputs
#   ci/inputs.sh save REFERENCE [ALSO]   pack the fetch outputs below the work
#                                        root, push them, print repo@sha256:...
#                                        ALSO is a second tag for the same
#                                        object (the architecture's "latest").
#   ci/inputs.sh restore REFERENCE       pull, unpack below the work root and
#                                        check; print repo@sha256:...
#                                        Exit 3: nothing readable is stored
#                                        under REFERENCE.
#   ci/inputs.sh latest ARCH             print the tag reference 'save' is given
#                                        as ALSO: the newest stored inputs of
#                                        the architecture, whatever their key
#   ci/inputs.sh seeds ARCH              print the references a fetch may start
#                                        from, one per line: 'latest', then the
#                                        tracked ones of ci/inputs-seeds.json
#   ci/inputs.sh seed REFERENCE          pull REFERENCE and unpack only its
#                                        self-verifying stores (below) into an
#                                        empty work root; print repo@sha256:...
#                                        Exit 3 as for 'restore'.
#
# A seed is not a restore. When the key has no stored object (a pin, a fetch
# script or any tracked JSON below takx/ changed), fetch.sh has to run, and on a
# hosted runner a fetch of everything is what Maven Central refuses (HTTP 429
# in the runs of 2026-11-25). 'seed' therefore puts the files of an earlier
# fetch where fetch.sh looks before it downloads: cache/ (files named by their
# SHA-256, which fetch_inputs.py recomputes and compares with the pin before it
# uses one) and jars/runtime-extras/ (each compared with the SHA-1 its
# repository publishes, and with the architecture pin where one exists).
# Nothing else is taken: the per-recipe views, the checkouts, status/fetch.json
# and logs/fetch.log are written afresh by the fetch that follows, so a file a
# lock no longer names cannot reach a recipe through a seed, and only that
# fetch decides whether the inputs are complete.
#
# The key covers what decides the fetch: the architecture, the toolchain image
# (its Git reconstructs archives), the pinned atak-civ commit, every tracked
# *.json below takx/ outside the directories of other parts of the prototype
# (all recipe locks are among them), the fetch scripts and the override patches
# of the architecture. The per-architecture client locks
# (client/inputs.lock.<arch>.json) are left out: the client stage writes them
# and the fetch does not read them. The key names where the inputs are stored;
# it is not a pin.
# Every recipe still verifies each input against its own lock when it builds.
#
# What is stored: cache, cache-cesium, cache-gis-java, cache-commoncommo-tools,
# inputs, jars and src of the work root, status/fetch.json and logs/fetch.log.
# 'save' refuses unless status/fetch.json says ok. 'restore' refuses to write
# over an existing cache/ or src/, checks that status/fetch.json says ok and
# that every digest-named file of cache/ has that digest.
#
# Environment: TAKX_PROTO_ROOT (default /var/lib/takx-proto), CI_IMAGES (the
# registry prefix, for 'key'). Uses the docker login of the caller; no other
# credential. Progress goes to stderr.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
members=(cache cache-cesium cache-gis-java cache-commoncommo-tools inputs jars src status/fetch.json logs/fetch.log)

say() { printf 'inputs.sh: %s\n' "$*" >&2; }

fetch_ok() {
  python3 -I -c 'import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get("status") == "ok" else 1)' "${root}/status/fetch.json" 2>/dev/null
}

digest_of() { # reference -> repository@sha256:... of a local image that was pulled or pushed
  local repository="${1%%[:@]*}" line
  while read -r line; do
    if [[ "${line}" == "${repository}@sha256:"* ]]; then echo "${line}"; return 0; fi
  done < <(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1")
  say "no registry digest recorded for $1"; return 1
}

# pull_stored REFERENCE -> exit 3 (after a message) when nothing readable is stored there
pull_stored() {
  local pulled
  if ! pulled="$(docker pull -q "$1" 2>&1)"; then
    case "${pulled}" in
      *"manifest unknown"*|*"not found"*|*"name unknown"*|*denied*)
        say "nothing readable is stored under $1: ${pulled##*: }"; return 3;;
      *) ;;  # any other pull error is reported just below
    esac
    say "pull of $1 failed: ${pulled}"; return 1
  fi
}

# unpack_stored REFERENCE [tar member patterns...] -> the stored tree, or the named parts of it, below the work root
unpack_stored() {
  local reference="$1" container status=0
  shift
  mkdir -p "${root}"
  # The image has no command; the name is never run, the container only exists to be exported.
  container="$(docker create "${reference}" /nothing)"
  if (($#)); then
    docker export "${container}" | tar -C "${root}" -xf - --strip-components=1 --wildcards "$@" || status=$?
  else
    docker export "${container}" | tar -C "${root}" -xf - --strip-components=1 takx-inputs || status=$?
  fi
  docker rm "${container}" >/dev/null
  ((status == 0)) || { say "unpacking ${reference} failed"; return 1; }
}

check_cache_digests() {
  python3 -I - "${root}/cache" >&2 <<'PY'
import hashlib, os, re, sys
cache, checked = sys.argv[1], 0
for name in sorted(os.listdir(cache)):
    path = os.path.join(cache, name)
    if not re.fullmatch(r'[0-9a-f]{64}', name) or not os.path.isfile(path):
        continue
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    if value.hexdigest() != name:
        sys.exit('inputs.sh: cache/%s does not have that digest' % name)
    checked += 1
print('inputs.sh: %d digest-named cache files have their digest' % checked)
PY
}

empty_work_root() {
  local member
  for member in cache src jars; do
    [[ ! -e "${root}/${member}" ]] || { say "${root}/${member} exists; inputs are restored into an empty work root only"; return 1; }
  done
}

latest_reference() { # ARCH
  : "${CI_IMAGES:?set CI_IMAGES to the registry prefix of the CI-built images}"
  # shellcheck source=takx/desktop-prototype/arch.sh
  . "${here}/../arch.sh"
  local arch
  arch="$(takx_arch_get arch "$1")" || return 2
  echo "${CI_IMAGES}/inputs:${arch}-latest"
}

case "${1:-}" in
  latest)
    [[ $# -eq 2 ]] || { echo "usage: inputs.sh latest ARCH" >&2; exit 2; }
    latest_reference "$2"
    ;;
  seeds)
    [[ $# -eq 2 ]] || { echo "usage: inputs.sh seeds ARCH" >&2; exit 2; }
    latest_reference "$2"
    python3 -I - "${here}/inputs-seeds.json" "$2" <<'PY'
import json, re, sys
for reference in json.load(open(sys.argv[1]))['seeds'].get(sys.argv[2], []):
    if not re.fullmatch(r'[a-z0-9./_-]+@sha256:[0-9a-f]{64}', reference):
        sys.exit('inputs.sh: inputs-seeds.json: not a digest reference: %r' % (reference,))
    print(reference)
PY
    ;;
  seed)
    [[ $# -eq 2 ]] || { echo "usage: inputs.sh seed REFERENCE" >&2; exit 2; }
    reference="$2"
    empty_work_root
    pull_stored "${reference}"
    unpack_stored "${reference}" 'takx-inputs/cache/*' 'takx-inputs/jars/runtime-extras/*'
    check_cache_digests
    say "seeded cache/ ($(find "${root}/cache" -maxdepth 1 -type f | wc -l) files) and jars/runtime-extras/ ($(find "${root}/jars/runtime-extras" -maxdepth 1 -type f | wc -l) files) from ${reference}; fetch.sh verifies each one against its pin before it is used"
    digest_of "${reference}"
    docker image rm "${reference}" >/dev/null 2>&1 || true
    ;;
  key)
    [[ $# -eq 3 && "$3" == sha256:* ]] || { echo "usage: inputs.sh key ARCH TOOLCHAIN_ID" >&2; exit 2; }
    : "${CI_IMAGES:?set CI_IMAGES to the registry prefix of the CI-built images}"
    # shellcheck source=takx/desktop-prototype/arch.sh
    . "${here}/../arch.sh"
    arch="$(takx_arch_get arch "$2")"
    listing="$(git -C "${repo}" ls-files -s -- upstream/atak-civ ':(glob)takx/**/*.json' \
      takx/desktop-prototype/fetch_inputs.py takx/desktop-prototype/fetch.sh takx/desktop-prototype/arch.sh \
      takx/desktop-prototype/arch_tree.py takx/desktop-prototype/run-in-toolchain.sh \
      "takx/desktop-prototype/overrides/${arch}" \
      | grep -Ev $'\ttakx/desktop-prototype/((server|image|zarf|chart|bundle|assemble|ci)/|client/inputs\\.lock\\.[a-z0-9]+\\.json$)')"
    grep -q $'\tupstream/atak-civ$' <<<"${listing}" || { say "upstream/atak-civ is not in the index of ${repo}"; exit 1; }
    key="$(printf 'inputs 1\n%s\n%s\n%s\n' "${arch}" "$3" "${listing}" | sha256sum | cut -c1-16)"
    echo "${CI_IMAGES}/inputs:${arch}-${key}"
    ;;
  save)
    [[ $# -eq 2 || $# -eq 3 ]] || { echo "usage: inputs.sh save REFERENCE [ALSO]" >&2; exit 2; }
    reference="$2"
    also="${3:-}"
    fetch_ok || { say "status/fetch.json is not ok; nothing is saved"; exit 1; }
    present=()
    for member in "${members[@]}"; do
      if [[ -e "${root}/${member}" ]]; then present+=("${member}"); fi
    done
    say "packing ${present[*]} ($(du -sch "${present[@]/#/${root}/}" 2>/dev/null | tail -n 1 | cut -f1))"
    # Everything goes below one directory, so that 'restore' takes exactly that
    # directory and none of the files docker adds to an exported container.
    # 'S': symbolic link targets are stored as they are.
    tar -C "${root}" -cf - --transform='s,^,takx-inputs/,S' "${present[@]}" | docker import - "${reference}" >/dev/null
    docker push -q "${reference}" >&2
    if [[ -n "${also}" ]]; then
      # A second name for the same object. It is a convenience for the next
      # fetch (inputs.sh seed); failing to move it does not undo the save.
      if docker tag "${reference}" "${also}" && docker push -q "${also}" >&2; then
        say "${also} now names the same object"
      else
        say "warning: ${also} could not be moved; the inputs are stored under ${reference}"
      fi
      docker image rm "${also}" >/dev/null 2>&1 || true
    fi
    digest_of "${reference}"
    docker image rm "${reference}" >/dev/null 2>&1 || true
    ;;
  restore)
    [[ $# -eq 2 ]] || { echo "usage: inputs.sh restore REFERENCE" >&2; exit 2; }
    reference="$2"
    empty_work_root
    pull_stored "${reference}"
    unpack_stored "${reference}"
    fetch_ok || { say "${reference} holds no status/fetch.json that says ok"; exit 1; }
    check_cache_digests
    digest_of "${reference}"
    docker image rm "${reference}" >/dev/null 2>&1 || true
    ;;
  *)
    echo "usage: inputs.sh key ARCH TOOLCHAIN_ID | save REFERENCE [ALSO] | restore REFERENCE | latest ARCH | seeds ARCH | seed REFERENCE" >&2
    exit 2;;
esac
