#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, connected capture: record the TAKX 5.7 prototype while it
# is connected to the local TAK Server of ../server/ (README.md there) and
# exchanges markers with CloudTAK, the browser client of the same deployment.
#
#   record   1. CloudTAK: start a headed Chrome on its own X display and sign
#               in through Keycloak (../server/connect-cloudtak.sh). NOT
#               recorded: the Keycloak form is on screen
#            2. TAKX: start a fresh connected attempt (../server/connect.sh
#               start). NOT recorded: add the TAK Server connection through
#               the client's own form (../server/connect-configure.sh; the
#               form has masked password fields), wait for the stream, then
#               untick the connection's 'enabled' box so the take can start
#               disconnected
#            3. start two silent real streaming clients on the host
#               (../server/connect_peer.py): takx-peer (same TAK group as
#               TAKX and the CloudTAK user) and takx-outsider (unrelated
#               group); each records what the server delivers to it
#            4. record both X displays losslessly (ffmpeg x11grab, 1920x1080,
#               30 fps) while the real user interfaces are driven:
#               TAKX      tick 'enabled' (status goes green)
#               CloudTAK  Coordinate Input: SYNTHETIC-PEER-<n>, then Share >
#                         Broadcast To All Users
#               TAKX      the CloudTAK marker is on the globe (hover: label);
#                         Point Dropper places a marker, its details dialog
#                         names it SYNTHETIC-TAKX-<n>, radial menu > Send >
#                         Broadcast
#               CloudTAK  the TAKX marker appears; the search box finds it
#                         by name and its details panel is opened
#            5. collect the server-side records (../server/connect_evidence.py)
#   finish   encode the deliverables (H.264 yuv420p 1920x1080 30 fps) from
#            the two lossless masters, verify them with ffprobe, signalstats,
#            freezedetect and blackdetect, extract review frames
#   all      record, then finish
#
# <n> is the attempt number. Both markers are at typed synthetic positions in
# the open South Atlantic. Credential forms (TAKX connection form, Keycloak
# sign-in) are closed before the recording starts and are never reopened; no
# password, key or certificate body is displayed, logged or written by this
# script.
#
# Layout:
#   $TAKX_PROTO_ROOT/connect/<attempt>/capture/master.mkv (TAKX display),
#       cloudtak-master.mkv (browser display), events.tsv, offsets.env,
#       cloudtak-place.json, cloudtak-find.json, peer.json, outsider.json;
#       ../evidence/*.json
#   demo/out/takx/ (git-ignored)  takx57-connected-*.mp4 (TAKX display),
#       takx57-connected-cloudtak-*.mp4 (browser display), frames/,
#       capture-connected.json
#   $TAKX_PROTO_ROOT/status/capture-connected.json
#
# TAKX_CAPTURE_PEER_XY="x y" is where the marker at -10, -25 appears on the
# globe of a new project while the TAK Server dock is open (only used to
# hover it for its label). The Kernel is public 8.30.5 plus project patches
# and CommonCommo is 1.17.2 patched, not the private 8.57.11 / 1.20.0. The
# coordinates are those of this prototype's 1920x1080 session and of
# CloudTAK 13.112.3; this is not a general UI driver.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
server="${repo}/takx/desktop-prototype/server"
connect="${server}/connect.sh"
cloudtak="${server}/connect-cloudtak.sh"
cloudtak_display="${TAKX_CLOUDTAK_DISPLAY:-:97}"
media="${repo}/demo/out/takx"
name=takx-connect
read -r peer_x peer_y <<<"${TAKX_CAPTURE_PEER_XY:-755 687}"

mkdir -p "${root}"/{connect,status} "${media}"
# shellcheck disable=SC1091
source "${root}/server/endpoints.env"

status() { # status seconds notes outputs...
  python3 -I - "${root}/status/capture-connected.json" "$@" <<'PY'
import json, os, sys
path, status, seconds, notes, *outputs = sys.argv[1:]
tmp = path + '.tmp.%d' % os.getpid()
with open(tmp, 'w') as stream:
    json.dump({'stage': 'capture-connected', 'status': status, 'outputs': outputs,
               'seconds': int(seconds), 'notes': notes}, stream, indent=1)
    stream.write('\n')
os.replace(tmp, path)
PY
}

