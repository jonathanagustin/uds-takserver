#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Self-test of the TAKX desktop prototype image without a cluster and without
# any bind mount into the running containers. Every step is bounded, so a
# caller with a time limit can run them one by one.
#
#   selftest.sh start             desktop + web containers of IMAGE
#   selftest.sh wait [SECONDS]    until 'healthcheck.sh started' passes (max 500)
#   selftest.sh check             evidence: mounts, probes, JVM age, mapped
#                                 libraries, one frame from inside the desktop
#                                 container, one frame through the WebSocket
#                                 bridge; writes status/selftest.json. Fails
#                                 on GDAL's "Unable to open EPSG support
#                                 file", on GDAL_DATA or PROJ_LIB not naming
#                                 the data directories of the tree, on an
#                                 FFmpeg library in the JVM (the image ships
#                                 none), and on any UnsatisfiedLinkError
#                                 other than the one the absent FFmpeg
#                                 natives cause when a project opens
#   selftest.sh auth              the bridge's own sign-in check, with real
#                                 HTTP and WebSocket clients (auth_probe.py):
#                                 every request variant of 'token_tool.py
#                                 cases' against port 6080, from inside the
#                                 containers and, when the containers are on
#                                 a Docker network, from the host as well;
#                                 a WebSocket that ends when its token
#                                 expires; a bridge that cannot fetch keys.
#                                 Only a correct token may reach the RFB
#                                 handshake
#   selftest.sh browser           Playwright Chromium on the host loads noVNC
#                                 through exec_relay.py, which adds the token
#                                 as Authservice would, and saves a screenshot
#                                 (needs node with the playwright module)
#   selftest.sh stats             memory and CPU of both containers now, and the
#                                 peak the kernel recorded
#   selftest.sh supervision       kill websockify, then the JVM; probes and exit
#                                 codes (ends the session)
#   selftest.sh audit             readelf-free ELF audit ('ldd -r') in the image
#   selftest.sh linker            a boot under LD_DEBUG=libs: what the dynamic
#                                 linker searched; fails when a search path
#                                 has an empty or relative element or a file
#                                 is tried by a relative name (both mean the
#                                 working directory, which the application
#                                 user can write), or when a Kernel library
#                                 still brings a RUNPATH; then 'docker stop'
#   selftest.sh identity          identity stage against a synthetic identity
#   selftest.sh logs              container logs into the evidence directory
#   selftest.sh stop              remove the containers and the socket volume
#
# Containers: takx-img-desktop (uid 10001, --network none, read-only root,
# all capabilities dropped, 1.5 CPU, 5 GiB), takx-img-web (uid 10002 with
# supplementary group 10001, same network namespace, 0.5 CPU) and
# takx-img-jwks (same image and namespace, uid 10002): a stand-in for the
# realm's key endpoint on 127.0.0.1:8081 that serves the public half of a
# key made for the run. The web container runs with TAKX_AUTH_MODE=oidc, as
# in the chart; the probes sign their tokens with that key
# (token_tool.py, OpenSSL on the host). Writable: tmpfs /tmp,
# /run/takx/session (exec), /run/takx/identity, /run/takx/web, and a tmpfs
# volume for the RFB socket shared by desktop and web. X display :1.
#
# Environment:
#   TAKX_IMAGE          image, default: the first tag in status/image.json
#   TAKX_PROTO_ROOT     work root, default /var/lib/takx-proto
#   TAKX_SELFTEST_RUN   evidence subdirectory, default 'selftest'
#   TAKX_SELFTEST_JVM   EXTRA_JVM_OPTS of the desktop, default -Xmx2g
#   TAKX_SELFTEST_NET   'none' (default) or the name of an existing Docker
#                       network, e.g. an --internal one, when a browser on the
#                       host has to reach port 6080 of the desktop container
#
# The identity step bind-mounts a synthetic Secret directory read-only into a
# short-lived container; nothing else is ever mounted from the host. The test
# keys stay on the host in pkg/selftest/auth (mode 0700) and are deleted by
# 'stop'; the containers get the public JWKS document and signed tokens only.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
run="${TAKX_SELFTEST_RUN:-selftest}"
evidence="${root}/pkg/evidence/${run}"
status_dir="${root}/pkg/status"
desktop=takx-img-desktop
web=takx-img-web
jwks=takx-img-jwks
volume=takx-img-vnc
app=/opt/takx-desktop
auth_dir="${root}/pkg/selftest/auth"
auth_issuer=https://sso.selftest.invalid/realms/uds
auth_audience=uds-takx-desktop
auth_group='/UDS Core/Admin'
jwks_url=http://127.0.0.1:8081/realms/uds/protocol/openid-connect/certs

image="${TAKX_IMAGE:-}"
if [[ -z "${image}" && -s "${status_dir}/image.json" ]]; then
  image="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["tags"][0])' "${status_dir}/image.json")"
fi
[[ -n "${image}" ]] || { echo "selftest: no image; set TAKX_IMAGE or run image/build.sh" >&2; exit 1; }
mkdir -p "${evidence}" "${status_dir}"

hardening=(--read-only --cap-drop ALL --security-opt no-new-privileges)

do_stop() {
  docker rm -f "${web}" "${jwks}" "${desktop}" >/dev/null 2>&1 || true
  docker volume rm "${volume}" >/dev/null 2>&1 || true
  rm -rf "${auth_dir}"
}

# Fresh request variants (token_tool.py cases) signed with the run's key;
# written to a file only the owner reads. The keys are made on first use.
auth_cases() { # [short-lifetime-seconds]
  mkdir -p "${auth_dir}"
  chmod 0700 "${auth_dir}"
  (umask 077; python3 -I "${here}/token_tool.py" cases "${auth_dir}" --issuer "${auth_issuer}" \
    --audience "${auth_audience}" --group "${auth_group}" --short "${1:-20}" >"${auth_dir}/cases.json")
}

good_token() {
  python3 -I -c 'import json, sys
print(next(case for case in json.load(open(sys.argv[1])) if case["name"] == "good-token")["headers"][0].split()[2])' \
    "${auth_dir}/cases.json"
}

