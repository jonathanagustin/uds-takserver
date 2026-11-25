#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, boot stage: start the assembled TAKX 5.7 tree in the
# takx-desktop-proto:runtime image under Xvfb 1920x1080x24, openbox and Mesa
# llvmpipe with the official runTakx command line (../assemble/launch.sh), and
# collect evidence. Every attempt gets its own number, run directory and log;
# nothing is reused between attempts and no log is deleted.
#
#   start [--headless] [--trust-sandbox-ca] [takx arguments...]
#            start attempt <n> detached in container takx-boot. --headless
#            passes the client's own -headless switch and starts no X server.
#            --trust-sandbox-ca gives the JVM the trust store that holds the
#            JDK roots plus the sandbox proxy CA, so public map tiles can be
#            fetched through the TLS-intercepting proxy with verification on.
#            Default arguments: -create
#   wait S   wait up to S seconds (at most 540) for the JVM to end; prints
#            'running' or 'exited <code>'
#   check    evidence for the running attempt: JVM age, top-level windows,
#            one x11grab frame with signalstats; writes status/boot.json.
#            'ok' needs: JVM up for more than 120 s, a mapped TAKX top-level
#            window, a non-blank frame, and no fatal JVM error in the log
#   exec CMD run a command inside the container (DISPLAY=:99)
#   stop     stop the container (the run directory and log stay)
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto):
#   runs/boot-<n>/       bin/ (links to dist/takx57, own resources copy),
#                        ProjectData/, tmp/, home/, java.exit, glxinfo.txt
#   logs/boot-<n>.log    Xvfb/GL facts and the complete JVM output
#   status/boot.json     written by 'check'
#   The screenshot goes to demo/out/takx/ in the repository (git-ignored).
#
# Environment:
#   TAKX_PROTO_ROOT     work root
#   TAKX_BOOT_JVM_OPTS  extra JVM options (EXTRA_JVM_OPTS of the official task)
#   TAKX_BOOT_ENV       extra KEY=VALUE pairs for the container, space separated
#
# The Kernel is public 8.30.5 plus project patches and CommonCommo is 1.17.2
# patched; neither is the private 8.57.11 / 1.20.0 TAKX 5.7 declares.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
dist="${root}/dist/takx57"
media="${repo}/demo/out/takx"
name=takx-boot

mkdir -p "${root}"/{runs,logs,status}

current() { cat "${root}/runs/current" 2>/dev/null || true; }
running() { [[ "$(docker inspect -f '{{.State.Running}}' "${name}" 2>/dev/null)" == true ]]; }
inside() { docker exec -e DISPLAY=:99 "${name}" "$@"; }

