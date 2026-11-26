#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Diagnostics for Kernel controls that did not pass (Kernel 8.30.5 + project
# patches; kernel/controls.py). It repeats nothing as a control and decides
# nothing: it only records, for the JVM command line of the stock-basic
# control, what a reader needs to tell a class problem from a library problem.
#
#   ci/diagnose-controls.sh OUT
#
# OUT is a directory below the work root; gdal-probe.log is written there. It
# does nothing when P/kernel-composed/controls/controls.json says all_passed,
# or when there is no stock-basic.log. The exit status is always 0.
#
# What it records, in the runtime image the controls ran in:
#   1. every class path entry that holds org/gdal/gdal/gdalJNI.class, with its
#      SHA-256, and the static initializer and isAvailable() of the class the
#      JVM would load (javap);
#   2. shared objects the extracted native libraries need and the image lacks
#      (ldd);
#   3. a probe JVM with the flags, library path and class path of the control:
#      EngineLibrary.initialize(), then the first use of gdalJNI, with the
#      source of the class, isAvailable(), VersionInfo and everything the JVM
#      prints (the binding reports a failed load on standard error);
#   4. the same without EngineLibrary.initialize(): the binding alone, which
#      shows the loader's own message for libgdalalljni.so.
#
# Environment: TAKX_PROTO_ROOT (default /var/lib/takx-proto), TAKX_CI_REPO (the
# repository copy of ci/enter.sh; default: the repository of this file),
# TAKX_CI_DIAGNOSE=always (record even when every control passed).
set -uo pipefail

if ((EUID != 0)); then
  exec sudo -E env "PATH=${PATH}" "DOCKER_CONFIG=${DOCKER_CONFIG:-${HOME}/.docker}" "$0" "$@"
