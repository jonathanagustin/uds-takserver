# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

"""Create and verify the staged production v1beta1 package on a native runner."""

import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import tempfile

import yaml

from tests.test_zarf_beta_production_parity import ROOT, stage_beta_tree


def zarf(*args, capture=False):
    result = subprocess.run(
        ["uds", "zarf", *map(str, args), "--no-color"],
        check=False, text=True, stdout=subprocess.PIPE if capture else None,
    )
    if result.returncode:
        raise RuntimeError(f"zarf {args[0]} {args[1]} exited {result.returncode}")
    return result.stdout


def component_inventory(package):
    return [
        (entry["name"], [chart["name"] for chart in entry["charts"]],
         {image["name"] if isinstance(image, dict) else image for image in entry["images"]})
        for entry in package["components"]
    ]


def main():
    arch = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}[platform.machine()]
    if arch != os.environ["EXPECTED_ARCHITECTURE"]:
        raise ValueError("Runner architecture differs from the qualification matrix")
    with tempfile.TemporaryDirectory(prefix="uds-beta-package-") as tmp:
        work = Path(tmp)
        stage = work / "source"
        stage.mkdir()
        stage_beta_tree(stage)
        # The onCreate hook builds the chart in its own isolated source tree.
        (stage / "pepr").unlink()
        shutil.copytree(ROOT / "pepr", stage / "pepr",
                        ignore=shutil.ignore_patterns("dist", "node_modules"))
        password = secrets.token_hex(32)
        subprocess.run(["cosign", "generate-key-pair"], check=True, cwd=work,
                       env={**os.environ, "COSIGN_PASSWORD": password})
        zarf("package", "create", stage, "--flavor", "upstream", "--architecture", arch,
             "--confirm", "--output", work, "--signing-key", work / "cosign.key",
             "--signing-key-pass", password)
        package, = work.glob("zarf-package-takserver-*.tar.zst")
        key = work / "cosign.pub"
        zarf("package", "verify", package, "--key", key)
        embedded = yaml.safe_load(zarf("package", "inspect", "definition", package,
                                       "--key", key, capture=True))
        baseline = yaml.safe_load(zarf("dev", "inspect", "definition", ROOT,
                                      "--flavor", "upstream", "--architecture", arch, capture=True))
        if (embedded["apiVersion"], embedded["build"]["architecture"]) != ("zarf.dev/v1beta1", arch):
            raise ValueError("Unexpected package schema or build architecture")
        if component_inventory(embedded) != component_inventory(baseline):
            raise ValueError("Composed component, chart, or image inventory differs from alpha")
        if embedded["attachments"].keys() != baseline["documentation"].keys():
            raise ValueError("Document attachment inventory differs from alpha")
        if [entry.get("optional", False) for entry in embedded["components"]] != [
            not entry["required"] for entry in baseline["components"]
        ]:
            raise ValueError("Component optionality differs from alpha")
        zarf("package", "inspect", "documentation", package, "--key", key, "--output", work)
        documentation = work / "takserver-documentation"
        for relative in baseline["documentation"].values():
            if (documentation / Path(relative).name).read_bytes() != (ROOT / relative).read_bytes():
                raise ValueError(f"Package documentation differs from source: {relative}")
        print(f"Verified signed {arch} package with the production component, chart, image and attachment inventory")


if __name__ == "__main__":
    main()
