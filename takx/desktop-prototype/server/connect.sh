#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, connected run: start the assembled TAKX 5.7 tree next to
# the local TAK Server of this directory (README.md) so that the client can be
# given a TAK Server connection through its own user interface.
#
#   start       new attempt connect-<n> in container takx-connect: the same
#               session (../assemble/session.sh: Xvfb 1920x1080x24, openbox,
#               Mesa llvmpipe, official runTakx command line) as stages/boot.sh,
#               plus one host entry: the TAK server certificate name
#               (TAK_CERT_NAME, 'takserver') -> the passthrough gateway address
#   restart     stop, then start the JVM again on the current attempt's run
#               directory (same ProjectData, so a saved connection is restored)
#   exec CMD    run a command in the container (DISPLAY=:99)
#   shot NAME   one x11grab frame to the private directory
#               $TAKX_PROTO_ROOT/connect/<attempt>/shots/NAME.png. Private on
#               purpose: frames taken while a credential dialog is open must
#               never reach demo/out
#   evidence    server-side and client-side records of the stream, written to
#               $TAKX_PROTO_ROOT/connect/<attempt>/evidence/ (connect_evidence.py)
#   stop        stop the container (run directory and logs stay)
#
# No password, key or PKCS#12 file is read or written by this script. The
# container sees the work root, which holds secrets/<takclient>/ with mode 600
# files; the client's own file choosers read them from there.
#
# Trust: the JVM gets the stock JDK trust store (no --trust-sandbox-ca), so
# the only additional trust in the process is the CA file given to the TAK
# Server connection. Public map tile requests therefore fail their
# certificate check behind the sandbox proxy; the built-in base map shows.
#
# Environment: TAKX_PROTO_ROOT (default /var/lib/takx-proto), TAKX_CONNECT_DIST
# (default $TAKX_PROTO_ROOT/dist/takx57), TAKX_BOOT_JVM_OPTS, TAKX_BOOT_ENV,
# TAKX_CONNECT_SERVER_IP (default: the passthrough gateway; an experiment
# may point the certificate name at a relay on the workstation instead).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
dist="${TAKX_CONNECT_DIST:-${root}/dist/takx57}"
name=takx-connect

mkdir -p "${root}"/{runs,logs,status,connect}
# shellcheck disable=SC1091
source "${root}/server/endpoints.env"

current() { cat "${root}/connect/current" 2>/dev/null || true; }
inside() { docker exec -e DISPLAY=:99 "${name}" "$@"; }

launch() { # attempt [takx arguments...]
  local attempt="$1" envs=() pair; shift
  docker rm -f "${name}" >/dev/null 2>&1 || true
  for pair in ${TAKX_BOOT_ENV:-}; do envs+=(-e "${pair}"); done
  mkdir -p "${root}/connect/${attempt}"
  echo "${attempt}" >"${root}/connect/current"
  # shellcheck disable=SC2154 # TAK_CERT_NAME is set by ${root}/server/endpoints.env (written by server/45-hosts.sh), sourced above
  "${run}" --runtime -d --name "${name}" --docker-arg --shm-size=1g \
    --docker-arg "--add-host=${TAK_CERT_NAME}:${TAKX_CONNECT_SERVER_IP:-${PASSTHROUGH_GATEWAY_IP}}" "${envs[@]}" \
    -e "EXTRA_JVM_OPTS=${TAKX_BOOT_JVM_OPTS:-}" -- \
    "${repo}/takx/desktop-prototype/assemble/session.sh" "${dist}" "${root}/runs/${attempt}" "${root}/logs/${attempt}.log" "$@" >/dev/null
  echo "connect: ${attempt} started; log ${root}/logs/${attempt}.log" >&2
}

case "${1:-}" in
  start)
    shift; n=1
    [[ -s "${dist}/manifest.json" ]] || { echo "connect: no assembled tree at ${dist}" >&2; exit 1; }
    while [[ -e "${root}/logs/connect-${n}.log" || -e "${root}/runs/connect-${n}" ]]; do n=$((n + 1)); done
    (($#)) || set -- -create
    launch "connect-${n}" "$@";;
  restart)
    shift; attempt="$(current)"
    [[ -d "${root}/runs/${attempt}/ProjectData" ]] || { echo "connect: no current attempt to restart" >&2; exit 1; }
    launch "${attempt}" "$@";;
  exec) shift; inside "$@";;
  shot)
    out="${root}/connect/$(current)/shots"; mkdir -p "${out}"; chmod 700 "${out}"
    inside ffmpeg -nostdin -loglevel error -y -f x11grab -video_size 1920x1080 -i :99 -frames:v 1 "${out}/${2:?name}.png"
    echo "${out}/$2.png";;
  evidence) shift; exec python3 -I "${here}/connect_evidence.py" "${root}" "$(current)" "$@";;
  stop) docker rm -f "${name}" >/dev/null 2>&1 || true;;
  -h|--help|"") sed -n '5,37p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) echo "connect.sh: unknown target '$1'" >&2; exit 2;;
esac