await_log() { # pattern seconds: wait until the client log has one more matching line than before
  local pattern="$1" budget="$2" log="$3" before now
  before="$(grep -c "${pattern}" "${log}" || true)"
  shift 3; "$@"
  for _ in $(seq "${budget}"); do
    now="$(grep -c "${pattern}" "${log}" || true)"
    ((now > before)) && return 0
    sleep 1
  done
  return 1
}

do_record() {
  local attempt n dir log t0 recorder before end_takx end_browser previous
  # Off the record: the browser peer signs in.
  "${cloudtak}" start
  mkdir -p "${root}/connect/cloudtak"
  "${cloudtak}" signin >"${root}/connect/cloudtak/signin.json" ||
    { status failed 0 "CloudTAK sign-in failed; see ${root}/connect/cloudtak"; exit 1; }

  "${connect}" start
  attempt="$(cat "${root}/connect/current")"; n="${attempt#connect-}"
  dir="${root}/connect/${attempt}/capture"; log="${root}/logs/${attempt}.log"
  mkdir -p "${dir}"
  cp "${root}/connect/cloudtak/signin.json" "${dir}/cloudtak-signin.json"
  status running 0 "attempt ${attempt}: booting"
  for _ in $(seq 100); do docker exec -e DISPLAY=:99 "${name}" xdotool search --onlyvisible --name 'TAKX 5.7' >/dev/null 2>&1 && break; sleep 3; done
  sleep 50   # project load and first globe frames

  # Off the record: the credential form.
  await_log 'Stream connection to ssl:.* is up' 60 "${log}" "${server}/connect-configure.sh" takx-desktop ||
    { status failed 0 "attempt ${attempt}: no 'Stream connection ... is up' in ${log} after the form"; exit 1; }
  sleep 6
  await_log 'TAK Server Disconnected' 20 "${log}" docker exec -e DISPLAY=:99 "${name}" bash -c \
    'xdotool mousemove --sync 1769 241; sleep 0.4; xdotool click 1; sleep 0.5; xdotool mousemove --sync 1500 700' ||
    { status failed 0 "attempt ${attempt}: the connection did not report Disconnected after unticking 'enabled'"; exit 1; }
  sleep 5   # let the notification toast leave the screen
  # The browser keeps the markers of an earlier take: clear them, then show
  # the overview.
  if [[ -s "${root}/connect/capture-current" ]]; then
    previous="$(cat "${root}/connect/capture-current")"; previous="${previous#connect-}"
    "${cloudtak}" remove "SYNTHETIC-PEER-${previous}" >/dev/null 2>&1 || true
    "${cloudtak}" remove "SYNTHETIC-TAKX-${previous}" >/dev/null 2>&1 || true
  fi
  "${cloudtak}" close >/dev/null
  "${cloudtak}" view -20 -20 3.2 >/dev/null

  # Two silent real clients for the whole take.
  python3 -I "${server}/connect_peer.py" "${root}/secrets/takx-outsider" --connect "${PASSTHROUGH_GATEWAY_IP}" \
    --listen 150 --out "${dir}/outsider.json" >/dev/null 2>"${dir}/outsider.err" &
  local outsider=$!
  python3 -I "${server}/connect_peer.py" "${root}/secrets/takx-peer" --connect "${PASSTHROUGH_GATEWAY_IP}" \
    --listen 150 --out "${dir}/peer.json" >/dev/null 2>"${dir}/peer.err" &
  local peer=$!

  # Both displays, lossless. ffmpeg starts grabbing some seconds before its
  # output file shows anything, so a master's own time zero is found at the
  # end: its duration minus the moment it was told to stop (offsets.env).
  rm -f "${dir}/master.mkv" "${dir}/cloudtak-master.mkv" "${dir}/events.tsv"
  docker exec -d "${name}" bash -c "exec ffmpeg -nostdin -loglevel warning -y -f x11grab -draw_mouse 1 -framerate 30 \
    -video_size 1920x1080 -i :99 -t 200 -c:v libx264 -preset ultrafast -qp 0 '${dir}/master.mkv' >'${dir}/ffmpeg.log' 2>&1"
  ffmpeg -nostdin -loglevel warning -y -f x11grab -draw_mouse 0 -framerate 30 -video_size 1920x1080 \
    -i "${cloudtak_display}" -t 200 -c:v libx264 -preset ultrafast -qp 0 "${dir}/cloudtak-master.mkv" >"${dir}/cloudtak-ffmpeg.log" 2>&1 &
  recorder=$!
  for _ in $(seq 400); do [[ -s "${dir}/master.mkv" && -s "${dir}/cloudtak-master.mkv" ]] && break; sleep 0.05; done
  t0="$(date +%s.%N)"   # zero of events.tsv

  mark_at() { printf '%.2f\t%s\t%s\n' "$(awk -v a="$1" -v b="${t0}" 'BEGIN{print a-b}')" "$(date -u -d "@$1" +%T)" "$2" >>"${dir}/events.tsv"; }
  mark() { mark_at "$(date +%s.%N)" "$1"; }
  takx() { docker exec -e DISPLAY=:99 "${name}" bash -c 'click() { xdotool mousemove --sync "$1" "$2"; sleep 0.35; xdotool click 1; }; '"$1"; }
  ups() { grep -c 'Stream connection to ssl:.* is up' "${log}" || true; }

  sleep 2
  mark "recording starts: TAKX with the TAK Server dock open, connection disabled, status red; CloudTAK signed in, overview of the South Atlantic"
  sleep 2.5
  before="$(ups)"
  mark "TAKX: tick the connection's enabled box"
  takx 'click 1769 241'
  for _ in $(seq 100); do [[ "$(ups)" -gt "${before}" ]] && break; sleep 0.1; done
  mark "TAKX client log: stream connection is up ($(ups) > ${before})"
  takx 'xdotool mousemove --sync 1500 620'
  sleep 4

  mark "CloudTAK: Geometry Editing > Coordinate Input, name SYNTHETIC-PEER-${n}, position -10, -25"
  "${cloudtak}" place "SYNTHETIC-PEER-${n}" -10 -25 >"${dir}/cloudtak-place.json" 2>"${dir}/cloudtak-place.err" ||
    mark "CloudTAK: place FAILED (see cloudtak-place.err)"
  if [[ -s "${dir}/cloudtak-place.json" ]]; then
    mark_at "$(date -u -d "$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["broadcast_clicked_utc"])' "${dir}/cloudtak-place.json")" +%s.%N)" \
      "CloudTAK: Share > Broadcast To All Users clicked"
  fi
  sleep 1.5
  mark "TAKX: hover the CloudTAK marker on the globe: label SYNTHETIC-PEER-${n}"
  takx "xdotool mousemove --sync $((peer_x - 40)) $((peer_y - 30)); sleep 0.3; xdotool mousemove --sync ${peer_x} ${peer_y}"
  sleep 4.5
  "${cloudtak}" close >/dev/null
  "${cloudtak}" view -20 -20 3.2 >/dev/null   # browser back to the overview

  mark "TAKX: Point Dropper: open"
  takx 'click 744 95'; sleep 3
  mark "TAKX: Point Dropper: type the prefix SYNTHETIC-TAKX and the index ${n}"
  takx "click 1505 302; sleep 0.3; xdotool type --delay 60 SYNTHETIC-TAKX; sleep 0.3; click 1650 302; sleep 0.3; xdotool type --delay 60 ${n}; sleep 0.4"
  mark "TAKX: Point Dropper: choose the neutral affiliation"
  takx 'click 1558 352'; sleep 1
  mark "TAKX: Point Dropper: type the location -30.0, -15.0 (open South Atlantic)"
  takx 'click 1660 544; sleep 0.3; xdotool type --delay 60 -- "-30.0, -15.0"; sleep 0.8'
  mark "TAKX: Point Dropper: OK, the marker is placed"
  takx 'click 1732 602; sleep 3; xdotool key Escape; sleep 1'
  mark "TAKX: placed marker: radial menu > details"
  takx 'click 625 841; sleep 2.5; click 793 695; sleep 2.5'
  mark "TAKX: Device Configuration: name the marker SYNTHETIC-TAKX-${n}"
  takx "click 1125 233; sleep 0.3; xdotool key ctrl+a; xdotool type --delay 60 SYNTHETIC-TAKX-${n}; sleep 0.8; click 1046 918; sleep 2.5"
  mark "TAKX: named marker: open its radial menu"
  takx 'click 733 602; sleep 2.5'
  mark "TAKX: radial menu: Send"
  takx 'click 817 527; sleep 2.5'
  mark "TAKX: Send As Cot: Broadcast"
  takx 'click 961 745; sleep 1; xdotool mousemove --sync 700 580; sleep 0.3; xdotool mousemove --sync 733 603'
  sleep 4.5

  mark "CloudTAK: search box, type SYNTHETIC-TAKX-${n}, select the map feature, open its details"
  "${cloudtak}" find "SYNTHETIC-TAKX-${n}" 30 >"${dir}/cloudtak-find.json" 2>"${dir}/cloudtak-find.err" ||
    mark "CloudTAK: find FAILED (see cloudtak-find.err)"
  sleep 3
  mark "CloudTAK: back to the overview, both markers on the map"
  "${cloudtak}" view -20 -20 3.2 >/dev/null
  sleep 2
  mark "TAKX: TAK Server dock: open again, connection still green"
  takx 'click 1720 95'; sleep 4.5
  mark "driving ends"
  docker exec "${name}" pkill -INT -x ffmpeg || true
  end_takx="$(date +%s.%N)"
  kill -INT "${recorder}" 2>/dev/null || true
  end_browser="$(date +%s.%N)"
  wait "${recorder}" || true
  for _ in $(seq 50); do docker exec "${name}" pgrep -x ffmpeg >/dev/null || break; sleep 0.2; done
  mark "recording ends"
  lead() { # master, stop time: seconds from the master's first frame to the zero of events.tsv
    awk -v d="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$1")" -v e="$2" -v z="${t0}" 'BEGIN{printf "%.2f", d - (e - z)}'
  }
  printf 'TAKX_LEAD=%s\nCLOUDTAK_LEAD=%s\n' "$(lead "${dir}/master.mkv" "${end_takx}")" "$(lead "${dir}/cloudtak-master.mkv" "${end_browser}")" >"${dir}/offsets.env"

  wait "${peer}" || true
  wait "${outsider}" || true
  [[ -s "${dir}/master.mkv" && -s "${dir}/cloudtak-master.mkv" && -s "${dir}/events.tsv" ]] ||
    { status failed 0 "a master recording is missing; see ${dir}"; exit 1; }
  "${connect}" evidence stream --name "capture-stream" >/dev/null || true
  "${connect}" evidence stream --identity takx-cloudtak --name "capture-stream-cloudtak" >/dev/null || true
  "${connect}" evidence search "SYNTHETIC-TAKX-${n}" --name "capture-takx-marker" >/dev/null || true
  "${connect}" evidence search "SYNTHETIC-PEER-${n}" --name "capture-peer-marker" >/dev/null || true
  echo "${attempt}" >"${root}/connect/capture-current"
  status recorded 0 "attempt ${attempt}: masters recorded; run finish"
  echo "capture-connected: recorded ${dir}/master.mkv and cloudtak-master.mkv" >&2
}

