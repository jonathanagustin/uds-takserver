#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Build the notices context of the TAKX desktop image.

  gen_notices.py --tree TREE --out OUT [--root WORK_ROOT] [--repo REPOSITORY]

Writes, for /usr/share/takx-desktop in the image (and the package attachments):

  NOTICE.md      copy of image/NOTICE.md: what the build is and is not
  LICENSES/      GPL-3.0-only.txt (the repository LICENSE), the licence files
                 of TAKX, atak-civ (with THIRDPARTY.md), NASA WorldWind Java
                 and every native provider, and every LICENSE/NOTICE/COPYING
                 file found inside the JARs of the tree; INDEX.tsv lists them
  LICENSES/texts one text per licence identifier of licence-policy.json:
                 SPDX texts fetched once into --texts-cache and verified
                 against the SHA-256 in the policy, texts taken from a JAR,
                 and composed or stated ones
  licence-gate.json  report of licence_gate.py over the tree and these
                 notices. The run fails when the gate says the build may
                 not proceed (something denied or unknown)
  patches/       every patch a build receipt names, verified against the
                 SHA-256 in that receipt (taken from the git history when the
                 working tree no longer has that version), and the other
                 patch files of takx/ as they are in the working tree
  locks/         takx/sources.lock.json and client/inputs.lock.json
  SOURCES.json   the corresponding-source manifest: repository revision and
                 dirty state, lock digests, all fetched inputs with URL and
                 SHA-256, receipt digests, base image and JDK, every file of
                 the tree mapped to a component, every patch with its digest

SOURCES.json identifies the corresponding source; it is not the source. It
says "publishable": false while the licence gate flags anything or any
component is unresolved, and lists why. Its counts are computed from the
records, per category; nothing is rounded up to "with URL and SHA-256".
Every file of the tree must map to a component, and every patch a receipt
names must be found with its recorded digest, or the run fails.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import licence_gate  # noqa: E402  (the script directory is added above for python3 -I)

LABEL = ("TAKX 5.7 (0b59a9bb) built from public sources on Kernel 8.30.5 + project patches, "
         "CommonCommo 1.17.2 patched and the NASA WorldWind Java v2.2.1 interop build; "
         "not the official TAKX 5.7 distribution")
BASE_IMAGE = "mirror.gcr.io/library/debian:trixie@sha256:9c6e381421d0f48d1b8133e573f33c185ee16cbf505eda9c4a8a4791b25ea8a6"
JDK = {"name": "Eclipse Temurin 17.0.21+9 JDK",
       "url": "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.21%2B9/"
              "OpenJDK17U-jdk_x64_linux_hotspot_17.0.21_9.tar.gz",
       "sha256": "8b9b611d917e3dfd8419d3566b5949226f69b052fed7e109c1368add1dfe8711",
       "licence": "GPL-2.0-only WITH Classpath-exception-2.0; texts in the JDK's legal/ directory in the image",
       "source": "https://github.com/adoptium/jdk17u tag jdk-17.0.21+9; lib/src.zip in the image"}
LICENCE_NAME = re.compile(r"(^|/)(LICEN[CS]E|NOTICE|COPYING|COPYRIGHT|THIRD-?PARTY)[^/]*$", re.I)
ATAK = "https://github.com/TAK-Product-Center/atak-civ"

