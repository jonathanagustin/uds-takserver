#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Entry point of the TAKX 5.7 desktop prototype image. tini is PID 1 and
# starts this script with one mode:
#
#   desktop    Xtigervnc on a Unix socket, openbox, then the TAKX JVM with the
#              official runTakx command line (launch.sh). When any of the
#              three ends, the others are stopped and the container exits.
#   web        websockify: serves noVNC on TCP 6080 and bridges WebSocket
#              clients to the RFB Unix socket. No VNC TCP port exists.
#   identity   stage_identity.py: validate a mounted TakClient data package
#              and stage it for a manual import. Exits 0 when none is mounted.
#
# The root filesystem is expected to be read-only. Writable mounts:
#   /tmp                  X socket and lock (desktop)
#   /run/takx/session     ProjectData, home, java.io.tmpdir, bin, jvm.pid
#                         (desktop; needs exec: the Kernel loader extracts its
#                         native libraries below tmp/ and maps them from there)
#   /run/takx/vnc         rfb.sock (desktop writes, web connects; shared)
#   /run/takx/identity    staged identity files (identity writes, desktop reads)
# Read-only input of the identity mode: /run/takx/secret (package.zip, password).
#
# Environment (all optional):
#   TAKX_GEOMETRY        desktop size, default 1920x1080
#   TAKX_DEPTH           colour depth 16, 24 or 32, default 24
#   TAKX_DISPLAY_NUMBER  X display number, default 1
#   TAKX_ARGS            TAKX program arguments, space separated. Default:
#                        -create when ProjectData is empty, otherwise none
#   EXTRA_JVM_OPTS       extra JVM options, as the official runTakx task reads
#                        them. Heap dumps are always switched off afterwards:
#                        a dump would hold key material in a file.
#   TAKX_WEB_LISTEN      web mode listen address, default 0.0.0.0:6080
#   TAKX_IDENTITY_*      see stage_identity.py
#
# The Kernel is public 8.30.5 plus project patches and CommonCommo is 1.17.2
# patched; this is not the official TAKX 5.7 distribution.
set -euo pipefail

app=/opt/takx-desktop
tree=/opt/takx
run_dir="${TAKX_RUN_DIR:-/run/takx}"
session="${run_dir}/session"
vnc="${run_dir}/vnc"

