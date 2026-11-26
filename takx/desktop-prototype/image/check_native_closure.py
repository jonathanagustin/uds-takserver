#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Check that every native library of an assembled TAKX tree can be linked
on this system without any directory outside the tree and the system.

  check_native_closure.py TREE [--report FILE] [--system-list FILE]
                          [--jvm-lib DIR ...] [--other-os PREFIX ...]

Every ELF shared object is read, loose or inside a JAR, with a small ELF
parser (no binutils needed). For each x86-64 library every DT_NEEDED name
must be met by

  bundled  a library next to it (same directory or same JAR directory) whose
           SONAME, or file name when it has none, is that name. The loaders
           in the tree (the Kernel stock loader, JOGL, JavaCPP) map their
           libraries by absolute path in dependency order, so the dynamic
           linker finds an already loaded SONAME and searches no directory;
  jvm      a library of the JDK that runs the client (libjawt.so and others);
  system   an entry of 'ldconfig -p'.

RPATH and RUNPATH are recorded, never relied on. The check fails when a name
is unresolved, or when an absolute RPATH/RUNPATH directory exists on this
system: such a directory would be searched and could supply a library that
is not part of the tree. Libraries of another machine type, and libraries
below an --other-os prefix (builds for Android or Solaris inside a
multi-platform JAR), are listed as foreign and not checked; they cannot be
loaded here.
"""
import argparse
import io
import json
import os
import struct
import subprocess
import sys
import zipfile

DT_NEEDED, DT_STRTAB, DT_SONAME, DT_RPATH, DT_RUNPATH = 1, 5, 14, 15, 29
PT_LOAD, PT_DYNAMIC = 1, 2
MACHINES = {3: "x86", 40: "arm", 62: "x86-64", 183: "aarch64"}


class NotElf(ValueError):
    pass


def parse_elf(data):
    """Return machine, soname, needed, rpath and runpath of an ELF image."""
    if len(data) < 64 or data[:4] != b"\x7fELF":
        raise NotElf("no ELF magic")
    bits, order = data[4], data[5]
    if bits not in (1, 2) or order not in (1, 2):
        raise NotElf("unknown ELF class or byte order")
    end = "<" if order == 1 else ">"
    machine = struct.unpack_from(end + "H", data, 18)[0]
    facts = {"machine": MACHINES.get(machine, str(machine)), "bits": 32 * bits,
             "soname": None, "needed": [], "rpath": [], "runpath": []}
    if bits == 2:
        phoff, = struct.unpack_from(end + "Q", data, 32)
        phentsize, phnum = struct.unpack_from(end + "HH", data, 54)
    else:
        phoff, = struct.unpack_from(end + "I", data, 28)
        phentsize, phnum = struct.unpack_from(end + "HH", data, 42)
    loads, dynamic = [], None
    for index in range(phnum):
        base = phoff + index * phentsize
        if bits == 2:
            kind, _flags, offset, vaddr, _paddr, filesz = struct.unpack_from(end + "IIQQQQ", data, base)
        else:
            kind, offset, vaddr, _paddr, filesz = struct.unpack_from(end + "IIIII", data, base)
        if kind == PT_LOAD:
            loads.append((vaddr, offset, filesz))
        elif kind == PT_DYNAMIC:
            dynamic = (offset, filesz)
    if dynamic is None:
        return facts
    entry = "qQ" if bits == 2 else "iI"
    size = struct.calcsize(end + entry)
    tags = []
    for position in range(dynamic[0], dynamic[0] + dynamic[1], size):
        tag, value = struct.unpack_from(end + entry, data, position)
        if tag == 0:
            break
        tags.append((tag, value))
    strtab = next((value for tag, value in tags if tag == DT_STRTAB), None)
    if strtab is None:
        return facts
    table = next((offset + strtab - vaddr for vaddr, offset, filesz in loads
                  if vaddr <= strtab < vaddr + filesz), None)
    if table is None:
        raise NotElf("DT_STRTAB is outside every PT_LOAD segment")

    def text(value):
        start = table + value
        return data[start:data.index(b"\0", start)].decode("utf-8", "replace")

    for tag, value in tags:
        if tag == DT_NEEDED:
            facts["needed"].append(text(value))
        elif tag == DT_SONAME:
            facts["soname"] = text(value)
        elif tag == DT_RPATH:
            facts["rpath"] = [part for part in text(value).split(":") if part]
            facts["rpath_raw"] = text(value)
        elif tag == DT_RUNPATH:
            facts["runpath"] = [part for part in text(value).split(":") if part]
            facts["runpath_raw"] = text(value)
    return facts


def looks_native(name):
    base = os.path.basename(name)
    return base.endswith(".so") or ".so." in base


def collect(tree):
    """Yield (container, group, name, facts) for every ELF object of the tree."""
    for folder, dirs, files in os.walk(tree):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(folder, name)
            relative = os.path.relpath(full, tree)
            if name.endswith(".jar"):
                with zipfile.ZipFile(full) as archive:
                    for member in sorted(archive.namelist()):
                        if not looks_native(member):
                            continue
                        try:
                            facts = parse_elf(archive.read(member))
                        except NotElf:
                            continue
                        yield relative, os.path.dirname(member), os.path.basename(member), facts
            else:
                with open(full, "rb") as stream:
                    if stream.read(4) != b"\x7fELF":
                        continue
                    stream.seek(0)
                    yield "", os.path.dirname(relative), name, parse_elf(stream.read())


def system_sonames(listing=None):
    """Map SONAME to path from 'ldconfig -p' output (x86-64 entries)."""
    if listing is None:
        listing = subprocess.run(["ldconfig", "-p"], check=True, capture_output=True, text=True).stdout
    found = {}
    for line in io.StringIO(listing):
        if "=>" not in line:
            continue
        left, path = line.strip().split(" => ", 1)
        name, _, flags = left.partition(" (")
        if "x86-64" in flags or "x86_64" in flags:
            found.setdefault(name, path)
    return found


def jvm_sonames(directories):
    found = {}
    for directory in directories:
        for folder, _dirs, files in os.walk(directory):
            for name in sorted(files):
                if looks_native(name):
                    found.setdefault(name, os.path.join(folder, name))
    return found


def check(tree, system, jvm, other_os=(), exists=os.path.isdir):
    """Return the report dictionary; report['problems'] is empty on success."""
    objects = list(collect(tree))
    groups = {}
    def label(container, group, name):
        return f"{container}!/{group}/{name}" if container else f"{group}/{name}"

    def foreign(where, facts):
        if facts["machine"] != "x86-64":
            return "machine " + facts["machine"]
        if any(where.startswith(prefix) for prefix in other_os):
            return "another operating system"
        return None

    for container, group, name, facts in objects:
        if not foreign(label(container, group, name), facts):
            groups.setdefault((container, group), {})[facts["soname"] or name] = name
    report = {"libraries": [], "foreign": [], "problems": [], "dead_search_paths": []}
    for container, group, name, facts in objects:
        where = label(container, group, name)
        reason = foreign(where, facts)
        if reason:
            report["foreign"].append({"library": where, "reason": reason})
            continue
        resolved = {}
        for needed in facts["needed"]:
            if needed in groups[(container, group)]:
                resolved[needed] = "bundled:" + groups[(container, group)][needed]
            elif needed in jvm:
                resolved[needed] = "jvm:" + jvm[needed]
            elif needed in system:
                resolved[needed] = "system:" + system[needed]
            else:
                resolved[needed] = "UNRESOLVED"
                report["problems"].append(f"{where}: DT_NEEDED {needed} is not bundled, in the JDK or known to ldconfig")
        for kind in ("rpath", "runpath"):
            for path in facts[kind]:
                if path.startswith("$ORIGIN") or path.startswith("${ORIGIN}"):
                    continue
                if exists(path):
                    report["problems"].append(f"{where}: {kind.upper()} directory {path} exists and would be searched")
                else:
                    report["dead_search_paths"].append({"library": where, "kind": kind, "path": path})
        report["libraries"].append({"library": where, "soname": facts["soname"], "needed": resolved,
                                    "rpath": facts["rpath"], "runpath": facts["runpath"]})
    report["counts"] = {"x86_64": len(report["libraries"]), "foreign": len(report["foreign"]),
                        "dead_search_paths": len(report["dead_search_paths"]),
                        "problems": len(report["problems"])}
    report["status"] = "failed" if report["problems"] else "ok"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tree")
    parser.add_argument("--report")
    parser.add_argument("--system-list", help="file with 'ldconfig -p' output instead of running ldconfig")
    parser.add_argument("--jvm-lib", action="append", default=[], help="JDK library directory (repeatable)")
    parser.add_argument("--other-os", action="append", default=[],
                        help="library path prefix that holds builds for another operating system (repeatable)")
    options = parser.parse_args(argv)
    listing = None
    if options.system_list:
        with open(options.system_list, encoding="utf-8") as stream:
            listing = stream.read()
    report = check(options.tree, system_sonames(listing), jvm_sonames(options.jvm_lib), tuple(options.other_os))
    if options.report:
        with open(options.report, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=1, sort_keys=True)
            stream.write("\n")
    for problem in report["problems"]:
        print(f"check_native_closure: {problem}", file=sys.stderr)
    print(json.dumps(report["counts"], sort_keys=True))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