do_finish() {
  local attempt dir start
  attempt="$(cat "${root}/connect/capture-current")"; dir="${root}/connect/${attempt}/capture"; start=$(date +%s)
  # shellcheck disable=SC2016
  "${run}" --runtime --mount "${media}:${media}" -e "DIR=${dir}" -e "OUT=${media}" -- bash -euo pipefail -c '
    at() { awk -F"\t" -v k="$1" "index(\$3,k)==1{print \$1; exit}" "$DIR/events.tsv"; }
    enc() { # output start duration
      ffmpeg -nostdin -loglevel error -y -ss "$2" -t "$3" -i "$DIR/$MASTER" -an -vf fps=30,format=yuv420p \
        -c:v libx264 -preset slow -crf 16 -movflags +faststart "$OUT/$1"
    }
    MASTER=master.mkv
    rm -f "$OUT"/takx57-connected-*
    . "$DIR/offsets.env"
    # A time of events.tsv on a master: add the lead of that master, then shift.
    on() { awk -v t="$1" -v l="$2" -v s="$3" "BEGIN{t+=l+s; if (t<0) t=0; printf \"%.2f\", t}"; }
    takx() { on "$(at "$1")" "$TAKX_LEAD" "$2"; }
    browser() { on "$(at "$1")" "$CLOUDTAK_LEAD" "$2"; }
    # The raw takes are the whole masters, uncut.
    enc takx57-connected-raw-take-1080p30.mp4 0 100000
    enc takx57-connected-clip-1-status-goes-green.mp4 "$(takx "TAKX: tick" -1.5)" 7
    enc takx57-connected-clip-2-cloudtak-marker-arrives.mp4 "$(takx "CloudTAK: Share > Broadcast" -2)" 10
    enc takx57-connected-clip-3-takx-marker-placed.mp4 "$(takx "TAKX: Point Dropper: type the prefix" -0.5)" 10
    enc takx57-connected-clip-4-takx-marker-named.mp4 "$(takx "TAKX: placed marker: radial menu" -0.5)" 10
    enc takx57-connected-clip-5-takx-marker-broadcast.mp4 "$(takx "TAKX: named marker: open its radial menu" -1)" 10
    MASTER=cloudtak-master.mkv
    enc takx57-connected-cloudtak-raw-take-1080p30.mp4 0 100000
    enc takx57-connected-cloudtak-clip-1-peer-marker-created.mp4 "$(browser "CloudTAK: Geometry Editing" 1.5)" 10
    enc takx57-connected-cloudtak-clip-2-peer-marker-broadcast.mp4 "$(browser "CloudTAK: Share > Broadcast" -6.5)" 9
    enc takx57-connected-cloudtak-clip-3-takx-marker-arrives.mp4 "$(browser "TAKX: Send As Cot: Broadcast" -1)" 10
    enc takx57-connected-cloudtak-clip-4-takx-marker-found.mp4 "$(browser "CloudTAK: search box" 1)" 10
    mkdir -p "$OUT/frames"; rm -f "$OUT"/frames/takx57-connected-*
    for f in "$OUT"/takx57-connected-*.mp4; do
      b="$(basename "$f" .mp4)"
      ffprobe -v error -select_streams v:0 -count_frames -show_entries \
        stream=codec_name,profile,pix_fmt,width,height,r_frame_rate,avg_frame_rate,nb_read_frames:format=duration,size \
        -of json "$f" >"$OUT/$b.ffprobe.json"
      ffmpeg -nostdin -loglevel error -i "$f" -vf signalstats,metadata=print:file="$OUT/$b.signalstats.txt" -f null -
      ffmpeg -nostdin -hide_banner -i "$f" -vf "freezedetect=n=0.001:d=2,blackdetect=d=0.5:pix_th=0.05" -f null - 2>&1 \
        | grep -E "freeze_(start|duration|end)|black_start" >"$OUT/$b.detect.txt" || true
      d="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")"
      for p in 0.10 0.35 0.60 0.85; do
        t="$(awk -v d="$d" -v p="$p" "BEGIN{printf \"%.2f\", d*p}")"
        ffmpeg -nostdin -loglevel error -y -ss "$t" -i "$f" -frames:v 1 "$OUT/frames/$b-at-${t}s.png"
      done
    done
    { printf "# seconds (add %s for the position in the TAKX takes, %s in the CloudTAK takes)\tUTC\taction\n" "$TAKX_LEAD" "$CLOUDTAK_LEAD"
      cat "$DIR/events.tsv"; } >"$OUT/takx57-connected-events.tsv"
  '
  python3 -I - "${media}" "${dir}" "${attempt}" <<'PY'