# id: (label, licence as stated by the source, source, resolved, note)
COMPONENTS = {
    "takx": ("TAKX 5.7 client, official public sources", "GPL-3.0 (LICENSE.md)",
             "https://github.com/TAK-Product-Center/TAKX revision 0b59a9bb2d0e90dc35c9edfff1c2056daab2b713", True,
             "compiled by the client stage; inputs in locks/inputs.lock.json"),
    "atak-civ-assets": ("icons, icon sets and data files from atak-civ", "GPL-3.0 (LICENSE.md), THIRDPARTY.md",
                        ATAK + " revision 9f6893dd657feacc35ec5de03dad721c2e44170e", True,
                        "Git LFS objects are verified against their pointers (fetched_inputs)"),
    "kernel": ("Kernel 8.30.5 + project patches, unqualified; substitute for the private 8.57.11; the JAR in the tree "
               "is a derived copy (tree.kernel_jar_derivation)",
               "GPL-3.0 (atak-civ LICENSE.md); statically linked third-party code under its own licences",
               ATAK + " revision 9f6893dd657feacc35ec5de03dad721c2e44170e, takkernel/ and takthirdparty/", True,
               "patches in patches/ (receipts.kernel-composed); third-party archives in fetched_inputs; "
               "provider licence texts in LICENSES/providers"),
    "kernel-java-overlay": ("Kernel classes rebuilt with the desktop-prototype overlay patches and from the "
                            "takx/kernel-render-context sources where they are newer than the composed Kernel; "
                            "tree.kernel_java_overlay lists the patches, the refreshed sources and every class",
                            "GPL-3.0 (atak-civ LICENSE.md)",
                            "kernel sources + patches/ (manifest.json kernel_java_overlay)", True, ""),
    "kernel-dependency-jars": ("JARs the Kernel build depends on, frozen by takx/kernel-java",
                               "per JAR; texts found inside are in LICENSES/jars",
                               "public Maven artifacts and atak-civ third-party builds", False,
                               "binary inputs; no source archive is recorded for them here"),
    "commoncommo": ("CommonCommo 1.17.2 patched; substitute for the private 1.20.0",
                    "GPL-3.0 (atak-civ LICENSE.md); statically linked third-party code under its own licences",
                    ATAK + " revision 9f6893dd657feacc35ec5de03dad721c2e44170e, commoncommo/", True,
                    "patches named in locks/sources.lock.json"),
    "worldwind": ("NASA WorldWind Java v2.2.1 + interop profile; substitute for the private 2.2.1-TAKX-4",
                  "Apache-2.0 according to its README; third-party notices PDF in LICENSES/worldwind",
                  "https://github.com/NASAWorldWind/WorldWindJava", True,
                  "revision in repository_sources; patches takx/worldwind-java/*.patch"),
    "ui-java": ("source rebuilds of UI libraries; substitutes for private artifacts",
                "LGPL-2.1-or-later, Apache-2.0, BSD (per artifact, receipts.ui-original)",
                "source archives with URL and SHA-256 in that receipt", True,
                "the CDDL-only wizard reference JAR of takx/ui-java is not in the tree"),
    "provider-data": ("data directories of GDAL 2.4.4 and PROJ 4.9.1 as installed by the native lanes",
                      "MIT (GDAL LICENSE.TXT, PROJ COPYING); tables derived from EPSG, ESRI and IGNF under their own terms",
                      "the GDAL and PROJ source archives in fetched_inputs", True, ""),
    "maven": ("unmodified public Maven artifacts", "per artifact; texts found inside are in LICENSES/jars",
              "coordinates and repository in files[].origin; binary URL and SHA-256 in fetched_inputs", False,
              "binaries as published; their source archives are not recorded or archived here"),
    "uds-takserver": ("files written by this repository: module descriptors, launcher facts, SystemClock JNI",
                      "GPL-3.0-only", "https://github.com/jonathanagustin/uds-takserver (repository)", True,
                      "see repository.dirty: a dirty tree is not reproducible from the commit alone"),
}


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def component_of(entry):
    """Map one manifest file entry to a component id; unknown origins are an error."""
    origin = entry["origin"]
    rules = (
        ("public atak-civ ", "atak-civ-assets"),
        ("public Maven artifact ", "maven"),
        ("official TAKX 5.7 ", "takx"),
        ("takx/desktop-prototype client stage (official TAKX 5.7 sources)", "takx"),
        ("Kernel 8.30.5 dependency JAR frozen by takx/kernel-java", "kernel-dependency-jars"),
        ("Kernel 8.30.5 + project patches", "kernel"),
        ("desktop-prototype Kernel Java overlay", "kernel-java-overlay"),
        ("CommonCommo 1.17.2 patched", "commoncommo"),
        ("NASA WorldWind Java v2.2.1 + interop profile", "worldwind"),
        ("public source rebuild by takx/ui-java", "ui-java"),
        ("hand-written JBoss Modules descriptor", "uds-takserver"),
        ("launcher fact: ", "uds-takserver"),
        ("takx/compat/build-system-clock.py", "uds-takserver"),
        ("GDAL 2.4.4 data directory", "provider-data"),
        ("PROJ 4.9.1 data directory", "provider-data"),
    )
    for prefix, component in rules:
        if origin.startswith(prefix):
            return component
    raise ValueError(f"no component rule for {entry['path']}: {origin}")


