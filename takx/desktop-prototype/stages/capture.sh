#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, capture stage: record the booted TAKX 5.7 prototype.
#
#   record   boot a fresh attempt (boot.sh start --trust-sandbox-ca, with a
#            short delay before the JVM), record the X display losslessly
#            with ffmpeg x11grab at 1920x1080 30 fps while xdotool drives the
#            real UI, and log every action with its time in the recording
#   finish   encode the deliverables (H.264 yuv420p 1920x1080 30 fps) from the
#            lossless master, verify them with ffprobe, signalstats,
#            freezedetect and blackdetect, and extract review frames
#   all      record, then finish
#
# What is driven, all through the UI with the mouse and keyboard only:
# startup from the empty desktop; pan, wheel zoom and heading rotation of the
# globe; Maps & Favorites, switching the imagery layer to public USGS Imagery;
# Point Dropper, placing one neutral marker named SYNTHETIC-DEMO at the typed
# location 30 S 15 W in the open South Atlantic; the View, Range & Bearing and
# Help tabs. No server is connected, no credential dialog is opened and no
# real-world position of anything is entered. The recording stops when the
# driving ends (TAKX_CAPTURE_SECONDS, default 170, is only an upper bound).
#
# Layout:
#   $TAKX_PROTO_ROOT/capture/<attempt>/master.mkv   lossless x11grab master
#   $TAKX_PROTO_ROOT/capture/<attempt>/events.tsv   seconds <TAB> action
#   demo/out/takx/ (git-ignored)   takx57-*.mp4, frames/, capture.json
#   status/capture.json
#
# The media is never committed. The Kernel is public 8.30.5 plus project
# patches and CommonCommo is 1.17.2 patched, not the private 8.57.11 / 1.20.0.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
boot="${here}/boot.sh"
media="${repo}/demo/out/takx"
name=takx-boot
seconds="${TAKX_CAPTURE_SECONDS:-170}"

mkdir -p "${root}"/{capture,logs,status} "${media}"

status() { # status seconds notes outputs...
  python3 -I - "${root}/status/capture.json" "$@" <<'PY'
import json, os, sys
path, status, seconds, notes, *outputs = sys.argv[1:]
tmp = path + '.tmp.%d' % os.getpid()
with open(tmp, 'w') as stream:
    json.dump({'stage': 'capture', 'status': status, 'outputs': outputs,
               'seconds': int(seconds), 'notes': notes}, stream, indent=1)
    stream.write('\n')
os.replace(tmp, path)
PY
}

do_record() {
  local attempt dir
  "${boot}" stop
  TAKX_BOOT_ENV="TAKX_BOOT_DELAY_S=8 ${TAKX_BOOT_ENV:-}" "${boot}" start --trust-sandbox-ca
  attempt="$(cat "${root}/runs/current")"
  dir="${root}/capture/${attempt}"
  [[ ! -e "${dir}" ]] || { echo "capture: ${dir} exists" >&2; exit 1; }
  mkdir -p "${dir}"
  # Everything below runs inside the boot container, next to the X server.
  docker exec -i -e DISPLAY=:99 -e DIR="${dir}" -e SECONDS_TOTAL="${seconds}" "${name}" bash -s <<'DRIVE' >"${dir}/drive.log" 2>&1
set -u
for _ in $(seq 100); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.1; done
ffmpeg -nostdin -loglevel warning -y -f x11grab -draw_mouse 1 -framerate 30 -video_size 1920x1080 -i :99 \
  -t "$SECONDS_TOTAL" -c:v libx264 -preset ultrafast -qp 0 "$DIR/master.mkv" >"$DIR/ffmpeg.log" 2>&1 &
recorder=$!
start=$(date +%s.%N)
mark() { printf '%.2f\t%s\n' "$(echo "$(date +%s.%N) - $start" | awk '{print $1 - $3}')" "$1" >>"$DIR/events.tsv"; }
drag() { # button x y dx dy steps
  xdotool mousemove --sync "$2" "$3"; sleep 0.3; xdotool mousedown "$1"
  for _ in $(seq "$6"); do xdotool mousemove_relative --sync -- "$4" "$5"; sleep 0.04; done
  xdotool mouseup "$1"
}
wheel() { for _ in $(seq "$2"); do xdotool click "$1"; sleep 0.45; done; }
click() { xdotool mousemove --sync "$1" "$2"; sleep 0.35; xdotool click 1; }

mark "recording starts on the empty Xvfb desktop (openbox, no client yet)"
for _ in $(seq 600); do xdotool search --onlyvisible --name 'TAKX 5.7' >/dev/null 2>&1 && break; sleep 0.1; done
mark "TAKX main window is mapped"
sleep 13
mark "globe pan: left-button drag"
drag 1 820 560 14 4 28
sleep 1
mark "heading rotation: right-button drag"
drag 3 960 600 12 -5 20
sleep 1
mark "wheel zoom in"
xdotool mousemove --sync 960 600; wheel 4 5
sleep 1.5
mark "wheel zoom out"
wheel 5 5
sleep 1.5
mark "end of the raw take section"

mark "Maps & Favorites: open"
click 361 95; sleep 2.2
mark "Maps: enable USGS Imagery"
click 46 413; sleep 1.6
mark "Maps: disable USGS Imagery Topo"
click 46 391; sleep 4.5
mark "Maps: close panel"
click 117 151; sleep 1.5

mark "Point Dropper: open"
click 744 95; sleep 3
mark "Point Dropper: type the prefix SYNTHETIC-DEMO"
click 1505 302; sleep 0.3; xdotool type --delay 70 "SYNTHETIC-DEMO"; sleep 0.5
mark "Point Dropper: choose the neutral affiliation"
click 1558 352; sleep 1
mark "Point Dropper: type the location -30.0, -15.0 (open South Atlantic)"
click 1660 544; sleep 0.3; xdotool type --delay 70 -- "-30.0, -15.0"; sleep 0.8
mark "Point Dropper: OK"
click 1732 602; sleep 3
xdotool key Escape; sleep 0.8
mark "Point Dropper: close panel"
click 1583 149; sleep 2

mark "globe pan with the marker placed"
drag 1 1000 560 -8 5 24
sleep 2

mark "ribbon: View tab"
click 298 47; sleep 2.5
mark "ribbon: Range & Bearing tab"
click 468 47; sleep 2.5
mark "ribbon: Help tab"
click 565 47; sleep 2.5
mark "ribbon: Home tab"
click 64 47; sleep 2
mark "driving ends"
kill -INT "$recorder" 2>/dev/null
wait "$recorder"
mark "recording ends"
DRIVE
  [[ -s "${dir}/master.mkv" && -s "${dir}/events.tsv" ]] || { status failed 0 "no master recording; see ${dir}/drive.log"; exit 1; }
  echo "${attempt}" >"${root}/capture/current"
  echo "capture: recorded ${dir}/master.mkv" >&2
}