# The web container as the chart runs it: token check on, keys from a URL.
run_web() { # name [docker run option...]
  local name="$1"; shift
  docker run -d --name "${name}" --network "container:${desktop}" \
    "${hardening[@]}" --user 10002:10002 --group-add 10001 --cpus 0.5 --memory 256m \
    --tmpfs /run/takx/web:rw,noexec,nosuid,nodev,size=1m,uid=10002,gid=10002,mode=0700 \
    --mount "type=volume,src=${volume},dst=/run/takx/vnc,readonly" \
    -e TAKX_AUTH_MODE=oidc -e "TAKX_AUTH_ISSUER=${auth_issuer}" -e "TAKX_AUTH_AUDIENCE=${auth_audience}" \
    -e "TAKX_AUTH_GROUPS=[\"${auth_group}\"]" -e "TAKX_AUTH_JWKS_URL=${jwks_url}" "$@" \
    "${image}" web >/dev/null
}

do_start() {
  local network="${TAKX_SELFTEST_NET:-none}"
  do_stop
  docker volume create --driver local --opt type=tmpfs --opt device=tmpfs \
    --opt o=size=1m,uid=10001,gid=10001,mode=2770 "${volume}" >/dev/null
  # The host name resolves, as it does in a pod; with --network none Docker
  # writes no such entry and log4j reports an UnknownHostException at start.
  docker run -d --name "${desktop}" --network "${network}" --hostname takx-desktop --add-host takx-desktop:127.0.0.1 \
    "${hardening[@]}" --user 10001:10001 --cpus 1.5 --memory 5g \
    --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
    --tmpfs /run/takx/session:rw,exec,nosuid,nodev,size=2g,uid=10001,gid=10001,mode=0750 \
    --tmpfs /run/takx/identity:rw,noexec,nosuid,nodev,size=8m,uid=10001,gid=10001,mode=0750 \
    --mount "type=volume,src=${volume},dst=/run/takx/vnc" \
    -e "EXTRA_JVM_OPTS=${TAKX_SELFTEST_JVM:--Xmx2g}" \
    "${image}" desktop >/dev/null
  # The realm's key endpoint, stood in for: the public JWKS document of the
  # run's key, handed over in the environment and served on loopback.
  auth_cases
  # shellcheck disable=SC2016 # expanded by Python in the container
  docker run -d --name "${jwks}" --network "container:${desktop}" "${hardening[@]}" --user 10002:10002 --cpus 0.2 \
    --memory 64m -e "TAKX_SELFTEST_JWKS=$(cat "${auth_dir}/jwks.json")" --entrypoint python3 "${image}" -I -c '
import http.server, os
DOCUMENT = os.environ["TAKX_SELFTEST_JWKS"].encode()
class Keys(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        found = self.path == "/realms/uds/protocol/openid-connect/certs"
        body = DOCUMENT if found else b"{}"
        self.send_response(200 if found else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
http.server.ThreadingHTTPServer(("127.0.0.1", 8081), Keys).serve_forever()' >/dev/null
  run_web "${web}"
  date -u +%FT%TZ >"${evidence}/started-at.txt"
  echo "selftest: started ${desktop}, ${web} and ${jwks} from ${image} on network ${network}"
}

do_wait() {
  local budget="${1:-480}" waited=0
  ((budget <= 500)) || budget=500
  while ((waited < budget)); do
    if docker exec "${desktop}" "${app}/healthcheck.sh" started 2>/dev/null; then
      echo "selftest: 'started' probe passed after about ${waited} s of waiting"
      echo "${waited}" >"${evidence}/started-after-seconds${wait_label:-}.txt"
      return 0
    fi
    [[ "$(docker inspect -f '{{.State.Running}}' "${desktop}" 2>/dev/null)" == true ]] ||
      { echo "selftest: ${desktop} is not running"; docker logs --tail 30 "${desktop}" 2>&1; return 1; }
    sleep 10; waited=$((waited + 10))
  done
  echo "selftest: 'started' probe did not pass within ${budget} s"
  docker exec "${desktop}" "${app}/healthcheck.sh" started || true
  return 1
}

probe() { # container mode -> "mode=rc"
  local rc=0
  docker exec "$1" "${app}/healthcheck.sh" "$2" >/dev/null 2>"${evidence}/probe-$2.err" || rc=$?
  echo "$2=${rc}"
}

do_check() {
  docker inspect "${desktop}" "${web}" >"${evidence}/inspect.json"
  {
    probe "${desktop}" started; probe "${desktop}" ready; probe "${desktop}" live; probe "${web}" web
  } >"${evidence}/probes.txt"
  docker logs "${desktop}" >"${evidence}/desktop.log" 2>&1
  docker logs "${web}" >"${evidence}/web.log" 2>&1
  # Facts from inside the desktop container: JVM age, user, mapped libraries.
  docker exec -i "${desktop}" python3 -I - >"${evidence}/jvm.json" <<'PY'
import json, os, re
pid = int(open("/run/takx/session/jvm.pid").read())
ticks = os.sysconf("SC_CLK_TCK")
start = int(open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()[19])
age = float(open("/proc/uptime").read().split()[0]) - start / ticks
paths = sorted({line.split(None, 5)[5].strip() for line in open(f"/proc/{pid}/maps") if line.count("/") and len(line.split(None, 5)) == 6})
status = dict(line.split(":", 1) for line in open(f"/proc/{pid}/status") if ":" in line)
kernel = [path for path in paths if "/takkernel/" in path]
environ = dict(item.split("=", 1) for item in open(f"/proc/{pid}/environ").read().split("\0") if "=" in item)
def listeners():
    # TCP sockets in LISTEN state in the network namespace both containers
    # share: the web bridge (6080) and whatever the JVM binds.
    found = []
    for family in ("tcp", "tcp6"):
        try:
            rows = open(f"/proc/net/{family}").read().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            cells = row.split()
            if cells[3] == "0A":
                address, port = cells[1].rsplit(":", 1)
                loopback = address in ("0100007F", "00000000000000000000000001000000")
                found.append({"family": family, "port": int(port, 16), "loopback_only": loopback,
                              "every_address": set(address) == {"0"}})
    return sorted(found, key=lambda item: (item["port"], item["family"]))
def udp_ports():
    # Bound UDP sockets of the namespace: CommonCommo's default inputs would
    # show here as 4242, 8087, 10011 and the multicast ports.
    ports = set()
    for family in ("udp", "udp6"):
        try:
            rows = open(f"/proc/net/{family}").read().splitlines()[1:]
        except OSError:
            continue
        ports.update(int(row.split()[1].rsplit(":", 1)[1], 16) for row in rows)
    return sorted(ports)
def program_inputs():
    # What the session's program properties say about CommonCommo's inputs.
    import re
    try:
        text = open("/run/takx/session/ProjectData/ProjectData/LocalProgramProperties.xml", encoding="utf-8").read()
    except OSError:
        return None
    block = text[text.find('<node name="DefaultInputs">'):text.find('<node name="DefaultOutputs">')]
    return {"inputs": len(re.findall(r'<entry key="uri"', block)),
            "enabled": len(re.findall(r'<entry key="enabled" value="true"/>', block))}
print(json.dumps({
    "jvm_pid": pid, "jvm_age_seconds": round(age), "uid": status["Uid"].split()[0], "gid": status["Gid"].split()[0],
    "cap_eff": status["CapEff"].strip(), "no_new_privs": status["NoNewPrivs"].strip(),
    "vm_rss_kb": int(status["VmRSS"].split()[0]), "vm_hwm_kb": int(status["VmHWM"].split()[0]),
    "var_lib_takx_proto_exists": os.path.exists("/var/lib/takx-proto"),
    "mapped_paths_below_var_lib_takx_proto": [path for path in paths if path.startswith("/var/lib/takx-proto")],
    "mapped_kernel_libraries": kernel,
    "mapped_gles_and_dispatch": [path for path in paths if re.search(r"libGLESv2|libGLdispatch|libEGL|libGLX|libGL\.so", path)],
    "mapped_native_dir": [path for path in paths if path.startswith("/opt/takx/native/")],
    "mapped_shared_objects": len([path for path in paths if ".so" in path]),
    "mapped_ffmpeg_libraries": [path for path in paths if re.search(r"/lib(jni)?(av|sw|postproc)[a-z]*\.so", path)],
    "working_directory": os.readlink(f"/proc/{pid}/cwd"),
    "tcp_listeners": listeners(),
    "udp_ports": udp_ports(),
    "program_properties_default_inputs": program_inputs(),
    "gdal_data": environ.get("GDAL_DATA"), "proj_lib": environ.get("PROJ_LIB"),
    "gdal_data_has_gcs_csv": os.path.isfile(os.path.join(environ.get("GDAL_DATA", "/nonexistent"), "gcs.csv")),
    "proj_lib_has_epsg": os.path.isfile(os.path.join(environ.get("PROJ_LIB", "/nonexistent"), "epsg")),
    "addon_library_path": environ.get("TAKX_ADDON_LIBRARY_PATH"),
    "ffmpeg_native_jars_in_tree": sorted(name for name in os.listdir("/opt/takx/modules/prototype/libs/main")
                                         if name.startswith("ffmpeg-") and "linux" in name),
    "root_writable": os.access("/", os.W_OK), "opt_takx_writable": os.access("/opt/takx", os.W_OK),
}, indent=1))
PY
  local rc_desktop=0 rc_web=0
  docker exec -i "${desktop}" python3 -I - --unix /run/takx/vnc/rfb.sock --png - \
    <"${here}/rfb_probe.py" >"${evidence}/frame-desktop.png" 2>"${evidence}/frame-desktop.json" || rc_desktop=$?
  # Through the gate, with a token of the run's key, as a signed-in browser.
  auth_cases
  TAKX_PROBE_BEARER="$(good_token)" docker exec -i -e TAKX_PROBE_BEARER "${web}" \
    python3 -I - --ws 127.0.0.1:6080 --png - --second-after 5 \
    <"${here}/rfb_probe.py" >"${evidence}/frame-web.png" 2>"${evidence}/frame-web.json" || rc_web=$?
  # shellcheck disable=SC2016 # expanded by the shell in the container
  docker exec "${web}" bash -c 'for comm in /proc/[0-9]*/comm; do
      pid="${comm#/proc/}"; pid="${pid%/comm}"
      printf "%s %s\n" "$(cat "${comm}")" "$(tr "\0" " " <"/proc/${pid}/cmdline")"
    done; stat -c "%a %U %n" /run/takx/web /run/takx/web/websockify.sock' >"${evidence}/web-processes.txt" 2>&1 || true
  docker exec "${desktop}" bash -c 'export DISPLAY=:1 XAUTHORITY=/run/takx/session/home/.Xauthority
    for id in $(xdotool search --onlyvisible --name . 2>/dev/null); do
      printf "%s|%s|%s\n" "${id}" "$(xdotool getwindowname "${id}")" "$(xdotool getwindowgeometry "${id}" | tr "\n" " ")"
    done' >"${evidence}/windows.txt" 2>/dev/null || true
  python3 -I - "${evidence}" "${status_dir}/selftest.json" "${image}" "${rc_desktop}" "${rc_web}" <<'PY'
import json, os, re, sys
evidence, status_path, image, rc_desktop, rc_web = sys.argv[1:]
load = lambda name: json.load(open(os.path.join(evidence, name)))
inspect, jvm = load("inspect.json"), load("jvm.json")
probes = dict(line.strip().split("=") for line in open(os.path.join(evidence, "probes.txt")))
log = open(os.path.join(evidence, "desktop.log"), errors="replace").read()
fatal = len(re.findall(r"A fatal error has been detected|SIGSEGV|OutOfMemoryError", log))
# The image ships no FFmpeg natives: MediaPlayerManager.startService fails
# once when a project opens ("no jniavutil in java.library.path", caused by
# "no avutil ..."). Any other unresolved library is a failure.
link_lines = [line for line in log.splitlines() if re.search(r"UnsatisfiedLinkError|cannot open shared object", line)]
ffmpeg_absent = [line for line in link_lines if re.search(r"UnsatisfiedLinkError: no (jni)?avutil in java\.library\.path", line)]
unsatisfied = len(link_lines) - len(ffmpeg_absent)
epsg = len(re.findall(r"Unable to open EPSG support file", log))
containers = {}
for item in inspect:
    host = item["HostConfig"]
    containers[item["Name"].lstrip("/")] = {
        "image": item["Config"]["Image"], "user": item["Config"]["User"], "network_mode": host["NetworkMode"],
        "read_only_rootfs": host["ReadonlyRootfs"], "cap_drop": host["CapDrop"], "security_opt": host["SecurityOpt"],
        "nano_cpus": host["NanoCpus"], "memory": host["Memory"], "group_add": host["GroupAdd"],
        "tmpfs": host.get("Tmpfs") or {}, "binds": host.get("Binds") or [],
        "mounts": [{"type": m["Type"], "destination": m["Destination"], "rw": m["RW"], "name": m.get("Name")} for m in item["Mounts"]],
        "bind_mounts": [m["Destination"] for m in item["Mounts"] if m["Type"] == "bind"],
        "auth_mode": dict(pair.split("=", 1) for pair in item["Config"]["Env"] if "=" in pair).get("TAKX_AUTH_MODE"),
    }
frames = {name: load(f"frame-{name}.json") for name in ("desktop", "web")}
checks = {
    "no_bind_mounts": all(not c["bind_mounts"] and not c["binds"] for c in containers.values()),
    "read_only_root": all(c["read_only_rootfs"] for c in containers.values()) and not jvm["root_writable"],
    "non_root": jvm["uid"] == "10001" and containers["takx-img-web"]["user"] == "10002:10002",
    "no_capabilities": int(jvm["cap_eff"], 16) == 0,
    "probes_pass": all(value == "0" for value in probes.values()),
    "jvm_up_over_120_s": jvm["jvm_age_seconds"] > 120,
    "no_fatal_jvm_marker": fatal == 0,
    "no_unsatisfied_link_other_than_absent_ffmpeg": unsatisfied == 0,
    "no_gdal_epsg_support_file_error": epsg == 0,
    "gdal_data_and_proj_lib_point_into_the_tree": jvm["gdal_data"] == "/opt/takx/share/gdal" and jvm["gdal_data_has_gcs_csv"]
        and jvm["proj_lib"] == "/opt/takx/share/proj" and jvm["proj_lib_has_epsg"],
    "no_ffmpeg_natives_shipped_or_mapped": not jvm["mapped_ffmpeg_libraries"] and not jvm["ffmpeg_native_jars_in_tree"]
        and jvm["addon_library_path"] is None,
    "nothing_mapped_below_var_lib_takx_proto": not jvm["mapped_paths_below_var_lib_takx_proto"] and not jvm["var_lib_takx_proto_exists"],
    "nine_kernel_libraries_from_session_tmp": len(jvm["mapped_kernel_libraries"]) == 9 and all(
        path.startswith("/run/takx/session/tmp/takkernel/") for path in jvm["mapped_kernel_libraries"]),
    # CommonCommo's default inputs are switched off: of the TCP listeners on
    # every address only the gate (6080) and one ephemeral port are left
    # (CommonCommo's HTTPS port for peer-to-peer mission packages).
    "commo_default_inputs_off": (jvm["program_properties_default_inputs"] or {}).get("enabled") == 0
        and not {4242, 8087} & {item["port"] for item in jvm["tcp_listeners"]}
        and not {4242, 8087, 10011, 6969, 17012, 18740, 18999, 18200, 18000} & set(jvm["udp_ports"]),
    "tcp_on_every_address_is_the_gate_and_one_ephemeral_port":
        sorted(item["port"] for item in jvm["tcp_listeners"] if item["every_address"] and item["port"] < 32768) == [6080]
        and len({item["port"] for item in jvm["tcp_listeners"] if item["every_address"]}) == 2,
    "web_container_runs_with_the_token_check_on": containers["takx-img-web"]["auth_mode"] == "oidc",
    "frame_inside_desktop_ok": rc_desktop == "0" and frames["desktop"].get("status") == "ok",
    "frame_through_websocket_ok": rc_web == "0" and frames["web"].get("status") == "ok",
}
status = "ok" if all(checks.values()) else "failed"
document = {"stage": "image-selftest", "status": status, "image": image, "checks": checks, "probes_exit_codes": probes,
            "jvm": jvm, "fatal_markers": fatal, "unsatisfied_link_markers": unsatisfied,
            "unsatisfied_link_lines_from_absent_ffmpeg_natives": len(ffmpeg_absent),
            "gdal_epsg_support_file_errors": epsg, "containers": containers,
            "frames": frames, "evidence": evidence,
            "notes": "automatic evidence only: whether the frames show the TAKX main window with the globe is for a "
                     "person who looks at frame-desktop.png and frame-web.png. Kernel 8.30.5 + project patches, "
                     "CommonCommo 1.17.2 patched; not the official TAKX 5.7 distribution"}
with open(status_path, "w") as stream:
    json.dump(document, stream, indent=1)
    stream.write("\n")
print(json.dumps({"status": status, "checks": checks, "jvm_age_seconds": jvm["jvm_age_seconds"]}, indent=1))
sys.exit(0 if status == "ok" else 1)
PY
}

do_stats() {
  {
    date -u +%FT%TZ
    docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}} pids={{.PIDs}}' "${desktop}" "${web}"
    for name in "${desktop}" "${web}"; do
      # cgroup v2 names first, then cgroup v1.
      # shellcheck disable=SC2016 # expanded by the shell in the container
      docker exec "${name}" bash -c 'cd /sys/fs/cgroup
        read_first() { local file; for file in "$@"; do if [[ -r "${file}" ]]; then cat "${file}"; return; fi; done; echo unknown; }
        printf "%s memory_peak_bytes=%s memory_current_bytes=%s memory_limit_bytes=%s\n" "$1" \
          "$(read_first memory.peak memory/memory.max_usage_in_bytes)" \
          "$(read_first memory.current memory/memory.usage_in_bytes)" \
          "$(read_first memory.max memory/memory.limit_in_bytes)"' _ "${name}"
    done
    docker exec "${desktop}" df -h /tmp /run/takx/session /dev/shm | sed 's/^/desktop df: /'
  } | tee -a "${evidence}/stats.txt"
}