def git(repo, *arguments, binary=False):
    done = subprocess.run(("git", "-C", repo) + arguments, capture_output=True, check=False)
    if done.returncode != 0:
        return None
    return done.stdout if binary else done.stdout.decode("utf-8", "replace")


def find_patch(repo, path, digest):
    """Return (bytes, where) of the patch with that SHA-256: working tree first, then git history."""
    full = os.path.join(repo, path)
    if os.path.isfile(full):
        with open(full, "rb") as stream:
            data = stream.read()
        if sha256_bytes(data) == digest:
            return data, "working tree"
    for commit in (git(repo, "log", "--all", "--format=%H", "--", path) or "").split():
        for revision in (commit, commit + "^"):
            data = git(repo, "show", f"{revision}:{path}", binary=True)
            if data is not None and sha256_bytes(data) == digest:
                return data, "git history, " + (git(repo, "rev-parse", revision) or revision).strip()
    return None, None


def write_licence(data, target, index, origin):
    """Write one licence file and index it once.

    Two sources may flatten to the same name (a provider keeps a file both in
    source/ and in licenses/). With equal bytes that is one file with two
    origins; with different bytes the second gets a numbered name, so nothing
    is overwritten and the index has exactly one row per file."""
    os.makedirs(os.path.dirname(target), exist_ok=True)
    known = {path: position for position, (path, _) in enumerate(index)}
    candidate, number = target, 1
    while candidate in known:
        with open(candidate, "rb") as stream:
            if stream.read() == data:
                index[known[candidate]] = (candidate, index[known[candidate]][1] + "; " + origin)
                return
        number += 1
        candidate = f"{target}.{number}"
    with open(candidate, "wb") as stream:
        stream.write(data)
    index.append((candidate, origin))


def copy_licence(source, target, index, origin):
    with open(source, "rb") as stream:
        write_licence(stream.read(), target, index, origin)


# Licence files of code linked into the bundled native libraries that the
# walk below does not reach (it looks two levels into source/ and licenses/).
EXTRA_PROVIDER_TEXTS = (
    ("commoncommo", "dependencies/libmicrohttpd-1.0.1/COPYING", "libmicrohttpd 1.0.1, linked into libcommoncommojni.so"),
    ("commoncommo", "dependencies/nghttp2-1.62.1/COPYING", "nghttp2 1.62.1, linked into libcommoncommojni.so"),
    ("commoncommo", "dependencies/ngtcp2-1.6.0/COPYING", "ngtcp2 1.6.0, linked into libcommoncommojni.so"),
    ("commoncommo", "dependencies/protobuf/LICENSE", "protobuf, linked into libcommoncommojni.so"),
    ("commoncommo", "dependencies/curl/COPYING", "curl, linked into libcommoncommojni.so"),
    ("gles", "source/libglvnd-1.7.0/README.md", "libglvnd 1.7.0 (libGLESv2.so, libGLdispatch.so); its licence is a section of this file"),
    ("gles", "source/libglvnd-1.7.0/src/util/cJSON/LICENSE", "cJSON inside libglvnd 1.7.0"),
    ("gles", "source/libglvnd-1.7.0/src/util/uthash/LICENSE", "uthash inside libglvnd 1.7.0"),
)