do_finish() {
  local attempt dir start
  attempt="$(cat "${root}/capture/current")"; dir="${root}/capture/${attempt}"; start=$(date +%s)
  # The script expands in the container's shell (DIR and OUT are passed with -e). Quoting it here would expand it now.
  # shellcheck disable=SC2016
  "${run}" --runtime --mount "${media}:${media}" -e "DIR=${dir}" -e "OUT=${media}" -- bash -euo pipefail -c '
    at() { awk -F"\t" -v k="$1" "index(\$2,k)==1{print \$1; exit}" "$DIR/events.tsv"; }
    enc() { # output start duration
      ffmpeg -nostdin -loglevel error -y -ss "$2" -t "$3" -i "$DIR/master.mkv" -an -vf fps=30,format=yuv420p \
        -c:v libx264 -preset slow -crf 16 -movflags +faststart "$OUT/$1"
    }
    # The raw take is one uncut stretch of the master: from 3 s before the
    # main window is mapped (still the empty desktop) to the end of the first
    # globe interaction, at most 59.5 s.
    raw_end="$(at "end of the raw take section")"
    mapped="$(at "TAKX main window is mapped")"
    raw_start="$(awk -v e="$raw_end" -v m="$mapped" "BEGIN{s=m-3; if (e-s>59.5) s=e-59.5; if (s<0) s=0; printf \"%.2f\", s}")"
    raw_length="$(awk -v e="$raw_end" -v s="$raw_start" "BEGIN{printf \"%.2f\", e-s}")"
    enc takx57-raw-take-1080p30.mp4 "$raw_start" "$raw_length"
    enc takx57-clip-1-globe-pan-zoom-rotate.mp4 "$(at "globe pan")" 9.5
    enc takx57-clip-2-maps-imagery-layer.mp4 "$(at "Maps & Favorites: open")" 10
    enc takx57-clip-3-point-dropper-synthetic-marker.mp4 "$(at "Point Dropper: type the prefix")" 9.5
    enc takx57-session-full-1080p30.mp4 0 100000
    mkdir -p "$OUT/frames"
    for f in "$OUT"/takx57-*.mp4; do
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
    # Events after the end of the master were driven but not recorded.
    length="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$DIR/master.mkv")"
    awk -F"\t" -v d="$length" -v s="$raw_start" -v e="$raw_end" "BEGIN{OFS=\"\t\"; print \"# seconds in the master recording\", \"action (raw take = \" s \" s to \" e \" s of the master)\"} \$1<=d{print} \$1>d{print \$1, \$2 \" [after the recording ended; not in the footage]\"}" \
      "$DIR/events.tsv" >"$OUT/takx57-events.tsv"
  '
  python3 -I - "${media}" "${dir}" "${attempt}" <<'PY'
import glob, hashlib, json, os, re, sys
media, source, attempt = sys.argv[1:]
report = {'attempt': attempt, 'master': os.path.join(source, 'master.mkv'), 'files': {}}
ok = True
for path in sorted(glob.glob(os.path.join(media, 'takx57-*.mp4'))):
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
        'has_motion': item['luma_avg_range'][1] - item['luma_avg_range'][0] > 0.5 and frozen < duration,
    }
    ok = ok and all(item['checks'].values())
    report['files'][os.path.basename(path)] = item
report['all_checks_passed'] = ok
with open(os.path.join(media, 'capture.json'), 'w') as stream:
    json.dump(report, stream, indent=1)
    stream.write('\n')
for name, item in report['files'].items():
    print(name, item['duration_s'], item['frames'], item['checks'], 'frozen', item['frozen_seconds_freezedetect_n0.001_d2'])
sys.exit(0 if ok else 1)
PY
  status ok "$(($(date +%s) - start))" \
    "attempt ${attempt}: lossless x11grab master and H.264 yuv420p 1920x1080 30 fps deliverables in demo/out/takx (git-ignored, never committed); ffprobe, signalstats and freezedetect checks passed (capture.json); Kernel 8.30.5 + project patches and CommonCommo 1.17.2 patched; what the frames show is described in demo/out/takx/PROVENANCE.md" \
    "${media}/capture.json" "${dir}/master.mkv" "${dir}/events.tsv"
}

case "${1:-}" in
  record) do_record;;
  finish) do_finish;;
  all) do_record; do_finish;;
  -h|--help|"") sed -n '5,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) echo "capture.sh: unknown target '$1'" >&2; exit 2;;
esac