fi
[[ $# -eq 1 ]] || { echo "usage: diagnose-controls.sh OUT" >&2; exit 0; }
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="${TAKX_CI_REPO:-$(cd "${here}/../../.." && pwd)}"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
out="$1"
controls="${root}/P/kernel-composed/controls"
say() { printf 'diagnose-controls.sh: %s\n' "$*" >&2; }

case "${out}" in "${root}"/*) ;; *) say "OUT must be below ${root}"; exit 0;; esac
[[ -s "${controls}/stock-basic.log" ]] || { say "no stock-basic.log; nothing to diagnose"; exit 0; }
if [[ "${TAKX_CI_DIAGNOSE:-}" != always ]] && python3 -I -c 'import json, sys
sys.exit(0 if json.load(open(sys.argv[1])).get("all_passed") is True else 1)' "${controls}/controls.json" 2>/dev/null; then
  say "every control passed; nothing to diagnose"
  exit 0
fi
mkdir -p "${out}"
cat > "${out}/GdalProbe.java" <<'JAVA'
// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
/** Diagnostic only (ci/diagnose-controls.sh): reports, never asserts. */
public class GdalProbe {
  static void say(String text) { System.out.println("PROBE " + text); }
  public static void main(String[] args) {
    say("os.arch=" + System.getProperty("os.arch") + " java.version=" + System.getProperty("java.version"));
    say("java.library.path=" + System.getProperty("java.library.path"));
    ClassLoader loader = ClassLoader.getSystemClassLoader();
    if (args[0].equals("engine")) {
      try {
        Class.forName("com.atakmap.map.EngineLibrary", true, loader).getMethod("initialize").invoke(null);
        say("EngineLibrary.initialize() returned");
      } catch (Throwable failure) {
        say("EngineLibrary.initialize() threw " + failure + " cause " + failure.getCause());
      }
    }
    try {
      Class<?> binding = Class.forName("org.gdal.gdal.gdalJNI", false, loader);
      say("gdalJNI comes from " + binding.getProtectionDomain().getCodeSource().getLocation());
      say("gdalJNI.isAvailable()=" + binding.getMethod("isAvailable").invoke(null));
    } catch (Throwable failure) {
      say("gdalJNI threw " + failure + " cause " + failure.getCause());
    }
    try {
      Object version = Class.forName("org.gdal.gdal.gdal", true, loader).getMethod("VersionInfo", String.class)
          .invoke(null, "RELEASE_NAME");
      say("gdal.VersionInfo(RELEASE_NAME)=" + version);
    } catch (Throwable failure) {
      say("gdal.VersionInfo threw " + failure + " cause " + failure.getCause());
    }
  }
}
JAVA

# The script below runs inside the container; nothing in it is expanded here.
# shellcheck disable=SC2016
"${repo}/takx/desktop-prototype/run-in-toolchain.sh" --runtime -- bash -c '
set -uo pipefail
controls="$1"; out="$2"
read -r -a argv < <(head -n 1 "$controls/stock-basic.log")
java="${argv[1]}"; flags=(); classpath=""
for ((i = 2; i < ${#argv[@]}; i++)); do
  if [[ "${argv[i]}" == -cp ]]; then classpath="${argv[i + 1]}"; break; fi
  flags+=("${argv[i]}")
done
[[ -x "$java" && -n "$classpath" ]] || { echo "the command line of stock-basic.log was not understood"; exit 0; }
bin="$(dirname "$java")"
library=""
for flag in "${flags[@]}"; do
  case "$flag" in -Djava.library.path=*) library="${flag#-Djava.library.path=}"; library="${library%%:*}";; *) ;; esac
done
echo "===== control command line"
head -n 1 "$controls/stock-basic.log" | cut -c1-600
echo "===== class path entries with org/gdal/gdal/gdalJNI.class"
IFS=: read -r -a entries <<<"$classpath"
for entry in "${entries[@]}"; do
  if [[ -d "$entry" && -f "$entry/org/gdal/gdal/gdalJNI.class" ]]; then
    echo "directory $entry $(sha256sum "$entry/org/gdal/gdal/gdalJNI.class" | cut -d" " -f1)"
  elif [[ -f "$entry" ]] && unzip -l "$entry" org/gdal/gdal/gdalJNI.class >/dev/null 2>&1; then
    echo "jar $entry sha256 $(sha256sum "$entry" | cut -d" " -f1) class sha256 $(unzip -p "$entry" org/gdal/gdal/gdalJNI.class | sha256sum | cut -d" " -f1)"
  fi
done
echo "===== javap: fields, static initializer and isAvailable of the class the JVM loads"
"$bin/javap" -p -c -cp "$classpath" org.gdal.gdal.gdalJNI > "$out/gdalJNI.javap.txt" 2>&1
grep -n "available\|static {}" "$out/gdalJNI.javap.txt" | head -n 20
sed -n "/static {};/,\$p" "$out/gdalJNI.javap.txt" | head -n 60
echo "===== extracted native directory $library"
ls -l "$library" 2>&1
echo "===== ldd: needed objects that are not found (with the directory itself on the loader path)"
for object in "$library"/*.so; do
  [[ -f "$object" ]] || continue
  echo "--- $(basename "$object") $(sha256sum "$object" | cut -d" " -f1)"
  LD_LIBRARY_PATH="$library" ldd "$object" 2>&1 | grep -v "=> /" | grep -v "linux-vdso\|ld-linux" || true
done
echo "===== probe: EngineLibrary.initialize(), then the binding (flags of the control)"
"$java" "${flags[@]}" -cp "$classpath" "$out/GdalProbe.java" engine 2>&1 | head -n 200
echo "exit ${PIPESTATUS[0]}"
echo "===== probe: the binding alone"
"$java" "${flags[@]}" -cp "$classpath" "$out/GdalProbe.java" binding 2>&1 | head -n 200
echo "exit ${PIPESTATUS[0]}"
echo "===== probe with library logging"
"$java" "${flags[@]}" -Xlog:library=info -cp "$classpath" "$out/GdalProbe.java" engine 2>&1 \
  | grep "Loaded library\|PROBE\|UnsatisfiedLink\|Native library load failed\|cannot open\|undefined symbol" | head -n 80
echo "===== processor"
grep -m1 "model name\|CPU part" /proc/cpuinfo
' probe "${controls}" "${out}" > "${out}/gdal-probe.log" 2>&1
say "wrote ${out}/gdal-probe.log ($(wc -l < "${out}/gdal-probe.log") lines)"
exit 0
