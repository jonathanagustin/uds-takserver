#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Architecture names of the desktop prototype, in one place. Every script and
# Python file of the prototype asks this file; none maps `uname -m` itself.
#
#   arch.sh get FIELD [ARCH]   print one name
#   arch.sh json [ARCH]        print every name as one JSON object
#   arch.sh fields             list the field names
#   . arch.sh                  define takx_arch, takx_arch_get, takx_arch_enter
#
# ARCH is amd64 or arm64. Without it the architecture is TAKX_PROTO_ARCH when
# that is set, otherwise the host (`uname -m`: x86_64 -> amd64, aarch64 or
# arm64 -> arm64). Any other value is refused.
#
# Fields:
#   arch        amd64 | arm64 (Debian, Docker TARGETARCH and Go spelling)
#   machine     `uname -m` / Python platform.machine(): x86_64 | aarch64
#   debian      dpkg --print-architecture
#   multiarch   Debian library directory below /usr/lib
#   docker      docker --platform value
#   loader      dynamic loader name
#   temurin     architecture word of the Eclipse Temurin asset name
#   jogamp      JOGL/GlueGen/JOCL natives classifier
#   bytedeco    JavaCPP presets (ffmpeg) natives classifier
#   openssl     OpenSSL/quictls Configure target
#   vcpkg       vcpkg triplet (named for completeness; the Cesium recipe uses
#               plain CMake and no triplet)
#   gnu         GNU build triplet passed to old configure scripts
#   commoncommo CommonCommo make TARGET label
#   kernel_jar  file name of the composed Kernel 8.30.5 JAR with natives
#   kernel_libs native directory inside that JAR; linux64 on both, because the
#               stock EngineLibrary loader keys on os.arch containing "64"
#   runner      GitHub-hosted runner label used by the opt-in workflow
#
# Recipes under takx/ that hard-code x86_64 are not edited. On an architecture
# with a directory overrides/<arch>/ the stage scripts continue from a patched
# copy of the tree (arch_tree.py): takx_arch_enter does that hand-over. On
# amd64 there is no such directory and the tracked tree is used as it is.

takx_arch() { # -> amd64 | arm64 on stdout; non-zero and a message otherwise
  local value="${TAKX_PROTO_ARCH:-}"
  if [[ -z "${value}" ]]; then value="$(uname -m)" || return 2; fi
  case "${value}" in
    amd64|x86_64) echo amd64;;
    arm64|aarch64) echo arm64;;
    *) echo "arch.sh: unsupported architecture '${value}' (amd64 and arm64 only)" >&2; return 2;;
  esac
}

takx_arch_fields() {
  printf '%s\n' arch machine debian multiarch docker loader temurin jogamp bytedeco openssl vcpkg gnu \
    commoncommo kernel_jar kernel_libs runner
}

takx_arch_get() { # field [arch] -> the name on stdout
  local field="$1" arch="${2:-}"
  if [[ -z "${arch}" ]]; then arch="$(takx_arch)" || return 2; fi
  case "${arch}:${field}" in
    amd64:arch) echo amd64;;                          arm64:arch) echo arm64;;
    amd64:machine) echo x86_64;;                      arm64:machine) echo aarch64;;
    amd64:debian) echo amd64;;                        arm64:debian) echo arm64;;
    amd64:multiarch) echo x86_64-linux-gnu;;          arm64:multiarch) echo aarch64-linux-gnu;;
    amd64:docker) echo linux/amd64;;                  arm64:docker) echo linux/arm64;;
    amd64:loader) echo ld-linux-x86-64.so.2;;         arm64:loader) echo ld-linux-aarch64.so.1;;
    amd64:temurin) echo x64;;                         arm64:temurin) echo aarch64;;
    amd64:jogamp) echo natives-linux-amd64;;          arm64:jogamp) echo natives-linux-aarch64;;
    amd64:bytedeco) echo linux-x86_64;;               arm64:bytedeco) echo linux-arm64;;
    amd64:openssl) echo linux-x86_64;;                arm64:openssl) echo linux-aarch64;;
    amd64:vcpkg) echo x64-linux;;                     arm64:vcpkg) echo arm64-linux;;
    amd64:gnu) echo x86_64-pc-linux-gnu;;             arm64:gnu) echo aarch64-unknown-linux-gnu;;
    amd64:commoncommo) echo linux-amd64;;             arm64:commoncommo) echo linux-arm64;;
    amd64:kernel_jar) echo kernel-8.30.5-linux-amd64-public-cpu.jar;;
    arm64:kernel_jar) echo kernel-8.30.5-linux-arm64-public-cpu.jar;;
    amd64:kernel_libs|arm64:kernel_libs) echo linux64;;
    amd64:runner) echo ubuntu-24.04;;                 arm64:runner) echo ubuntu-24.04-arm;;
    amd64:*|arm64:*) echo "arch.sh: unknown field '${field}'" >&2; return 2;;
    *) echo "arch.sh: unsupported architecture '${arch}' (amd64 and arm64 only)" >&2; return 2;;
  esac
}

takx_arch_json() { # [arch] -> {"field": "name", ...}
  local arch="${1:-}" field value text="" separator=""
  if [[ -z "${arch}" ]]; then arch="$(takx_arch)" || return 2; fi
  while read -r field; do
    value="$(takx_arch_get "${field}" "${arch}")" || return 2
    text+="${separator}\"${field}\": \"${value}\""
    separator=", "
  done < <(takx_arch_fields)
  printf '{%s}\n' "${text}"
}

# takx_arch_enter SCRIPT [ARGS...]
# Called by a stage script right after it knows its own path. Returns at once
# when this architecture has no override patches (amd64) or when the script
# already runs from the patched tree. Otherwise it builds or re-verifies that
# tree and replaces the process with the same script inside it, so every
# recipe path, the provider cache keys and the mounts refer to the patched copy.
takx_arch_enter() {
  local script="$1"; shift
  local arch here repo root tree relative
  arch="$(takx_arch)" || exit 2
  here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)" || exit 2
  repo="$(cd "${here}/../.." && pwd)" || exit 2
  compgen -G "${here}/overrides/${arch}/*.patch" >/dev/null || return 0
  [[ -f "${repo}/.takx-arch-tree.json" ]] && return 0
  root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
  tree="$(python3 -I -B "${here}/arch_tree.py" ensure --arch "${arch}" --root "${root}")" || {
    echo "arch.sh: the ${arch} tree could not be prepared; see the message above" >&2; exit 2; }
  script="$(cd "$(dirname "${script}")" && pwd)/$(basename "${script}")" || exit 2
  relative="${script#"${repo}"/}"
  [[ "${relative}" != "${script}" && -x "${tree}/${relative}" ]] || {
    echo "arch.sh: ${script} has no counterpart in ${tree}" >&2; exit 2; }
  exec "${tree}/${relative}" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  case "${1:-}" in
    get) [[ $# -ge 2 && $# -le 3 ]] || { echo "usage: arch.sh get FIELD [ARCH]" >&2; exit 2; }
         takx_arch_get "$2" "${3:-}";;
    json) [[ $# -le 2 ]] || { echo "usage: arch.sh json [ARCH]" >&2; exit 2; }
          takx_arch_json "${2:-}";;
    fields) takx_arch_fields;;
    -h|--help) sed -n '5,39p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
    *) echo "usage: arch.sh get FIELD [ARCH] | json [ARCH] | fields" >&2; exit 2;;
  esac
fi
