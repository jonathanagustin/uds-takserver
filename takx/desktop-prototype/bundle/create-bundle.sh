#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Build the takx-desktop-proto bundle from a staged copy of uds-bundle.yaml.
# The sources are only read.
#
#   create-bundle.sh PACKAGE_DIR TAG [--packages NAME[,NAME...]] [uds create flags]
#   create-bundle.sh PACKAGE_DIR TAG [--packages NAME[,NAME...]] --stage-only DIR
#
# PACKAGE_DIR holds zarf-package-takx-desktop-amd64-TAG.tar.zst, as
# ../zarf/create-package.sh TAG writes it. TAG becomes the bundle version.
#
# --packages keeps only the named packages of uds-bundle.yaml, in their
# order, and names the bundle takx-desktop-proto-partial. With
# `--packages takx-desktop` nothing is pulled from a registry: that bundle is
# for a cluster that already runs UDS Core and the TAK Server package.
#
# Without --packages the four upstream packages are pulled from GHCR, about
# 2.7 GB, and uds needs roughly three times the bundle size in free disk
# while it works (layer cache, staging directory, archive).
#
# --stage-only DIR writes the staged uds-bundle.yaml and uds-config.yaml to
# DIR and stops. DIR must not exist.
#
# Environment: UDS (the uds binary, default `uds`; mise.toml pins 0.42.0).
set -euo pipefail

usage="Usage: create-bundle.sh PACKAGE_DIR TAG [--packages NAMES] [uds create flags | --stage-only DIR]"
package_dir="${1:?${usage}}"
tag="${2:?${usage}}"
shift 2
uds="${UDS:-uds}"
if [[ ! "${tag}" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "Invalid TAKX desktop package version: ${tag}" >&2
  exit 2
fi
keep=""
if [[ "${1:-}" == --packages ]]; then
  keep="${2:?--packages needs a comma-separated list of package names}"
  shift 2
  if [[ ! "${keep}" =~ ^[a-z0-9-]+(,[a-z0-9-]+)*$ ]]; then
    echo "--packages takes names separated by commas, got '${keep}'" >&2
    exit 2
  fi
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
repo="$(cd "${here}/../../.." && pwd)"
package_dir="$(cd "${package_dir}" && pwd)"
# Neither path may hold a character that sed would read as syntax.
for path in "${package_dir}" "${repo}"; do
  if [[ ! "${path}" =~ ^[A-Za-z0-9_./+-]+$ ]]; then
    echo "Path with unsupported characters: ${path}" >&2
    exit 2
  fi
done
wanted=",${keep:-init,core-base,core-identity-authorization,takserver,takx-desktop},"
if [[ "${wanted}" == *,takx-desktop,* && ! -s "${package_dir}/zarf-package-takx-desktop-amd64-${tag}.tar.zst" ]]; then
  echo "No zarf-package-takx-desktop-amd64-${tag}.tar.zst in ${package_dir}; run ../zarf/create-package.sh ${tag} first" >&2
  exit 1
fi

stage="$(mktemp -d)"
trap 'rm -rf -- "${stage}"' EXIT
sed -e "s|path: takx-package-dir|path: ${package_dir}|" \
  -e "s|takx-build-tag|${tag}|g" \
  -e "s|- \.\./\.\./\.\./bundle/test-values\.yaml|- ${repo}/bundle/test-values.yaml|" \
  "${here}/uds-bundle.yaml" >"${stage}/uds-bundle.yaml"
cp "${here}/uds-config.yaml" "${stage}/uds-config.yaml"
if [[ -n "${keep}" ]]; then
  # $keep and $name below are yq variables, not shell ones.
  # shellcheck disable=SC2016
  KEEP="${keep}" "${uds}" zarf tools yq --inplace \
    '(strenv(KEEP) | split(",")) as $keep
     | .packages |= map(select(.name as $name | $keep | contains([$name])))
     | .metadata.name = "takx-desktop-proto-partial"
     | .metadata.description = "Packages " + strenv(KEEP) + " of the takx-desktop-proto bundle"' \
    "${stage}/uds-bundle.yaml"
  count="$("${uds}" zarf tools yq '.packages | length' "${stage}/uds-bundle.yaml")"
  IFS=, read -r -a names <<<"${keep}"
  if [[ "${count}" != "${#names[@]}" ]]; then
    echo "--packages ${keep}: ${count} of ${#names[@]} names are packages of uds-bundle.yaml" >&2
    exit 2
  fi
fi
# Comments may still name the placeholders; the definition may not.
if grep -v '^[[:space:]]*#' "${stage}/uds-bundle.yaml" |
  grep -q -e takx-package-dir -e takx-build-tag -e '\.\./\.\./\.\./bundle'; then
  echo "A placeholder is left in the staged uds-bundle.yaml" >&2
  exit 1
fi

if [[ -n "${stage_only}" ]]; then
  mkdir -p "$(dirname "${stage_only}")"
  cp -R "${stage}" "${stage_only}"
  echo "Staged ${stage_only}/uds-bundle.yaml"
  exit 0
fi
"${uds}" create "${stage}" "$@"