import glob, hashlib, json, os, re, sys
media, source, attempt = sys.argv[1:]
report = {'attempt': attempt, 'masters': [os.path.join(source, 'master.mkv'), os.path.join(source, 'cloudtak-master.mkv')], 'files': {}}
ok = True
for path in sorted(glob.glob(os.path.join(media, 'takx57-connected-*.mp4'))):
    base = path[:-4]
    probe = json.load(open(base + '.ffprobe.json'))
    stream = probe['streams'][0]
    values = {}
    for line in open(base + '.signalstats.txt'):
        match = re.search(r'lavfi\.signalstats\.(YMIN|YMAX|YAVG)=([0-9.]+)', line)
        if match:
            values.setdefault(match.group(1), []).append(float(match.group(2)))
    detect = open(base + '.detect.txt').read()
    frozen = sum(float(x) for x in re.findall(r'freeze_duration: ([0-9.]+)', detect))
    duration = float(probe['format']['duration'])
    item = {
        'sha256': hashlib.sha256(open(path, 'rb').read()).hexdigest(),
        'bytes': os.path.getsize(path), 'duration_s': round(duration, 2),
        'codec': stream['codec_name'], 'profile': stream.get('profile'), 'pix_fmt': stream['pix_fmt'],
        'width': stream['width'], 'height': stream['height'], 'r_frame_rate': stream['r_frame_rate'],
        'frames': int(stream['nb_read_frames']),
        'luma_min': min(values['YMIN']), 'luma_max': max(values['YMAX']),
        'luma_avg_range': [round(min(values['YAVG']), 2), round(max(values['YAVG']), 2)],
        'frozen_seconds_freezedetect_n0.001_d2': round(frozen, 2),
        'black_segments': len(re.findall('black_start', detect)),
    }
    item['checks'] = {
        'h264_yuv420p_1920x1080_30fps': (stream['codec_name'], stream['pix_fmt'], stream['width'], stream['height'],
                                         stream['r_frame_rate']) == ('h264', 'yuv420p', 1920, 1080, '30/1'),
        'non_blank': item['luma_max'] - item['luma_min'] > 16,
        'has_motion': item['luma_avg_range'][1] - item['luma_avg_range'][0] > 0.05 and frozen < duration,
        'clip_length_5_to_10_s': 'clip' not in path or 5 <= duration <= 10.05,
    }
    ok = ok and all(item['checks'].values())
    report['files'][os.path.basename(path)] = item