do_start() {
  local headless=0 n=1 extra=() envs=() pair jvm="${TAKX_BOOT_JVM_OPTS:-}" store="${root}/ca/java-cacerts.p12"
  while [[ "${1:-}" == --headless || "${1:-}" == --trust-sandbox-ca ]]; do
    if [[ "$1" == --headless ]]; then headless=1; else
      # The sandbox reaches the network through a TLS-intercepting proxy. The
      # trust store built by run-in-toolchain.sh holds the JDK roots plus that
      # proxy CA; verification stays on. Without it every map tile request
      # fails its certificate check, which is the correct default.
      "${run}" --java-truststore >/dev/null
      [[ -s "${store}" ]] || { echo "boot: no sandbox trust store at ${store}" >&2; exit 1; }
      jvm="${jvm} -Djavax.net.ssl.trustStore=${store} -Djavax.net.ssl.trustStorePassword=changeit"
    fi
    shift
  done
  (($#)) || set -- -create
  [[ -s "${dist}/manifest.json" ]] || { echo "boot: no assembled tree; run assemble.sh" >&2; exit 1; }
  while [[ -e "${root}/logs/boot-${n}.log" || -e "${root}/runs/boot-${n}" ]]; do n=$((n + 1)); done
  docker rm -f "${name}" >/dev/null 2>&1 || true
  if ((headless)); then envs+=(-e TAKX_BOOT_NO_X=1); set -- -headless "$@"; fi
  for pair in ${TAKX_BOOT_ENV:-}; do envs+=(-e "${pair}"); done
  # The repository mount is read-only; only the git-ignored media directory
  # is writable, for screenshots and recordings.
  mkdir -p "${media}"
  extra+=(--mount "${media}:${media}" --docker-arg --shm-size=1g)
  echo "boot-${n}" >"${root}/runs/current"
  "${run}" --runtime -d --name "${name}" "${extra[@]}" "${envs[@]}" \
    -e "EXTRA_JVM_OPTS=${jvm}" -- \
    "${repo}/takx/desktop-prototype/assemble/session.sh" "${dist}" "${root}/runs/boot-${n}" "${root}/logs/boot-${n}.log" "$@" >/dev/null
  echo "boot: attempt ${n} started; log ${root}/logs/boot-${n}.log" >&2
}

do_wait() {
  local budget="${1:-60}" rc
  ((budget <= 540)) || budget=540
  if rc="$(timeout "${budget}" docker wait "${name}" 2>/dev/null)"; then echo "exited ${rc}"; else echo running; fi
}

do_check() {
  local attempt log age=0 windows="" title="" frame stats ymin=0 ymax=0 fatal status notes
  attempt="$(current)"; log="${root}/logs/${attempt}.log"
  frame="${media}/${attempt}-check.png"
  if running; then
    age="$(inside bash -c 'ps -o etimes= -C java | head -1 | tr -d " "' || echo 0)"
    windows="$(inside bash -c 'for w in $(xdotool search --onlyvisible --name . 2>/dev/null); do printf "%s|%s|%s\n" "$w" "$(xdotool getwindowname "$w")" "$(xdotool getwindowgeometry "$w" | tr "\n" " ")"; done' || true)"
    title="$(printf '%s\n' "${windows}" | grep -i 'TAK' | head -1 | cut -d'|' -f2 || true)"
    inside ffmpeg -nostdin -loglevel error -y -f x11grab -video_size 1920x1080 -i :99 -frames:v 1 "${frame}" || true
    if [[ -s "${frame}" ]]; then
      stats="$(inside ffmpeg -nostdin -loglevel error -i "${frame}" -vf signalstats,metadata=print:file=- -f null - | grep -E 'YMIN|YMAX' || true)"
      ymin="$(printf '%s\n' "${stats}" | sed -n 's/.*YMIN=//p' | head -1)"
      ymax="$(printf '%s\n' "${stats}" | sed -n 's/.*YMAX=//p' | head -1)"
    fi
  fi
  fatal="$(grep -cE 'A fatal error has been detected|SIGSEGV|OutOfMemoryError' "${log}" 2>/dev/null || true)"
  status=failed
  if running && ((${age:-0} > 120)) && [[ -n "${title}" ]] && [[ "${fatal:-0}" == 0 ]] &&
     python3 -I -c "import sys; sys.exit(0 if float(sys.argv[2] or 0) - float(sys.argv[1] or 0) > 16 else 1)" "${ymin}" "${ymax}"; then
    status=ok
  fi
  notes="attempt ${attempt}: container running $(running && echo yes || echo no); JVM age ${age:-0}s; TAKX window title '${title}'; frame YMIN=${ymin:-} YMAX=${ymax:-}; fatal JVM markers in log: ${fatal:-0}. Kernel 8.30.5 + project patches and CommonCommo 1.17.2 patched (not the private 8.57.11 / 1.20.0). 'ok' here is the automatic evidence only; what the frame shows is described in STATUS.md"
  python3 -I - "${root}/status/boot.json" "${status}" "${age:-0}" "${notes}" "${windows}" "${log}" "${frame}" "${root}/runs/${attempt}" <<'PY'
import json, os, sys
path, status, seconds, notes, windows, *outputs = sys.argv[1:]
tmp = path + '.tmp.%d' % os.getpid()
with open(tmp, 'w') as stream:
    json.dump({'stage': 'boot', 'status': status, 'outputs': [o for o in outputs if os.path.exists(o)],
               'seconds': int(seconds or 0), 'notes': notes,
               'windows': [line for line in windows.splitlines() if line]}, stream, indent=1)
    stream.write('\n')
os.replace(tmp, path)
PY
  cat "${root}/status/boot.json"
  [[ "${status}" == ok ]]
}

case "${1:-}" in
  start) shift; do_start "$@";;
  wait) shift; do_wait "$@";;
  check) do_check;;
  exec) shift; inside "$@";;
  stop) docker rm -f "${name}" >/dev/null 2>&1 || true;;
  -h|--help|"") sed -n '5,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) echo "boot.sh: unknown target '$1'" >&2; exit 2;;
esac
