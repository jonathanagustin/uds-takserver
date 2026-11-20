#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Restore provider outputs saved by push-cache.sh. Reads a tab-separated index
# (provider, image reference, tar SHA-256), pulls each image, checks the tar
# digest and unpacks it under $TAKX_PROTO_ROOT/P. An existing provider
# directory is never overwritten. The work root must be the same absolute
# path the outputs were built under, because receipts embed absolute paths.
#
# Usage: restore-cache.sh INDEX.tsv [provider ...]
set -euo pipefail

ROOT="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
[ "$#" -ge 1 ] || { echo "usage: restore-cache.sh INDEX.tsv [provider ...]" >&2; exit 2; }
INDEX="$1"; shift
[ -f "$INDEX" ] || { echo "restore-cache: index $INDEX not found" >&2; exit 1; }
command -v docker >/dev/null || { echo "restore-cache: docker not found" >&2; exit 1; }
mkdir -p "$ROOT/P" "$ROOT/status"

while IFS=$'\t' read -r name ref digest; do
  [ -n "$name" ] || continue
  if [ "$#" -gt 0 ]; then
    wanted=0
    for want in "$@"; do [ "$want" = "$name" ] && wanted=1; done
    [ "$wanted" -eq 1 ] || continue
  fi
  if [ -e "$ROOT/P/$name" ]; then
    echo "restore-cache: skip $name (already present)"
    continue
  fi
  tmp="$(mktemp -d "$ROOT/.restore-$name.XXXXXX")"
  docker pull -q "$ref" >/dev/null
  cid="$(docker create "$ref" /nonexistent)"
  docker cp "$cid:/$name.tar" "$tmp/$name.tar"
  docker cp "$cid:/status.json" "$tmp/status.json"
  docker rm "$cid" >/dev/null
  actual="$(sha256sum "$tmp/$name.tar" | cut -c1-64)"
  if [ "$actual" != "$digest" ]; then
    rm -rf "$tmp"
    echo "restore-cache: $name tar digest mismatch (expected $digest, got $actual)" >&2
    exit 1
  fi
  mkdir "$tmp/out"
  tar -C "$tmp/out" -xf "$tmp/$name.tar"
  [ -d "$tmp/out/$name" ] || { rm -rf "$tmp"; echo "restore-cache: $name archive has no top-level $name directory" >&2; exit 1; }
  mv "$tmp/out/$name" "$ROOT/P/$name"
  cp "$tmp/status.json" "$ROOT/status/$name.json"
  rm -rf "$tmp"
  echo "restore-cache: restored $name from $ref"
done < "$INDEX"
