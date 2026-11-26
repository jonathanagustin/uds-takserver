#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# readelf and 'ldd -r' transcript of every Linux x86-64 native library the
# TAKX tree bundles, loose or inside a JAR. Works in the built image with a
# read-only root: it needs a writable TMPDIR (about 250 MB) and, for the
# readelf part, binutils (present only in the build's verify stage; without
# it that part is skipped and said so).
#
# Usage: elf_audit.sh [REPORT]      (default: standard output)
#
# The libraries of one JAR directory are extracted next to each other and
# made visible under their SONAMEs through LD_LIBRARY_PATH. That is what the
# loaders in the tree achieve by mapping each library by absolute path in
# dependency order: a later DT_NEEDED finds an already loaded SONAME. No file
# of the tree is changed and nothing is left behind.
#
# Fails when 'ldd -r' reports a library as not found or a symbol as
# undefined (one upstream JOGL library is the exception, see below), when
# anything resolves below /var/lib/takx-proto, when a RPATH or RUNPATH names
# that directory, or when one has an empty or relative element: glibc reads
# an empty element as the working directory of the process, which the
# application user can write. '$ORIGIN' elements are fine.
set -euo pipefail

report="${1:-}"
tree=/opt/takx
work="$(mktemp -d "${TMPDIR:-/tmp}/elf-audit.XXXXXX")"
trap 'rm -rf "${work}"' EXIT
transcript="${work}/transcript.txt"

test ! -e /var/lib/takx-proto || { echo "elf_audit: /var/lib/takx-proto exists in this filesystem"; exit 1; }

python3 -I - "${tree}" "${work}" <<'PY'
import os, sys, zipfile
sys.path.insert(0, "/opt/takx-desktop")
from check_native_closure import parse_elf
tree, work = sys.argv[1:]
skip = ("/Android/", "/Solaris/")
number = 0
def is_linux_amd64(head):
    return head[:4] == b"\x7fELF" and head[4] == 2 and head[18:20] == b"\x3e\x00"
def group_dir(label):
    global number
    number += 1
    path = os.path.join(work, "%03d" % number)
    os.makedirs(path)
    os.mkdir(os.path.join(path, ".sonames"))
    with open(os.path.join(path, ".origin"), "w") as stream:
        stream.write(label + "\n")
    return path
def by_soname(target, base, data):
    # SONAME view: one symbolic link per library, named by its SONAME.
    os.symlink(os.path.join(target, base), os.path.join(target, ".sonames", parse_elf(data)["soname"] or base))
for folder, dirs, files in os.walk(tree):
    dirs.sort()
    loose = []
    for name in sorted(files):
        full = os.path.join(folder, name)
        if name.endswith(".jar"):
            with zipfile.ZipFile(full) as archive:
                groups = {}
                for member in sorted(archive.namelist()):
                    base = os.path.basename(member)
                    if not (base.endswith(".so") or ".so." in base) or any(part in "/" + member for part in skip):
                        continue
                    data = archive.read(member)
                    if is_linux_amd64(data[:20]):
                        groups.setdefault(os.path.dirname(member), []).append((base, data))
                for inner, members in groups.items():
                    target = group_dir(os.path.relpath(full, tree) + "!/" + inner)
                    for base, data in members:
                        with open(os.path.join(target, base), "wb") as stream:
                            stream.write(data)
                        by_soname(target, base, data)
        else:
            with open(full, "rb") as stream:
                if is_linux_amd64(stream.read(20)):
                    loose.append(full)
    if loose:
        target = group_dir(os.path.relpath(folder, tree))
        for full in loose:
            os.symlink(full, os.path.join(target, os.path.basename(full)))
            with open(full, "rb") as stream:
                by_soname(target, os.path.basename(full), stream.read())
PY

