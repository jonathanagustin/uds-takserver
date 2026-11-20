#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Run one command in the desktop-prototype toolchain (or runtime) image with the
# standard mounts and environment used by every stage of the prototype:
#
#   * the repository, read-only, at the same absolute path as on the host;
#   * the persistent work root (default /var/lib/takx-proto) at the same path;
#   * an optional CA bundle, read-only, at /ca.crt, exported through
#     SSL_CERT_FILE, CURL_CA_BUNDLE, GIT_SSL_CAINFO, PIP_CERT and
#     REQUESTS_CA_BUNDLE. TLS verification is never disabled;
#   * no proxy variables and none of the caller's JAVA_TOOL_OPTIONS.
#
# Usage:
#   run-in-toolchain.sh [options] -- command [args...]
#
# Options:
#   --runtime            use takx-desktop-proto:runtime instead of :toolchain
#   --image IMAGE        use another image
#   --name NAME          container name (required with -d)
#   -d, --detach         start detached and print the container id; wait with
#                        'timeout 540 docker wait NAME', read 'docker logs NAME'
#   --log FILE           with -d: run the command with stdout and stderr appended
#                        to FILE (must be under the work root)
#   --cpus N             CPU limit (docker --cpus)
#   --memory SIZE        memory limit (docker --memory), e.g. 6g
#   -e, --env KEY=VALUE  extra environment (repeatable)
#   -w, --workdir DIR    working directory (default: the work root)
#   --mount SPEC         extra docker -v specification (repeatable)
#   --docker-arg ARG     extra raw docker run argument (repeatable)
#   --java-truststore    build (once) and print the Java trust store that holds
#                        the JDK roots plus the mounted CA bundle, then exit
#
# Environment:
#   TAKX_PROTO_ROOT  work root, default /var/lib/takx-proto
#   TAKX_PROTO_CA    CA bundle to mount, default /root/.ccr/ca-bundle.crt when
#                    that file exists, otherwise none (image trust store)
#
# Inside the container TAKX_PROTO_ROOT and TAKX_PROTO_REPO are set. When the
# trust store exists, TAKX_JAVA_TRUSTSTORE names it; pass it to a JVM that must
# reach the network with -Djavax.net.ssl.trustStore="$TAKX_JAVA_TRUSTSTORE"
# -Djavax.net.ssl.trustStorePassword=changeit. It is deliberately not placed in
# JAVA_TOOL_OPTIONS because the JVM banner that variable causes breaks recipes
# that compare tool output.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
ca="${TAKX_PROTO_CA-}"
if [[ -z "$ca" && -s /root/.ccr/ca-bundle.crt ]]; then ca=/root/.ccr/ca-bundle.crt; fi

image=takx-desktop-proto:toolchain
name=""
detach=0
log=""
workdir="$root"
truststore_only=0
extra=()
envs=()

while (($#)); do
  case "$1" in
    --runtime) image=takx-desktop-proto:runtime; shift;;
    --image) image="$2"; shift 2;;
    --name) name="$2"; shift 2;;
    -d|--detach) detach=1; shift;;
    --log) log="$2"; shift 2;;
    --cpus) extra+=(--cpus "$2"); shift 2;;
    --memory) extra+=(--memory "$2"); shift 2;;
    -e|--env) envs+=(-e "$2"); shift 2;;
    -w|--workdir) workdir="$2"; shift 2;;
    --mount) extra+=(-v "$2"); shift 2;;
    --docker-arg) extra+=("$2"); shift 2;;
    --java-truststore) truststore_only=1; shift;;
    --) shift; break;;
    -h|--help) sed -n '5,45p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "run-in-toolchain.sh: unknown option '$1' (use -- before the command)" >&2; exit 2;;
  esac
done

mkdir -p "$root"/{cache,src,P,logs,status,failed,ca}

base=(--init
  -v "$repo:$repo:ro"
  -v "$root:$root"
  -e "TAKX_PROTO_ROOT=$root"
  -e "TAKX_PROTO_REPO=$repo"
  -e HOME=/tmp)