do_supervision() {
  local out="${evidence}/supervision.txt" rc
  : >"${out}"
  {
    echo "== before: probes"
    probe "${desktop}" live; probe "${web}" web
    echo "== kill websockify in ${web}"
  } >>"${out}"
  # shellcheck disable=SC2016 # expanded by the shell in the container
  docker exec "${web}" bash -c 'for stat in /proc/[0-9]*/comm; do
      if [[ "$(cat "${stat}")" == websockify ]]; then pid="${stat#/proc/}"; kill -KILL "${pid%/comm}"; fi; done' >>"${out}" 2>&1 || true
  rc="$(timeout 30 docker wait "${web}" 2>/dev/null || echo timeout)"
  {
    echo "web container exit code: ${rc}"
    echo "desktop still running: $(docker inspect -f '{{.State.Running}}' "${desktop}")"
    probe "${desktop}" live
    echo "== JVM killed while the supervisor is paused: the probes must fail on their own"
  } >>"${out}"
  # The supervisor (parent of the JVM) is stopped first, so the container
  # stays up long enough to run the probes against a dead JVM; then it is
  # continued and has to end the container.
  # shellcheck disable=SC2016 # expanded by the shell in the container
  docker exec "${desktop}" bash -c '
    jvm="$(cat /run/takx/session/jvm.pid)"
    supervisor="$(sed -n "s/^PPid:[[:space:]]*//p" "/proc/${jvm}/status")"
    kill -STOP "${supervisor}"
    kill -KILL "${jvm}"
    sleep 1
    for mode in started ready live; do
      rc=0; /opt/takx-desktop/healthcheck.sh "${mode}" || rc=$?
      echo "probe ${mode} with the JVM killed: exit ${rc}"
    done
    kill -CONT "${supervisor}"' >>"${out}" 2>&1 || true
  rc="$(timeout 60 docker wait "${desktop}" 2>/dev/null || echo timeout)"
  {
    echo "desktop container exit code after the JVM was killed: ${rc}"
    echo "== last lines of the desktop log"
    docker logs --tail 4 "${desktop}" 2>&1
  } >>"${out}"
  cat "${out}"
  grep -q '^web container exit code: [1-9]' "${out}" &&
    grep -q '^desktop still running: true' "${out}" &&
    grep -q 'probe started with the JVM killed: exit 1' "${out}" &&
    grep -q 'probe live with the JVM killed: exit 1' "${out}" &&
    grep -q '^desktop container exit code after the JVM was killed: [1-9]' "${out}"
}

