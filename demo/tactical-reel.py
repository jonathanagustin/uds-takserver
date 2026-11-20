#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Weave the tactical topographic branding, Fieldmark logo, and synthesized
tactical soundscape around a recorded UI preview clip using ffmpeg.

Usage:
    python3 demo/tactical-reel.py --input /path/to/ui-preview.mp4 --output out.mp4 [--poster poster.jpg]
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TOPO = ROOT / "assets" / "tactical-topo.jpg"
WORDMARK = ROOT / "assets" / "tak-on-uds-dark.svg"
MARK = ROOT / "assets" / "tak-mark-dark.svg"

W, H, FPS = 1920, 1080, 30
INTRO_S = 3.5
OUTRO_S = 3.5
FADE_S = 0.6


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(out)


def rasterize_svg(svg: Path, width: int, out_png: Path) -> None:
    run([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(svg),
        "-vf", f"scale={width}:-1",
        "-frames:v", "1",
        str(out_png),
    ])


def render_slate(
    work: Path,
    name: str,
    wordmark_png: Path,
    kicker: str,
    subtitle: str,
    duration: float,
    zoom_in: bool,
) -> Path:
    """Render an animated 1080p Ken Burns tactical slate on the topographic backdrop."""
    out_mp4 = work / f"{name}.mp4"
    frames = int(round(duration * FPS))
    zoom_expr = (
        f"1.0+0.06*(on/{frames})"
        if zoom_in
        else f"1.06-0.06*(on/{frames})"
    )
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    mono = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
    if not Path(font).exists():
        font = ""
    font_opt = f":fontfile={font}" if font else ""
    mono_opt = f":fontfile={mono}" if Path(mono).exists() else font_opt

    # Escape colons/quotes for drawtext
    kicker_esc = kicker.replace(":", r"\:").replace("'", r"\'")
    sub_esc = subtitle.replace(":", r"\:").replace("'", r"\'")

    vf = (
        f"[0:v]scale=2304:1296:force_original_aspect_ratio=increase,crop=2304:1296,"
        f"zoompan=z='{zoom_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
        f"eq=brightness=-0.08:contrast=1.08,"
        f"vignette=PI/3.2,"
        # Subtle amber tactical top/bottom hairline rules
        f"drawbox=x=140:y=110:w={W-280}:h=2:color=0xE5A00D@0.55:t=fill,"
        f"drawbox=x=140:y={H-112}:w={W-280}:h=2:color=0xE5A00D@0.35:t=fill,"
        # Center dark scrim for razor-sharp wordmark contrast
        f"drawbox=x=240:y=290:w={W-480}:h=500:color=0x0B0E11@0.68:t=fill[bg];"
        f"[1:v]format=rgba,fade=t=in:st=0.25:d=0.6:alpha=1[wm];"
        f"[bg][wm]overlay=(W-w)/2:(H-h)/2-40,"
        f"drawtext=text='{kicker_esc}'{mono_opt}:fontsize=24:fontcolor=0xE5A00D:"
        f"x=(w-text_w)/2:y=(h/2)-185:alpha='if(lt(t,0.2),0,min(1,(t-0.2)/0.5))',"
        f"drawtext=text='{sub_esc}'{font_opt}:fontsize=30:fontcolor=0xCBD5E1:"
        f"x=(w-text_w)/2:y=(h/2)+130:alpha='if(lt(t,0.45),0,min(1,(t-0.45)/0.5))',"
        f"format=yuv420p[v]"
    )
    run([
        "ffmpeg", "-y", "-v", "error",
        "-loop", "1", "-i", str(TOPO),
        "-loop", "1", "-i", str(wordmark_png),
        "-t", f"{duration:.2f}",
        "-filter_complex", vf,
        "-map", "[v]",
        "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(out_mp4),
    ])
    return out_mp4


