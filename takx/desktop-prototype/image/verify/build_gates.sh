#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Build gates of image/Dockerfile (stage 'verify'). Runs as root inside the
# image under construction, plus binutils and mesa-utils, which are not in
# the final image. Any failed gate fails the build. The reports written to
# OUT are copied into the final image at /usr/share/takx-desktop/verify.
#
# Usage: build_gates.sh TREE_MANIFEST_SHA256 OUT
#
#   1. tree       every file of /opt/takx matches manifest.json, and the
#                 manifest has the digest the build was started with
#   2. closure    every DT_NEEDED of every bundled x86-64 library resolves to
#                 a bundled SONAME, the JDK or ldconfig; no RUNPATH directory
#                 exists (check_native_closure.py)
#   3. elf audit  readelf and 'ldd -r' transcript of every bundled Linux
#                 x86-64 library, linked the way the loaders in the tree do
#                 it: siblings visible under their SONAMEs. Nothing may be
#                 unresolved and nothing may resolve below /var/lib/takx-proto
#   4. display    Xtigervnc as uid 10001 gives GLX direct rendering on llvmpipe
#   5. keytool    the JDK tool the identity stage needs runs
#   6. licences   licence_gate.py over the tree and the notices as they are
#                 in the image: nothing denied, nothing unknown, the text of
#                 every licence in use present. Items under review do not
#                 fail the build; they keep the image unpublishable and are
#                 listed in the report
set -euo pipefail

manifest_sha256="$1"; out="$2"
app=/opt/takx-desktop
tree=/opt/takx
other_os=(
  --other-os 'modules/prototype/libs/main/jSerialComm-2.6.2.jar!/Android/'
  --other-os 'modules/prototype/libs/main/jSerialComm-2.6.2.jar!/Solaris/'
)

mkdir -p "${out}"
test ! -e /var/lib/takx-proto

echo "== gate 1: tree"
[[ "${manifest_sha256}" =~ ^[0-9a-f]{64}$ ]] || { echo "TREE_MANIFEST_SHA256 build argument is missing"; exit 1; }
python3 -I "${app}/verify_tree.py" "${tree}" "${manifest_sha256}" | tee "${out}/tree.json"

echo "== gate 2: native closure"
python3 -I "${app}/check_native_closure.py" "${tree}" --jvm-lib "${JAVA_HOME:?JAVA_HOME is not set}/lib" "${other_os[@]}" \
  --report "${out}/native-closure.json"

echo "== gate 3: ELF audit"
"${app}/elf_audit.sh" "${out}/elf-audit.txt"

echo "== gate 4: display"
install -d -o 10001 -g 10001 /tmp/gate-home /tmp/gate-vnc
# shellcheck disable=SC2016 # the script text is expanded by the inner shell
setpriv --reuid 10001 --regid 10001 --clear-groups env HOME=/tmp/gate-home bash -euo pipefail -c '
  Xtigervnc :7 -geometry 1920x1080 -depth 24 -rfbport -1 -rfbunixpath /tmp/gate-vnc/rfb.sock \
    -rfbunixmode 0660 -SecurityTypes None -nolisten tcp >/tmp/gate-home/x.log 2>&1 &
  xpid=$!
  export DISPLAY=:7 LIBGL_ALWAYS_SOFTWARE=1
  for _ in $(seq 50); do if xdpyinfo >/dev/null 2>&1; then break; fi; sleep 0.2; done
  glxinfo -B >/tmp/gate-home/glxinfo.txt
  kill "${xpid}"; wait "${xpid}" || true
'
grep -E 'direct rendering|OpenGL (vendor|renderer|version|core profile version)' /tmp/gate-home/glxinfo.txt | tee "${out}/glxinfo.txt"
grep -q 'direct rendering: Yes' "${out}/glxinfo.txt"
grep -q 'OpenGL renderer string: llvmpipe' "${out}/glxinfo.txt"
rm -rf /tmp/gate-home /tmp/gate-vnc /tmp/.X7-lock /tmp/.X11-unix

echo "== gate 5: keytool"
keytool -help >/dev/null 2>&1
java -version 2>&1 | tee "${out}/java-version.txt"
grep -q 'Temurin-17.0.21+9' "${out}/java-version.txt"

echo "== gate 6: licences"
python3 -I "${app}/licence_gate.py" --tree "${tree}" --policy "${app}/licence-policy.json" \
  --notices /usr/share/takx-desktop --report "${out}/licence-gate.json" 2>"${out}/licence-gate-flagged.txt" ||
  { grep -E '^(DENY|UNKNOWN)' "${out}/licence-gate-flagged.txt"; echo "the licence gate failed"; exit 1; }
grep -c '' "${out}/licence-gate-flagged.txt" | sed 's/^/items the licence gate flags for review: /'
test ! -e "${tree}/modules/prototype/libs/main/ffmpeg-4.1.3-1.5.1-linux-x86_64.jar"
test -z "$(find "${tree}" -name '*joost*' -o -name '*jocl*' -o -name '*wizard-reference*')"

date -u +%FT%TZ >"${out}/verify.ok"
echo "all gates passed"
