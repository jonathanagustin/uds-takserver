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
#   web        web_gate.py on TCP 6080 in front of websockify. The gate
#              verifies the token Authservice forwards (web_auth.py) and only
#              then passes a request on; websockify serves noVNC and bridges
#              WebSocket clients to the RFB Unix socket, and listens on a Unix
#              socket only the web user can reach. No VNC TCP port exists.
#   identity   stage_identity.py: validate a mounted TakClient data package
#              and stage it for a manual import. Exits 0 when none is mounted.
#
# The root filesystem is expected to be read-only. Writable mounts:
#   /tmp                  X socket and lock (desktop)
#   /run/takx/session     ProjectData, home, java.io.tmpdir, bin, jvm.pid
#                         (desktop; needs exec: the Kernel loader extracts its
#                         native libraries below tmp/ and maps them from there)
#   /run/takx/vnc         rfb.sock (desktop writes, web connects; shared)
#   /run/takx/web         websockify.sock (web only, mode 0700)
#   /run/takx/identity    staged identity files (identity writes, desktop reads)
# Read-only input of the identity mode: /run/takx/secret (package.zip, password).
# Optional read-only input of the desktop mode: /opt/takx-addons/video, a
# directory of FFmpeg natives the user supplies (see below).
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
#   TAKX_LOCAL_INPUTS    'off' (default): every network input TAKX would
#                        listen on by default is switched off in the program
#                        properties of a new session (below). 'default': the
#                        client's own defaults (TCP and UDP 4242 and 8087, UDP
#                        10011, six multicast groups)
#   TAKX_WEB_LISTEN      web mode listen address, default 0.0.0.0:6080
#   TAKX_WEB_HEARTBEAT   seconds between WebSocket pings from websockify to the
#                        browser, default 25; keeps a quiet session from
#                        looking idle to anything on the path
#   TAKX_AUTH_*          web mode: what the gate checks; see web_gate.py and
#                        web_auth.py. Without a complete set the web mode does
#                        not start. TAKX_AUTH_MODE=none switches the check off
#                        (a developer's own machine, port on loopback only)
#   TAKX_ADDON_VIDEO     directory with FFmpeg natives for JavaCPP
#                        (libavutil.so.56, libjniavutil.so, ...), default
#                        /opt/takx-addons/video. The image ships none: the
#                        build TAKX declares states that it is nonfree and
#                        unredistributable. When the directory holds a
#                        library it is appended to java.library.path;
#                        otherwise video stays off and one error is logged
#                        when a project opens.
#
# GDAL_DATA and PROJ_LIB are set to the data directories in the tree: the
# libraries were built with the paths of their build directories, and without
# the files GDAL logs "Unable to open EPSG support file gcs.csv".
#   TAKX_IDENTITY_*      see stage_identity.py
#
# The Kernel is public 8.30.5 plus project patches and CommonCommo is 1.17.2
# patched; this is not the official TAKX 5.7 distribution.
set -euo pipefail

app=/opt/takx-desktop
tree=/opt/takx
gdal_data="${tree}/share/gdal"
proj_data="${tree}/share/proj"
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

# TAKX keeps its program settings in ProjectData/LocalProgramProperties.xml,
# "the file expected to be edited by users to make program configuration
# changes" (IProgramEnvironment.getLocalProgramPropertiesFile), and copies it
# from conf/DefaultProgramProperties.xml when it does not exist. The node
# CommonCommo/DefaultInputs there lists what CommonCommo listens on, each
# with an 'enabled' entry; the same switches are in Preferences > Network.
# CommonCommo takes only the port of a TCP input (addTcpInboundInterface), so
# an input cannot be bound to loopback: it is on every address or off.
#
# In a pod nothing can reach these inputs and nothing should, so a new
# session starts with all of them off. An existing file is left alone: it is
# the user's. Not covered: the HTTPS port CommonCommo opens for peer-to-peer
# mission packages (setMissionPackageLocalHttpsParams, an ephemeral port, no
# setting), and one listener on loopback.
seed_program_properties() {
  local mode="${TAKX_LOCAL_INPUTS:-off}" target="${session}/ProjectData/ProjectData/LocalProgramProperties.xml"
  case "${mode}" in
    default) log "local inputs: the client's defaults (TCP 4242 and 8087 on every address)"; return 0;;
    off) ;;
    *) die "TAKX_LOCAL_INPUTS must be off or default, got '${mode}'";;
  esac
  if [[ -e "${target}" ]]; then
    log "local inputs: ${target} exists and is left as it is"
    return 0
  fi
  mkdir -p "$(dirname "${target}")"
  python3 -I - "${tree}/conf/DefaultProgramProperties.xml" "${target}" <<'PY' || die "could not switch the default inputs off"
import os, re, sys
source, target = sys.argv[1:]
text = open(source, encoding="utf-8").read()
begin, end = text.find('<node name="DefaultInputs">'), text.find('<node name="DefaultOutputs">')
if begin < 0 or end < begin:
    sys.exit("no CommonCommo DefaultInputs node before DefaultOutputs in " + source)
block = text[begin:end]
inputs = len(re.findall(r'<entry key="uri" value="(?:tcp|udp)://', block))
block, changed = re.subn(r'(<entry key="enabled" value=")true("/>)', r"\1false\2", block)
if inputs == 0 or changed != inputs:
    sys.exit(f"{inputs} inputs but {changed} 'enabled' entries in {source}")
