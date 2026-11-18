# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Chapter captions are display text, with one literal repository endcard."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "sync_assets", Path(__file__).resolve().parents[1] / "demo/sync-assets.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


class ChapterTests(unittest.TestCase):
    def chapters(self, text):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chapters.txt"
            path.write_text(text)
            return sync.chapters(path)

    def test_only_title_and_exact_repository_endcard_are_omitted(self):
        self.assertEqual(self.chapters(
            "0:00 TAK on UDS\n"
            "0:05 Deploy\n"
            "0:08   explanatory caption\n"
            "0:12   2. build the Zarf package: upstream chart\n"
            "1:02.9 Operate and Trust\n"
            "2:00 github.com/jonathanagustin/uds-takserver\n"
        ), ["- 0:05 Deploy", "- 0:12 Build the Zarf Package", "- 1:02 Operate and Trust"])

    def test_host_like_chapter_names_are_preserved_as_display_text(self):
        for caption in (
            "github.com/another/project", "github.com/example/setup instructions",
            "github.com/jonathanagustin/uds-takserver-extra",
            "github.com/jonathanagustin/uds-takserver/docs",
            "github.com.example/jonathanagustin/uds-takserver",
            "prefixgithub.com/jonathanagustin/uds-takserver", "Ordinary Chapter",
        ):
            with self.subTest(caption=caption):
                self.assertEqual(self.chapters("0:05 " + caption), ["- 0:05 " + caption])


if __name__ == "__main__":
    unittest.main()
