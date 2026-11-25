#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""The look of the demo videos, in one place: canvas, palette, fonts, the
window frame around each recording, title cards and lower-thirds.

    style.py fonts                         check the fonts (no fallbacks)
    style.py branding DIRECTORY             render title/endcard preview PNGs
    style.py background OUT.png            the canvas background
    style.py frame UNDER.png OVER.png MASK.png
                                           the window frame: shadow and title
                                           bar under the recording, border
                                           over it, and the recording's
                                           rounded-corner mask
    style.py card OUT.png TITLE SUBTITLE   a title card
    style.py lower OUT.png TEXT [TAG]      a lower-third; prints "X Y" where
                                           it goes on the canvas
    style.py still IN.png OUT.png          a still for the docs, in the frame
    style.py panel OUT.png TEXT TAG SCREEN_W SCREEN_H
                                           the caption beside the phone
                                           close-up; prints "X Y"
    style.py phone UNDER.png OVER.png MASK.png SCREEN_W SCREEN_H [TITLE SUBTITLE]
                                           the phone close-up: background and
                                           label, screen border, rounded-corner
                                           mask; prints "X Y W H" of the screen

demo/assemble.sh and demo/filters.py use these. Pillow draws everything, so
the sizes and colours below are exact.
"""

import io
import subprocess
from functools import lru_cache
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Canvas and timing.
W, H = 1920, 1080
FPS = 30
# The recording inside the window frame: about 85% of the canvas. The frame
# sits a little above centre so a lower-third has room under it.
CONTENT_W, CONTENT_H = 1632, 840
TITLE_BAR = 36
FRAME_X = (W - CONTENT_W) // 2
FRAME_Y = 40
CONTENT_X, CONTENT_Y = FRAME_X, FRAME_Y + TITLE_BAR
FRAME_W, FRAME_H = CONTENT_W, CONTENT_H + TITLE_BAR
RADIUS = 12

# Palette. Tactical Obsidian, Signal Amber, and Phosphor Emerald.
BG = "#0B0E11"
BG_EDGE = "#050709"
SURFACE = "#11161D"
AMBER = "#E5A00D"
AMBER_BRIGHT = "#F5B82E"
INDIGO = AMBER_BRIGHT
EMERALD = "#34D399"
HEADER = "#F8FAFC"
BODY = "#CBD5E1"
MUTED = "#8B98A8"
HAIRLINE = "#1F2937"
OVERLAY = (11, 14, 17, 236)  # rgba(11,14,17,0.93)
OVERLAY_BORDER = (229, 160, 13, 82)  # crisp amber hairline
FRAME_BORDER = (255, 255, 255, 36)  # rgba(255,255,255,0.14)
ACCENT_RULE = (229, 160, 13, 140)  # Signal Amber tactical rule
ACCENT_RULE_SOFT = (229, 160, 13, 84)

# Fonts, by fontconfig name. check_fonts() fails on a fallback.
FONTS = {
    "title": ("Inter:style=SemiBold", "Inter", "SemiBold"),
    "lower": ("Inter:style=Medium", "Inter", "Medium"),
    "mono": ("JetBrains Mono", "JetBrains Mono", None),
}
TITLE_SIZE = 64
SUBTITLE_SIZE = 30
LOWER_SIZE = 28
TEXT_MAX_W = int(W * 0.80)
LOWER_MAX_W = 1180
ASSETS = Path(__file__).resolve().parents[1] / "assets"
LOGO = ASSETS / "tak-on-uds-dark.svg"
MARK = ASSETS / "tak-mark-dark.svg"
TOPO = ASSETS / "tactical-topo.jpg"
REPOSITORY = "github.com/jonathanagustin/uds-takserver"


@lru_cache(maxsize=8)
def logo(width, mark=False):
    """Rasterize the canonical vector artwork; no recreated lettering/fonts."""
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(MARK if mark else LOGO), "-vf", f"scale={width}:-1",
        "-frames:v", "1", "-f", "image2pipe", "-c:v", "png", "-",
    ], capture_output=True, check=True)
    return Image.open(io.BytesIO(result.stdout)).convert("RGBA")


def watermark(image, x, y, width=200, mark=False):
    """Native artwork in frame chrome or canvas margin, outside recordings."""
    image.alpha_composite(logo(width, mark), (x, y))


def hex_rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def font_file(role):
    pattern, family, style = FONTS[role]
    out = subprocess.run(["fc-match", "-f", "%{file}\n%{family}\n%{style}", pattern],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    path, families, styles = out[0], out[1], out[2] if len(out) > 2 else ""
    if family not in families.split(","):
        sys.exit(f"font {pattern}: fontconfig falls back to {families} ({path})")
    if style and style not in styles and style.replace(" ", "") not in path:
        sys.exit(f"font {pattern}: no {style} face, got {styles} ({path})")
    return path


def font(role, size):
    return ImageFont.truetype(font_file(role), size)


def check_fonts():
    for role in FONTS:
        print(f"{role}: {font_file(role)}")


def _radial_gradient(width, height, inner_hex, outer_hex):
    small = Image.new("RGB", (max(1, width // 4), max(1, height // 4)))
    inner, outer = hex_rgb(inner_hex), hex_rgb(outer_hex)
    cx, cy = small.width / 2, small.height / 2
    reach = (cx ** 2 + cy ** 2) ** 0.5
    px = small.load()
    for y in range(small.height):
        for x in range(small.width):
            d = (((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / reach)
            t = max(0.0, (d - 0.30) / 0.70) ** 1.5
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(inner, outer))
    return small.resize((width, height), Image.BICUBIC).filter(ImageFilter.GaussianBlur(2))


def _vignette_mask(width, height):
    small = Image.new("L", (max(1, width // 4), max(1, height // 4)))
    cx, cy = small.width / 2, small.height / 2
    reach = (cx ** 2 + cy ** 2) ** 0.5
    px = small.load()
    for y in range(small.height):
        for x in range(small.width):
            d = (((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / reach)
            t = max(0.0, (d - 0.42) / 0.58) ** 1.7
            px[x, y] = round(min(210, 210 * t))
    return small.resize((width, height), Image.BICUBIC).filter(ImageFilter.GaussianBlur(4))


@lru_cache(maxsize=2)
def background():
    """Tactical Obsidian radial backdrop subtly layered with topographic contours."""
    base = _radial_gradient(W, H, BG, BG_EDGE)
    if TOPO.is_file():
        topo = Image.open(TOPO).convert("RGB")
        scale = max(W / topo.width, H / topo.height)
        tw, th = round(topo.width * scale), round(topo.height * scale)
        topo = topo.resize((tw, th), Image.LANCZOS)
        left, top = (tw - W) // 2, (th - H) // 2
        topo = topo.crop((left, top, left + W, top + H))
        base = Image.blend(base, topo, 0.22)
        vig = Image.new("RGBA", (W, H), hex_rgb(BG_EDGE) + (0,))
        vig.putalpha(_vignette_mask(W, H))
        base = Image.alpha_composite(base.convert("RGBA"), vig).convert("RGB")
    return base.copy()


def shadow(size, box, radius, offset_y, blur, alpha):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    x0, y0, x1, y1 = box
    ImageDraw.Draw(layer).rounded_rectangle((x0, y0 + offset_y, x1, y1 + offset_y), radius, fill=(0, 0, 0, alpha))
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _draw_chrome_bar(image, x, y, w, h, radius):
    """Draw the machined obsidian window header, status indicators, and separator."""
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((x, y, x + w - 1, y + h - 1), radius, fill=hex_rgb(SURFACE))
    # Crisp 1px separator under the title bar.
    sep_y = y + TITLE_BAR - 1
    draw.line([(x + 1, sep_y), (x + w - 2, sep_y)], fill=hex_rgb(HAIRLINE), width=1)
    # Subtle Signal Amber highlight along the top-left chrome edge.
    accent = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ad = ImageDraw.Draw(accent)
    ad.rectangle((x + radius, y, x + radius + 96, y + 1), fill=hex_rgb(AMBER) + (190,))
    dot_y = y + TITLE_BAR // 2
    dot_colors = (
        hex_rgb(EMERALD) + (210,),
        hex_rgb(MUTED) + (115,),
        hex_rgb(MUTED) + (80,),
    )
    for i, color in enumerate(dot_colors):
        cx = x + 22 + i * 18
        ad.ellipse((cx - 4.5, dot_y - 4.5, cx + 4.5, dot_y + 4.5), fill=color)
    image.alpha_composite(accent)


def frame():
    """UNDER: background, shadow (0 24px 48px rgba(0,0,0,.5)) and the machined
    obsidian title bar. OVER: the 1px border. MASK: the recording's rounded
    bottom corners (white shows the recording)."""
    box = (FRAME_X, FRAME_Y, FRAME_X + FRAME_W - 1, FRAME_Y + FRAME_H - 1)
    under = background().convert("RGBA")
    # A CSS blur radius of 48px is about a Gaussian sigma of 24.
    under.alpha_composite(shadow((W, H), box, RADIUS, 24, 24, 150))
    _draw_chrome_bar(under, FRAME_X, FRAME_Y, FRAME_W, FRAME_H, RADIUS)
    watermark(under, FRAME_X + FRAME_W - 42, FRAME_Y + 7, 22, mark=True)
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(over).rounded_rectangle(box, RADIUS, outline=FRAME_BORDER, width=1)
    mask = Image.new("L", (CONTENT_W, CONTENT_H), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle((0, -RADIUS * 2, CONTENT_W - 1, CONTENT_H - 1), RADIUS, fill=255)
    return under, over, mask


# The phone close-up: the device screen large on the right, on the
# background. On the left: a short label level with the top of the screen,
# and the current caption as a panel (panel()) in the middle.
PHONE_H = 940
PHONE_RIGHT = 96
PHONE_RADIUS = 28
PHONE_LABEL_SIZE = 40
PANEL_X = 120
PANEL_SIZE = 38


def phone_box(screen_w, screen_h):
    """x, y, w, h of the device screen in the close-up."""
    h = PHONE_H
    w = round(h * screen_w / screen_h) // 2 * 2
    return W - PHONE_RIGHT - w, (H - h) // 2, w, h


def phone(screen_w, screen_h, title="", subtitle=""):
    """UNDER: the background with TITLE and SUBTITLE on the left. OVER: the
    screen's 1px border. MASK: the screen's rounded corners."""
    x, y, w, h = phone_box(screen_w, screen_h)
    box = (x, y, x + w - 1, y + h - 1)
    under = background().convert("RGBA")
    under.alpha_composite(shadow((W, H), box, PHONE_RADIUS, 20, 24, 155))
    watermark(under, PANEL_X, H - 96, 200)
    draw = ImageDraw.Draw(under)
    left, max_w = PANEL_X, x - 96 - PANEL_X
    tfont, tlines = fitted("title", PHONE_LABEL_SIZE, title, max_w, 2, draw) if title else (None, [])
    sfont, slines = fitted("mono", 26, subtitle, max_w, 2, draw) if subtitle else (None, [])
    t_line = round(tfont.size * 1.2) if tfont else 0
    s_line = round(sfont.size * 1.4) if sfont else 0
    gap = 14 if tlines and slines else 0
    ty = y + 8  # level with the top of the screen
    if tlines or slines:
        draw.rectangle((left, ty, left + 56, ty + 3), fill=hex_rgb(AMBER))
        ty += 22
    for line in tlines:
        draw.text((left, ty), line, font=tfont, fill=HEADER)
        ty += t_line
    ty += gap
    for line in slines:
        draw.text((left, ty), line, font=sfont, fill=AMBER_BRIGHT)
        ty += s_line
    over = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(over).rounded_rectangle(box, PHONE_RADIUS, outline=OVERLAY_BORDER, width=2)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), PHONE_RADIUS, fill=255)
    return under.convert("RGB"), over, mask


def wrap(text, fnt, max_w, draw):
    words, lines, line = text.split(), [], ""
    for word in words:
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=fnt) <= max_w or not line:
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def balanced(text, fnt, max_w, draw):
    """Wraps TEXT in as few lines as wrap() would, with the lines as even as
    possible: the narrowest width that still needs no extra line."""
    lines = wrap(text, fnt, max_w, draw)
    if len(lines) < 2:
        return lines
    lo, hi = max(draw.textlength(w, font=fnt) for w in text.split()), max_w
    while hi - lo > 1:
        mid = (lo + hi) / 2
        if len(wrap(text, fnt, mid, draw)) <= len(lines):
            hi = mid
        else:
            lo = mid
    return wrap(text, fnt, hi, draw)


def fitted(role, size, text, max_w, max_lines, draw):
    """The largest size up to SIZE whose wrapped text has at most MAX_LINES."""
    while size > 12:
        fnt = font(role, size)
        lines = wrap(text, fnt, max_w, draw)
        if len(lines) <= max_lines and all(draw.textlength(l, font=fnt) <= max_w for l in lines):
            return fnt, lines
        size -= 2
    fnt = font(role, size)
    return fnt, wrap(text, fnt, max_w, draw)


def _draw_tactical_rails(img):
    """Subtle top/bottom amber hairline rails with corner brackets."""
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    mx, top_y, bot_y = 128, 88, H - 88
    tick = 16
    # Top and bottom hairlines
    d.line([(mx, top_y), (W - mx, top_y)], fill=ACCENT_RULE, width=2)
    d.line([(mx, bot_y), (W - mx, bot_y)], fill=ACCENT_RULE_SOFT, width=1)
    # Corner vertical ticks
    for x in (mx, W - mx):
        d.line([(x, top_y), (x, top_y + tick)], fill=hex_rgb(AMBER) + (210,), width=2)
        d.line([(x, bot_y - tick), (x, bot_y)], fill=hex_rgb(AMBER) + (150,), width=2)
    img.alpha_composite(overlay)


def card(title, subtitle):
    img = background().convert("RGBA")
    # Soft obsidian scrim behind center content for razor-sharp readability over topo lines.
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    scrim_box = (176, 152, W - 176, H - 152)
    sd.rounded_rectangle(scrim_box, 18, fill=(11, 14, 17, 178), outline=(229, 160, 13, 38), width=1)
    img.alpha_composite(scrim)
    _draw_tactical_rails(img)

    mark_img = logo(460)
    draw = ImageDraw.Draw(img)
    tfont, tlines = fitted("title", TITLE_SIZE, title, TEXT_MAX_W, 2, draw)
    sfont, slines = fitted("mono", SUBTITLE_SIZE, subtitle, TEXT_MAX_W, 2, draw)
    # Even lines: no word left alone on the last one.
    tlines = balanced(" ".join(tlines), tfont, TEXT_MAX_W, draw)
    slines = balanced(" ".join(slines), sfont, TEXT_MAX_W, draw)
    t_line = round(tfont.size * 1.22)
    s_line = round(sfont.size * 1.42)
    logo_gap = 40
    bar_gap = 38
    gap = 26
    total = (
        mark_img.height + logo_gap + 3 + bar_gap
        + t_line * len(tlines) + gap + s_line * len(slines)
    )
    y = (H - total) // 2
    watermark(img, (W - mark_img.width) // 2, y, 460)
    y += mark_img.height + logo_gap
    draw.rectangle(((W - 84) // 2, y, (W + 84) // 2, y + 3), fill=hex_rgb(AMBER))
    y += 3 + bar_gap
    for line in tlines:
        draw.text((W / 2, y), line, font=tfont, fill=HEADER, anchor="mt")
        y += t_line
    y += gap
    for line in slines:
        draw.text((W / 2, y), line, font=sfont, fill=AMBER_BRIGHT, anchor="mt")
        y += s_line
    draw.text((W / 2, H - 52), REPOSITORY, font=font("mono", 22), fill=MUTED, anchor="mm")
    return img.convert("RGB")


def text_box(text, tag="", size=LOWER_SIZE, max_w=LOWER_MAX_W, max_lines=2):
    """A floating machined HUD box with a left Signal Amber accent bar, crisp
    hairline border, and soft shadow. TAG is an emerald pill for a speed-up.
    Returns the image and the shadow margin around the box."""
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    fnt = font("lower", size)
    tag_font = font("lower", 22)
    tag_w = probe.textlength(tag, font=tag_font) + 28 if tag else 0
    bar_inset = 10
    lines = wrap(text, fnt, max_w - tag_w - bar_inset, probe)[:max_lines]
    line_h = round(size * 1.32)
    text_w = max(probe.textlength(l, font=fnt) for l in lines)
    pad_x, pad_y = round(size * 6 / 7), round(size * 4 / 7)
    box_w = round(text_w + (tag_w + 18 if tag else 0) + 2 * pad_x + bar_inset)
    box_h = line_h * len(lines) + 2 * pad_y
    margin = 32  # room for the shadow
    img = Image.new("RGBA", (box_w + 2 * margin, box_h + 2 * margin), (0, 0, 0, 0))
    box = (margin, margin, margin + box_w - 1, margin + box_h - 1)
    img.alpha_composite(shadow(img.size, box, 10, 10, 14, 140))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(box, 10, fill=OVERLAY, outline=OVERLAY_BORDER, width=1)
    # Left Signal Amber vertical HUD bar.
    draw.rounded_rectangle(
        (margin + 6, margin + 8, margin + 10, margin + box_h - 9),
        2,
        fill=hex_rgb(AMBER) + (245,),
    )
    text_x = margin + pad_x + bar_inset
    y = margin + pad_y
    for line in lines:
        draw.text((text_x, y + line_h / 2), line, font=fnt, fill=HEADER, anchor="lm")
        y += line_h
    if tag:
        tx = text_x + max(probe.textlength(l, font=fnt) for l in lines[-1:]) + 18
        ty = y - line_h / 2
        pill = (tx, ty - 17, tx + tag_w, ty + 17)
        draw.rounded_rectangle(pill, 17, fill=hex_rgb(EMERALD) + (36,), outline=hex_rgb(EMERALD) + (155,), width=1)
        draw.text(((pill[0] + pill[2]) / 2, ty), tag, font=tag_font, fill=EMERALD, anchor="mm")
    return img, margin


def lower(text, tag=""):
    """A lower-third. Returns the image and its canvas position."""
    img, margin = text_box(text, tag)
    return img, 48 - margin, H - 48 - (img.height - 2 * margin) - margin


def panel(text, tag="", screen_w=720, screen_h=1208):
    """The caption beside the phone close-up: the lower-third's box, larger,
    up to three lines, left-aligned at PANEL_X and centred on the screen.
    Returns the image and its canvas position."""
    max_w = phone_box(screen_w, screen_h)[0] - PANEL_X - 96
    img, margin = text_box(text, tag, PANEL_SIZE, max_w, 3)
    return img, PANEL_X - margin, (H - img.height) // 2


def still(src):
    """A docs still: the screenshot in the window frame, on the background,
    cropped to the frame with a margin."""
    shot = Image.open(src).convert("RGB")
    scale = min(CONTENT_W / shot.width, CONTENT_H / shot.height, 1.0)
    cw, ch = round(shot.width * scale), round(shot.height * scale)
    shot = shot.resize((cw, ch), Image.LANCZOS) if scale < 1 else shot
    m = 48
    fw, fh = cw, ch + TITLE_BAR
    size = (fw + 2 * m, fh + 2 * m + 24)
    img = background().resize(size, Image.BICUBIC).convert("RGBA")
    box = (m, m, m + fw - 1, m + fh - 1)
    img.alpha_composite(shadow(size, box, RADIUS, 24, 24, 150))
    _draw_chrome_bar(img, m, m, fw, fh, RADIUS)
    if cw >= 240:
        watermark(img, m + fw - 42, m + 7, 22, mark=True)
    mask = Image.new("L", (cw, ch), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, -RADIUS * 2, cw - 1, ch - 1), RADIUS, fill=255)
    img.paste(shot, (m, m + TITLE_BAR), mask)
    ImageDraw.Draw(img).rounded_rectangle(box, RADIUS, outline=FRAME_BORDER, width=1)
    return img.convert("RGB")


def main():
    cmd = sys.argv[1]
    if cmd == "fonts":
        check_fonts()
    elif cmd == "branding":
        out = Path(sys.argv[2])
        out.mkdir(parents=True, exist_ok=True)
        card("Deploy", "Upstream TAK, packaged with Zarf, deployed on UDS Core").save(out / "title.png")
        card("Get started", "uds run test-install").save(out / "endcard.png")
        under, _, _ = frame()
        under.save(out / "frame.png")
        under, _, _ = phone(720, 1208, "ATAK device", "Demonstration layout preview")
        under.save(out / "phone.png")
    elif cmd == "background":
        background().save(sys.argv[2])
    elif cmd == "frame":
        under, over, mask = frame()
        under.save(sys.argv[2])
        over.save(sys.argv[3])
        mask.save(sys.argv[4])
    elif cmd == "phone":
        # phone UNDER OVER MASK SCREEN_W SCREEN_H [TITLE SUBTITLE]: prints x y w h
        sw, sh = int(sys.argv[5]), int(sys.argv[6])
        under, over, mask = phone(sw, sh, *sys.argv[7:9])
        under.save(sys.argv[2])
        over.save(sys.argv[3])
        mask.save(sys.argv[4])
        print(*phone_box(sw, sh))
    elif cmd == "card":
        card(sys.argv[3], sys.argv[4]).save(sys.argv[2])
    elif cmd == "lower":
        img, x, y = lower(sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")
        img.save(sys.argv[2])
        print(x, y)
    elif cmd == "panel":
        # panel OUT.png TEXT TAG SCREEN_W SCREEN_H: prints x y
        img, x, y = panel(sys.argv[3], sys.argv[4], int(sys.argv[5]), int(sys.argv[6]))
        img.save(sys.argv[2])
        print(x, y)
    elif cmd == "still":
        still(sys.argv[2]).save(sys.argv[3])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