do_audit() {
  docker run --rm --name takx-img-audit --network none "${hardening[@]}" --user 10001:10001 --cpus 1 --memory 1g \
    --tmpfs /tmp:rw,exec,nosuid,nodev,size=600m,mode=1777 --entrypoint "${app}/elf_audit.sh" \
    "${image}" >"${evidence}/elf-audit-runtime.txt" 2>"${evidence}/elf-audit-runtime.summary"
  cat "${evidence}/elf-audit-runtime.summary"
  docker run --rm --name takx-img-audit --network none "${hardening[@]}" --user 10001:10001 --cpus 1 \
    --entrypoint cat "${image}" /usr/share/takx-desktop/verify/elf-audit.txt >"${evidence}/elf-audit-build.txt"
  echo "selftest: build-time transcript with readelf: ${evidence}/elf-audit-build.txt"
  grep -c 'RUNPATH' "${evidence}/elf-audit-build.txt" | sed 's/^/RUNPATH lines in the build transcript: /'
  tail -n 8 "${evidence}/elf-audit-build.txt"
}

identity_run() { # label secret-dir [env...] ; prints the container output, returns its exit code
  local label="$1" secret="$2"; shift 2
  local envs=() pair rc=0
  for pair in "$@"; do envs+=(-e "${pair}"); done
  # shellcheck disable=SC2016 # expanded by the shell in the container
  docker run --rm --name takx-img-identity --network none "${hardening[@]}" --user 10001:10001 --cpus 1 --memory 512m \
    --tmpfs /run/takx/identity:rw,noexec,nosuid,nodev,size=8m,uid=10001,gid=10001,mode=0700 \
    -v "${secret}:/run/takx/secret:ro" "${envs[@]}" --entrypoint bash "${image}" -c '
      rc=0; /opt/takx-desktop/entrypoint.sh identity || rc=$?
      echo "exit ${rc}"
      cd /run/takx/identity && for name in *; do
        if [[ -e "${name}" ]]; then stat -c "staged %a %s %n" "${name}"; fi; done
      if [[ -e truststore-java.p12 ]]; then
        for store in truststore.p12 truststore-java.p12; do
          echo "keytool lists in ${store}: $(keytool -J-XX:-UsePerfData -list -storetype PKCS12 -keystore "${store}" \
            -storepass:file password.txt 2>/dev/null | sed -n "s/^Your keystore contains \([0-9]*\) entr.*/\1/p")"
        done
      fi
      if [[ -e takx-package.zip ]]; then
        python3 -I -c "import re, zipfile; print(\"connect string in takx-package.zip:\", re.search(rb\"connectString0[^>]*>([^<]*)<\", zipfile.ZipFile(\"takx-package.zip\").read(\"certs/server.pref\")).group(1).decode())"
      fi
      exit "${rc}"' >"${evidence}/identity-${label}.txt" 2>&1 || rc=$?
  cat "${evidence}/identity-${label}.txt"
  return "${rc}"
}

