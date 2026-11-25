#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, lane 4: the Java producers.
#
#   gis-java            takx/gis-java            SWIG GDAL wrappers + jassimp
#   kernel-java         takx/kernel-java         Kernel 8.30.5 Java, scope
#                                                engine-shared, profile patched,
#                                                with JNI headers (the profile
#                                                kernel-native/providers.py
#                                                accepts)
#   gdal-jni            takx/native-gdal-jni     delegated to lane3.sh, which
#                                                owns that stage definition
#   worldwind-java      takx/worldwind-java      NASA WorldWind v2.2.1, standard
#   worldwind-interop   takx/worldwind-java      the --interop profile the TAKX
#                                                client is compiled against
#   consumers57         takx/worldwind-consumers stage.py over official TAKX 5.7
#   ui-original         takx/ui-java             default (original) profile
#   ui-wizard-reference takx/ui-java             --profile wizard-reference
#   java-classpath      (no recipe)              status/java-classpath.json: the
#                                                client-compiler lock classpath
#                                                mapped to files here
#
# Every artifact is produced by the unmodified recipe under takx/, run inside
# the takx-desktop-proto:toolchain image through run-in-toolchain.sh. This
# script only prepares input directories from the verified fetch outputs, runs
# the recipe, runs the recipe's own tests and records the result. It never
# edits a recipe, a lock or a receipt. All of these recipes are compile-only:
# none of them loads a native library or accepts a client.
#
# kernel-client-interop is not a stage here: its build.py needs --sdk-proof
# (the qualified sdk-package output), which exists only after the native
# Kernel stage. It belongs to the Kernel/client lane.
#
# Usage:
#   lane4.sh [all|gis-java|kernel-java|gdal-jni|worldwind-java|worldwind-interop|
#             consumers57|ui-original|ui-wizard-reference|java-classpath]...
#
# Layout (TAKX_PROTO_ROOT, default /var/lib/takx-proto):
#   cache-gis-java/            gis-java archive cache (<sha256>.tar.gz)
#   inputs/<recipe>/           by-name inputs written by fetch.sh
#   cache-lane4/<stage>/       per-stage copies of by-name inputs a recipe may
#                              add to (kernel-java --fetch-dependencies)
#   src/WorldWindJava          NASA WorldWindJava at v2.2.1
#   src/takx57                 official TAKX 5.7 checkout
#   P/<stage>/                 the recipe output
#   failed/<stage>-<n>/        a failed or stale output, moved aside
#   logs/<stage>.log           recipe output; logs/<stage>-verify.log the tests
#   status/<stage>.json        {"stage","status","outputs","seconds","notes"}
#
# Environment:
#   TAKX_PROTO_ROOT   work root
#   TAKX_LANE4_FETCH  1 (default) passes --fetch-dependencies to kernel-java and
#                     lets gis-java download a missing archive; 0 is offline
#
# A stage whose status file says "ok" and whose marker output exists is skipped.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="$repo/takx/desktop-prototype/run-in-toolchain.sh"
jdk=/tools/temurin-17.0.21_9/jdk-17.0.21+9
atak="$repo/upstream/atak-civ"
fetch="${TAKX_LANE4_FETCH:-1}"

mkdir -p "$root"/{P,failed,logs,status,cache-lane4}