# One library is allowed undefined symbols, and they are counted on their own
# line: libnewt_head.so of the JOGL 2.5.0 Maven artifact, shipped unmodified.
# It carries JOGL's window drivers for every platform in one object and
# leaves the Broadcom VideoCore and EGL helper entry points (and ceil/floor)
# to lazy binding; a desktop X11 session never calls them.
upstream_lazy='modules/prototype/libs/main/jogl-all-2.5.0-natives-linux-amd64.jar!/natives/linux-amd64/libnewt_head.so'
libraries=0; unresolved=0; undefined=0; undefined_upstream=0; proto_resolved=0; proto_runpath=0; unsafe_runpath=0
java_home="${JAVA_HOME:?JAVA_HOME is not set}"
jvm_lib="${java_home}/lib:${java_home}/lib/server"
{
  echo "elf_audit $(date -u +%FT%TZ) tree ${tree} dynamic sections read with $(command -v readelf || echo 'check_native_closure.parse_elf (no readelf here)')"
  for group in "${work}"/*/; do
    origin="$(cat "${group}.origin")"
    view="${group}.sonames"
    for library in "${group}"*; do
      libraries=$((libraries + 1))
      echo "=== ${origin}/$(basename "${library}")"
      if command -v readelf >/dev/null; then
        dynamic="$(readelf -d "${library}" | grep -E 'NEEDED|SONAME|RPATH|RUNPATH' | sed -E 's/^ *0x[0-9a-f]+ //' || true)"
      else
        dynamic="$(python3 -I -c '
import sys
sys.path.insert(0, "/opt/takx-desktop")
from check_native_closure import parse_elf
with open(sys.argv[1], "rb") as stream:
    facts = parse_elf(stream.read())
for needed in facts["needed"]:
    print("(NEEDED) [%s]" % needed)
if facts["soname"]:
    print("(SONAME) [%s]" % facts["soname"])
for kind in ("rpath", "runpath"):
    if kind + "_raw" in facts:
        print("(%s) [%s]" % (kind.upper(), facts[kind + "_raw"]))' "${library}")"
      fi
      echo "${dynamic}"
      if grep -E 'RPATH|RUNPATH' <<<"${dynamic}" | grep -q '/var/lib/takx-proto'; then
        proto_runpath=$((proto_runpath + 1))
      fi
      while IFS= read -r paths; do
        # A trailing or doubled colon is an empty element.
        if [[ "${paths}" == *: || "${paths}" == :* || "${paths}" == *::* || -z "${paths}" ]]; then
          unsafe_runpath=$((unsafe_runpath + 1)); continue
        fi
        IFS=: read -r -a elements <<<"${paths}"
        for element in "${elements[@]}"; do
          # shellcheck disable=SC2016 # a literal $ORIGIN, as it is written in the ELF file
          if [[ "${element}" != /* && "${element}" != '$ORIGIN'* && "${element}" != '${ORIGIN}'* ]]; then
            unsafe_runpath=$((unsafe_runpath + 1)); break
          fi
        done
      done < <(grep -E 'RPATH|RUNPATH' <<<"${dynamic}" | sed -E 's/^[^[]*\[(.*)\]$/\1/' || true)
      linked="$(LD_LIBRARY_PATH="${view}:${group}:${jvm_lib}" ldd -r "${library}" 2>&1 | sed "s#${work}#<extracted>#g" || true)"
      echo "${linked}"
      unresolved=$((unresolved + $(grep -c 'not found' <<<"${linked}" || true)))
      count="$(grep -c 'undefined symbol' <<<"${linked}" || true)"
      if [[ "${origin}/$(basename "${library}")" == "${upstream_lazy}" ]]; then
        undefined_upstream=$((undefined_upstream + count))
      else
        undefined=$((undefined + count))
      fi
      proto_resolved=$((proto_resolved + $(grep -c '=> /var/lib/takx-proto' <<<"${linked}" || true)))
    done
  done
  echo "=== summary"
  echo "libraries ${libraries}"
  echo "ldd_not_found ${unresolved}"
  echo "ldd_undefined_symbols ${undefined}"
  echo "ldd_undefined_symbols_in_upstream_jogl_libnewt_head ${undefined_upstream}"
  echo "resolved_below_var_lib_takx_proto ${proto_resolved}"
  echo "libraries_with_runpath_below_var_lib_takx_proto ${proto_runpath}"
  echo "run_paths_with_an_empty_or_relative_element ${unsafe_runpath}"
  echo "var_lib_takx_proto_exists no"
} >"${transcript}"
if [[ -n "${report}" ]]; then cp "${transcript}" "${report}"; else cat "${transcript}"; fi
tail -n 9 "${transcript}" >&2
((unresolved == 0 && undefined == 0 && proto_resolved == 0 && proto_runpath == 0 && unsafe_runpath == 0))
