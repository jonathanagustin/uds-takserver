#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, client stage: compile and package the official TAKX 5.7
# sources (0b59a9bb) from scratch, and resolve the runtime JAR closure.
#
#   prepare       prep, xjc, templates: Bootstrap preferences, Bootstrap and
#                 ProcessManager javac; XJC over the seven XSDs; java-template
#                 expansion. Needs no Kernel.
#   runtime-jars  Apache Maven resolves the runtime-only closure from Maven
#                 Central and JogAmp into P/takx57-runtime-jars (manifest.json).
#   wait-kernel   poll status/kernel-java-ready.json every 60 s for the
#                 composed Kernel JAR (TAKX_CLIENT_WAIT_S seconds, default
#                 10800); returns 3 when the wait ran out.
#   compile       platform, aspects, client: whole-Platform javac with the real
#                 AutoValue and EventBus processors, ajc over the four .aj
#                 aspects (no weaving), Client javac of all 1,678 sources.
#   package       bootstrap, preferences-factory, platform, client and
#                 processmanager JARs with their resources.
#   lock          write P/takx57/inputs.lock.json and compare it with the
#                 tracked client/inputs.lock.json (on arm64:
#                 client/inputs.lock.arm64.json).
#   all           everything above, in that order.
#
# The work is done by ../client/build.py and ../client/resolve_runtime.py
# inside the takx-desktop-proto:toolchain image through run-in-toolchain.sh.
# Nothing is fed to takx/client-compiler and its locks are only read: they are
# the specification of the source lists, the classpath order, the processor
# path, the javac flags and the expected class-name sets. The Kernel is public
# 8.30.5 plus project patches and CommonCommo is 1.17.2 patched; neither is
# the private 8.57.11 / 1.20.0 that TAKX 5.7 declares. No GUI is launched and
# no native library is loaded here.
#
# Usage:
#   client.sh [all|prepare|runtime-jars|wait-kernel|compile|package|lock]...
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto):
#   P/takx57/<step>/               output of one build.py step
#   P/takx57/receipts/<step>.json  its receipt (inputs, commands, comparisons)
#   P/takx57/inputs.lock.json      every input digest actually consumed
#   P/takx57/result.json           output digests and the comparison summary
#   P/takx57-runtime-jars/         jars/ and manifest.json
#   failed/takx57-<step>-<n>/      a replaced or failed step output
#   logs/takx57-<step>.log         driver output of the step
#   status/takx57-<step>.json      {"stage","status","outputs","seconds","notes"}
#   status/takx57.json             the whole stage, written by 'lock'
#
# Environment:
#   TAKX_PROTO_ROOT          work root
#   TAKX_CLIENT_KERNEL_JAR   composed Kernel JAR; default: the "jar"."path" of
#                            status/kernel-java-ready.json
#   TAKX_CLIENT_WAIT_S       wait-kernel budget in seconds (default 10800)
#   TAKX_CLIENT_CPUS         docker --cpus for every step (default 1.5; the
#                            native Kernel build runs next to this stage)
#   TAKX_CLIENT_FORCE        1 reruns steps whose receipt already says ok
#   TAKX_CLIENT_RECORD_LOCK  1 replaces a tracked lock that differs from this
#                            run. Without it a difference fails the stage. A
#                            missing tracked lock is always recorded.
#
# A step whose receipt says ok is skipped; a Kernel-dependent step is skipped
# only if it was built against the Kernel JAR that is current now.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Architecture hand-over (../arch.sh): on a host whose architecture has override
# patches under ../overrides/<arch>/ (arm64) this script continues from the
# patched copy of the tree; on amd64 it does nothing and the tracked recipes run.
# shellcheck source=takx/desktop-prototype/arch.sh
. "${here}/../arch.sh"
takx_arch_enter "${BASH_SOURCE[0]}" "$@"
repo="$(cd "${here}/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${repo}/takx/desktop-prototype/run-in-toolchain.sh"
build="${repo}/takx/desktop-prototype/client/build.py"
resolve="${repo}/takx/desktop-prototype/client/resolve_runtime.py"
# The tracked lock is per architecture: the JDK digests differ, and so do the
# locally built inputs. amd64 keeps client/inputs.lock.json. Any other
# architecture uses client/inputs.lock.<arch>.json of the base repository (the
# stage itself runs from the patched copy of the tree, see ../arch.sh), so a
# first run records it where it can be reviewed and committed.
arch="$(takx_arch)"
# shellcheck disable=SC2250 # test_arch.py (StageHandOver) asserts this text unbraced
tracked_lock="$repo/takx/desktop-prototype/client/inputs.lock.json"
if [[ "${arch}" != amd64 ]]; then
  lock_home="${repo}"
  [[ -f "${repo}/.takx-arch-tree.base" ]] && lock_home="$(<"${repo}/.takx-arch-tree.base")"
  tracked_lock="${lock_home}/takx/desktop-prototype/client/inputs.lock.${arch}.json"
