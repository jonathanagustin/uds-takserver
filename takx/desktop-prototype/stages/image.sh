#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, image stage: build the self-contained TAKX 5.7 desktop
# image from a copy of the assembled tree and prove that it runs with nothing
# mounted from the host. The work is done by ../image/*; this script is the
# record of the exact commands. Nothing is pushed and no cluster is touched.
#
#   context       copy dist/<tree> to pkg/context/<tree> (cp -a) and compare;
#                 the original is only read. <tree> is TAKX_ASSEMBLE_NAME,
#                 as in assemble.sh (default takx57)
#   notices       image/gen_notices.py: licence texts, NOTICE.md, SOURCES.json,
#                 patches, lock files and the licence gate report -> pkg/notices.
#                 Fails when the licence gate (image/licence_gate.py) finds a
#                 denied or unknown JAR, native library or asset; SPDX licence
#                 texts are fetched once into pkg/licence-texts
#   gate          the licence gate alone, on the context copy and the notices
#   test          unit tests of the image scripts, of the bridge's token
#                 check and gate (web_auth.py, web_gate.py; tokens signed
#                 with the openssl command) and of the licence gate (no
#                 Docker), with every python3.11, 3.12 and 3.13 that is installed
#   build         image/build.sh: docker buildx build with the tree and the
#                 notices as named contexts and the build gates
#                 (verify/build_gates.sh); tags takx-desktop:5.7-proto-<digest12>
#   inspect       size, layers, 'docker history', labels, and a scan of every
#                 layer for a proxy CA, private keys, key stores and build
#                 paths (verify/scan_image.py) -> pkg/evidence/<run>/
#   selftest STEP image/verify/selftest.sh STEP: start | wait | check | auth |
#                 browser | reconnect | stats | supervision | audit | linker |
#                 identity | logs | stop
#   all           context, notices, test, build, inspect, then the self-test
#                 steps in order with a five-minute soak before 'stats'
#                 (about 12 minutes; run the steps one by one under a time limit)
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto):
#   dist/<tree>/              assembled tree (input, read-only here)
#   pkg/context/<tree>/       the copy the image is built from
#   pkg/notices/              notices build context
#   pkg/logs/image-build-<n>.log
#   pkg/status/image.json, pkg/status/selftest.json
#   pkg/evidence/<run>/       frames, probes, transcripts (TAKX_SELFTEST_RUN,
#                             default 'selftest')
#
# The self-test runs containers of the one image: takx-img-desktop
# (uid 10001), takx-img-web (uid 10002, token check on) and takx-img-jwks (a
# stand-in for the realm's key endpoint), with a read-only root, all
# capabilities dropped, --network none and at most 2 CPUs together.
# Writable are tmpfs mounts for /tmp, /run/takx/session, /run/takx/identity
# and /run/takx/web and a tmpfs volume for the RFB socket. X display :1.
#
# What is inside the image: TAKX 5.7 (0b59a9bb) built from public sources on
# Kernel 8.30.5 + project patches, CommonCommo 1.17.2 patched and the NASA
# WorldWind interop build. It is not the official TAKX 5.7 distribution.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
proto="$(cd "${here}/.." && pwd)"
repo="$(cd "${proto}/../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
tree_name="${TAKX_ASSEMBLE_NAME:-takx57}"
dist="${root}/dist/${tree_name}"
context="${root}/pkg/context/${tree_name}"
notices="${root}/pkg/notices"
evidence="${root}/pkg/evidence/${TAKX_SELFTEST_RUN:-selftest}"
selftest="${proto}/image/verify/selftest.sh"

image_tag() {
  python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["tags"][0])' "${root}/pkg/status/image.json"
}

