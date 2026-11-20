#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Render the civilian first-responder hero trailer and transparent TU Fieldmark alpha overlays."""

from __future__ import annotations

import argparse
import math
import os
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30

# Exact polygon vertices from assets/tak-mark.svg (viewBox 0 0 96 96)
T_VERTS = [(12, 8), (96, 8), (84, 24), (56, 24), (56, 64), (40, 64), (40, 24), (0, 24)]
U_VERTS = [
    (14, 32), (32, 32), (32, 72), (64, 72), (64, 32),
    (82, 32), (82, 72), (66, 88), (30, 88), (14, 72),
]

AMBER = (229, 160, 13)
WHITE = (248, 250, 252)
SLATE = (203, 213, 225)
OBSIDIAN = (11, 14, 17)
BORDER = (51, 65, 85)


def load_font(bold: bool = False, mono: bool = False, size: int = 24) -> ImageFont.FreeTypeFont:
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ]
        if mono
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
    )
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


FONT_MONO_SM = load_font(bold=True, mono=True, size=20)
FONT_MONO_MD = load_font(bold=True, mono=True, size=24)
FONT_TITLE_HUD = load_font(bold=True, mono=False, size=30)
FONT_WORD_LG = load_font(bold=True, mono=False, size=84)
FONT_WORD_SUB = load_font(bold=False, mono=False, size=44)
FONT_URL = load_font(bold=False, mono=True, size=26)


def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1.0 - (1.0 - t) ** 3


def ease_out_back(t: float, s: float = 1.15) -> float:
    t = clamp(t)
    t1 = t - 1.0
    return 1.0 + (s + 1.0) * (t1 ** 3) + s * (t1 ** 2)