with open(target + ".tmp", "w", encoding="utf-8") as stream:
    stream.write(text[:begin] + block + text[end:])
os.replace(target + ".tmp", target)
print(f"local inputs: {changed} default inputs switched off in {target}", file=sys.stderr)
PY
}

desktop() {
  local number="${TAKX_DISPLAY_NUMBER:-1}" geometry="${TAKX_GEOMETRY:-1920x1080}" depth="${TAKX_DEPTH:-24}"
  local dir xpid wmpid jpid ended="" rc=0 stop=0 args=() cookie addon="${TAKX_ADDON_VIDEO:-/opt/takx-addons/video}"
  [[ "${number}" =~ ^[0-9]{1,3}$ ]] || die "TAKX_DISPLAY_NUMBER must be a number, got '${number}'"
  [[ "${geometry}" =~ ^[0-9]{3,5}x[0-9]{3,5}$ ]] || die "TAKX_GEOMETRY must be WIDTHxHEIGHT, got '${geometry}'"
  [[ "${depth}" =~ ^(16|24|32)$ ]] || die "TAKX_DEPTH must be 16, 24 or 32, got '${depth}'"
  for dir in /tmp "${session}" "${vnc}"; do
    [[ -d "${dir}" && -w "${dir}" ]] || die "${dir} is not a writable directory; mount a tmpfs or a volume there"
  done
  test ! -e /var/lib/takx-proto || log "warning: /var/lib/takx-proto exists; this image is meant to run without it"
  [[ -s "${gdal_data}/gcs.csv" ]] || die "${gdal_data}/gcs.csv is missing; the tree has no GDAL data directory"
  [[ -s "${proj_data}/epsg" ]] || die "${proj_data}/epsg is missing; the tree has no PROJ data directory"
  export GDAL_DATA="${gdal_data}" PROJ_LIB="${proj_data}"
  unset TAKX_ADDON_LIBRARY_PATH
  if [[ -d "${addon}" && -n "$(find "${addon}" -maxdepth 1 -name 'lib*.so*' -print -quit)" ]]; then
    export TAKX_ADDON_LIBRARY_PATH="${addon}"
    log "video add-on: FFmpeg natives supplied by the user in ${addon} (not part of this image)"
  else
    log "video add-on: none in ${addon}; video playback and recording are off"
  fi

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
  # -create unless a project exists already (a volume that was used before).
  if [[ -n "${TAKX_ARGS:-}" ]]; then
    read -r -a args <<<"${TAKX_ARGS}"
  elif [[ -z "$(find "${session}/ProjectData" -mindepth 3 -maxdepth 3 -path '*/Projects/*' -type d -print -quit 2>/dev/null)" ]]; then
    args=(-create)
  fi
  seed_program_properties
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
  local listen="${TAKX_WEB_LISTEN:-0.0.0.0:6080}" heartbeat="${TAKX_WEB_HEARTBEAT:-25}" dir="${run_dir}/web"
  local socket wpid gpid ended="" rc=0 stop=0
  [[ "${listen}" =~ ^[0-9a-fA-F.:]+:[0-9]{2,5}$ ]] || die "TAKX_WEB_LISTEN must be ADDRESS:PORT, got '${listen}'"
  [[ "${heartbeat}" =~ ^[0-9]{1,4}$ ]] || die "TAKX_WEB_HEARTBEAT must be a number of seconds, got '${heartbeat}'"
  [[ -d "${vnc}" ]] || die "${vnc} is not mounted; the web bridge needs the socket directory of the desktop"
  [[ -d "${dir}" && -w "${dir}" ]] || die "${dir} is not a writable directory; mount a tmpfs there for the bridge's own socket"
  socket="${dir}/websockify.sock"
  umask 077
  rm -f "${socket}"
  # websockify is reachable through this socket only: mode 0600 in a
  # directory of the web user. The gate in front of it owns TCP 6080.
  log "starting websockify on ${socket} for ${vnc}/rfb.sock"
  websockify --unix-listen="${socket}" --unix-listen-mode=0600 --web "${app}/web-root" --file-only \
    --heartbeat="${heartbeat}" --unix-target "${vnc}/rfb.sock" &
  wpid=$!
  for _ in $(seq 100); do
    if [[ -S "${socket}" ]]; then break; fi
    alive "${wpid}" || die "websockify ended during start"
    sleep 0.1
  done
  [[ -S "${socket}" ]] || { stop_children "${wpid}"; die "websockify did not create ${socket}"; }
  log "starting the gate on ${listen} (authentication: ${TAKX_AUTH_MODE:-oidc})"
  TAKX_WEB_LISTEN="${listen}" TAKX_WEB_UPSTREAM="${socket}" python3 -I "${app}/web_gate.py" &
  gpid=$!

  trap 'stop=1' TERM INT
  wait -n -p ended "${wpid}" "${gpid}" || rc=$?
  trap - TERM INT
  if ((stop)); then
    log "stop requested; ending the bridge"
    stop_children "${gpid}" "${wpid}"
    exit 0
  fi
  case "${ended}" in
    "${gpid}") log "the gate ended with status ${rc}"; ((rc != 0)) || rc=73;;
    "${wpid}") log "websockify ended with status ${rc}"; ((rc != 0)) || rc=74;;
    *) log "a child ended with status ${rc}"; rc=72;;
  esac
  stop_children "${gpid}" "${wpid}"
  exit "${rc}"
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