log() { printf '%s takx-desktop: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
die() { log "$*"; exit 64; }

alive() { kill -0 "$1" 2>/dev/null; }

stop_children() { # pid...
  local pid
  for pid in "$@"; do
    if alive "${pid}"; then kill -TERM "${pid}" 2>/dev/null || true; fi
  done
  for _ in $(seq 100); do
    local left=0
    for pid in "$@"; do
      if alive "${pid}"; then left=1; fi
    done
    if ((left == 0)); then return 0; fi
    sleep 0.1
  done
  for pid in "$@"; do
    if alive "${pid}"; then kill -KILL "${pid}" 2>/dev/null || true; fi
  done
}

# The run directory links to the read-only tree; only 'resources' is copied,
# because TAKX writes resources/tak-assets/wmm_cof at start, and takx.log and
# nodeUUID.txt land in the run directory. Same layout as ../assemble/session.sh.
build_run_tree() {
  local bin="${session}/bin" entry name
  rm -rf "${bin}"
  mkdir -p "${bin}"
  for entry in "${tree}"/*; do
    name="$(basename "${entry}")"
    case "${name}" in
      resources) cp -r "${entry}" "${bin}/resources"; chmod -R u+w "${bin}/resources";;
      manifest.json) ;;
      *) ln -s "${entry}" "${bin}/${name}";;
    esac
  done
}

desktop() {
  local number="${TAKX_DISPLAY_NUMBER:-1}" geometry="${TAKX_GEOMETRY:-1920x1080}" depth="${TAKX_DEPTH:-24}"
  local dir xpid wmpid jpid ended="" rc=0 stop=0 args=() cookie
  [[ "${number}" =~ ^[0-9]{1,3}$ ]] || die "TAKX_DISPLAY_NUMBER must be a number, got '${number}'"
  [[ "${geometry}" =~ ^[0-9]{3,5}x[0-9]{3,5}$ ]] || die "TAKX_GEOMETRY must be WIDTHxHEIGHT, got '${geometry}'"
  [[ "${depth}" =~ ^(16|24|32)$ ]] || die "TAKX_DEPTH must be 16, 24 or 32, got '${depth}'"
  for dir in /tmp "${session}" "${vnc}"; do
    [[ -d "${dir}" && -w "${dir}" ]] || die "${dir} is not a writable directory; mount a tmpfs or a volume there"
  done
  test ! -e /var/lib/takx-proto || log "warning: /var/lib/takx-proto exists; this image is meant to run without it"

  umask 027
  ulimit -c 0
  mkdir -p "${session}"/{home,tmp,ProjectData}
  export HOME="${session}/home" DISPLAY=":${number}" LIBGL_ALWAYS_SOFTWARE=1
  export XAUTHORITY="${session}/home/.Xauthority"
  # Every start is a fresh PID namespace, so anything left by an earlier
  # container that used the same volumes is stale.
  rm -f "/tmp/.X${number}-lock" "/tmp/.X11-unix/X${number}" "${vnc}/rfb.sock" "${session}/jvm.pid" "${XAUTHORITY}"
  # The X server only creates its socket directory when it runs as root.
  if [[ ! -d /tmp/.X11-unix ]]; then mkdir -m 1777 /tmp/.X11-unix; fi

  # A fresh MIT cookie: only processes that can read the session directory
  # reach the X server; '-nolisten local' drops the abstract socket, which
  # would otherwise be visible to every container of the pod.
  (umask 077; : >"${XAUTHORITY}")
  cookie="$(mcookie)"
  xauth -q add ":${number}" . "${cookie}"
  unset cookie

  log "starting Xtigervnc on :${number} ${geometry}x${depth}, RFB on ${vnc}/rfb.sock"
  Xtigervnc ":${number}" -geometry "${geometry}" -depth "${depth}" -auth "${XAUTHORITY}" \
    -rfbport -1 -rfbunixpath "${vnc}/rfb.sock" -rfbunixmode 0660 -SecurityTypes None \
    -nolisten tcp -nolisten local -AlwaysShared -AcceptCutText=0 -SendCutText=0 &
  xpid=$!
  for _ in $(seq 100); do
    if xdpyinfo >/dev/null 2>&1; then break; fi
    alive "${xpid}" || die "Xtigervnc ended during start"
    sleep 0.2
  done
  xdpyinfo >/dev/null 2>&1 || { stop_children "${xpid}"; die "Xtigervnc did not accept connections"; }
  openbox &
  wmpid=$!

  build_run_tree
  if [[ -n "${TAKX_ARGS:-}" ]]; then
    read -r -a args <<<"${TAKX_ARGS}"
  elif [[ -z "$(find "${session}/ProjectData" -mindepth 1 -print -quit)" ]]; then
    args=(-create)
  fi
  export EXTRA_JVM_OPTS="${EXTRA_JVM_OPTS:-} -XX:-HeapDumpOnOutOfMemoryError -XX:ErrorFile=${session}/hs_err_%p.log"
  log "starting the JVM: launch.sh ${session}/bin ${session} ${args[*]}"
  "${app}/launch.sh" "${session}/bin" "${session}" "${args[@]}" &
  jpid=$!
  echo "${jpid}" >"${session}/jvm.pid"

  trap 'stop=1' TERM INT
  wait -n -p ended "${xpid}" "${wmpid}" "${jpid}" || rc=$?
  trap - TERM INT
  if ((stop)); then
    log "stop requested; ending the session"
    stop_children "${jpid}"
    stop_children "${wmpid}" "${xpid}"
    exit 0
  fi
  case "${ended}" in
    "${jpid}") log "the JVM ended with status ${rc}";;
    "${xpid}") log "Xtigervnc ended with status ${rc}"; rc=70;;
    "${wmpid}") log "openbox ended with status ${rc}"; rc=71;;
    *) log "a child ended with status ${rc}"; rc=72;;
  esac
  rm -f "${session}/jvm.pid"
  stop_children "${jpid}"
  stop_children "${wmpid}" "${xpid}"
  exit "${rc}"
}

web() {
  local listen="${TAKX_WEB_LISTEN:-0.0.0.0:6080}"
  [[ "${listen}" =~ ^[0-9a-fA-F.:]+:[0-9]{2,5}$ ]] || die "TAKX_WEB_LISTEN must be ADDRESS:PORT, got '${listen}'"
  [[ -d "${vnc}" ]] || die "${vnc} is not mounted; the web bridge needs the socket directory of the desktop"
  log "starting websockify on ${listen} for ${vnc}/rfb.sock"
  exec websockify --web "${app}/web-root" --file-only --unix-target "${vnc}/rfb.sock" "${listen}"
}

identity() {
  exec python3 -I "${app}/stage_identity.py"
}

case "${1:-}" in
  desktop) desktop;;
  web) web;;
  identity) identity;;
  *) die "usage: entrypoint.sh desktop|web|identity";;
esac
