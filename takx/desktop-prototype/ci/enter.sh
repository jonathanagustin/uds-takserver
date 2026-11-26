#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# First step of every build job of the opt-in workflow: prepare the runner and
# provide the pinned inputs.
#
#   ci/enter.sh ARCH "TOOLCHAIN_IMAGE RUNTIME_IMAGE TOOLCHAIN_ID IMAGE_SOURCE" MODE [--runtime]
#
# The second argument is the output of the toolchain job (ci/images.sh).
# --runtime also pulls the runtime image (Kernel controls, boot).
# MODE says where the inputs come from:
#   --provide-inputs     the inputs job, once per architecture and run: restore
#                        the inputs stored for these pins (ci/inputs.sh) or, when
#                        none are stored, fetch them (fetch.sh) and store them.
#                        Such a fetch starts from the files of the newest stored
#                        inputs of the architecture (ci/inputs.sh seed), each of
#                        which fetch.sh verifies against its pin, so only what
#                        changed is downloaded.
#                        Writes status/inputs.ref for the job output.
#   --inputs REFERENCE   every other job: restore exactly REFERENCE, the output
#                        of the inputs job. No public source is asked. An empty
#                        REFERENCE means the inputs job failed for this
#                        architecture: the job stops before it does anything.
#
# What it does, as root (the stages write root-owned files below the work root
# and Git refuses a checkout owned by another user):
#   1. copies the checkout, with its upstream/atak-civ submodule, to
#      TAKX_CI_REPO (default /home/user/uds-takserver). The provider cache keys
#      contain the repository path, so the path is fixed; it is the path the
#      hand-pushed amd64 cache was keyed with;
#   2. pulls the images by digest and tags them takx-desktop-proto:toolchain
#      and :runtime;
#   3. writes status/images.json and exports TAKX_PROTO_TOOLCHAIN_ID,
#      TAKX_CI_REPO and the image facts to GITHUB_ENV for the later steps;
#   4. provides the inputs as MODE says and writes status/inputs.json.
#
# Environment: TAKX_PROTO_ROOT (default /var/lib/takx-proto), TAKX_CI_REPO,
# CI_IMAGES (with --provide-inputs), GITHUB_WORKSPACE, GITHUB_ENV. Uses the
# docker login of the calling user.
set -euo pipefail

if ((EUID != 0)); then
  exec sudo -E env "PATH=${PATH}" "DOCKER_CONFIG=${DOCKER_CONFIG:-${HOME}/.docker}" "$0" "$@"
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf 'enter.sh: %s\n' "$*" >&2; }

