#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, connected capture: record the TAKX 5.7 prototype while it
# is connected to the local TAK Server of ../server/ (README.md there).
#
#   record   1. start a fresh connected attempt (../server/connect.sh start)
#            2. NOT recorded: add the TAK Server connection through the
#               client's own form (../server/connect-configure.sh; the form
#               has masked password fields), wait for the stream, then untick
#               the connection's 'enabled' box so the take can start
#               disconnected
#            3. start two more real streaming clients on the host
#               (../server/connect_peer.py): takx-peer (same group, sends
#               SYNTHETIC-PEER-<n> on a signal, records what it receives) and
#               takx-outsider (unrelated group, silent, records what it
#               receives)
#            4. record the X display losslessly (ffmpeg x11grab, 1920x1080,
#               30 fps) while xdotool drives the real UI: tick 'enabled'
#               (status goes green), the peer marker arrives on the globe,
#               Point Dropper places SYNTHETIC-TAKX <n>, the marker's radial
#               menu > Send > Broadcast sends it to the server
#            5. collect the server-side records (../server/connect_evidence.py)
#   finish   encode the deliverables (H.264 yuv420p 1920x1080 30 fps) from
#            the lossless master, verify them with ffprobe, signalstats,
#            freezedetect and blackdetect, extract review frames
#   all      record, then finish
#
# <n> is the attempt number. Both markers are at typed synthetic positions in
# the open South Atlantic. The credential form is closed before the recording
# starts and is never reopened; no password, key or certificate body is
# displayed, logged or written by this script.
#
# Layout:
#   $TAKX_PROTO_ROOT/connect/<attempt>/capture/master.mkv, events.tsv,
#       peer.json, outsider.json; ../evidence/*.json
#   demo/out/takx/ (git-ignored)  takx57-connected-*.mp4, frames/,
#       capture-connected.json
#   $TAKX_PROTO_ROOT/status/capture-connected.json
#
# TAKX_CAPTURE_PEER_XY="x y" is where the peer marker appears on the globe of
# a new project (found by a rehearsal; only used to hover it for its label).
# The Kernel is public 8.30.5 plus project patches and CommonCommo is 1.17.2
# patched, not the private 8.57.11 / 1.20.0. The coordinates are those of
# this prototype's 1920x1080 session; this is not a general UI driver.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="$repo/takx/desktop-prototype/run-in-toolchain.sh"
server="$repo/takx/desktop-prototype/server"
connect="$server/connect.sh"
media="$repo/demo/out/takx"
name=takx-connect
read -r peer_x peer_y <<<"${TAKX_CAPTURE_PEER_XY:-755 687}"

mkdir -p "$root"/{connect,status} "$media"
# shellcheck disable=SC1091
source "$root/server/endpoints.env"

status() { # status seconds notes outputs...
  python3 -I - "$root/status/capture-connected.json" "$@" <<'PY'
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
  before="$(grep -c "$pattern" "$log" || true)"
  shift 3; "$@"
  for _ in $(seq "$budget"); do
    now="$(grep -c "$pattern" "$log" || true)"
    ((now > before)) && return 0
    sleep 1
  done
  return 1
}