if [[ -n "$ca" ]]; then
  [[ -s "$ca" ]] || { echo "run-in-toolchain.sh: CA bundle '$ca' is missing or empty" >&2; exit 2; }
  base+=(-v "$ca:/ca.crt:ro"
    -e SSL_CERT_FILE=/ca.crt -e CURL_CA_BUNDLE=/ca.crt -e GIT_SSL_CAINFO=/ca.crt
    -e PIP_CERT=/ca.crt -e REQUESTS_CA_BUNDLE=/ca.crt)
fi

truststore="$root/ca/java-cacerts.p12"
build_truststore() {
  # JDK roots plus every certificate of the mounted bundle, written once and
  # published atomically. The fingerprint file ties it to the bundle it came from.
  local want have=""
  want="$(sha256sum "$ca" | cut -d' ' -f1)"
  [[ -s "$truststore.source-sha256" ]] && have="$(cat "$truststore.source-sha256")"
  if [[ -s "$truststore" && "$want" == "$have" ]]; then return 0; fi
  (
    flock 9
    [[ -s "$truststore.source-sha256" ]] && have="$(cat "$truststore.source-sha256")"
    if [[ -s "$truststore" && "$want" == "$have" ]]; then exit 0; fi
    docker run --rm "${base[@]}" "$image" bash -euo pipefail -c '
      work="$(mktemp -d)"; out="$1"
      keytool -importkeystore -noprompt -srckeystore "$JAVA_HOME/lib/security/cacerts" \
        -srcstorepass changeit -destkeystore "$work/store.p12" -deststoretype PKCS12 \
        -deststorepass changeit >/dev/null 2>&1
      awk -v dir="$work" "/-----BEGIN CERTIFICATE-----/{n++} n{print > (dir \"/ca-\" n \".pem\")}" /ca.crt
      for pem in "$work"/ca-*.pem; do
        keytool -importcert -noprompt -trustcacerts -alias "bundle-$(basename "$pem" .pem)" \
          -file "$pem" -keystore "$work/store.p12" -storepass changeit >/dev/null 2>&1 || true
      done
      keytool -list -keystore "$work/store.p12" -storepass changeit | grep -c trustedCertEntry >&2
      cp "$work/store.p12" "$out.tmp.$$" && mv "$out.tmp.$$" "$out"
    ' truststore "$truststore" 2>"$root/logs/java-truststore.log"
    echo "$want" > "$truststore.source-sha256"
  ) 9>"$root/ca/.lock"
}

if ((truststore_only)); then
  [[ -n "$ca" ]] || { echo "run-in-toolchain.sh: no CA bundle; the JDK trust store applies" >&2; exit 0; }
  build_truststore
  echo "$truststore"
  exit 0
fi
if [[ -n "$ca" && -s "$truststore" ]]; then base+=(-e "TAKX_JAVA_TRUSTSTORE=$truststore"); fi

(($#)) || { echo "run-in-toolchain.sh: no command given" >&2; exit 2; }

args=(run "${base[@]}" -w "$workdir")
if [[ -n "$name" ]]; then args+=(--name "$name"); fi
if ((detach)); then
  [[ -n "$name" ]] || { echo "run-in-toolchain.sh: -d needs --name" >&2; exit 2; }
  args+=(-d)
else
  args+=(--rm)
  if [[ -t 0 && -t 1 ]]; then args+=(-it); fi
fi
if ((${#extra[@]})); then args+=("${extra[@]}"); fi
if ((${#envs[@]})); then args+=("${envs[@]}"); fi
args+=("$image")

if [[ -n "$log" ]]; then
  case "$log" in "$root"/*) ;; *) echo "run-in-toolchain.sh: --log must be under $root" >&2; exit 2;; esac
  # A '..' component would name a file outside the work root after all.
  case "/${log#"$root"/}/" in */../*) echo "run-in-toolchain.sh: --log must be under $root" >&2; exit 2;; esac
  mkdir -p "$(dirname "$log")"
  # The command runs as given; only its output is redirected, and the exit
  # status of the container is the exit status of the command.
  exec docker "${args[@]}" bash -c 'log="$1"; shift; exec "$@" >>"$log" 2>&1' takx-log "$log" "$@"
fi
exec docker "${args[@]}" "$@"