do_identity() {
  local work="${root}/pkg/selftest/identity" password rc summary="${evidence}/identity-summary.txt"
  rm -rf "${work}"
  mkdir -p "${work}"/{pki,good/certs,good/MANIFEST,secret-good,secret-missing-entry,secret-bad-password,secret-empty}
  # A synthetic CA and client identity, made here and never tracked. The
  # stores are built like the TakClient operator builds them: the client
  # store holds the key, the client certificate and the CA; the trust store
  # holds the CA certificate only, without the attribute the JDK wants.
  password="$(openssl rand -hex 16)"
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj '/CN=selftest-ca/O=takx-desktop-selftest' \
    -addext 'basicConstraints=critical,CA:TRUE' -keyout "${work}/pki/ca.key" -out "${work}/pki/ca.pem" 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj '/CN=selftest-client/O=takx-desktop-selftest' \
    -keyout "${work}/pki/client.key" -out "${work}/pki/client.csr" 2>/dev/null
  openssl x509 -req -in "${work}/pki/client.csr" -CA "${work}/pki/ca.pem" -CAkey "${work}/pki/ca.key" -CAcreateserial \
    -days 1 -out "${work}/pki/client.pem" 2>/dev/null
  openssl pkcs12 -export -name selftest-client -inkey "${work}/pki/client.key" -in "${work}/pki/client.pem" \
    -certfile "${work}/pki/ca.pem" -passout "pass:${password}" -out "${work}/good/certs/selftest-client.p12"
  openssl pkcs12 -export -nokeys -name tak-ca -in "${work}/pki/ca.pem" -passout "pass:${password}" \
    -out "${work}/good/certs/truststore.p12"
  cat >"${work}/good/MANIFEST/manifest.xml" <<'XML'
<MissionPackageManifest version="2">
  <Configuration>
    <Parameter name="uid" value="00000000-0000-4000-8000-000000000001"/>
    <Parameter name="name" value="selftest-client"/>
    <Parameter name="onReceiveDelete" value="true"/>
  </Configuration>
  <Contents>
    <Content ignore="false" zipEntry="certs/server.pref"/>
    <Content ignore="false" zipEntry="certs/truststore.p12"/>
    <Content ignore="false" zipEntry="certs/selftest-client.p12"/>
  </Contents>
</MissionPackageManifest>
XML
  cat >"${work}/good/certs/server.pref" <<XML
<?xml version='1.0' standalone='yes'?>
<preferences>
  <preference version="1" name="cot_streams">
    <entry key="count" class="class java.lang.Integer">1</entry>
    <entry key="description0" class="class java.lang.String">TAK Server</entry>
    <entry key="enabled0" class="class java.lang.Boolean">true</entry>
    <entry key="connectString0" class="class java.lang.String">takserver-stream.selftest.invalid:8089:ssl</entry>
    <entry key="caLocation0" class="class java.lang.String">cert/truststore.p12</entry>
    <entry key="caPassword0" class="class java.lang.String">${password}</entry>
    <entry key="certificateLocation0" class="class java.lang.String">cert/selftest-client.p12</entry>
    <entry key="clientPassword0" class="class java.lang.String">${password}</entry>
  </preference>
</preferences>
XML
  (cd "${work}/good" && zip -q -X ../secret-good/package.zip MANIFEST/manifest.xml certs/server.pref certs/truststore.p12 certs/selftest-client.p12)
  (cd "${work}/good" && zip -q -X ../secret-missing-entry/package.zip MANIFEST/manifest.xml certs/server.pref certs/selftest-client.p12)
  cp "${work}/secret-good/package.zip" "${work}/secret-bad-password/package.zip"
  printf '%s' "${password}" >"${work}/secret-good/password"
  printf '%s' "${password}" >"${work}/secret-missing-entry/password"
  printf '%s' "0000000000000000ffffffffffffffff" >"${work}/secret-bad-password/password"
  chmod -R a+rX "${work}"/secret-*
  chmod -R go-rwx "${work}/pki" "${work}/good"

  : >"${summary}"
  expect() { # label wanted-rc secret [env...]
    local label="$1" wanted="$2" got=0; shift 2
    identity_run "${label}" "$@" >/dev/null || got=$?
    echo "${label}: exit ${got} (wanted ${wanted})" | tee -a "${summary}"
    [[ "${got}" == "${wanted}" ]]
  }
  rc=0
  expect valid 0 "${work}/secret-good" TAKX_IDENTITY_EXPECT_HOST=takserver-stream.selftest.invalid \
    TAKX_IDENTITY_CONNECT_HOST=takserver || rc=1
  expect wrong-host 1 "${work}/secret-good" TAKX_IDENTITY_EXPECT_HOST=takserver-stream.other.invalid || rc=1
  expect missing-entry 1 "${work}/secret-missing-entry" || rc=1
  expect bad-password 1 "${work}/secret-bad-password" || rc=1
  expect no-secret 0 "${work}/secret-empty" || rc=1
  {
    echo "files staged by the valid case (mode size name):"
    grep '^staged ' "${evidence}/identity-valid.txt" || true
    grep -E '^keytool lists|^connect string' "${evidence}/identity-valid.txt" || true
    echo "files left by the rejected cases: $(cat "${evidence}"/identity-{wrong-host,missing-entry,bad-password}.txt | grep -c '^staged ' || true)"
    echo "occurrences of the password in any identity output: $(cat "${evidence}"/identity-*.txt | grep -c -F "${password}" || true)"
    echo "occurrences of key or PKCS#12 material markers: $(cat "${evidence}"/identity-*.txt | grep -c -E 'PRIVATE KEY|BEGIN CERTIFICATE|MII[A-Za-z0-9+/]{20}' || true)"
    echo "no-secret message: $(grep -m1 'no identity staged' "${evidence}/identity-no-secret.txt" || echo missing)"
  } | tee -a "${summary}"
  unset password
  rm -rf "${work}"
  grep -q '^occurrences of the password in any identity output: 0$' "${summary}" || rc=1
  grep -q '^occurrences of key or PKCS#12 material markers: 0$' "${summary}" || rc=1
  grep -q '^files left by the rejected cases: 0$' "${summary}" || rc=1
  [[ "$(grep -c '^staged 400 ' "${evidence}/identity-valid.txt")" == 6 ]] || rc=1
  return "${rc}"
}

