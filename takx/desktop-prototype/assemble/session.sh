#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Container entry point of one boot attempt (takx-desktop-proto:runtime):
# Xvfb 1920x1080x24 on :99, openbox, Mesa llvmpipe, then launch.sh. The JVM
# output goes to LOG; the JVM exit status is written to RUNDIR/java.exit and
# is the exit status of this script.
#
# Usage: session.sh DIST RUNDIR LOG [takx arguments...]
#
# TAKX_BOOT_NO_X=1 starts no X server (for the -headless switch).
# TAKX_BOOT_DELAY_S=N waits N seconds between the X session and the JVM.
set -uo pipefail

dist="$1"; rundir="$2"; log="$3"; shift 3
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$rundir"

# The run directory links to the assembled tree; only 'resources' is copied,
# because TAKX writes resources/tak-assets/wmm_cof at start, and takx.log
# lands in the run directory.
tree="$rundir/bin"
if [[ ! -d "$tree" ]]; then
  mkdir -p "$tree"
  for entry in "$dist"/*; do
    case "$(basename "$entry")" in
      resources) cp -a "$entry" "$tree/resources";;
      manifest.json) ;;
      *) ln -s "$entry" "$tree/$(basename "$entry")";;
    esac
  done
fi

{
  echo "===== $(date -u +%FT%TZ) session: $*"
  if [[ "${TAKX_BOOT_NO_X:-0}" != 1 ]]; then
    export DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1
    Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp +extension GLX >"$rundir/xvfb.log" 2>&1 &
    for _ in $(seq 50); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.2; done
    xdpyinfo >/dev/null 2>&1 || { echo "session: Xvfb did not start"; cat "$rundir/xvfb.log"; exit 70; }
    openbox >"$rundir/openbox.log" 2>&1 &
    sleep 1
    glxinfo -B 2>&1 | grep -E 'OpenGL (vendor|renderer|version|core profile version)|direct rendering' | tee "$rundir/glxinfo.txt"
  fi
  java -version 2>&1
} >>"$log" 2>&1

# TAKX_BOOT_DELAY_S leaves the empty desktop up for that many seconds first,
# so that a screen recording can start before the JVM does.
sleep "${TAKX_BOOT_DELAY_S:-0}"
echo "===== $(date -u +%FT%TZ) session: starting the JVM" >>"$log"
"$here/launch.sh" "$tree" "$rundir" "$@" >>"$log" 2>&1
rc=$?
echo "$rc" >"$rundir/java.exit"
echo "===== $(date -u +%FT%TZ) session: JVM exit $rc" >>"$log"
exit "$rc"
