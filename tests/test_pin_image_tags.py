# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Execute image pinning in a real checkout; never change the source checkout."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ImageTagPinning(unittest.TestCase):
    def test_every_bundle_uses_the_tak_tag_packaged_by_ci(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            subprocess.run(["git", "clone", "--shared", "--quiet", str(ROOT), str(root)], check=True)
            # Exercise the current script, including uncommitted changes.
            shutil.copy(ROOT / ".github/scripts/pin-image-tags.sh", root / ".github/scripts/pin-image-tags.sh")
            tag = subprocess.check_output(
                ["bash", ".github/container-and-chart/docker/build.sh", "--tag"], cwd=root, text=True,
            ).strip()
            subprocess.run(["bash", ".github/scripts/pin-image-tags.sh"], cwd=root, check=True,
                           env={**os.environ, 'GITHUB_EVENT_NAME': 'unit-test'},
                           capture_output=True, text=True, timeout=30)
            package = yaml.safe_load((root / "zarf.yaml").read_text())
            if "variables" in package:
                variables = {variable["name"]: variable for variable in package["variables"]}
                self.assertEqual(variables["TAK_IMAGE_TAG"]["default"], tag)
            else:
                values = yaml.safe_load((root / "values/zarf-values.yaml").read_text())
                self.assertEqual(values["tak"]["imageTag"], tag)
            images = [
                (image["name"] if isinstance(image, dict) else image)
                for component in package["components"]
                for image in component.get("images", [])
                if "/takserver-" in (image["name"] if isinstance(image, dict) else image)
            ]
            self.assertEqual(len(images), 6)
            self.assertTrue(all(image.endswith(":" + tag) for image in images))
            configs = [path for path in (root / "bundle").rglob("*.yaml") if "TAK_IMAGE_TAG:" in path.read_text()]
            self.assertIn(root / "bundle/airgap-config.yaml", configs)
            for config in configs:
                with self.subTest(config=str(config.relative_to(root))):
                    data = yaml.safe_load(config.read_text())
                    self.assertEqual(data["variables"]["takserver"]["TAK_IMAGE_TAG"], tag)
            self.assertIs(yaml.safe_load((root / "bundle/airgap-config.yaml").read_text())
                          ["variables"]["takserver"]["CLOUDTAK_BASEMAP_ENABLED"], False)
            postgis_tag = subprocess.check_output(
                ["bash", ".github/container-and-chart/postgis/build.sh", "--tag"], cwd=root, text=True,
            ).strip()
            tak_values = yaml.safe_load((root / "charts/takserver/values.yaml").read_text())
            self.assertTrue(
                tak_values["backup"]["dump"]["image"].endswith(":" + postgis_tag),
                tak_values["backup"]["dump"]["image"],
            )

    def test_v1beta1_pinning_updates_values_and_object_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "repo"
            subprocess.run(["git", "clone", "--shared", "--quiet", str(ROOT), str(root)], check=True)
            shutil.copy(ROOT / ".github/scripts/pin-image-tags.sh", root / ".github/scripts/pin-image-tags.sh")
            # Rewrite zarf.yaml to v1beta1 shape (no variables, object images) and concrete values/zarf-values.yaml.
            alpha = yaml.safe_load((root / "zarf.yaml").read_text())
            tak_default = next(v["default"] for v in alpha["variables"] if v["name"] == "TAK_IMAGE_TAG")
            beta = {
                "apiVersion": "zarf.dev/v1beta1",
                "kind": "ZarfPackageConfig",
                "metadata": alpha["metadata"],
                "values": alpha["values"],
                "components": [
                    {
                        "name": comp["name"],
                        "images": [{"name": img} for img in comp.get("images", [])],
                    }
                    for comp in alpha["components"]
                ],
            }
            lines = yaml.safe_dump(beta, sort_keys=False).splitlines()
            quoted = "\n".join(
                (line.split(": ", 1)[0] + ': "' + line.split(": ", 1)[1] + '"'
                 if line.lstrip().startswith("- name: ghcr.io/") else line)
                for line in lines
            ) + "\n"
            (root / "zarf.yaml").write_text(quoted)
            values_text = (root / "values/zarf-values.yaml").read_text()
            (root / "values/zarf-values.yaml").write_text(
                values_text.replace("###ZARF_VAR_TAK_IMAGE_TAG###", tak_default)
            )
            tag = subprocess.check_output(
                ["bash", ".github/container-and-chart/docker/build.sh", "--tag"], cwd=root, text=True,
            ).strip()
            subprocess.run(["bash", ".github/scripts/pin-image-tags.sh"], cwd=root, check=True,
                           env={**os.environ, 'GITHUB_EVENT_NAME': 'unit-test'},
                           capture_output=True, text=True, timeout=30)
            values = yaml.safe_load((root / "values/zarf-values.yaml").read_text())
            self.assertEqual(values["tak"]["imageTag"], tag)
            pinned = yaml.safe_load((root / "zarf.yaml").read_text())
            images = [img["name"] for comp in pinned["components"] for img in comp["images"] if "/takserver-" in img["name"]]
            self.assertEqual(len(images), 6)
            self.assertTrue(all(img.endswith(":" + tag) for img in images))


if __name__ == "__main__":
    unittest.main()
