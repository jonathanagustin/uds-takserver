#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Build the self-contained TAKX 5.7 desktop prototype image (image/Dockerfile)
# with docker buildx. The image is tagged locally and never pushed.
#
# Usage: build.sh
#
# Inputs:
#   TAKX_IMAGE_TREE     a copy of the assembled tree, default
#                       $TAKX_PROTO_ROOT/pkg/context/takx57 (stages/image.sh
#                       makes that copy; the original is never a build input)
#   TAKX_IMAGE_NOTICES  output of gen_notices.py, default
#                       $TAKX_PROTO_ROOT/pkg/notices
#   TAKX_PROTO_CA       CA bundle for a TLS-intercepting proxy; default
#                       /root/.ccr/ca-bundle.crt when that file exists. It is a
#                       BuildKit secret: mounted for the JDK download, never
#                       written to a layer
#
# Tags, with <digest12> the first 12 hex digits of the SHA-256 of the tree's
# manifest.json:
#   takx-desktop:5.7-proto-<digest12>
#   ghcr.io/jonathanagustin/uds-takserver/takx-desktop:5.7-0b59a9bb-proto-<digest12>
# The second name is what the Zarf package refers to; it only exists in the
# local Docker image store.
#
# --provenance=false --sbom=false keeps the image a single manifest, which
# 'zarf package create' needs when it reads images from the Docker daemon.
#
# Output: $TAKX_PROTO_ROOT/pkg/logs/image-build-<n>.log and
#         $TAKX_PROTO_ROOT/pkg/status/image.json
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
proto="$(cd "${here}/.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
tree="${TAKX_IMAGE_TREE:-${root}/pkg/context/takx57}"
notices="${TAKX_IMAGE_NOTICES:-${root}/pkg/notices}"
ca="${TAKX_PROTO_CA-}"
if [[ -z "${ca}" && -s /root/.ccr/ca-bundle.crt ]]; then ca=/root/.ccr/ca-bundle.crt; fi

[[ -s "${tree}/manifest.json" ]] || { echo "build.sh: no tree at ${tree}" >&2; exit 1; }
[[ -s "${notices}/SOURCES.json" ]] || { echo "build.sh: no notices at ${notices}; run gen_notices.py" >&2; exit 1; }
[[ -s "${notices}/licence-gate.json" ]] || { echo "build.sh: no licence gate report in ${notices}; run gen_notices.py" >&2; exit 1; }
# The licence gate decides whether an image may be built at all; the build
# runs it again on the tree inside the image (verify/build_gates.sh, gate 6).
# The label lists the identifiers in use in the tree and the JDK's.
licenses="$(python3 -I - "${notices}/licence-gate.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
if not report["build_may_proceed"]:
    sys.exit("build.sh: the licence gate found denied or unknown items; see " + sys.argv[1])
print(" AND ".join(sorted(set(report["licences_in_use"]) | {"GPL-2.0-only WITH Classpath-exception-2.0"})))
PY
)"
gate_clean="$(python3 -I -c 'import json, sys; print(str(json.load(open(sys.argv[1]))["clean"]).lower())' "${notices}/licence-gate.json")"

digest="$(sha256sum "${tree}/manifest.json" | cut -d' ' -f1)"
short="${digest:0:12}"
local_tag="takx-desktop:5.7-proto-${short}"
package_tag="ghcr.io/jonathanagustin/uds-takserver/takx-desktop:5.7-0b59a9bb-proto-${short}"

mkdir -p "${root}/pkg/logs" "${root}/pkg/status"
n=1
while [[ -e "${root}/pkg/logs/image-build-${n}.log" ]]; do n=$((n + 1)); done
log="${root}/pkg/logs/image-build-${n}.log"

secret=()
if [[ -n "${ca}" ]]; then secret=(--secret "id=ca,src=${ca}"); fi

started="$(date +%s)"
status=ok
docker buildx build --provenance=false --sbom=false --progress=plain \
  -f "${here}/Dockerfile" \
  --build-context "tree=${tree}" \
  --build-context "notices=${notices}" \
  "${secret[@]}" \
  --build-arg "TREE_MANIFEST_SHA256=${digest}" \
  --build-arg "IMAGE_LICENSES=${licenses}" \
  -t "${local_tag}" -t "${package_tag}" \
  "${proto}" >"${log}" 2>&1 || status=failed
seconds=$(($(date +%s) - started))

image_id=""; size=0
if [[ "${status}" == ok ]]; then
  image_id="$(docker image inspect -f '{{.Id}}' "${local_tag}")"
  size="$(docker image inspect -f '{{.Size}}' "${local_tag}")"
fi
python3 -I - "${root}/pkg/status/image.json" "${status}" "${seconds}" "${digest}" "${local_tag}" "${package_tag}" \
  "${image_id}" "${size}" "${log}" "${gate_clean}" "${licenses}" "${tree}" <<'PY'
import json, os, sys
path, status, seconds, digest, local_tag, package_tag, image_id, size, log, gate_clean, licenses, tree = sys.argv[1:]
tmp = path + ".tmp.%d" % os.getpid()
with open(tmp, "w") as stream:
    json.dump({"stage": "image", "status": status, "seconds": int(seconds), "tree_manifest_sha256": digest,
               "tags": [local_tag, package_tag], "image_id": image_id, "size_bytes": int(size or 0),
               "pushed": False, "log": log, "tree": tree,
               "licence_gate_clean": gate_clean == "true", "publishable": False,
               "image_licenses_label": licenses,
               "notes": "TAKX 5.7 (0b59a9bb) public-source build on Kernel 8.30.5 + project patches, CommonCommo "
                        "1.17.2 patched, NASA WorldWind interop; local image, single manifest, never pushed"},
              stream, indent=1)
    stream.write("\n")
os.replace(tmp, path)
PY
cat "${root}/pkg/status/image.json"
if [[ "${status}" != ok ]]; then
  tail -n 40 "${log}" >&2
  exit 1
fi