do_context() {
  [[ -s "${dist}/manifest.json" ]] || { echo "image: no assembled tree at ${dist}; run assemble.sh" >&2; exit 1; }
  mkdir -p "${root}/pkg/context"
  if [[ -d "${context}" ]] && diff -rq "${dist}" "${context}" >/dev/null; then
    echo "image: ${context} is already identical to ${dist}"
  else
    rm -rf "${context}"
    cp -a "${dist}" "${context}"
    diff -rq "${dist}" "${context}"
    echo "image: copied ${dist} to ${context}"
  fi
  python3 -I "${proto}/image/verify_tree.py" "${context}" "$(sha256sum "${dist}/manifest.json" | cut -d' ' -f1)"
}

do_notices() {
  local ca=()
  # Python reads SSL_CERT_FILE; only needed behind a TLS-intercepting proxy
  # and only while a licence text is not in the cache yet.
  if [[ -s /root/.ccr/ca-bundle.crt ]]; then ca=(env SSL_CERT_FILE=/root/.ccr/ca-bundle.crt); fi
  "${ca[@]}" python3 -I "${proto}/image/gen_notices.py" --tree "${context}" --out "${notices}" --root "${root}" \
    --repo "${repo}" --fetch-texts
}

do_gate() {
  python3 -I "${proto}/image/licence_gate.py" --tree "${context}" --notices "${notices}" \
    --report "${root}/pkg/status/licence-gate.json"
}

do_test() {
  local python ran=0
  local suites=(takx/desktop-prototype/image/test_image.py takx/desktop-prototype/image/test_web_auth.py
    takx/desktop-prototype/image/test_web_gate.py takx/desktop-prototype/image/test_licence_gate.py
    takx/desktop-prototype/assemble/test_derive_jar.py)
  for python in python3.11 python3.12 python3.13; do
    if command -v "${python}" >/dev/null; then
      (cd "${repo}" && "${python}" -B -m unittest "${suites[@]}")
      ran=$((ran + 1))
    fi
  done
  if ((ran == 0)); then
    (cd "${repo}" && python3 -B -m unittest "${suites[@]}")
  fi
}

do_build() {
  TAKX_IMAGE_TREE="${context}" TAKX_IMAGE_NOTICES="${notices}" "${proto}/image/build.sh"
}