do_record() {
  local attempt n dir log
  "$connect" start
  attempt="$(cat "$root/connect/current")"; n="${attempt#connect-}"
  dir="$root/connect/$attempt/capture"; log="$root/logs/$attempt.log"
  mkdir -p "$dir"
  status running 0 "attempt $attempt: booting"
  for _ in $(seq 100); do docker exec -e DISPLAY=:99 "$name" xdotool search --onlyvisible --name 'TAKX 5.7' >/dev/null 2>&1 && break; sleep 3; done
  sleep 50   # project load and first globe frames

  # Off the record: the credential form.
  await_log 'Stream connection to ssl:.* is up' 60 "$log" "$server/connect-configure.sh" takx-desktop ||
    { status failed 0 "attempt $attempt: no 'Stream connection ... is up' in $log after the form"; exit 1; }
  sleep 6
  await_log 'TAK Server Disconnected' 20 "$log" docker exec -e DISPLAY=:99 "$name" bash -c \
    'xdotool mousemove --sync 1769 241; sleep 0.4; xdotool click 1; sleep 0.5; xdotool mousemove --sync 1500 700' ||
    { status failed 0 "attempt $attempt: the connection did not report Disconnected after unticking 'enabled'"; exit 1; }
  sleep 5   # let the notification toast leave the screen

  # Two more real clients. The peer starts sending when the drive says so.
  rm -f "$dir/peer-go"
  python3 -I "$server/connect_peer.py" "$root/secrets/takx-outsider" --connect "$PASSTHROUGH_GATEWAY_IP" \
    --listen 110 --out "$dir/outsider.json" >/dev/null 2>"$dir/outsider.err" &
  local outsider=$!
  ( for _ in $(seq 600); do [[ -e "$dir/peer-go" ]] && break; sleep 0.2; done
    exec python3 -I "$server/connect_peer.py" "$root/secrets/takx-peer" --connect "$PASSTHROUGH_GATEWAY_IP" \
      --listen 75 --marker "SYNTHETIC-PEER-$n" --type a-u-G --lat -10 --lon -25 --out "$dir/peer.json" ) \
    >/dev/null 2>"$dir/peer.err" &
  local peer=$!

  docker exec -i -e DISPLAY=:99 -e DIR="$dir" -e LOG="$log" -e N="$n" -e PX="$peer_x" -e PY="$peer_y" "$name" bash -s <<'DRIVE' >"$dir/drive.log" 2>&1
set -u
ffmpeg -nostdin -loglevel warning -y -f x11grab -draw_mouse 1 -framerate 30 -video_size 1920x1080 -i :99 \
  -t 120 -c:v libx264 -preset ultrafast -qp 0 "$DIR/master.mkv" >"$DIR/ffmpeg.log" 2>&1 &
recorder=$!
start=$(date +%s.%N)
mark() { printf '%.2f\t%s\t%s\n' "$(echo "$(date +%s.%N) - $start" | awk '{print $1 - $3}')" "$(date -u +%T)" "$1" >>"$DIR/events.tsv"; }
click() { xdotool mousemove --sync "$1" "$2"; sleep 0.35; xdotool click 1; }
ups() { grep -c 'Stream connection to ssl:.* is up' "$LOG" || true; }

sleep 1.5
mark "recording starts: TAK Server dock open, connection disabled, status red"
sleep 2.5
before=$(ups)
mark "TAK Server: tick the connection's enabled box"
click 1769 241
for _ in $(seq 100); do [ "$(ups)" -gt "$before" ] && break; sleep 0.1; done
mark "client log: stream connection is up ($(ups) > $before)"
xdotool mousemove --sync 1500 620
sleep 4.5
mark "peer: takx-peer starts sending SYNTHETIC-PEER-$N"
touch "$DIR/peer-go"
sleep 6
mark "hover the peer marker: label SYNTHETIC-PEER-$N"
xdotool mousemove --sync $((PX - 40)) $((PY - 30)); sleep 0.3; xdotool mousemove --sync "$PX" "$PY"
sleep 4

mark "Point Dropper: open"
click 744 95; sleep 3
mark "Point Dropper: type the prefix SYNTHETIC-TAKX and the index $N"
click 1505 302; sleep 0.3; xdotool type --delay 60 "SYNTHETIC-TAKX"; sleep 0.3
click 1650 302; sleep 0.3; xdotool type --delay 60 "$N"; sleep 0.4
mark "Point Dropper: choose the neutral affiliation"
click 1558 352; sleep 1
mark "Point Dropper: type the location -30.0, -15.0 (open South Atlantic)"
click 1660 544; sleep 0.3; xdotool type --delay 60 -- "-30.0, -15.0"; sleep 0.8
mark "Point Dropper: OK, the marker is placed"
click 1732 602; sleep 3
xdotool key Escape; sleep 1
mark "placed marker: open its radial menu"
click 625 841; sleep 2.5
mark "radial menu: Send"
click 817 527; sleep 2.5
mark "Send As Cot: Broadcast"
click 961 745; sleep 2
xdotool mousemove --sync 700 580; sleep 0.3; xdotool mousemove --sync 733 603
sleep 3.5
mark "TAK Server dock: open again, connection still green"
click 1720 95; sleep 4.5
mark "driving ends"
kill -INT "$recorder" 2>/dev/null
wait "$recorder"
mark "recording ends"
DRIVE
  wait "$peer" || true
  wait "$outsider" || true
  [[ -s "$dir/master.mkv" && -s "$dir/events.tsv" ]] || { status failed 0 "no master recording; see $dir/drive.log"; exit 1; }
  "$connect" evidence stream --name "capture-stream" >/dev/null || true
  "$connect" evidence search "SYNTHETIC-TAKX $n" --name "capture-takx-marker" >/dev/null || true
  "$connect" evidence cot "SYNTHETIC-PEER-$n" --name "capture-peer-marker" >/dev/null || true
  echo "$attempt" >"$root/connect/capture-current"
  status recorded 0 "attempt $attempt: master recorded; run finish"
  echo "capture-connected: recorded $dir/master.mkv" >&2
}

