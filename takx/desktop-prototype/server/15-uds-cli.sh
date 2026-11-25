#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Install the UDS CLI pinned in mise.toml beside the system one, and check the
# binary against the checksums file published with the same release. mise is
# not installed in this sandbox, so this replaces `mise install` for one tool.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"

pinned="$(sed -n 's/^"aqua:defenseunicorns\/uds-cli" = "\(.*\)"$/\1/p' "${here}/../../../mise.toml")"
if [[ "${pinned}" != "${UDS_VERSION}" ]]; then
  echo "mise.toml pins uds-cli ${pinned}, env.sh has ${UDS_VERSION}" >&2
  exit 1
fi
dest="${TOOLS}/uds-${UDS_VERSION}"
asset="uds-cli_v${UDS_VERSION}_Linux_amd64"
base="https://github.com/defenseunicorns/uds-cli/releases/download/v${UDS_VERSION}"
mkdir -p "${dest}"
if [[ ! -x "${dest}/uds" ]]; then
  curl -sfL --retry 3 -o "${dest}/${asset}" "${base}/${asset}"
  curl -sfL --retry 3 -o "${dest}/checksums.txt" "${base}/checksums.txt"
  (cd "${dest}" && grep " ${asset}\$" checksums.txt | sha256sum -c -)
  install -m 0755 "${dest}/${asset}" "${dest}/uds"
  rm -f "${dest:?}/${asset}"
fi
"${dest}/uds" version
"${dest}/uds" zarf version
write_status server-uds-cli ok "uds $("${dest}/uds" version) with zarf $("${dest}/uds" zarf version), sha256 checked against the release checksums.txt"