def collect_texts(out, tree, policy, cache, fetch, index, problems):
    """One text per licence identifier of the policy, in LICENSES/texts; return {identifier: file}."""
    texts = policy.get("texts", {})
    base = os.path.join(out, "LICENSES", "texts")
    os.makedirs(base, exist_ok=True)
    written = {}

    def spdx(identifier):
        digest = texts.get("spdx", {}).get(identifier)
        if digest is None:
            return None
        path = os.path.join(cache, identifier + ".txt")
        if not os.path.isfile(path) and fetch:
            os.makedirs(cache, exist_ok=True)
            with urllib.request.urlopen(texts["spdx_base"] + identifier + ".txt", timeout=60) as reply:
                data = reply.read()
            if sha256_bytes(data) == digest:
                with open(path, "wb") as stream:
                    stream.write(data)
        if not os.path.isfile(path) or sha256_file(path) != digest:
            problems.append(f"licence text {identifier}: not in {cache} with the SHA-256 of the policy (use --fetch-texts)")
            return None
        with open(path, "rb") as stream:
            return stream.read()

    for identifier in sorted(set(policy["allow"]) | set(policy["review"])):
        data, origin = spdx(identifier), None
        if data is not None:
            origin = "SPDX license-list-data " + texts["spdx_base"] + identifier + ".txt"
        elif identifier in texts.get("compose", {}):
            parts = [spdx(part) for part in texts["compose"][identifier]]
            if None not in parts:
                data = b"\n\n".join(parts)
                origin = "composed from the SPDX texts " + " + ".join(texts["compose"][identifier])
        elif identifier in texts.get("embedded", {}):
            jar, _, member = texts["embedded"][identifier].partition("!/")
            try:
                with zipfile.ZipFile(os.path.join(tree, jar)) as archive:
                    data = archive.read(member)
                origin = "inside " + texts["embedded"][identifier]
            except (OSError, KeyError, zipfile.BadZipFile):
                data = None
        elif identifier in texts.get("statement", {}):
            data = (texts["statement"][identifier] + "\n").encode("utf-8")
            origin = "statement recorded in licence-policy.json; no licence text exists"
        if data is None:
            problems.append(f"no licence text for {identifier}")
            continue
        target = os.path.join(base, identifier.replace(" ", "_") + ".txt")
        write_licence(data, target, index, f"text of {identifier}: {origin}")
        written[identifier] = os.path.relpath(target, out)
    return written