log() { printf '%s lane4: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

write_status() { # stage status seconds notes outputs...
  local stage="$1" status="$2" seconds="$3" notes="$4"; shift 4
  python3 -I - "$root/status/$stage.json" "$stage" "$status" "$seconds" "$notes" "$@" <<'PY'
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

status_of() { # stage -> ok|failed|missing
  python3 -I - "$root/status/$1.json" <<'PY' 2>/dev/null || echo missing
import json, sys
print(json.load(open(sys.argv[1]))['status'])
PY
}

set_aside() { # stage: move an existing output dir to failed/<stage>-<n>
  local name="$1" n=1
  [[ -e "$root/P/$name" ]] || return 0
  while [[ -e "$root/failed/$name-$n" ]]; do n=$((n + 1)); done
  mv "$root/P/$name" "$root/failed/$name-$n"
  log "moved previous P/$name to failed/$name-$n"
}

need() { # stage dependency marker-relative-to-P/dependency
  local stage="$1" dep="$2" marker="$3"
  if [[ "$(status_of "$dep")" != ok || ! -s "$root/P/$dep/$marker" ]]; then
    log "$stage is blocked: prerequisite '$dep' is not ok"
    write_status "$stage" failed 0 "blocked: prerequisite $dep is not ok; recipe not run"
    return 1
  fi
}

# stage <name> <recipe dir> <marker> <verify env or ''> <outputs csv> -- docker-env... -- command...
# Runs the command detached in the toolchain image, then the recipe's own
# test*.py. The stage is ok only if the command exits 0, the marker and every
# listed output exist and the tests pass.
stage() {
  local name="$1" recipe="$2" marker="$3" verifyenv="$4" outs="$5"; shift 6
  local out="$root/P/$name" logf="$root/logs/$name.log" vlog="$root/logs/$name-verify.log"
  local start rc seconds item outputs=() envargs=() venv=()
  while [[ "$1" != -- ]]; do envargs+=(-e "$1"); shift; done
  shift
  if [[ "$(status_of "$name")" == ok && -s "$out/$marker" ]]; then
    log "$name already ok, skipping"; return 0
  fi
  set_aside "$name"
  docker rm -f "takx-$name" >/dev/null 2>&1 || true
  start=$(date +%s)
  printf '\n===== %s lane4 %s: %s\n' "$(date -u +%FT%TZ)" "$name" "$*" >>"$logf"
  log "$name: running takx/$recipe (log $logf)"
  "$run" -d --name "takx-$name" --log "$logf" "${envargs[@]}" -- "$@" >/dev/null
  rc="$(docker wait "takx-$name")"
  docker rm "takx-$name" >/dev/null 2>&1 || true
  seconds=$(($(date +%s) - start))
  if [[ "$rc" != 0 || ! -s "$out/$marker" ]]; then
    log "$name: recipe failed (exit $rc) after ${seconds}s"
    write_status "$name" failed "$seconds" "takx/$recipe exit $rc; see logs/$name.log and the retained output directory"
    return 1
  fi
  IFS=, read -r -a outputs <<<"$outs"
  for item in "${!outputs[@]}"; do
    [[ -e "$out/${outputs[$item]}" ]] || {
      write_status "$name" failed "$seconds" "expected output ${outputs[$item]} is missing"; return 1; }
    outputs[$item]="$out/${outputs[$item]}"
  done
  for item in $verifyenv; do venv+=(-e "$item"); done
  printf '\n===== %s lane4 %s: unittest test*.py %s\n' "$(date -u +%FT%TZ)" "$name" "$verifyenv" >>"$vlog"
  if ! "$run" -e "PATH=$jdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      -e "JAVA_HOME=$jdk" "${venv[@]}" -- python3 -B -m unittest discover \
      -s "$repo/takx/$recipe" -p 'test*.py' -v >>"$vlog" 2>&1; then
    seconds=$(($(date +%s) - start))
    log "$name: recipe passed but its tests failed"
    write_status "$name" failed "$seconds" "recipe exit 0 but its tests failed; see logs/$name-verify.log"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  write_status "$name" ok "$seconds" \
    "takx/$recipe exit 0; its tests (test*.py) passed ($(tail -n 3 "$vlog" | tr '\n' ' ' | sed 's/  */ /g')); $marker sha256 $(sha256sum "$out/$marker" | cut -d' ' -f1); compile-only, no native library loaded${extra_note:+; $extra_note}" \
    "${outputs[@]}"
  log "$name: ok in ${seconds}s"
}

javapath="PATH=$jdk/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

stage_gis_java() {
  # gis-java is configured by environment and builds in place; a directory
  # left with .building by an interrupted run is refused by the recipe, so it
  # is set aside by stage() like any other failed output.
  local offline=true
  [[ "$fetch" == 1 ]] && offline=false
  local extra_note="SWIG 4.3.0 sources generated from the two atak-civ archives; jar hashes are not reproducible (jar --create), kernel-java checks the class bytes"
  stage gis-java gis-java build-state.json "" \
    build-state.json,gdal-2.4.4-mod-java.jar,assimp-4.0.1-mod-jassimp-java.jar -- \
    "$javapath" "GIS_JAVA_SWIG=/usr/bin/swig" "GIS_JAVA_CACHE_DIR=$root/cache-gis-java" \
    "GIS_JAVA_BUILD_DIR=$root/P/gis-java" "GIS_JAVA_OFFLINE=$offline" -- \
    python3 -B "$repo/takx/gis-java/build.py"
}

stage_kernel_java() {
  need kernel-java gis-java build-state.json || return 1
  # The recipe may add downloaded JARs to --classpath-dir, so it gets its own
  # copy of the fetched by-name inputs; every JAR is hash-checked by the recipe.
  local cp="$root/cache-lane4/kernel-java-classpath" flags=()
  mkdir -p "$cp"
  local jar
  for jar in "$root"/inputs/kernel-java/*.jar; do
    [[ -e "$cp/$(basename "$jar")" ]] || cp "$jar" "$cp"/
  done
  [[ "$fetch" == 1 ]] && flags=(--fetch-dependencies)
  local extra_note="scope engine-shared, profile patched, --jni-headers; output must not be moved (inputs.json holds absolute paths)"
  stage kernel-java kernel-java result.json "" \
    result.json,inputs.json,classes,jni-headers,classpath,class-hashes.json,jni-header-hashes.json -- \
    "$javapath" -- \
    python3 -B "$repo/takx/kernel-java/build.py" --scope engine-shared --profile patched \
    --source "$atak" --classpath-dir "$cp" --gis-build "$root/P/gis-java" \
    --output "$root/P/kernel-java" --java-home "$jdk" "${flags[@]}" --jni-headers
}

stage_gdal_jni() {
  # lane3.sh owns the native-gdal-jni invocation (flags per the native-kernel
  # map, step 16); this lane only supplies the gis-java build it waits for.
  need gdal-jni gis-java build-state.json || return 1
  TAKX_LANE3_GIS_BUILD="$root/P/gis-java" TAKX_LANE3_WAIT_S=0 "$here/lane3.sh" gdal-jni
}

worldwind() { # stage-name jar-name [--interop]
  local name="$1" jar="$2"; shift 2
  need "$name" gis-java build-state.json || return 1
  stage "$name" worldwind-java build-state.json "" "build-state.json,$jar" -- \
    "$javapath" -- \
    python3 -B "$repo/takx/worldwind-java/build.py" "$@" --source "$root/src/WorldWindJava" \
    --public-jars "$root/inputs/worldwind-java" --gis-build "$root/P/gis-java" \
    --output "$root/P/$name"
}

stage_worldwind_java() {
  local extra_note="NASA WorldWindJava v2.2.1 + standard-gdal.patch; not the private 2.2.1-TAKX-4 artifact; tests are the recipe's offline guards"
  worldwind worldwind-java uds-takserver-worldwind-java.jar
}

stage_worldwind_interop() {
  local extra_note="NASA WorldWindJava v2.2.1 + standard-gdal.patch + project-interop.patch (--interop); not the private 2.2.1-TAKX-4 artifact; tests are the recipe's offline guards"
  worldwind worldwind-interop uds-takserver-worldwind-java-interop.jar --interop
}

stage_consumers57() {
  local extra_note="staged from official TAKX 5.7 0b59a9bb; test_stage.py reran exact staging with TAKX_SOURCE_DIR"
  stage consumers57 worldwind-consumers staging-receipt.json "TAKX_SOURCE_DIR=$root/src/takx57" \
    staging-receipt.json,Platform,Client -- "$javapath" -- \
    python3 -B "$repo/takx/worldwind-consumers/stage.py" --source "$root/src/takx57" \
    --output "$root/P/consumers57"
}

ui() { # stage-name profile outputs
  local name="$1" profile="$2" outs="$3"
  stage "$name" ui-java receipt.json "" "receipt.json,$outs" -- "$javapath" -- \
    python3 -B "$repo/takx/ui-java/build.py" --sources-dir "$root/inputs/ui-java" \
    --classpath-dir "$root/inputs/ui-java" --java-home "$jdk" --profile "$profile" \
    --output "$root/P/$name" --failure-report "$root/logs/$name-failure-$(date +%s).json"
}

stage_ui_original() {
  local extra_note="source-built candidates for the private UI libraries (DockingFrames, ColorChooser 1.5, toast 1.0.3, svgSalamander v1.1.1); tests are the recipe's offline guards"
  ui ui-original original uds-takserver-ui-docking-core.jar,uds-takserver-ui-docking-common.jar,uds-takserver-ui-colorchooser-author-1.5.jar,uds-takserver-ui-toast-1.0.3.jar,uds-takserver-ui-salamander-tag-v1.1.1.jar
}

stage_ui_wizard_reference() {
  local extra_note="unauthenticated Wizard reference copy, source-built; tests are the recipe's offline guards"
  ui ui-wizard-reference wizard-reference uds-takserver-ui-wizard-reference.jar
}

stage_java_classpath() {
  # Maps every classpath and processor-path entry of the client-compiler lock
  # to a concrete file or directory under the work root. It hashes what is
  # there and compares with the lock; it builds and changes nothing. A locally
  # rebuilt project artifact normally differs from the frozen lock digest.
  local start seconds
  start=$(date +%s)
  if ! python3 -I - "$root" "$repo/takx/client-compiler/inputs.lock.json" <<'PY'
import hashlib, json, os, sys
root, lock_path = sys.argv[1:]
lock = json.load(open(lock_path))
fetched = {r['name']: r for r in json.load(open(root + '/status/fetch.json'))['inputs']
           if r.get('kind') == 'client-compiler-jar'}

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()

def ok(stage):
    try:
        return json.load(open('%s/status/%s.json' % (root, stage)))['status'] == 'ok'
    except (OSError, ValueError, KeyError):
        return False

P = root + '/P/'
built = {  # lock entry prefix -> (directory here, stage, description)
    'ww-interop-proof:build/': (P + 'worldwind-interop/', 'worldwind-interop',
                                'project-built: takx/worldwind-java --interop (NASA v2.2.1 + project patches)'),
    'takx-client-compiler-closure:ui-original/': (P + 'ui-original/', 'ui-original',
                                                  'project-built: takx/ui-java original profile'),
    'takx-client-compiler-closure:ui-wizard-reference/': (P + 'ui-wizard-reference/', 'ui-wizard-reference',
                                                          'project-built: takx/ui-java wizard-reference profile'),
    'commoncommo-jni-exception-proof:install/java/': (P + 'commoncommo/install/java/', 'commoncommo',
                                                      'project-built: takx/native-commoncommo (CommonCommo 1.17.2 patched)'),
}
gis = ('gdal-2.4.4-mod-java.jar', 'assimp-4.0.1-mod-jassimp-java.jar')
missing = {
    'kernel-client-interop-public-bitmap-controls-01:classes':
        'takx/kernel-client-interop (needs --sdk-proof from the native Kernel stage)',
    'takx-client-compiler-closure:texture-compile-only-overlay-families/classes':
        'takx/kernel-texture overlay classes, composed Kernel stage',
    'render-context-java-client-frozen-01:classes':
        'takx/kernel-render-context Java classes, composed Kernel stage',
    'sdk-package-bounded-stock-qualification:package/kernel-8.30.5-linux-amd64-public-cpu.jar':
        'Kernel 8.30.5 jar, Kernel packaging stage',
}

def entry(name):
    want = lock['files'].get(name)
    record = {'entry': name, 'lock_sha256': want, 'path': None, 'kind': None, 'sha256': None,
              'lock_digest_equal': None, 'source': None}
    path = None
    base = name.rsplit('/', 1)[-1]
    if name.startswith('sdk-kernel-jni-coverage-proof:classpath/') and base in gis:
        path, stage, record['source'] = P + 'kernel-java/classpath/' + base, 'kernel-java', \
            'project-built: takx/gis-java, frozen copy in the kernel-java classpath'
        if not ok(stage):
            path = None
    else:
        for prefix, (directory, stage, source) in built.items():
            if name.startswith(prefix):
                record['source'] = source
                if ok(stage):
                    path = directory + name[len(prefix):]
        if record['source'] is None and name in fetched and fetched[name].get('verified'):
            path, record['source'] = fetched[name]['path'], 'public JAR: ' + str(fetched[name].get('source'))
    if path and os.path.isfile(path):
        record.update(path=path, kind='file', sha256=sha(path))
        record['lock_digest_equal'] = record['sha256'] == want
    elif name.startswith('client57-public-interop-compiler-02:probe-5.7/'):
        record.update(path='MISSING: produced by kernel/client stage', kind='missing',
                      source='TAKX 5.7 %s, compiled by the client stage' % base)
    elif name in missing:
        record.update(path='MISSING: produced by kernel/client stage', kind='missing', source=missing[name])
    else:
        record.update(path='MISSING: no producer or fetched input found', kind='unresolved')
    return record

classpath = [entry(name) for name in lock['classpath']]
processor = [entry(name) for name in lock['processorpath']]
count = lambda rows, key: sum(1 for r in rows if key(r))
summary = {
    'classpath_entries': len(classpath), 'processorpath_entries': len(processor),
    'classpath_resolved_files': count(classpath, lambda r: r['kind'] == 'file'),
    'classpath_lock_digest_equal': count(classpath, lambda r: r['lock_digest_equal'] is True),
    'classpath_lock_digest_differs': count(classpath, lambda r: r['lock_digest_equal'] is False),
    'classpath_missing_kernel_client_stage': count(classpath, lambda r: r['kind'] == 'missing'),
    'classpath_unresolved': count(classpath, lambda r: r['kind'] == 'unresolved'),
    'processorpath_resolved_files': count(processor, lambda r: r['kind'] == 'file'),
    'processorpath_missing_kernel_client_stage': count(processor, lambda r: r['kind'] == 'missing'),
    'processorpath_unresolved': count(processor, lambda r: r['kind'] == 'unresolved'),
}
unresolved = summary['classpath_unresolved'] + summary['processorpath_unresolved']
body = {
    'stage': 'java-classpath', 'status': 'failed' if unresolved else 'ok',
    'outputs': [root + '/status/java-classpath.json'], 'seconds': 0,
    'notes': 'map of takx/client-compiler/inputs.lock.json classpath and processorpath to files here; '
             'a mapping only, nothing was compiled against it; lock_digest_equal false marks a locally '
             'rebuilt project artifact that differs from the frozen lock digest; the Kernel is 8.30.5 '
             'and CommonCommo 1.17.2, never the private 8.57.11 / 1.20.0',
    'lock': lock_path, 'lock_sha256': sha(lock_path), 'summary': summary,
    'classpath': classpath, 'processorpath': processor,
}
target = root + '/status/java-classpath.json'
with open(target + '.tmp', 'w') as stream:
    json.dump(body, stream, indent=1)
    stream.write('\n')
os.replace(target + '.tmp', target)
print(json.dumps(summary, indent=1))
sys.exit(1 if unresolved else 0)
PY
  then
    log "java-classpath: unresolved entries; see status/java-classpath.json"
    return 1
  fi
  seconds=$(($(date +%s) - start))
  log "java-classpath: written in ${seconds}s"
}

(($#)) || set -- all
extra_note=""
failed=0
for target in "$@"; do
  case "$target" in
    all)
      # Later stages check their own prerequisites, so one failure blocks only
      # the stages that depend on it.
      for name in gis_java kernel_java gdal_jni worldwind_java worldwind_interop consumers57 \
          ui_original ui_wizard_reference java_classpath; do
        "stage_$name" || failed=1
      done;;
    gis-java|kernel-java|gdal-jni|worldwind-java|worldwind-interop|consumers57|ui-original|ui-wizard-reference|java-classpath)
      "stage_${target//-/_}" || failed=1;;
    *) echo "lane4.sh: unknown stage '$target'" >&2; exit 2;;
  esac
done
exit "$failed"
