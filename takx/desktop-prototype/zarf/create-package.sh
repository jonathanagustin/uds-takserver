#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Build the takx-desktop Zarf package from a staged copy of this directory
# and ../chart (the pattern of atak/ci/create-package.sh). The sources are
# only read.
#
#   create-package.sh TAG [zarf package create flags]
#   create-package.sh TAG --stage-only DIR
#
# TAG is the tag of the image
#   ghcr.io/jonathanagustin/uds-takserver/takx-desktop:TAG
# and becomes the package version. ../stages/image.sh builds that image into
# the local Docker image store; it is in no registry.
#
# What staging does:
#   - replaces the placeholder `takx-build-tag` with TAG in zarf.yaml, and
#     with TAG@sha256:DIGEST in components/image.yaml and the chart's default
#     image: the package pins its image by digest. With `daemon` the digest
#     is read from the local image store; with `registry` TAKX_IMAGE_DIGEST
#     must name it;
#   - sets the image source: `daemon` (default, the local Docker image store)
#     or `registry` (TAKX_IMAGE_SOURCE);
#   - writes the attachments NOTICE.md, SOURCES.json, LICENCE-GATE.json (the
#     licence gate's report: what it still flags), LICENSES.tar,
#     PATCHES.tar and DEBIAN-PACKAGES.tsv. They are copied out of the image
#     itself (/usr/share/takx-desktop, no container is started), so the
#     package cannot carry notices that differ from the ones in its image.
#     TAKX_NOTICES_DIR names a directory to take them from instead, for an
#     image that is not in the local store.
#
# It refuses to go on when the three places that name the assembled tree
# disagree: the image label, SOURCES.json, and the digest at the end of TAG.
#
# --stage-only DIR writes the staged tree to DIR/zarf and DIR/chart and stops,
# for `zarf dev lint` and `zarf dev inspect manifests`. DIR must not exist.
#
# Environment: TAKX_IMAGE_SOURCE (daemon | registry), TAKX_NOTICES_DIR,
# UDS (the uds binary, default `uds`; mise.toml pins 0.42.0).
set -euo pipefail

