# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Regression tests for demo/civilian-trailer.py title and HUD overlays."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "civilian_trailer", REPO_ROOT / "demo" / "civilian-trailer.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ct = _load_module()


class CivilianTrailerOverlayTests(unittest.TestCase):
    def test_branded_frame_zero_renders_translucent_backdrop_and_wordmark(self) -> None:
        frame = ct.render_lockin_frame(
            0.0, 4.6, show_url=False, backdrop=True, start_visible=True
        )
        self.assertEqual(frame.size, (ct.W, ct.H))

        # Translucent Obsidian card behind the lock-in composition (see-through, not opaque)
        card_px = frame.getpixel((960, 520))
        self.assertEqual(card_px[:3], ct.OBSIDIAN)
        self.assertGreaterEqual(card_px[3], 170)
        self.assertLess(card_px[3], 240)

        # Left amber accent bar on the backdrop card
        accent_x = int(round(960.0 - 310.0 - 165.0)) + 2
        accent_px = frame.getpixel((accent_x, 520))
        self.assertEqual(accent_px[:3], ct.AMBER)
        self.assertGreaterEqual(accent_px[3], 220)

        # Full-opacity wordmark text present on frame 0 for inline video thumbnails
        wordmark_alpha = max(
            frame.getpixel((x, 490))[3] for x in range(815, 1320, 8)
        )
        self.assertEqual(wordmark_alpha, 255)

        # Outside the card and rules remains transparent
        self.assertEqual(frame.getpixel((40, 40))[3], 0)

    def test_animated_stinger_starts_transparent_and_fades_out(self) -> None:
        start = ct.render_lockin_frame(
            0.0, 3.8, show_url=False, backdrop=True, start_visible=False
        )
        self.assertEqual(start.getpixel((960, 520))[3], 0)

        mid = ct.render_lockin_frame(
            2.2, 3.8, show_url=False, backdrop=True, start_visible=False
        )
        self.assertEqual(mid.getpixel((960, 520))[:3], ct.OBSIDIAN)
        self.assertGreaterEqual(mid.getpixel((960, 520))[3], 170)

        end = ct.render_lockin_frame(
            3.8, 3.8, show_url=False, backdrop=True, start_visible=False
        )
        self.assertEqual(end.getpixel((960, 520))[3], 0)

    def test_outro_omits_backdrop_card(self) -> None:
        frame = ct.render_lockin_frame(
            2.6, 5.5, show_url=True, backdrop=False, start_visible=False
        )
        # Card background region outside mark/text/rules stays transparent on dark topo outro
        self.assertEqual(frame.getpixel((500, 380))[3], 0)
        self.assertEqual(frame.getpixel((1350, 380))[3], 0)

    def test_hud_lower_third_renders_card_and_fades_out(self) -> None:
        mid = ct.render_hud_frame(
            2.0, 4.8, "FIELD TEAMS  //  ATAK & ITAK", "Live position and hazard sharing"
        )
        # Card body uses translucent Obsidian fill; left edge has Amber bar
        self.assertEqual(mid.getpixel((74, ct.H - 130))[:3], ct.AMBER)
        self.assertEqual(mid.getpixel((200, ct.H - 175))[:3], ct.OBSIDIAN)
        self.assertGreaterEqual(mid.getpixel((200, ct.H - 175))[3], 200)

        end = ct.render_hud_frame(
            4.8, 4.8, "FIELD TEAMS  //  ATAK & ITAK", "Live position and hazard sharing"
        )
        self.assertEqual(end.getpixel((200, ct.H - 175))[3], 0)


if __name__ == "__main__":
    unittest.main()