do_inspect() {
  local image must=() file
  image="$(image_tag)"
  mkdir -p "${evidence}" "${root}/pkg/tmp"
  {
    echo "image ${image}"
    docker image inspect "${image}" --format \
      'id {{.Id}}{{"\n"}}size_bytes {{.Size}}{{"\n"}}layers {{len .RootFS.Layers}}{{"\n"}}user {{.Config.User}}{{"\n"}}entrypoint {{json .Config.Entrypoint}}{{"\n"}}cmd {{json .Config.Cmd}}{{"\n"}}exposed {{json .Config.ExposedPorts}}{{"\n"}}labels {{json .Config.Labels}}'
    echo "descriptor $(docker image inspect "${image}" --format '{{json .Descriptor}}' 2>/dev/null || echo unknown)"
    echo "== layers"
    docker image inspect "${image}" --format '{{range .RootFS.Layers}}{{println .}}{{end}}'
    echo "== docker history --no-trunc"
    docker history --no-trunc --format '{{.Size}}\t{{.CreatedBy}}' "${image}"
  } >"${evidence}/image-inspect.txt"
  # Nothing in the build instructions may name the CA, a key or a password.
  {
    echo "history lines naming a CA file, key material or a credential:"
    docker history --no-trunc --format '{{.CreatedBy}}' "${image}" |
      grep -n -i -E 'ca-bundle|\.ccr|/run/secrets|BEGIN CERT|PRIVATE KEY|passw|token|api[_-]?key' || echo "none"
  } >"${evidence}/image-history-scan.txt"
  cat "${evidence}/image-history-scan.txt"
  # Every layer, file by file. The proxy CA of this sandbox is searched for
  # explicitly when its file exists; the CA bundle adds every certificate
  # that is not a public root.
  for file in /root/.ccr/agent-proxy-ca.crt /root/.ccr/system-trust.ca.pem; do
    if [[ -s "${file}" ]]; then must+=(--must-not-contain "${file}"); fi
  done
  if [[ -s /root/.ccr/ca-bundle.crt ]]; then must+=(--ca-bundle /root/.ccr/ca-bundle.crt); fi
  docker save "${image}" | TMPDIR="${root}/pkg/tmp" python3 -I "${proto}/image/verify/scan_image.py" "${must[@]}" \
    --report "${evidence}/image-scan.json" >/dev/null
  python3 -I -c 'import json, sys
report = json.load(open(sys.argv[1]))
print(json.dumps({key: report[key] for key in ("status", "layers", "files", "bytes", "private_ca_searched_for",
      "private_ca_hits", "private_keys", "pem_keys_in_unmodified_debian_package_files", "key_stores", "build_paths")}, indent=1))' "${evidence}/image-scan.json"
  # Licence texts, notices and the corresponding-source manifest in the image.
  docker run --rm --name takx-img-notices --network none --read-only --cap-drop ALL --user 10001:10001 --cpus 1 \
    --entrypoint bash "${image}" -c '
      cd /usr/share/takx-desktop
      echo "== /usr/share/takx-desktop"; ls
      echo "licence files (LICENSES/INDEX.tsv rows): $(($(wc -l <LICENSES/INDEX.tsv) - 1))"
      echo "patch files: $(find patches -type f | wc -l)"
      python3 -I -c "import json; d = json.load(open(\"SOURCES.json\")); print(\"SOURCES.json:\", json.dumps({\"label\": d[\"label\"], \"publishable\": d[\"publishable\"], \"blockers\": d[\"publishable_blockers\"], \"counts\": d[\"counts\"], \"repository_commit\": d[\"repository\"][\"commit\"], \"tree\": {k: d[\"tree\"][k] for k in (\"manifest_sha256\", \"files\", \"bytes\", \"takx_source_revision\")}}, indent=1))"
      python3 -I -c "import json; d = json.load(open(\"verify/licence-gate.json\")); print(\"licence gate, run inside the build:\", json.dumps({k: d[k] for k in (\"clean\", \"build_may_proceed\", \"counts\", \"licences_in_use\")}))"
      echo "licence texts (LICENSES/texts): $(find LICENSES/texts -type f | wc -l)"
      echo "debian-packages.tsv rows: $(wc -l <debian-packages.tsv)"
      echo "Debian copyright files: $(find /usr/share/doc -name copyright | wc -l); novnc: $(ls /usr/share/doc/novnc/copyright)"
      echo "JDK legal files: $(find "${JAVA_HOME}/legal" -type f | wc -l)"
      head -n 12 NOTICE.md
      echo "== verify reports from the build"; ls verify; cat verify/tree.json verify/glxinfo.txt; tail -n 8 verify/elf-audit.txt' \
    >"${evidence}/image-notices.txt"
  cat "${evidence}/image-notices.txt"
}

do_all() {
  do_context; do_notices; do_gate; do_test; do_build; do_inspect
  "${selftest}" audit
  "${selftest}" identity
  "${selftest}" start
  "${selftest}" wait 480
  sleep 130
  "${selftest}" check
  "${selftest}" auth
  "${selftest}" browser || echo "image: browser step did not pass or was not run"
  "${selftest}" reconnect || echo "image: reconnect step did not pass or was not run"
  sleep 60
  "${selftest}" stats
  "${selftest}" supervision
  "${selftest}" linker
  "${selftest}" stop
}

case "${1:-}" in
  context) do_context;;
  notices) do_notices;;
  gate) do_gate;;
  test) do_test;;
  build) do_build;;
  inspect) do_inspect;;
  selftest) shift; "${selftest}" "$@";;
  all) do_all;;
  -h|--help|"") sed -n '5,54p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) echo "image.sh: unknown target '$1'" >&2; exit 2;;
esac