def build_audio(work: Path, total_duration: float, intro_s: float, body_s: float) -> Path:
    """Synthesize a subtle, defense-grade tactical ambient soundscape with clean UI telemetry pings."""
    out_wav = work / "tactical-audio.wav"
    t_body = intro_s - FADE_S
    t_outro = intro_s + body_s - 2 * FADE_S
    # Sub-bass 55 Hz + 82.41 Hz (A1 + E2 fifth) warm drone + gentle radar lock pings at intro, body, outro
    expr = (
        "0.045*sin(2*PI*55*t)*(0.85+0.15*sin(2*PI*0.35*t))"
        "+0.025*sin(2*PI*82.41*t)*(0.85+0.15*cos(2*PI*0.25*t))"
        "+0.012*sin(2*PI*110*t)*sin(2*PI*0.18*t)"
        # Crisp radar lock ping at t=0.45s
        "+0.12*between(t,0.45,0.85)*exp(-10*(t-0.45))*sin(2*PI*880*(t-0.45))"
        # Subtle transition tick entering UI footage
        f"+0.08*between(t,{t_body:.2f},{t_body+0.25:.2f})*exp(-16*(t-{t_body:.2f}))*sin(2*PI*660*(t-{t_body:.2f}))"
        # Warm low chime on outro reveal
        f"+0.10*between(t,{t_outro:.2f},{t_outro+0.90:.2f})*exp(-4.5*(t-{t_outro:.2f}))*sin(2*PI*440*(t-{t_outro:.2f}))"
    )
    af = (
        f"aevalsrc='{expr}':s=48000:d={total_duration:.2f},"
        f"lowpass=f=2400,"
        f"afade=t=in:st=0:d=0.6,"
        f"afade=t=out:st={max(0.1, total_duration - 1.2):.2f}:d=1.2,"
        f"aformat=channel_layouts=stereo"
    )
    run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", af,
        "-c:a", "pcm_s16le",
        str(out_wav),
    ])
    return out_wav


def assemble(src: Path, output: Path, poster: Path | None) -> None:
    body_s = probe_duration(src)
    total_s = INTRO_S + body_s + OUTRO_S - 2 * FADE_S

    with tempfile.TemporaryDirectory(prefix="tak-tactical-reel.") as tmp:
        work = Path(tmp)
        wm_png = work / "wordmark.png"
        mark_png = work / "mark.png"
        rasterize_svg(WORDMARK, 860, wm_png)
        rasterize_svg(MARK, 56, mark_png)

        intro = render_slate(
            work,
            "intro",
            wm_png,
            "ZERO-TRUST TACTICAL AWARENESS  //  UDS CORE",
            "Signed Zarf package  ·  Keycloak SSO  ·  CloudTAK + TAK Server",
            INTRO_S,
            zoom_in=True,
        )
        outro = render_slate(
            work,
            "outro",
            wm_png,
            "DEPLOY SIGNED  //  RUN AIR-GAPPED",
            "github.com/jonathanagustin/uds-takserver",
            OUTRO_S,
            zoom_in=False,
        )

        # Normalize UI recording to 1920x1080@30fps with subtle corner Fieldmark watermark
        body = work / "body.mp4"
        run([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(src),
            "-i", str(mark_png),
            "-filter_complex",
            (
                f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0B0E11,fps={FPS},setsar=1[base];"
                f"[1:v]format=rgba,colorchannelmixer=aa=0.72[mk];"
                f"[base][mk]overlay=W-w-28:H-h-24,format=yuv420p[v]"
            ),
            "-map", "[v]",
            "-an",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(body),
        ])

        audio = build_audio(work, total_s, INTRO_S, body_s)

        off1 = INTRO_S - FADE_S
        off2 = off1 + body_s - FADE_S
        output.parent.mkdir(parents=True, exist_ok=True)
        run([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(intro),
            "-i", str(body),
            "-i", str(outro),
            "-i", str(audio),
            "-filter_complex",
            (
                f"[0:v][1:v]xfade=transition=fade:duration={FADE_S}:offset={off1:.2f}[v01];"
                f"[v01][2:v]xfade=transition=fade:duration={FADE_S}:offset={off2:.2f},format=yuv420p[v]"
            ),
            "-map", "[v]",
            "-map", "3:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            str(output),
        ])

        if poster is not None:
            poster.parent.mkdir(parents=True, exist_ok=True)
            run([
                "ffmpeg", "-y", "-v", "error",
                "-ss", "2.0", "-i", str(intro),
                "-frames:v", "1", "-q:v", "2",
                str(poster),
            ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Build branded tactical demo reel with synthesized sound")
    parser.add_argument("--input", required=True, type=Path, help="Recorded UI preview MP4")
    parser.add_argument("--output", required=True, type=Path, help="Output MP4 path")
    parser.add_argument("--poster", type=Path, default=None, help="Optional poster JPG output path")
    args = parser.parse_args()
    assemble(args.input, args.output, args.poster)


if __name__ == "__main__":
    main()
