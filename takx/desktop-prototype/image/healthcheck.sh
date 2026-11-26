#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Probes of the TAKX desktop prototype image. Exit 0 means the condition
# holds; otherwise one line on stderr says what is missing.
#
#   started   the JVM is running and a visible top-level window of at least
#             1024x700 is mapped whose title is "TAKX 5.7 ... : <project> ...":
#             the real main window with a project open, not the X server
#             alone and not the bare frame TAKX shows while a start-up error
#             dialog is up. Startup probe.
#             TAKX_HEALTH_TITLE replaces the title pattern, for a session
#             that is meant to stay without a project.
#   ready     'started', and the RFB socket for the web bridge exists.
#   live      the JVM is running and the X server answers. Liveness probe.
#   web       the gate answers /healthz on TCP 6080 and websockify's socket
#             exists (web container). /healthz needs no token and says "ok",
#             nothing else: that the desktop is reachable for a signed-in
#             user is not something a probe without a token can see.
#
# Whether the globe is drawn is not checked here; verify/selftest.sh reads a
# frame for that.
set -euo pipefail

run_dir="${TAKX_RUN_DIR:-/run/takx}"
session="${run_dir}/session"
vnc="${run_dir}/vnc"
export DISPLAY=":${TAKX_DISPLAY_NUMBER:-1}" XAUTHORITY="${session}/home/.Xauthority"

title="${TAKX_HEALTH_TITLE:-^TAKX 5\.7[^:]* : .}"

fail() { echo "healthcheck: $*" >&2; exit 1; }

jvm_running() {
  local pid state
  pid="$(cat "${session}/jvm.pid" 2>/dev/null)" || fail "no ${session}/jvm.pid; the JVM was not started or has ended"
  [[ "${pid}" =~ ^[0-9]+$ ]] || fail "jvm.pid does not hold a process id"
  [[ "$(cat "/proc/${pid}/comm" 2>/dev/null)" == java ]] || fail "process ${pid} is not a running JVM"
  # A killed JVM that has not been reaped yet still has a comm entry.
  state="$(sed -E 's/^.*\) ([A-Za-z]) .*$/\1/' "/proc/${pid}/stat" 2>/dev/null)" || fail "process ${pid} is gone"
  [[ "${state}" != Z && "${state}" != X ]] || fail "JVM ${pid} has ended (state ${state})"
}

main_window_mapped() {
  local id shell width height
  while read -r id; do
    shell="$(xdotool getwindowgeometry --shell "${id}" 2>/dev/null)" || continue
    width="$(sed -n 's/^WIDTH=//p' <<<"${shell}")"
    height="$(sed -n 's/^HEIGHT=//p' <<<"${shell}")"
    if ((${width:-0} >= 1024 && ${height:-0} >= 700)); then return 0; fi
  done < <(xdotool search --onlyvisible --name "${title}" 2>/dev/null || true)
  fail "no visible window of at least 1024x700 with a title matching '${title}'"
}

case "${1:-ready}" in
  started) jvm_running; main_window_mapped;;
  ready)
    jvm_running; main_window_mapped
    [[ -S "${vnc}/rfb.sock" ]] || fail "no RFB socket at ${vnc}/rfb.sock";;
  live)
    jvm_running
    xdpyinfo >/dev/null 2>&1 || fail "the X server on ${DISPLAY} does not answer";;
  web)
    [[ -S "${run_dir}/web/websockify.sock" ]] || fail "no websockify socket at ${run_dir}/web/websockify.sock"
    python3 -I -c 'import sys, urllib.request
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open("http://127.0.0.1:" + sys.argv[1] + "/healthz", timeout=5) as reply:
    sys.exit(0 if reply.status == 200 and reply.read() == b"ok\n" else 1)' "${TAKX_WEB_PORT:-6080}" 2>/dev/null ||
      fail "the gate does not answer /healthz on 127.0.0.1:${TAKX_WEB_PORT:-6080}";;
  *) echo "usage: healthcheck.sh started|ready|live|web" >&2; exit 64;;
esac