def draw_tu_mark(
    img: Image.Image,
    cx: float,
    cy: float,
    scale: float,
    t_dy: float,
    u_dy: float,
    alpha: float,
    clip_box: tuple[int, int, int, int] | None = None,
) -> None:
    if alpha <= 0.005:
        return
    ss = 2
    box_w = int(math.ceil(160 * scale))
    box_h = int(math.ceil(260 * scale))
    layer = Image.new("RGBA", (box_w * ss, box_h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ox = box_w / 2.0 - 48.0 * scale
    oy = box_h / 2.0 - 48.0 * scale
    a = int(round(clamp(alpha) * 255))

    t_pts = [((ox + x * scale) * ss, (oy + (y + t_dy) * scale) * ss) for x, y in T_VERTS]
    u_pts = [((ox + x * scale) * ss, (oy + (y + u_dy) * scale) * ss) for x, y in U_VERTS]
    d.polygon(t_pts, fill=(*WHITE, a))
    d.polygon(u_pts, fill=(*WHITE, a))

    down = layer.resize((box_w, box_h), Image.Resampling.LANCZOS)
    px = int(round(cx - box_w / 2.0))
    py = int(round(cy - box_h / 2.0))

    if clip_box is not None:
        x0, y0, x1, y1 = clip_box
        ix0, iy0 = max(px, x0), max(py, y0)
        ix1, iy1 = min(px + box_w, x1), min(py + box_h, y1)
        if ix1 > ix0 and iy1 > iy0:
            cropped = down.crop((ix0 - px, iy0 - py, ix1 - px, iy1 - py))
            img.alpha_composite(cropped, (ix0, iy0))
    else:
        img.alpha_composite(down, (px, py))


def render_lockin_frame(t: float, duration: float, show_url: bool = False) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fade_out = 1.0 - clamp((t - (duration - 0.5)) / 0.5) if t > duration - 0.5 else 1.0

    p_lock = clamp(t / 0.85)
    e_lock = ease_out_back(p_lock, s=0.9)
    t_dy = -86.0 * (1.0 - e_lock)
    u_dy = 86.0 * (1.0 - e_lock)
    mark_alpha = clamp(t / 0.22) * fade_out

    p_shift = ease_out_cubic((t - 0.95) / 0.60)
    mark_cx = 960.0 - 430.0 * p_shift
    mark_cy = 520.0

    if 0.72 <= t <= 1.45:
        p_ring = (t - 0.72) / 0.73
        r = int(round(80 + 135 * ease_out_cubic(p_ring)))
        ring_a = int(round((1.0 - p_ring) * 235 * fade_out))
        ring_w = max(2, int(round(7 * (1.0 - p_ring))))
        d.ellipse(
            [mark_cx - r, mark_cy - r, mark_cx + r, mark_cy + r],
            outline=(*AMBER, ring_a),
            width=ring_w,
        )

    p_rule = ease_out_cubic((t - 0.90) / 0.65)
    if p_rule > 0:
        half_len = int(round(680 * p_rule))
        ra = int(round(120 * fade_out))
        d.line([(960 - half_len, 330), (960 + half_len, 330)], fill=(*AMBER, ra), width=2)
        d.line([(960 - half_len, 730), (960 + half_len, 730)], fill=(*AMBER, int(ra * 0.7)), width=2)

    draw_tu_mark(img, mark_cx, mark_cy, 2.05, t_dy, u_dy, mark_alpha)

    p_word = ease_out_cubic((t - 1.10) / 0.65)
    if p_word > 0.01:
        text_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(text_layer)
        wx = 695
        a_main = int(round(255 * fade_out))
        a_sub = int(round(230 * fade_out))
        td.text((wx, 420), "ZERO-TRUST TACTICAL AWARENESS", font=FONT_MONO_MD, fill=(*AMBER, a_main))
        td.text((wx - 3, 456), "TAK Server", font=FONT_WORD_LG, fill=(*WHITE, a_main))
        td.text((wx, 554), "on UDS", font=FONT_WORD_SUB, fill=(*SLATE, a_sub))
        if show_url:
            td.text(
                (wx, 622),
                "github.com/jonathanagustin/uds-takserver",
                font=FONT_URL,
                fill=(*AMBER, int(215 * fade_out)),
            )
        wipe_x = int(round(wx + 860 * p_word))
        cropped = text_layer.crop((wx - 10, 400, max(wx, wipe_x), 680))
        img.alpha_composite(cropped, (wx - 10, 400))

    return img


def render_hud_frame(t: float, duration: float, tag: str, headline: str) -> Image.Image:
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    fade_in = ease_out_cubic(t / 0.25)
    fade_out = 1.0 - clamp((t - (duration - 0.45)) / 0.45) if t > duration - 0.45 else 1.0
    alpha = fade_in * fade_out
    if alpha <= 0.01:
        return img

    tag_bbox = d.textbbox((0, 0), tag, font=FONT_MONO_SM)
    head_bbox = d.textbbox((0, 0), headline, font=FONT_TITLE_HUD)
    text_w = max(tag_bbox[2] - tag_bbox[0], head_bbox[2] - head_bbox[0])

    pad_left = 72
    icon_slot_w = 112
    pad_right = 34
    card_w = min(W - pad_left * 2, icon_slot_w + text_w + pad_right)
    card_h = 116
    cx0 = pad_left + int(round(-28 * (1.0 - fade_in)))
    cy0 = H - 188
    cx1, cy1 = cx0 + card_w, cy0 + card_h

    d.rounded_rectangle(
        [cx0, cy0, cx1, cy1],
        radius=8,
        fill=(*OBSIDIAN, int(round(224 * alpha))),
        outline=(*BORDER, int(round(210 * alpha))),
        width=2,
    )
    d.rectangle([cx0, cy0 + 10, cx0 + 5, cy1 - 10], fill=(*AMBER, int(round(255 * alpha))))

    p_lock = ease_out_back(clamp((t - 0.08) / 0.72), s=0.85)
    draw_tu_mark(
        img,
        cx0 + 56,
        cy0 + card_h / 2.0,
        scale=0.72,
        t_dy=-70.0 * (1.0 - p_lock),
        u_dy=70.0 * (1.0 - p_lock),
        alpha=alpha,
        clip_box=(cx0 + 8, cy0 + 8, cx0 + 106, cy1 - 8),
    )

    tx = cx0 + icon_slot_w
    bar_max_w = card_w - icon_slot_w - pad_right
    p_bar = ease_out_cubic((t - 0.32) / 0.50)
    if p_bar > 0.01:
        d.rectangle([tx, cy0 + 51, tx + int(round(bar_max_w * p_bar)), cy0 + 54], fill=(*AMBER, int(round(240 * alpha))))

    p_text = ease_out_cubic((t - 0.42) / 0.52)
    if p_text > 0.01:
        tlayer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        td = ImageDraw.Draw(tlayer)
        td.text((tx, cy0 + 21), tag, font=FONT_MONO_SM, fill=(*AMBER, int(round(255 * alpha))))
        td.text((tx, cy0 + 63), headline, font=FONT_TITLE_HUD, fill=(*WHITE, int(round(255 * alpha))))
        wipe_right = min(cx1 - 12, int(round(tx + (text_w + 16) * p_text)))
        if wipe_right > tx:
            cropped = tlayer.crop((tx, cy0 + 10, wipe_right, cy1 - 8))
            img.alpha_composite(cropped, (tx, cy0 + 10))

    return img


def pipe_frames_to_webm(out_path: Path, n_frames: int, frame_fn) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "26",
        "-auto-alt-ref", "0",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    for i in range(n_frames):
        proc.stdin.write(frame_fn(i / FPS).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed rendering {out_path}")


def pipe_frames_to_mp4(out_path: Path, n_frames: int, frame_fn, bg_image: Image.Image) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
        "-i", "-",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdin is not None
    for i in range(n_frames):
        t = i / FPS
        zoom = 1.0 + 0.03 * (t / (n_frames / FPS))
        zw, zh = int(W * zoom), int(H * zoom)
        bg_z = bg_image.resize((zw, zh), Image.Resampling.BILINEAR)
        x0, y0 = (zw - W) // 2, (zh - H) // 2
        base = bg_z.crop((x0, y0, x0 + W, y0 + H)).convert("RGBA")
        base.alpha_composite(frame_fn(t))
        proc.stdin.write(base.convert("RGB").tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed rendering {out_path}")


def overlay_on_clip(src_clip: Path, start: float, dur: float, overlay_webm: Path, out_mp4: Path) -> None:
    vf = (
        f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1[bg];"
        f"[bg][1:v]overlay=0:0:shortest=1[v]"
    )
    subprocess.run([
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src_clip),
        "-c:v", "libvpx-vp9", "-i", str(overlay_webm),
        "-filter_complex", vf,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
        "-t", f"{dur:.3f}",
        str(out_mp4),
    ], check=True, stderr=subprocess.DEVNULL)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--veo-clip", type=Path, required=True, help="25s live-action responder B-roll MP4")
    parser.add_argument("--ui-clip", type=Path, required=True, help="Recorded CloudTAK/TAK Server UI MP4")
    parser.add_argument("--topo-bg", type=Path, default=Path("assets/tactical-topo.jpg"), help="Topographic outro image")
    parser.add_argument("--music-track", type=Path, default=None, help="Optional soundtrack MP3/WAV replacing segment audio")
    parser.add_argument("--out-dir", type=Path, required=True, help="Output directory for MP4, poster, and alpha WebMs")
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tak-civilian-trailer-") as tmp:
        work = Path(tmp)

        pipe_frames_to_webm(
            args.out_dir / "tu-lockin-stinger-alpha.webm",
            int(3.8 * FPS),
            lambda t: render_lockin_frame(t, 3.8, show_url=False),
        )
        pipe_frames_to_webm(
            args.out_dir / "tu-hud-lowerthird-alpha.webm",
            int(4.5 * FPS),
            lambda t: render_hud_frame(
                t, 4.5, "CLOUDTAK  //  KEYCLOAK SSO", "Shared situational awareness in one signed package"
            ),
        )

        chapters = [
            ("hud1.webm", 4.8, "FIELD TEAMS  //  ATAK & ITAK", "Live position and hazard sharing across every crew"),
            ("hud2.webm", 4.8, "INCIDENT COMMAND  //  CLOUDTAK + SSO", "Browser common operating picture with zero-trust auth"),
            ("hud3.webm", 11.5, "RECORDED DEMO  //  CLOUDTAK", "Create a map marker and open sharing controls"),
            ("hud4.webm", 4.8, "AIR-GROUND COORDINATION  //  MULTI-ARCH", "Native builds for amd64 and arm64"),
            ("hud5.webm", 4.8, "DISCONNECTED EDGE  //  ZARF AIR-GAP", "Signed Zarf packages for disconnected installs"),
        ]
        for fname, dur, tag, head in chapters:
            pipe_frames_to_webm(
                work / fname,
                int(dur * FPS),
                lambda t, d=dur, tg=tag, hd=head: render_hud_frame(t, d, tg, hd),
            )

        intro_ov = work / "intro_5s.webm"
        pipe_frames_to_webm(intro_ov, int(5.0 * FPS), lambda t: render_lockin_frame(t, 4.6, show_url=False))
        overlay_on_clip(args.veo_clip, 0.0, 5.0, intro_ov, work / "seg1.mp4")
        overlay_on_clip(args.veo_clip, 5.0, 5.0, work / "hud1.webm", work / "seg2.mp4")
        overlay_on_clip(args.veo_clip, 10.0, 5.0, work / "hud2.webm", work / "seg3.mp4")

        ui_vf = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=#0B0E11,fps={FPS},setsar=1[bg];"
            f"[bg][1:v]overlay=0:0:shortest=1[v]"
        )
        subprocess.run([
            "ffmpeg", "-y",
            "-ss", "1.0", "-t", "11.5", "-i", str(args.ui_clip),
            "-c:v", "libvpx-vp9", "-i", str(work / "hud3.webm"),
            "-f", "lavfi", "-t", "11.5", "-i", "aevalsrc=0.015*sin(2*PI*58*t)+0.008*sin(2*PI*116*t):s=48000:c=stereo",
            "-filter_complex", ui_vf,
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
            "-t", "11.5",
            str(work / "seg4.mp4"),
        ], check=True, stderr=subprocess.DEVNULL)

        overlay_on_clip(args.veo_clip, 15.0, 5.0, work / "hud4.webm", work / "seg5.mp4")
        overlay_on_clip(args.veo_clip, 20.0, 5.0, work / "hud5.webm", work / "seg6.mp4")

        bg_topo = Image.open(args.topo_bg).convert("RGB").resize((W, H), Image.Resampling.LANCZOS)
        bg_topo = Image.blend(bg_topo, Image.new("RGB", (W, H), OBSIDIAN), 0.58)
        outro_raw = work / "outro_raw.mp4"
        pipe_frames_to_mp4(outro_raw, int(4.8 * FPS), lambda t: render_lockin_frame(t, 5.5, show_url=True), bg_topo)
        subprocess.run([
            "ffmpeg", "-y",
            "-i", str(outro_raw),
            "-f", "lavfi", "-t", "4.8", "-i", "aevalsrc=0.018*sin(2*PI*62*t)*exp(-t*0.45):s=48000:c=stereo",
            "-vf", "fade=t=in:st=0:d=0.35,fade=t=out:st=4.2:d=0.6",
            "-af", "afade=t=in:st=0:d=0.3,afade=t=out:st=4.0:d=0.8",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
            str(work / "seg7.mp4"),
        ], check=True, stderr=subprocess.DEVNULL)

        concat_file = work / "concat.txt"
        concat_file.write_text("\n".join(f"file '{work / f'seg{i}.mp4'}'" for i in range(1, 8)) + "\n")
        out_mp4 = args.out_dir / "uds-takserver-civilian-trailer.mp4"
        if args.music_track is not None:
            subprocess.run([
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-i", str(args.music_track),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                "-af", "afade=t=in:st=0:d=0.25,afade=t=out:st=40.5:d=0.8",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
                "-shortest",
                "-movflags", "+faststart",
                str(out_mp4),
            ], check=True, stderr=subprocess.DEVNULL)
        else:
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
                "-movflags", "+faststart",
                str(out_mp4),
            ], check=True, stderr=subprocess.DEVNULL)

        subprocess.run([
            "ffmpeg", "-y", "-ss", "2.6", "-i", str(out_mp4),
            "-frames:v", "1", "-q:v", "2",
            str(args.out_dir / "civilian-trailer-poster.jpg"),
        ], check=True, stderr=subprocess.DEVNULL)


if __name__ == "__main__":
    main()