report['all_checks_passed'] = ok
with open(os.path.join(media, 'capture-connected.json'), 'w') as stream:
    json.dump(report, stream, indent=1)
    stream.write('\n')
for name, item in report['files'].items():
    print(name, item['duration_s'], item['frames'], item['checks'], 'frozen', item['frozen_seconds_freezedetect_n0.001_d2'])
sys.exit(0 if ok else 1)
PY
  status ok "$(($(date +%s) - start))" \
    "attempt ${attempt}: lossless x11grab master and H.264 yuv420p 1920x1080 30 fps deliverables in demo/out/takx (git-ignored, never committed); ffprobe, signalstats and freezedetect checks passed (capture-connected.json). 'ok' is the automatic media check only: what the frames show and the connection evidence are in STATUS.md and demo/out/takx/PROVENANCE.md" \
    "${media}/capture-connected.json" "${dir}/master.mkv" "${dir}/cloudtak-master.mkv" "${dir}/events.tsv" \
    "${dir}/cloudtak-place.json" "${dir}/cloudtak-find.json" "${dir}/peer.json" "${dir}/outsider.json"
}

case "${1:-}" in
  record) do_record;;
  finish) do_finish;;
  all) do_record; do_finish;;
  -h|--help|"") sed -n '5,62p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) echo "capture-connected.sh: unknown target '$1'" >&2; exit 2;;
esac