# One more boot of the desktop alone, with the dynamic linker's own trace
# (LD_DEBUG=libs) for every process: which directories it searched and which
# files it tried. Then 'docker stop', to see the session end on SIGTERM.
do_linker() {
  local out="${evidence}/linker.txt" rc started
  do_stop
  docker volume create --driver local --opt type=tmpfs --opt device=tmpfs \
    --opt o=size=1m,uid=10001,gid=10001,mode=2770 "${volume}" >/dev/null
  docker run -d --name "${desktop}" --network none --hostname takx-desktop --add-host takx-desktop:127.0.0.1 \
    "${hardening[@]}" --user 10001:10001 --cpus 1.5 --memory 5g \
    --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
    --tmpfs /run/takx/session:rw,exec,nosuid,nodev,size=2g,uid=10001,gid=10001,mode=0750 \
    --mount "type=volume,src=${volume},dst=/run/takx/vnc" \
    -e "EXTRA_JVM_OPTS=${TAKX_SELFTEST_JVM:--Xmx2g}" -e LD_DEBUG=libs -e LD_DEBUG_OUTPUT=/tmp/ld \
    "${image}" desktop >/dev/null
  wait_label=-linker do_wait 300
  # shellcheck disable=SC2016 # expanded by the shell in the container
  docker exec -e LD_DEBUG= "${desktop}" bash -c '
    jvm="$(cat /run/takx/session/jvm.pid)"
    trace="/tmp/ld.${jvm}"
    echo "linker trace of the JVM (pid ${jvm}): $(wc -l <"${trace}") lines"
    echo "lines naming /var/lib/takx-proto in any process trace: $(cat /tmp/ld.* | grep -c "/var/lib/takx-proto" || true)"
    echo "working directory of the JVM: $(readlink "/proc/${jvm}/cwd") (writable by this user: $(if [[ -w "/proc/${jvm}/cwd/." ]]; then echo yes; else echo no; fi))"
    echo "search path lines of the JVM naming a RUNPATH: $(grep -c "RUNPATH from file" "${trace}" || true)"
    echo "search path lines of the JVM naming a Kernel library RUNPATH: $(grep "RUNPATH from file" "${trace}" | grep -c takkernel || true)"
    echo "search paths with an empty or relative element, all processes: $(cat /tmp/ld.* | grep "search path=" | sed -E "s/^.*search path=([^\t]*).*$/\1/" | grep -c -E "^$|^:|:$|::|^[^/]|:[^/]" || true)"
    echo "files tried by a relative name, all processes: $(cat /tmp/ld.* | grep -c -E "trying file=[^/]" || true)"
    echo "files the linker tried for the Kernel SONAMEs (none means they were already loaded):"
    tried="$(grep -E "trying file=.*(libGLESv2|libGLdispatch|libassimp|libgdal|liblas|libtakengine)" "${trace}" || true)"
    echo "  ${tried:-none}"
    echo "calling init of the Kernel libraries, in order:"
    grep -E "calling init: .*/takkernel/" "${trace}" | sed "s/^ *[0-9]*:\s*calling init: /  /"' >"${out}" 2>&1 || true
  started="$(date +%s)"
  docker stop -t 30 "${desktop}" >/dev/null
  rc="$(docker inspect -f '{{.State.ExitCode}}' "${desktop}")"
  {
    echo "docker stop (SIGTERM): exit code ${rc} after $(($(date +%s) - started)) s"
    docker logs "${desktop}" 2>&1 | grep 'takx-desktop:' | tail -n 2
  } >>"${out}"
  cat "${out}"
  do_stop
  grep -q '^lines naming /var/lib/takx-proto in any process trace: 0$' "${out}" &&
    grep -q '^search path lines of the JVM naming a Kernel library RUNPATH: 0$' "${out}" &&
    grep -q '^search paths with an empty or relative element, all processes: 0$' "${out}" &&
    grep -q '^files tried by a relative name, all processes: 0$' "${out}" &&
    grep -q '^docker stop (SIGTERM): exit code 0 ' "${out}"
}