do_finish() {
  local attempt dir start
  attempt="$(cat "$root/connect/capture-current")"; dir="$root/connect/$attempt/capture"; start=$(date +%s)
  # shellcheck disable=SC2016
  "$run" --runtime --mount "$media:$media" -e "DIR=$dir" -e "OUT=$media" -- bash -euo pipefail -c '
    at() { awk -F"\t" -v k="$1" "index(\$3,k)==1{print \$1; exit}" "$DIR/events.tsv"; }
    enc() { # output start duration
      ffmpeg -nostdin -loglevel error -y -ss "$2" -t "$3" -i "$DIR/master.mkv" -an -vf fps=30,format=yuv420p \
        -c:v libx264 -preset slow -crf 16 -movflags +faststart "$OUT/$1"
    }
    back() { awk -v t="$1" -v b="$2" "BEGIN{t-=b; if (t<0) t=0; printf \"%.2f\", t}"; }
    rm -f "$OUT"/takx57-connected-*
    # The raw take is the whole master, uncut.
    enc takx57-connected-raw-take-1080p30.mp4 0 100000
    enc takx57-connected-clip-1-status-goes-green.mp4 "$(back "$(at "TAK Server: tick")" 1.5)" 7
    enc takx57-connected-clip-2-peer-marker-arrives.mp4 "$(back "$(at "peer: takx-peer starts")" 1)" 10
    enc takx57-connected-clip-3-takx-marker-placed.mp4 "$(back "$(at "Point Dropper: type the prefix")" 0.5)" 10
    enc takx57-connected-clip-4-takx-marker-broadcast.mp4 "$(back "$(at "placed marker: open its radial menu")" 1)" 10
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
    { printf "# seconds in the master\tUTC\taction\n"; cat "$DIR/events.tsv"; } >"$OUT/takx57-connected-events.tsv"
  '
  python3 -I - "$media" "$dir" "$attempt" <<'PY'
import glob, hashlib, json, os, re, sys
media, source, attempt = sys.argv[1:]
report = {'attempt': attempt, 'master': os.path.join(source, 'master.mkv'), 'files': {}}
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
    "attempt $attempt: lossless x11grab master and H.264 yuv420p 1920x1080 30 fps deliverables in demo/out/takx (git-ignored, never committed); ffprobe, signalstats and freezedetect checks passed (capture-connected.json). 'ok' is the automatic media check only: what the frames show and the connection evidence are in STATUS.md and demo/out/takx/PROVENANCE.md" \
    "$media/capture-connected.json" "$dir/master.mkv" "$dir/events.tsv" "$dir/peer.json" "$dir/outsider.json"
}

case "${1:-}" in
  record) do_record;;
  finish) do_finish;;
  all) do_record; do_finish;;
  -h|--help|"") sed -n '5,46p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) echo "capture-connected.sh: unknown target '$1'" >&2; exit 2;;
esac