usage() { echo 'usage: enter.sh ARCH "TOOLCHAIN_IMAGE RUNTIME_IMAGE TOOLCHAIN_ID IMAGE_SOURCE" --provide-inputs|--inputs REFERENCE [--runtime]' >&2; exit 2; }
[[ $# -ge 3 ]] || usage
arch="$1"
read -r toolchain runtime identity source <<<"$2"
shift 2
with_runtime=0
mode=""
inputs=""
while (($#)); do
  case "$1" in
    --runtime) with_runtime=1; shift;;
    --provide-inputs) mode=provide; shift;;
    --inputs) [[ $# -ge 2 ]] || usage; mode=restore; inputs="$2"; shift 2;;
    *) usage;;
  esac
done
[[ -n "${mode}" ]] || usage
[[ -n "${toolchain}" && -n "${runtime}" && "${identity}" == sha256:* && -n "${source}" ]] || {
  say "blocked: the toolchain job gave no usable images for ${arch}"; exit 2; }
[[ "${mode}" != restore || -n "${inputs}" ]] || {
  say "blocked: the inputs job gave no inputs for ${arch}; nothing is fetched or built here"; exit 2; }

checkout="${GITHUB_WORKSPACE:-$(cd "${here}/../../.." && pwd)}"
repo="${TAKX_CI_REPO:-/home/user/uds-takserver}"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"

# shellcheck source=takx/desktop-prototype/arch.sh
. "${checkout}/takx/desktop-prototype/arch.sh"
host="$(TAKX_PROTO_ARCH='' takx_arch)"
[[ "$(takx_arch_get arch "${arch}")" == "${host}" ]] || { say "this runner is ${host}, the job is for ${arch}"; exit 2; }

# 1. Root-owned repository at the fixed path.
[[ -e "${checkout}/upstream/atak-civ/.git" ]] || {
  say "upstream/atak-civ is not checked out (git submodule update --init --depth 1 upstream/atak-civ)"; exit 2; }
if [[ "${checkout}" != "${repo}" ]]; then
  rm -rf "${repo}"
  mkdir -p "$(dirname "${repo}")"
  cp -a "${checkout}" "${repo}"
  chown -R 0:0 "${repo}"
fi
git -C "${repo}/upstream/atak-civ" rev-parse HEAD >&2

# 2. Images, by digest.
mkdir -p "${root}/status" "${root}/logs"
docker pull -q "${toolchain}" >&2
docker tag "${toolchain}" takx-desktop-proto:toolchain
if ((with_runtime)); then
  docker pull -q "${runtime}" >&2
  docker tag "${runtime}" takx-desktop-proto:runtime
fi

# 3. Image record and environment of the later steps.
python3 -I - "${root}/status/images.json" "${arch}" "${toolchain}" "${runtime}" "${identity}" "${source}" "${with_runtime}" <<'PY'
import json, os, subprocess, sys
path, arch, toolchain, runtime, identity, source, with_runtime = sys.argv[1:]

def local(tag):
    return subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()

ids = {'takx-desktop-proto:toolchain': local('takx-desktop-proto:toolchain')}
if with_runtime == '1':
    ids['takx-desktop-proto:runtime'] = local('takx-desktop-proto:runtime')
record = {'stage': 'images', 'status': 'ok', 'outputs': sorted(ids), 'seconds': 0, 'image_ids': ids, 'arch': arch,
          'registry': {'toolchain': toolchain, 'runtime': runtime}, 'toolchain_identity': identity,
          'image_source': source,
          'notes': 'pulled by digest in CI (%s); the provider cache keys use toolchain_identity through '
                   'TAKX_PROTO_TOOLCHAIN_ID, not the local image id' % source}
with open(path + '.tmp', 'w') as stream:
    json.dump(record, stream, indent=1)
    stream.write('\n')
os.replace(path + '.tmp', path)
PY
if [[ -n "${GITHUB_ENV:-}" ]]; then
  {
    echo "TAKX_PROTO_TOOLCHAIN_ID=${identity}"
    echo "TAKX_CI_REPO=${repo}"
    echo "TAKX_CI_ARCH=${arch}"
    echo "TAKX_CI_IMAGE_SOURCE=${source}"
  } >> "${GITHUB_ENV}"
fi
say "repository ${repo}, work root ${root}, toolchain ${identity} (${source})"

# 4. Inputs.
proto="${repo}/takx/desktop-prototype"
record_inputs() { # reference how
  python3 -I - "${root}/status/inputs.json" "$1" "$2" "${arch}" <<'PY'
import json, os, sys
path, reference, how, arch = sys.argv[1:]
with open(path + '.tmp', 'w') as stream:
    json.dump({'stage': 'inputs', 'status': 'ok', 'outputs': [], 'seconds': 0, 'arch': arch, 'reference': reference,
               'notes': '%s %s; status/fetch.json is the record of the fetch that produced them' % (how, reference)},
              stream, indent=1)
    stream.write('\n')
os.replace(path + '.tmp', path)
PY
}
if [[ "${mode}" == restore ]]; then
  reference="$("${proto}/ci/inputs.sh" restore "${inputs}")"
  record_inputs "${reference}" "restored from"
  say "inputs restored from ${reference}"
  exit 0
fi
tag="$("${proto}/ci/inputs.sh" key "${arch}" "${identity}")"
status=0
reference="$("${proto}/ci/inputs.sh" restore "${tag}")" || status=$?
if ((status == 0)); then
  record_inputs "${reference}" "restored (stored by an earlier run) from"
elif ((status == 3)); then
  seeded=""
  while read -r candidate; do
    status=0
    seeded="$("${proto}/ci/inputs.sh" seed "${candidate}")" || status=$?
    if ((status == 0)); then break; fi
    seeded=""
    ((status == 3)) || exit "${status}"
  done < <("${proto}/ci/inputs.sh" seeds "${arch}")
  if [[ -n "${seeded}" ]]; then
    say "the fetch starts from the files of ${seeded}"
    how="fetched in this job, starting from the verified files of ${seeded}, and stored as"
  else
    say "no stored inputs to start from; everything is fetched from the public sources"
    how="fetched from the public sources in this job and stored as"
  fi
  "${proto}/fetch.sh"
  reference="$("${proto}/ci/inputs.sh" save "${tag}" "$("${proto}/ci/inputs.sh" latest "${arch}")")"
  record_inputs "${reference}" "${how}"
else
  exit "${status}"
fi
printf '%s\n' "${reference}" > "${root}/status/inputs.ref"
say "inputs of ${arch}: ${reference}"