do_browser() {
  local relay rc=0 port="${TAKX_SELFTEST_RELAY_PORT:-16080}"
  if ! node -e "require('playwright')" >/dev/null 2>&1; then
    echo "selftest: node with the playwright module is not available on this host; browser step not run"
    return 3
  fi
  # The containers keep --network none: the browser reaches port 6080 of the
  # web container through 'docker exec' (exec_relay.py), nothing is published.
  # The relay adds the token to each request, as Authservice does.
  auth_cases
  TAKX_RELAY_BEARER="$(good_token)" python3 -I "${here}/exec_relay.py" "${web}" 6080 "${port}" \
    >"${evidence}/exec-relay.log" 2>&1 &
  relay=$!
  sleep 1
  NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost timeout 150 \
    node "${here}/browser_probe.js" "http://127.0.0.1:${port}/" "${evidence}/browser-novnc.png" \
    >"${evidence}/browser-probe.json" 2>"${evidence}/browser-probe.err" || rc=$?
  kill "${relay}" 2>/dev/null || true
  wait "${relay}" 2>/dev/null || true
  cat "${evidence}/browser-probe.json"
  return "${rc}"
}

# Run auth_probe.py against HOST:PORT from inside CONTAINER, or from the host
# when CONTAINER is '-'. The request variants go through the environment.
auth_probe() { # container target label output [probe option...]
  local container="$1" target="$2" label="$3" out="$4" rc=0; shift 4
  if [[ "${container}" == - ]]; then
    TAKX_AUTH_CASES="$(cat "${auth_dir}/cases.json")" NO_PROXY='*' no_proxy='*' \
      python3 -I "${here}/auth_probe.py" "${target}" --label "${label}" "$@" >"${out}" || rc=$?
  else
    TAKX_AUTH_CASES="$(cat "${auth_dir}/cases.json")" docker exec -i -e TAKX_AUTH_CASES "${container}" \
      python3 -I - "${target}" --label "${label}" "$@" <"${here}/auth_probe.py" >"${out}" || rc=$?
  fi
  python3 -I -c 'import json, sys
report = json.load(open(sys.argv[1]))
print(f"selftest auth: {report[\"label\"]}: {report[\"status\"]}; reached the desktop: "
      f"{report[\"variants_that_reached_the_desktop\"]}")' "${out}" || rc=1
  return "${rc}"
}