def collect_licences(out, tree, root, repo, problems, index):
    base = os.path.join(out, "LICENSES")
    wanted = (
        (os.path.join(repo, "LICENSE"), "GPL-3.0-only.txt", "uds-takserver repository LICENSE"),
        (os.path.join(root, "src/takx57/LICENSE.md"), "takx-5.7/LICENSE.md", "TAKX 0b59a9bb"),
        (os.path.join(root, "src/atak-civ/LICENSE.md"), "atak-civ/LICENSE.md", "atak-civ 9f6893dd"),
        (os.path.join(root, "src/atak-civ/THIRDPARTY.md"), "atak-civ/THIRDPARTY.md", "atak-civ 9f6893dd"),
    )
    for source, name, origin in wanted:
        if os.path.isfile(source):
            copy_licence(source, os.path.join(base, name), index, origin)
        else:
            problems.append(f"licence text missing: {source}")
    worldwind = os.path.join(root, "src/WorldWindJava")
    found = 0
    for name in sorted(os.listdir(worldwind)) if os.path.isdir(worldwind) else ():
        if re.search(r"licen[cs]e|notices", name, re.I) and os.path.isfile(os.path.join(worldwind, name)):
            copy_licence(os.path.join(worldwind, name), os.path.join(base, "worldwind", name), index,
                         "NASA WorldWind Java checkout")
            found += 1
    readme = os.path.join(worldwind, "README.md")
    if os.path.isfile(readme):
        copy_licence(readme, os.path.join(base, "worldwind", "README.md"), index,
                     "NASA WorldWind Java checkout (states the licence)")
    if not found:
        problems.append("no WorldWind licence file found")
    providers = os.path.join(root, "P")
    for provider, relative, origin in EXTRA_PROVIDER_TEXTS:
        source = os.path.join(providers, provider, relative)
        if os.path.isfile(source):
            copy_licence(source, os.path.join(base, "providers", provider, "linked__" + relative.replace(os.sep, "__")),
                         index, origin)
        else:
            problems.append(f"licence text missing: P/{provider}/{relative}")
    for provider in sorted(os.listdir(providers)) if os.path.isdir(providers) else ():
        for start in ("source", "licenses"):
            top = os.path.join(providers, provider, start)
            if not os.path.isdir(top):
                continue
            for folder, dirs, files in os.walk(top):
                depth = os.path.relpath(folder, top).count(os.sep) + (0 if folder == top else 1)
                if depth >= 2:
                    dirs[:] = []
                dirs.sort()
                for name in sorted(files):
                    relative = os.path.relpath(os.path.join(folder, name), top)
                    if start == "licenses" or LICENCE_NAME.search(relative):
                        if os.path.getsize(os.path.join(folder, name)) <= 1 << 20:
                            copy_licence(os.path.join(folder, name),
                                         os.path.join(base, "providers", provider, relative.replace(os.sep, "__")),
                                         index, f"provider {provider} ({start}/{relative})")
    for folder, dirs, files in os.walk(tree):
        dirs.sort()
        for name in sorted(files):
            if not name.endswith(".jar"):
                continue
            with zipfile.ZipFile(os.path.join(folder, name)) as archive:
                for info in archive.infolist():
                    if info.is_dir() or info.file_size > 1 << 20 or not LICENCE_NAME.search(info.filename):
                        continue
                    if info.filename.endswith((".class", ".png", ".gif")):
                        continue
                    target = os.path.join(base, "jars", name, info.filename.replace("/", "__"))
                    write_licence(archive.read(info), target, index,
                                  f"inside {os.path.relpath(os.path.join(folder, name), tree)}: {info.filename}")
    return len(index)


def write_index(out, index):
    with open(os.path.join(out, "LICENSES", "INDEX.tsv"), "w", encoding="utf-8") as stream:
        stream.write("path\tsha256\torigin\n")
        for target, origin in sorted(index):
            stream.write(f"{os.path.relpath(target, out)}\t{sha256_file(target)}\t{origin}\n")


