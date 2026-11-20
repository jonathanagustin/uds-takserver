#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Save finished provider outputs to a container registry so an interrupted
# build can resume elsewhere. Each provider directory whose status file says
# "ok" becomes one single-layer image holding <provider>.tar plus its status
# file. The tag is the SHA-256 of the provider's status file, so a changed
# result never overwrites an earlier one. Requires a prior `docker login`.
#
# Usage: push-cache.sh [provider ...]     (default: every finished provider)
# Restore: restore-cache.sh
set -euo pipefail

ROOT="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
REGISTRY="${TAKX_PROTO_CACHE:-ghcr.io/jonathanagustin/uds-takserver/takx-desktop-proto-cache}"
INDEX="$ROOT/status/pushed-cache.tsv"

command -v docker >/dev/null || { echo "push-cache: docker not found" >&2; exit 1; }
[ -d "$ROOT/P" ] || { echo "push-cache: $ROOT/P not found" >&2; exit 1; }
touch "$INDEX"

if [ "$#" -gt 0 ]; then
  providers=("$@")
else
  mapfile -t providers < <(find "$ROOT/P" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
fi

for name in "${providers[@]}"; do
  status="$ROOT/status/$name.json"
  dir="$ROOT/P/$name"
  if [ ! -f "$status" ] || [ ! -d "$dir" ]; then
    echo "push-cache: skip $name (no status file or directory)"
    continue
  fi
  if ! python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("status") == "ok" else 1)' "$status"; then
    echo "push-cache: skip $name (status is not ok)"
    continue
  fi
  key="$(sha256sum "$status" | cut -c1-64)"
  ref="$REGISTRY/$name:$key"
  if grep -q -F "$ref" "$INDEX"; then
    echo "push-cache: $name already pushed as $ref"
    continue
  fi
  ctx="$(mktemp -d "$ROOT/.push-$name.XXXXXX")"
  trap 'rm -rf "$ctx"' EXIT
  tar -C "$ROOT/P" -cf "$ctx/$name.tar" "$name"
  cp "$status" "$ctx/status.json"
  sha256sum "$ctx/$name.tar" | cut -c1-64 > "$ctx/tar.sha256"
  printf 'FROM scratch\nCOPY %s.tar status.json tar.sha256 /\n' "$name" > "$ctx/Dockerfile"
  docker build -q \
    --label "org.opencontainers.image.source=https://github.com/jonathanagustin/uds-takserver" \
    --label "org.opencontainers.image.licenses=GPL-3.0-only" \
    --label "dev.uds.takx.provider=$name" \
    -t "$ref" "$ctx" >/dev/null
  docker push -q "$ref"
  docker rmi "$ref" >/dev/null
  printf '%s\t%s\t%s\n' "$name" "$ref" "$(cat "$ctx/tar.sha256")" >> "$INDEX"
  rm -rf "$ctx"
  trap - EXIT
  echo "push-cache: pushed $ref"
done
