#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Check an assembled TAKX tree against its manifest.json.

  verify_tree.py TREE [MANIFEST_SHA256]

Passes only when manifest.json has the given SHA-256 (when one is given),
every file the manifest lists is a regular file with the recorded size and
SHA-256, and the tree holds no other file and no symbolic link. This is the
build gate that keeps the image byte-identical to what ../assemble produced.
"""
import hashlib
import json
import os
import sys


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify(tree, expected_manifest=None):
    """Return (facts, problems) for the tree."""
    problems = []
    manifest_path = os.path.join(tree, "manifest.json")
    manifest_digest = sha256_file(manifest_path)
    if expected_manifest and manifest_digest != expected_manifest:
        problems.append(f"manifest.json is {manifest_digest}, expected {expected_manifest}")
    with open(manifest_path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    listed = {}
    for entry in manifest["files"]:
        path = entry["path"]
        if path in listed or os.path.isabs(path) or ".." in path.split("/"):
            problems.append(f"manifest path is duplicated or unsafe: {path}")
        listed[path] = entry
    present = set()
    for folder, _dirs, files in os.walk(tree):
        for name in files:
            full = os.path.join(folder, name)
            relative = os.path.relpath(full, tree)
            if os.path.islink(full):
                problems.append(f"symbolic link in the tree: {relative}")
            present.add(relative)
        for name in _dirs:
            if os.path.islink(os.path.join(folder, name)):
                problems.append(f"symbolic link in the tree: {os.path.relpath(os.path.join(folder, name), tree)}")
    present.discard("manifest.json")
    for path in sorted(set(listed) - present):
        problems.append(f"missing file: {path}")
    for path in sorted(present - set(listed)):
        problems.append(f"file not in the manifest: {path}")
    total = 0
    for path in sorted(set(listed) & present):
        full = os.path.join(tree, path)
        size = os.path.getsize(full)
        total += size
        if size != listed[path]["bytes"]:
            problems.append(f"size of {path} is {size}, manifest says {listed[path]['bytes']}")
        elif sha256_file(full) != listed[path]["sha256"]:
            problems.append(f"SHA-256 of {path} differs from the manifest")
    facts = {"manifest_sha256": manifest_digest, "files": len(listed), "bytes": total,
             "takx_source_revision": manifest.get("takx", {}).get("source_revision")}
    return facts, problems


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    facts, problems = verify(argv[1], argv[2] if len(argv) == 3 else None)
    for problem in problems:
        print(f"verify_tree: {problem}", file=sys.stderr)
    facts["status"] = "failed" if problems else "ok"
    print(json.dumps(facts, indent=1, sort_keys=True))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