do_auth() {
  local rc=0 address strict=takx-img-web-strict nokeys=takx-img-web-nokeys out="${evidence}/auth-summary.txt" hits
  docker rm -f "${strict}" "${nokeys}" >/dev/null 2>&1 || true
  auth_cases
  : >"${out}"
  # 1. Every variant against the running bridge, from a process next to it.
  auth_probe "${web}" 127.0.0.1:6080 "inside the web container" "${evidence}/auth-inside.json" | tee -a "${out}" || rc=1
  # 2. The same from the host's network namespace, when the containers have
  #    an address there: what a process on a node sees of a pod.
  address="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "${desktop}")"
  if [[ -n "${address}" ]]; then
    auth_probe - "${address}:6080" "from the host's network namespace to ${address}:6080" \
      "${evidence}/auth-from-host.json" | tee -a "${out}" || rc=1
    {
      echo "curl from the host, no token: $(NO_PROXY='*' curl -s -o /dev/null -m 10 -w '%{http_code} %{size_download} bytes' "http://${address}:6080/vnc.html")"
      echo "curl from the host, no token, /healthz: $(NO_PROXY='*' curl -s -m 10 -w ' -> %{http_code}' "http://${address}:6080/healthz" | tr -d '\n')"
      echo "curl from the host, good token: $(NO_PROXY='*' curl -s -o /dev/null -m 10 -w '%{http_code} %{size_download} bytes' \
        -K <(printf 'header = "Authorization: Bearer %s"\n' "$(good_token)") "http://${address}:6080/vnc.html")"
      for port in 4242 8087; do
        echo "TCP ${port} from the host: $(if timeout 3 bash -c "exec 3<>/dev/tcp/${address}/${port}" 2>/dev/null; then echo open; else echo closed; fi)"
      done
    } | tee -a "${out}"
    grep -q '^curl from the host, no token: 401 ' "${out}" || rc=1
    grep -q '^curl from the host, good token: 200 ' "${out}" || rc=1
    grep -q '^TCP 4242 from the host: closed$' "${out}" || rc=1
    grep -q '^TCP 8087 from the host: closed$' "${out}" || rc=1
  else
    echo "selftest auth: the containers have no address the host can reach (--network none); host part not run" | tee -a "${out}"
  fi
  # 3. A second bridge on the same desktop that ends a WebSocket when its
  #    token expires (TAKX_AUTH_WS_MAX_SECONDS=0), on port 6090.
  run_web "${strict}" -e TAKX_WEB_LISTEN=0.0.0.0:6090 -e TAKX_AUTH_WS_MAX_SECONDS=0 -e TAKX_AUTH_LEEWAY_SECONDS=0
  # 4. A third one whose key endpoint does not answer, on port 6091.
  run_web "${nokeys}" -e TAKX_WEB_LISTEN=0.0.0.0:6091 -e TAKX_AUTH_JWKS_URL=http://127.0.0.1:9/certs
  sleep 3
  auth_cases 20
  auth_probe "${strict}" 127.0.0.1:6090 "a WebSocket and the expiry of its token" "${evidence}/auth-expiry.json" --expiry |
    tee -a "${out}" || rc=1
  python3 -I - "${auth_dir}/cases.json" <<'PY'
import json, sys
cases = [case for case in json.load(open(sys.argv[1])) if case["name"] != "short-lived-token"]
for case in cases:
    # Without keys nobody is let in: a well-formed Bearer token gets 503,
    # everything that is refused before a key is needed keeps its answer.
    if case["name"] in ("good-token", "wrong-signature", "payload-swapped", "wrong-issuer", "wrong-audience", "expired",
                        "not-yet-valid", "missing-group", "no-groups-claim", "unknown-kid"):
        case["expect"] = 503
json.dump(cases, open(sys.argv[1], "w"))
PY
  auth_probe "${nokeys}" 127.0.0.1:6091 "a bridge that cannot fetch the keys" "${evidence}/auth-no-keys.json" |
    tee -a "${out}" || rc=1
  docker logs "${strict}" >"${evidence}/web-strict.log" 2>&1 || true
  docker logs "${nokeys}" >"${evidence}/web-nokeys.log" 2>&1 || true
  docker logs "${web}" >"${evidence}/web.log" 2>&1 || true
  docker rm -f "${strict}" "${nokeys}" >/dev/null 2>&1 || true
  # No token, and no part of one, in any log of the bridge.
  hits="$(python3 -I - "${auth_dir}/cases.json" "${evidence}"/web.log "${evidence}"/web-strict.log "${evidence}"/web-nokeys.log <<'PY'
import json, re, sys
tokens = set()
for case in json.load(open(sys.argv[1])):
    for header in case["headers"]:
        tokens.update(part for part in re.split(r"[ .;=]", header) if len(part) > 40)
text = "".join(open(path, errors="replace").read() for path in sys.argv[2:])
print(sum(text.count(token[:40]) for token in tokens))
PY
)"
  {
    echo "token fragments in the bridge logs: ${hits}"
    echo "denials logged by the bridge: $(grep -c ' denied: ' "${evidence}/web.log" || true)"
    echo "websockets opened through the bridge: $(grep -c ' websocket open' "${evidence}/web.log" || true)"
    echo "ended by the session limit on the strict bridge: $(grep -c 'session limit reached' "${evidence}/web-strict.log" || true)"
  } | tee -a "${out}"
  [[ "${hits}" == 0 ]] || rc=1
  grep -q '^ended by the session limit on the strict bridge: [1-9]' "${out}" || rc=1
  return "${rc}"
}

do_logs() {
  docker logs "${desktop}" >"${evidence}/desktop.log" 2>&1 || true
  docker logs "${web}" >"${evidence}/web.log" 2>&1 || true
  ls -la "${evidence}"
}

case "${1:-}" in
  start) do_start;;
  wait) shift; do_wait "$@";;
  check) do_check;;
  stats) do_stats;;
  supervision) do_supervision;;
  audit) do_audit;;
  identity) do_identity;;
  auth) do_auth;;
  browser) do_browser;;
  linker) do_linker;;
  logs) do_logs;;
  stop) do_stop;;
  -h|--help|"") sed -n '5,75p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) echo "selftest.sh: unknown step '$1'" >&2; exit 2;;
esac