fi
out="${root}/P/takx57"
cpus="${TAKX_CLIENT_CPUS:-1.5}"
force="${TAKX_CLIENT_FORCE:-0}"

mkdir -p "${root}"/{P,failed,logs,status}

log() { printf '%s client: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

write_status() { # stage status seconds notes outputs...
  local stage="$1" status="$2" seconds="$3" notes="$4"; shift 4
  python3 -I - "${root}/status/${stage}.json" "${stage}" "${status}" "${seconds}" "${notes}" "$@" <<'PY'
import json, os, sys
path, stage, status, seconds, notes, *outputs = sys.argv[1:]
tmp = path + '.tmp.%d' % os.getpid()
with open(tmp, 'w') as stream:
    json.dump({'stage': stage, 'status': status, 'outputs': outputs,
               'seconds': int(seconds), 'notes': notes}, stream, indent=1)
    stream.write('\n')
os.replace(tmp, path)
PY
}

field() { # json-file python-expression-over-d -> value, or nothing
  python3 -I - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
d = json.load(open(sys.argv[1]))
value = eval(sys.argv[2], {'d': d})
print('' if value is None else value)
PY
}

kernel_jar() {
  if [[ -n "${TAKX_CLIENT_KERNEL_JAR:-}" ]]; then echo "${TAKX_CLIENT_KERNEL_JAR}"; return; fi
  local ready="${root}/status/kernel-java-ready.json"
  [[ -s "${ready}" && "$(field "${ready}" "d['status']")" == ok ]] || return 0
  field "${ready}" "d['jar']['path']"
}

# step <name> <needs-kernel 0|1> <summary expression over the receipt d>
# Runs one build.py step detached and bounded, then records its status.
step() {
  local name="$1" needs_kernel="$2" summary="$3"
  local receipt="${out}/receipts/${name}.json" logf="${root}/logs/takx57-${name}.log"
  local args=() jar="" start rc seconds notes
  if [[ "${needs_kernel}" == 1 ]]; then
    jar="$(kernel_jar)"
    if [[ -z "${jar}" || ! -s "${jar}" ]]; then
      log "${name} is blocked: no composed Kernel JAR (status/kernel-java-ready.json is not ok)"
      write_status "takx57-${name}" failed 0 "blocked: the composed Kernel JAR is not available; step not run"
      return 1
    fi
    args=(--kernel-jar "${jar}")
  fi
  if [[ "${force}" != 1 && "$(field "${receipt}" "d['status']")" == ok ]]; then
    if [[ "${needs_kernel}" != 1 ||
          "$(field "${receipt}" "d['kernel_jar_sha256']")" == "$(sha256sum "${jar}" | cut -d' ' -f1)" ]]; then
      log "${name} already ok, skipping"; return 0
    fi
    log "${name} was built against another Kernel JAR, rebuilding"
  fi
  docker rm -f "takx-client-${name}" >/dev/null 2>&1 || true
  start=$(date +%s)
  printf '\n===== %s client %s\n' "$(date -u +%FT%TZ)" "${name}" >>"${logf}"
  "${run}" -d --name "takx-client-${name}" --cpus "${cpus}" --log "${logf}" -- \
    python3 -I -B "${build}" "${name}" "${args[@]}" >/dev/null
  rc="$(timeout 1700 docker wait "takx-client-${name}" || echo timeout)"
  docker rm -f "takx-client-${name}" >/dev/null 2>&1 || true
  seconds=$(($(date +%s) - start))
  if [[ "${rc}" != 0 || "$(field "${receipt}" "d['status']")" != ok ]]; then
    log "${name} failed (exit ${rc}) after ${seconds}s; see ${logf} and ${out}/${name}/"
    write_status "takx57-${name}" failed "${seconds}" \
      "client/build.py ${name} exit ${rc}: $(tail -n 1 "${logf}" | cut -c1-300); output and logs retained in P/takx57/${name}"
    return 1
  fi
  notes="$(field "${receipt}" "${summary}")"
  write_status "takx57-${name}" ok "${seconds}" "${notes}" "${out}/${name}" "${receipt}"
  log "${name} ok in ${seconds}s: ${notes}"
}

cmp_note="'%d sources, %d classes, %d compiler errors, class names equal to the expected set: %s, class hashes equal to the frozen set (informational): %s of %d'"

do_prepare() {
  step prep 0 "'; '.join('%s: %d sources, %d classes, 0 errors, names equal %s, %d hash-equal (informational)' % (k, v['sources'], v['classes'], v['names_equal'], v['hash_equal_informational']) for k, v in sorted(d['modules'].items()))"
  step xjc 0 "'XJC 3.0.1 over 7 XSDs + GlobalBindings.xjb with -no-header: %d sources, names equal to the expected 70: %s, %d hash-equal (informational)' % (d['xjc']['classes'], d['xjc']['names_equal'], d['xjc']['hash_equal_informational'])"
  step templates 0 "'3 java-templates expanded (platform version %s); names equal %s; %d of 3 hash-equal (informational); the javadoc tables state the versions actually used' % (d['platform_version'], d['templates']['names_equal'], d['templates']['hash_equal_informational'])"
}

do_compile() {
  step platform 1 "${cmp_note} % (d['platform']['sources'], d['platform']['classes'], d['commands']['platform']['errors'], d['platform']['names_equal'], d['platform']['hash_equal_informational'], d['platform']['expected_names']) + '; 17 processor-generated sources: %s; Kernel JAR sha256 %s' % (d['platform']['generated_names_equal'], d['kernel_jar_sha256'])"
  step aspects 1 "${cmp_note} % (d['aspects']['sources'], d['aspects']['classes'], d['commands']['ajc']['errors'], d['aspects']['names_equal'], d['aspects']['hash_equal_informational'], d['aspects']['expected_names']) + '; aop/aop.xml equals the frozen file: %s; no weaving' % d['aspects']['aop_xml_equals_frozen_informational']"
  step client 1 "'%d sources, %d classes, %d compiler errors, class names equal to the expected 2,815: %s, ClientEventIndex generated: %s; Kernel JAR sha256 %s' % (d['client']['sources'], d['client']['classes'], d['commands']['client']['errors'], d['client']['names_equal'], d['client']['generated_names_equal'], d['kernel_jar_sha256'])"
}

do_package() {
  # Packaging follows the classes that are current now: it is redone whenever
  # a compile receipt is newer than the package receipt.
  local saved="${force}" item
  for item in prep platform aspects client; do
    [[ "${out}/receipts/${item}.json" -nt "${out}/receipts/package.json" ]] && force=1
  done
  step package 0 "'version %s; ' % d['version'] + '; '.join('%s %s' % (k, v['sha256'][:12]) for k, v in sorted(d['jars'].items())) + '; bootstrap signed with the official developer key: %s; resources: %s; 0 LFS pointers' % (d['signing']['signed'], d['resource_counts'])" || { force="${saved}"; return 1; }
  force="${saved}"
}

do_runtime_jars() {
  local dir="${root}/P/takx57-runtime-jars" logf="${root}/logs/takx57-runtime-jars.log" start rc seconds
  if [[ "${force}" != 1 && -s "${dir}/manifest.json" && "$(field "${root}/status/takx57-runtime-jars.json" "d['status']")" == ok ]]; then
    log "runtime-jars already ok, skipping"; return 0
  fi
  # The resolver needs the Java trust store that holds the mounted CA bundle.
  "${run}" --java-truststore >/dev/null
  docker rm -f takx-client-runtime-jars >/dev/null 2>&1 || true
  start=$(date +%s)
  printf '\n===== %s client runtime-jars\n' "$(date -u +%FT%TZ)" >>"${logf}"
  "${run}" -d --name takx-client-runtime-jars --cpus "${cpus}" --log "${logf}" -- \
    python3 -I -B "${resolve}" >/dev/null
  rc="$(timeout 3300 docker wait takx-client-runtime-jars || echo timeout)"
  docker rm -f takx-client-runtime-jars >/dev/null 2>&1 || true
  seconds=$(($(date +%s) - start))
  if [[ "${rc}" != 0 || ! -s "${dir}/manifest.json" ]]; then
    log "runtime-jars failed (exit ${rc}); see ${logf} and ${dir}/work/maven.log"
    write_status takx57-runtime-jars failed "${seconds}" \
      "client/resolve_runtime.py exit ${rc}: $(tail -n 1 "${logf}" | cut -c1-300); see P/takx57-runtime-jars/work/maven.log"
    return 1
  fi
  write_status takx57-runtime-jars ok "${seconds}" \
    "$(field "${dir}/manifest.json" "'Apache Maven %s, strict checksums, Maven Central and JogAmp only: %d JARs (%d with the same bytes as the compile closure, %d runtime-only additions); %d private coordinates listed with their public substitute and not downloaded; manifest sha256 ' % (d['resolver']['tool'].split()[-1], d['counts']['jars'], d['counts']['same_bytes_in_compile_closure'], d['counts']['runtime_only_additions'], d['counts']['private_not_downloaded'])")$(sha256sum "${dir}/manifest.json" | cut -d' ' -f1)" \
    "${dir}/jars" "${dir}/manifest.json"
  log "runtime-jars ok in ${seconds}s"
}

do_wait_kernel() {
  local budget="${TAKX_CLIENT_WAIT_S:-10800}" waited=0 jar
  while :; do
    jar="$(kernel_jar)"
    if [[ -n "${jar}" && -s "${jar}" ]]; then log "composed Kernel JAR: ${jar}"; return 0; fi
    if ((waited >= budget)); then
      log "no composed Kernel JAR after ${waited}s"
      return 3
    fi
    sleep 60; waited=$((waited + 60))
  done
}

do_lock() {
  local start seconds verdict
  start=$(date +%s)
  # 'lock' has no receipt of its own; its products are the two files below.
  rm -f "${out}/inputs.lock.json" "${out}/result.json"
  printf '\n===== %s client lock\n' "$(date -u +%FT%TZ)" >>"${root}/logs/takx57-lock.log"
  "${run}" --cpus "${cpus}" -- python3 -I -B "${build}" lock >>"${root}/logs/takx57-lock.log" 2>&1 || true
  if [[ ! -s "${out}/inputs.lock.json" || ! -s "${out}/result.json" ||
        "$(field "${out}/result.json" "d['inputs_lock_sha256']")" != "$(sha256sum "${out}/inputs.lock.json" | cut -d' ' -f1)" ]]; then
    write_status takx57-lock failed 0 "client/build.py lock did not produce a consistent inputs.lock.json and result.json; see logs/takx57-lock.log"
    write_status takx57 failed 0 "the client stage is incomplete: no lock; see status/takx57-*.json"
    return 1
  fi
  if [[ ! -e "${tracked_lock}" ]]; then
    cp "${out}/inputs.lock.json" "${tracked_lock}"; verdict="recorded as the tracked lock (none existed)"
  elif cmp -s "${out}/inputs.lock.json" "${tracked_lock}"; then
    verdict="identical to the tracked lock"
  elif [[ "${TAKX_CLIENT_RECORD_LOCK:-0}" == 1 ]]; then
    cp "${out}/inputs.lock.json" "${tracked_lock}"; verdict="replaced the tracked lock (TAKX_CLIENT_RECORD_LOCK=1)"
  else
    log "inputs of this run differ from ${tracked_lock}"
    python3 -I - "${out}/inputs.lock.json" "${tracked_lock}" >&2 <<'PY' || true
import json, sys
new, old = (json.load(open(p))['inputs'] for p in sys.argv[1:3])
for kind in sorted(set(new) | set(old)):
    a, b = new.get(kind, {}), old.get(kind, {})
    changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    if changed:
        print('  %s: %d differing inputs, e.g. %s' % (kind, len(changed), changed[:3]))
PY
    write_status takx57-lock failed 0 "the inputs consumed by this run differ from the tracked client/$(basename "${tracked_lock}"); set TAKX_CLIENT_RECORD_LOCK=1 to record them deliberately"
    write_status takx57 failed 0 "compiled and packaged, but the inputs differ from the tracked lock; see status/takx57-lock.json"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  write_status takx57-lock ok "${seconds}" \
    "$(field "${out}/inputs.lock.json" "'%d input digests (%s)' % (sum(d['input_counts'].values()), ', '.join('%d %s' % (v, k) for k, v in sorted(d['input_counts'].items())))"); ${verdict}; sha256 $(sha256sum "${out}/inputs.lock.json" | cut -d' ' -f1)" \
    "${out}/inputs.lock.json" "${out}/result.json" "${tracked_lock}"
  local total=0 item
  for item in prep xjc templates platform aspects client package lock; do
    [[ "$(field "${root}/status/takx57-${item}.json" "d['status']")" == ok ]] || {
      write_status takx57 failed 0 "step ${item} is not ok; see status/takx57-${item}.json"; return 1; }
    total=$((total + $(field "${root}/status/takx57-${item}.json" "d['seconds']")))
  done
  write_status takx57 ok "${total}" \
    "TAKX 5.7 (official 0b59a9bb) compiled from scratch with Temurin 17.0.21+9 and zero compiler errors: $(field "${out}/result.json" "'Platform %d classes (names equal %s, %d hash-equal), aspects %d (names equal %s), Client %d classes (names equal %s); JAR version %s' % (d['results']['platform']['platform']['classes'], d['results']['platform']['platform']['names_equal'], d['results']['platform']['platform']['hash_equal_informational'], d['results']['aspects']['aspects']['classes'], d['results']['aspects']['aspects']['names_equal'], d['results']['client']['client']['classes'], d['results']['client']['client']['names_equal'], d['jar_version'])"); Kernel is public 8.30.5 plus project patches and CommonCommo 1.17.2 patched, not the private 8.57.11 / 1.20.0; compile and packaging only: no GUI launched, no native library loaded" \
    "${out}/package/load" "${out}/package/boot" "${out}/package/unsigned" "${out}/result.json" "${out}/inputs.lock.json" "${out}/receipts"
  log "takx57 ok"
}

(($#)) || set -- all
for target in "$@"; do
  case "${target}" in
    prepare) do_prepare;;
    runtime-jars) do_runtime_jars;;
    wait-kernel) do_wait_kernel;;
    compile) do_compile;;
    package) do_package;;
    lock) do_lock;;
    all) do_prepare; do_runtime_jars; do_wait_kernel; do_compile; do_package; do_lock;;
    -h|--help) sed -n '5,63p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "client.sh: unknown target '${target}'" >&2; exit 2;;
  esac
done