def collect_patches(out, root, repo, problems, manifest):
    """Copy patches; return the list for SOURCES.json."""
    named = {}
    # The overlay of this tree is recorded in its own manifest (a tree
    # assembled under another name has its own overlay directory).
    for patch in manifest.get("kernel_java_overlay", {}).get("patches", []):
        named.setdefault((patch["patch"], patch["sha256"]), []).append({"receipt": "manifest.json kernel_java_overlay"})
    for receipt, key in (("P/kernel-composed/receipt.json", "kernel-composed"),):
        path = os.path.join(root, receipt)
        if not os.path.isfile(path):
            problems.append(f"receipt missing: {receipt}")
            continue
        with open(path, encoding="utf-8") as stream:
            for patch in json.load(stream).get("patches", []):
                if "patch" in patch and "sha256" in patch:
                    named.setdefault((patch["patch"], patch["sha256"]), []).append(
                        {"receipt": key, **{name: patch[name] for name in ("applied", "kind", "target", "reason")
                                            if name in patch}})
    lock = os.path.join(repo, "takx/sources.lock.json")
    if os.path.isfile(lock):
        with open(lock, encoding="utf-8") as stream:
            data = json.load(stream)
        for patch in data.get("patches", []) + data.get("dependency_patches", []):
            named.setdefault(("takx/" + patch["path"], patch["sha256"]), []).append({"receipt": "takx/sources.lock.json"})
    records, shipped = [], set()
    for (path, digest), references in sorted(named.items()):
        data, where = find_patch(repo, path, digest)
        if data is None:
            problems.append(f"patch {path} with SHA-256 {digest} is in neither the working tree nor the git history")
            records.append({"path": path, "sha256": digest, "shipped": False, "named_by": references})
            continue
        target = os.path.join(out, "patches", path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as stream:
            stream.write(data)
        shipped.add(path)
        records.append({"path": path, "sha256": digest, "bytes": len(data), "shipped": True,
                        "verified_against_receipt": True, "taken_from": where, "named_by": references})
    for folder, dirs, files in os.walk(os.path.join(repo, "takx")):
        dirs[:] = sorted(name for name in dirs if name not in ("mutation", "node_modules", "arm64", "__pycache__"))
        for name in sorted(files):
            path = os.path.relpath(os.path.join(folder, name), repo)
            if not name.endswith((".patch", ".diff")) or path in shipped:
                continue
            target = os.path.join(out, "patches", path)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(os.path.join(folder, name), target)
            records.append({"path": path, "sha256": sha256_file(target), "bytes": os.path.getsize(target),
                            "shipped": True, "verified_against_receipt": False, "taken_from": "working tree",
                            "note": "recipe patch as it is in the working tree; no receipt read here names its digest"})
    return records


def repository_facts(repo):
    status = git(repo, "status", "--porcelain") or ""
    diff = git(repo, "diff", "HEAD", binary=True) or b""
    return {"url": "https://github.com/jonathanagustin/uds-takserver",
            "commit": (git(repo, "rev-parse", "HEAD") or "unknown").strip(),
            "branch": (git(repo, "rev-parse", "--abbrev-ref", "HEAD") or "unknown").strip(),
            "dirty": bool(status.strip()),
            "dirty_paths": sorted(line[3:] for line in status.splitlines() if line.strip()),
            "tracked_diff_sha256": sha256_bytes(diff),
            "note": "commit and dirty state when the notices were generated, not when each provider was built; "
                    "the build receipts carry the recipe digests of that time"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--tree", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--root", default=os.environ.get("TAKX_PROTO_ROOT", "/var/lib/takx-proto"))
    parser.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
    parser.add_argument("--policy", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "licence-policy.json"))
    parser.add_argument("--texts-cache", help="directory with the SPDX texts (default ROOT/pkg/licence-texts)")
    parser.add_argument("--fetch-texts", action="store_true", help="fetch SPDX texts that are not in the cache")
    options = parser.parse_args(argv)
    out, tree, root, repo = (os.path.abspath(path) for path in (options.out, options.tree, options.root, options.repo))
    problems, blockers = [], []
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    manifest_path = os.path.join(tree, "manifest.json")
    with open(manifest_path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    files, per_component = [], {}
    for entry in manifest["files"]:
        component = component_of(entry)
        per_component[component] = per_component.get(component, 0) + 1
        files.append({"path": entry["path"], "sha256": entry["sha256"], "bytes": entry["bytes"],
                      "component": component, "origin": entry["origin"]})

    shutil.copyfile(os.path.join(repo, "takx/desktop-prototype/image/NOTICE.md"), os.path.join(out, "NOTICE.md"))
    policy = licence_gate.load_policy(options.policy)
    index = []
    collect_licences(out, tree, root, repo, problems, index)
    texts = collect_texts(out, tree, policy, options.texts_cache or os.path.join(root, "pkg", "licence-texts"),
                          options.fetch_texts, index, problems)
    write_index(out, index)
    licence_count = len(index)
    on_disk = sum(len(names) for _, _, names in os.walk(os.path.join(out, "LICENSES"))) - 1  # without INDEX.tsv
    if on_disk != licence_count or len({target for target, _ in index}) != licence_count:
        problems.append(f"LICENSES holds {on_disk} files but INDEX.tsv lists {licence_count}")
    patches = collect_patches(out, root, repo, problems, manifest)

    # The licence gate over the tree and the notices written so far.
    gate = licence_gate.Gate(policy)
    gate.tree(tree)
    gate.notices(out)
    gate_report = gate.report()
    with open(os.path.join(out, "licence-gate.json"), "w", encoding="utf-8") as stream:
        json.dump(gate_report, stream, indent=1)
        stream.write("\n")
    verdicts = {}
    for item in gate.items:
        if "!/" not in item["path"] and item["kind"] in ("jar", "asset", "native"):
            verdicts[item["path"]] = item
    aggregated = [(item["path"], item) for item in gate.items if "files" in item]
    for entry in files:
        item = verdicts.get(entry["path"])
        if item is None:
            item = next((found for glob, found in aggregated if licence_gate.fnmatch.fnmatchcase(entry["path"], glob)), None)
        entry["licence"] = item["licence"] if item else None
        entry["licence_gate"] = item["verdict"] if item else "unknown"
    embedded_text = {os.path.basename(os.path.dirname(target)) for target, _ in index
                     if os.sep + os.path.join("LICENSES", "jars") + os.sep in target}
    jars = [entry for entry in files if entry["path"].endswith(".jar")]

    locks = {}
    os.makedirs(os.path.join(out, "locks"))
    for path in ("takx/sources.lock.json", "takx/desktop-prototype/client/inputs.lock.json"):
        shutil.copyfile(os.path.join(repo, path), os.path.join(out, "locks", os.path.basename(path)))
        locks[path] = {"sha256": sha256_file(os.path.join(repo, path)), "copy": "locks/" + os.path.basename(path)}

    fetched, unverified, input_categories = [], [], {}
    fetch_path = os.path.join(root, "status/fetch.json")
    if os.path.isfile(fetch_path):
        with open(fetch_path, encoding="utf-8") as stream:
            fetch = json.load(stream)
        for item in fetch["inputs"]:
            fetched.append({name: item.get(name) for name in
                            ("kind", "name", "source", "method", "expected_sha256", "actual_sha256", "bytes",
                             "verified", "required", "built_by_prototype", "note")})
            if not item.get("verified") and not item.get("built_by_prototype"):
                unverified.append(item["name"])
            source = str(item.get("source") or "")
            if item.get("built_by_prototype"):
                category = "built_by_the_prototype_no_digest"
            elif item.get("kind") == "git-source":
                category = "git_checkout_local_clone_no_digest" if source.startswith("file:") else "git_checkout_no_digest"
            elif not item.get("verified"):
                category = "unverified"
            elif source.startswith("http") and item.get("actual_sha256"):
                category = "url_and_sha256"
            elif item.get("actual_sha256"):
                category = "sha256_without_url"
            else:
                category = "other"
            input_categories[category] = input_categories.get(category, 0) + 1
    else:
        problems.append("status/fetch.json is missing")

    receipts = {}
    providers = os.path.join(root, "P")
    for provider in sorted(os.listdir(providers)) if os.path.isdir(providers) else ():
        for name in ("receipt.json", "complete.json", "sources.json"):
            path = os.path.join(providers, provider, name)
            if os.path.isfile(path):
                receipts.setdefault(provider, {})[name] = sha256_file(path)
    sources = {}
    for name, url in (("takx57", "https://github.com/TAK-Product-Center/TAKX"), ("atak-civ", ATAK),
                      ("WorldWindJava", "https://github.com/NASAWorldWind/WorldWindJava")):
        revision = git(os.path.join(root, "src", name), "rev-parse", "HEAD")
        sources[name] = {"repository": url, "revision": revision.strip() if revision else None}
        if not revision:
            problems.append(f"no git revision for src/{name}")

    components = []
    for key, (label, licence, source, resolved, note) in COMPONENTS.items():
        components.append({"id": key, "label": label, "licence": licence, "source": source, "resolved": resolved,
                           "files_in_tree": per_component.get(key, 0), "note": note})
        if not resolved and per_component.get(key):
            blockers.append(f"component {key}: {note}")
    repository = repository_facts(repo)
    if repository["dirty"]:
        blockers.append("the repository working tree is dirty")
    if unverified:
        blockers.append("fetched inputs without a verified digest: " + ", ".join(unverified))
    blockers += [f"problem: {problem}" for problem in problems]
    flagged = gate_report["flagged"]
    if flagged:
        blockers.append("the licence gate flags %d item(s): %s (licence-gate.json; issue #934)" % (
            len(flagged), ", ".join("%d %s" % (count, verdict) for verdict, count in gate_report["counts"].items()
                                    if count and verdict != "allow")))
    blockers += ["export classification of this build is open (issue #934, compliance section D)",
                 "the image carries this manifest and the patches, not the complete corresponding source; "
                 "a distribution needs the source next to it or a written offer"]

    document = {
        "schema": 1,
        "label": LABEL,
        "publishable": not blockers,
        "publishable_blockers": blockers,
        "licence_gate": {"report": "licence-gate.json", "policy_sha256": sha256_file(options.policy),
                         "clean": gate_report["clean"], "build_may_proceed": gate_report["build_may_proceed"],
                         "counts": gate_report["counts"], "licences_in_use": gate_report["licences_in_use"],
                         "licence_texts": texts,
                         "flagged": [{key: item[key] for key in ("path", "kind", "verdict", "licence", "reason")}
                                     for item in flagged]},
        "tree": {"manifest_sha256": sha256_file(manifest_path), "files": len(files),
                 "bytes": sum(item["bytes"] for item in files),
                 "takx_source_revision": manifest["takx"]["source_revision"],
                 "substitutions": manifest["components"],
                 "licence_exclusions": manifest.get("licence_exclusions", []),
                 "kernel_jar_derivation": manifest.get("kernel_jar_derivation"),
                 "kernel_java_overlay": manifest.get("kernel_java_overlay")},
        "repository": repository,
        "repository_sources": sources,
        "base_image": BASE_IMAGE,
        "debian_packages": "debian-packages.tsv next to this file in the image: binary and source package versions; "
                           "sources from deb.debian.org or snapshot.debian.org with 'apt-get source NAME=VERSION'; "
                           "copyright files stay in /usr/share/doc",
        "jdk": JDK,
        "locks": locks,
        "components": components,
        "patches": patches,
        "receipts_sha256": receipts,
        "fetched_inputs": fetched,
        "files": files,
        "counts": {"files": len(files), "components": per_component, "licence_files": licence_count,
                   "patches_shipped": sum(1 for patch in patches if patch["shipped"]),
                   "patches_verified_against_receipt": sum(1 for patch in patches if patch.get("verified_against_receipt")),
                   "fetched_inputs": len(fetched), "fetched_inputs_unverified": len(unverified),
                   "fetched_inputs_by_category": input_categories,
                   "jars": len(jars),
                   "jars_with_a_licence_or_notice_file_inside": sum(
                       1 for entry in jars if os.path.basename(entry["path"]) in embedded_text),
                   "jars_covered_only_by_LICENSES_texts": sum(
                       1 for entry in jars if os.path.basename(entry["path"]) not in embedded_text and entry["licence"]),
                   "jars_without_any_licence_record": sum(1 for entry in jars if not entry["licence"]),
                   "licence_gate": gate_report["counts"]},
    }
    with open(os.path.join(out, "SOURCES.json"), "w", encoding="utf-8") as stream:
        json.dump(document, stream, indent=1)
        stream.write("\n")
    print(json.dumps({"out": out, "counts": document["counts"], "publishable": document["publishable"],
                      "blockers": len(blockers), "problems": problems,
                      "licence_gate_build_may_proceed": gate_report["build_may_proceed"]}, indent=1))
    for item in flagged:
        if item["verdict"] in licence_gate.FAILING:
            print("licence gate: %s %s: %s" % (item["verdict"].upper(), item["path"], item["reason"]), file=sys.stderr)
    return 1 if problems or not gate_report["build_may_proceed"] else 0


if __name__ == "__main__":
    sys.exit(main())
