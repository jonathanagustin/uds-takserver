#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail

tag="${1:?Usage: create-package.sh TAG [Zarf create flags]}"
shift
source="${ATAK_IMAGE_SOURCE:-registry}"
if [[ ! "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "Invalid ATAK image tag: $tag" >&2
  exit 2
fi
if [[ "$source" != registry && "$source" != daemon ]]; then
  echo "ATAK_IMAGE_SOURCE must be registry or daemon" >&2
  exit 2
fi
here="$(cd "$(dirname "$0")/.." && pwd)"
stage="$(mktemp -d)"
trap 'rm -rf -- "$stage"' EXIT
cp -R "$here/deploy" "$stage/"
cp "$here/values.yaml" "$stage/"
sed -e "s/atak-build-tag/$tag/g" -e "s/source: registry/source: $source/g" \
  "$here/zarf.yaml" > "$stage/zarf.yaml"
uds zarf package create "$stage" "$@"