image_repo=ghcr.io/jonathanagustin/uds-takserver/takx-desktop
tag="${1:?Usage: create-package.sh TAG [zarf package create flags] | TAG --stage-only DIR}"
shift
source="${TAKX_IMAGE_SOURCE:-daemon}"
uds="${UDS:-uds}"
if [[ ! "${tag}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "Invalid TAKX desktop image tag: ${tag}" >&2
  exit 2
fi
if [[ "${source}" != registry && "${source}" != daemon ]]; then
  echo "TAKX_IMAGE_SOURCE must be daemon or registry" >&2
  exit 2
fi
stage_only=""
if [[ "${1:-}" == --stage-only ]]; then
  stage_only="${2:?--stage-only needs a directory}"
  if [[ -e "${stage_only}" ]]; then
    echo "${stage_only} exists; --stage-only writes a new directory" >&2
    exit 2
  fi
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
proto="$(cd "${here}/.." && pwd)"
work="$(mktemp -d)"
container=""
cleanup() {
  if [[ -n "${container}" ]]; then docker rm "${container}" >/dev/null 2>&1 || true; fi
  rm -rf -- "${work}"
}
trap cleanup EXIT

# The notices: from TAKX_NOTICES_DIR, or out of the image.
notices="${TAKX_NOTICES_DIR:-}"
label=""
if [[ -z "${notices}" ]]; then
  if ! docker image inspect "${image_repo}:${tag}" >/dev/null 2>&1; then
    echo "No image ${image_repo}:${tag} in the local Docker image store." >&2
    echo "Build it with takx/desktop-prototype/stages/image.sh, or set TAKX_NOTICES_DIR." >&2
    exit 1
  fi
  label="$(docker image inspect "${image_repo}:${tag}" \
    --format '{{ index .Config.Labels "io.github.jonathanagustin.uds-takserver.takx.tree-manifest-sha256" }}')"
  container="$(docker create "${image_repo}:${tag}")"
  notices="${work}/notices"
  mkdir "${notices}"
  docker cp "${container}:/usr/share/takx-desktop/." "${notices}/" >/dev/null
  docker rm "${container}" >/dev/null
  container=""
fi
for item in NOTICE.md SOURCES.json licence-gate.json LICENSES patches locks; do
  if [[ ! -e "${notices}/${item}" ]]; then
    echo "The notices in ${TAKX_NOTICES_DIR:-the image} lack ${item}" >&2
    exit 1
  fi
done
# The package pins the image by digest, not by tag alone: a rebuild under the
# same tag (the tag only names the tree) cannot slip into a package unseen.
# With `daemon` the digest is the one the local image store has for the tag;
# with `registry` the caller names it.
digest="${TAKX_IMAGE_DIGEST:-}"
if [[ -z "${digest}" && "${source}" == daemon ]]; then
  digest="$(docker image inspect "${image_repo}:${tag}" --format '{{ .Descriptor.Digest }}' 2>/dev/null || true)"
  if [[ ! "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    digest="$(docker image inspect "${image_repo}:${tag}" --format '{{ .Id }}')"
  fi
fi
if [[ ! "${digest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "No image digest: set TAKX_IMAGE_DIGEST=sha256:... (required with TAKX_IMAGE_SOURCE=registry)" >&2
  exit 1
fi
recorded="$(python3 -I -c 'import json, sys
sources = json.load(open(sys.argv[1], encoding="utf-8"))
print(sources["tree"]["manifest_sha256"])' "${notices}/SOURCES.json")"
if [[ ! "${recorded}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "SOURCES.json has no tree manifest digest" >&2
  exit 1
fi
if [[ -n "${label}" && "${label}" != "${recorded}" ]]; then
  echo "Image label names tree ${label}, SOURCES.json in the image names ${recorded}" >&2
  exit 1
fi
if [[ "${tag}" != *"-${recorded:0:12}" ]]; then
  echo "Tag ${tag} does not end in -${recorded:0:12}, the tree digest SOURCES.json records" >&2
  exit 1
fi

stage="${work}/stage"
mkdir -p "${stage}/zarf"
cp -R "${here}/components" "${here}/manifests" "${stage}/zarf/"
cp "${here}/values.yaml" "${here}/values.schema.json" "${here}/image-values.yaml" "${here}/README.md" "${stage}/zarf/"
cp -R "${proto}/chart" "${stage}/chart"
sed -e "s/takx-build-tag/${tag}/g" "${here}/zarf.yaml" >"${stage}/zarf/zarf.yaml"
sed -e "s/takx-build-tag/${tag}@${digest}/g" -e "s/source: registry/source: ${source}/g" \
  "${here}/components/image.yaml" >"${stage}/zarf/components/image.yaml"
sed -e "s/takx-build-tag/${tag}@${digest}/g" "${proto}/chart/values.yaml" >"${stage}/chart/values.yaml"
if grep -rl takx-build-tag "${stage}" >/dev/null; then
  echo "The placeholder takx-build-tag is left in the staged copy:" >&2
  grep -rl takx-build-tag "${stage}" >&2
  exit 1
fi

cp "${notices}/NOTICE.md" "${notices}/SOURCES.json" "${stage}/zarf/"
cp "${notices}/licence-gate.json" "${stage}/zarf/LICENCE-GATE.json"
if [[ -s "${notices}/debian-packages.tsv" ]]; then
  cp "${notices}/debian-packages.tsv" "${stage}/zarf/DEBIAN-PACKAGES.tsv"
else
  # A notices directory outside the image has no package list; the image has.
  printf '# See /usr/share/takx-desktop/debian-packages.tsv in the image.\n' >"${stage}/zarf/DEBIAN-PACKAGES.tsv"
fi
# Same bytes for the same input: sorted names, no owner, one fixed time.
archive() { # output directory...
  local output="$1"
  shift
  tar --create --file "${output}" --directory "${notices}" --sort=name --owner=0 --group=0 --numeric-owner \
    --mtime='2026-01-01 00:00:00Z' --mode='u+rwX,go+rX,go-w' "$@"
}
archive "${stage}/zarf/LICENSES.tar" LICENSES
archive "${stage}/zarf/PATCHES.tar" patches locks

if [[ -n "${stage_only}" ]]; then
  mkdir -p "$(dirname "${stage_only}")"
  cp -R "${stage}" "${stage_only}"
  echo "Staged ${stage_only}/zarf (image ${image_repo}:${tag}@${digest}, source ${source}, tree ${recorded})"
  exit 0
fi
"${uds}" zarf package create "${stage}/zarf" "$@"
