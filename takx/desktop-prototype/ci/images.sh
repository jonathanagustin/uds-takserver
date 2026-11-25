#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Toolchain job of the opt-in workflow: make the toolchain and runtime images
# of one architecture available in the registry, once, and print how every
# later job finds them.
#
#   ci/images.sh ARCH      ARCH is amd64 or arm64 and must be the host's
#
# Output (stdout, one KEY=VALUE per line; progress goes to stderr):
#   TOOLCHAIN_IMAGE  repository@sha256:...   pulled by digest by every job
#   RUNTIME_IMAGE    repository@sha256:...
#   TOOLCHAIN_ID     the identity the provider cache keys use
#                    (TAKX_PROTO_TOOLCHAIN_ID)
#   IMAGE_SOURCE     reference | ci-reused | ci-built
#
# Order of preference:
#   1. reference: the images named in reference-images.json for ARCH, when both
#      can be pulled. Existing provider cache keys were derived from them, so
#      the providers restore instead of rebuilding (the amd64 control).
#   2. ci-reused: <CI_IMAGES>/toolchain:<arch>-<key> and runtime:<arch>-<key>
#      already pushed by an earlier run; <key> is the SHA-256 of ../Dockerfile
#      (first 16 digits), so a changed Dockerfile gets new images.
#   3. ci-built: built natively from ../Dockerfile and pushed under those tags.
#
# Environment:
#   CI_IMAGES                 registry prefix of the CI-built images (required)
#   TAKX_CI_REFERENCE_IMAGES  'off' skips step 1
# Needs a docker login that may push to CI_IMAGES; no other credential.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
proto="$(cd "${here}/.." && pwd)"
# shellcheck source=takx/desktop-prototype/arch.sh
. "${proto}/arch.sh"

say() { printf 'images.sh: %s\n' "$*" >&2; }

[[ $# -eq 1 ]] || { echo "usage: images.sh ARCH" >&2; exit 2; }
arch="$(takx_arch_get arch "$1")"
host="$(TAKX_PROTO_ARCH='' takx_arch)"
[[ "${arch}" == "${host}" ]] || { say "this runner is ${host}; ${arch} images are built natively only"; exit 2; }
: "${CI_IMAGES:?set CI_IMAGES to the registry prefix of the CI-built images}"

digest_of() { # reference -> repository@sha256:... of a local image that was pulled or pushed
  local reference="$1" repository="${1%%[:@]*}" line
  while read -r line; do
    if [[ "${line}" == "${repository}@sha256:"* ]]; then echo "${line}"; return 0; fi
  done < <(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "${reference}")
  say "no registry digest recorded for ${reference}"; return 1
}

emit() { # toolchain runtime id source
  printf 'TOOLCHAIN_IMAGE=%s\nRUNTIME_IMAGE=%s\nTOOLCHAIN_ID=%s\nIMAGE_SOURCE=%s\n' "$1" "$2" "$3" "$4"
}

# 1. Reference images.
if [[ "${TAKX_CI_REFERENCE_IMAGES:-}" != off ]]; then
  reference="$(python3 -I -c '
import json, sys
entry = json.load(open(sys.argv[1]))["images"].get(sys.argv[2])
if entry:
    print(entry["toolchain"], entry["runtime"], entry["toolchain_id"])
' "${here}/reference-images.json" "${arch}")"
  if [[ -n "${reference}" ]]; then
    read -r toolchain runtime identity <<<"${reference}"
    if docker pull -q "${toolchain}" >&2 && docker pull -q "${runtime}" >&2; then
      [[ "${toolchain##*@}" == "${identity}" ]] || { say "reference-images.json is inconsistent for ${arch}"; exit 1; }
      say "using the reference ${arch} images"
      emit "${toolchain}" "${runtime}" "${identity}" reference
      exit 0
    fi
    say "the reference ${arch} images cannot be pulled with this login; building instead (provider keys will be new)"
  fi
fi

# 2. Images an earlier run pushed for this Dockerfile.
key="$(sha256sum "${proto}/Dockerfile" | cut -c1-16)"
toolchain_tag="${CI_IMAGES}/toolchain:${arch}-${key}"
runtime_tag="${CI_IMAGES}/runtime:${arch}-${key}"
if docker pull -q "${toolchain_tag}" >&2 && docker pull -q "${runtime_tag}" >&2; then
  toolchain="$(digest_of "${toolchain_tag}")"
  runtime="$(digest_of "${runtime_tag}")"
  say "reusing ${toolchain_tag}"
  emit "${toolchain}" "${runtime}" "${toolchain##*@}" ci-reused
  exit 0
fi

# 3. Build natively and push. The Dockerfile asserts every gated tool version.
say "building ${toolchain_tag} and ${runtime_tag}"
DOCKER_BUILDKIT=1 docker build --target toolchain -t "${toolchain_tag}" "${proto}" >&2
DOCKER_BUILDKIT=1 docker build --target runtime -t "${runtime_tag}" "${proto}" >&2
docker push -q "${toolchain_tag}" >&2
docker push -q "${runtime_tag}" >&2
toolchain="$(digest_of "${toolchain_tag}")"
runtime="$(digest_of "${runtime_tag}")"
emit "${toolchain}" "${runtime}" "${toolchain##*@}" ci-built
