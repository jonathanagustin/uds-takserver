#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Evidence that the TAK Server is up and reachable from this host and from a
# container of the TAKX runtime image. Read-only. Prints public facts only.
# With an exported identity (70-takclients.sh) it also completes a mutual TLS
# handshake and calls the client-certificate API.
#
#   60-verify.sh [identity-name]     default identity: takx-desktop
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"
# shellcheck disable=SC1091
. "${SERVER_STATE}/endpoints.env"
identity="${1:-takx-desktop}"
dir="${SECRETS}/${identity}"
RUNTIME_IMAGE="${RUNTIME_IMAGE:-takx-desktop-proto:runtime}"
fail=0
note() { printf '\n== %s\n' "$*"; }
check() { if "$@"; then echo "PASS: $*"; else echo "FAIL: $*"; fail=1; fi; }

note "workloads"
kubectl get pods -n takserver -o wide --no-headers | awk '{print $1, $2, $3, $4}'
kubectl get pods -n cloudtak --no-headers 2>/dev/null | awk '{print $1, $2, $3, $4}'
for deployment in takserver-api takserver-messaging takserver-config takserver-plugins; do
  check kubectl -n takserver wait "deployment/${deployment}" --for=condition=Available --timeout=5s
done
kubectl get packages.uds.dev -A --no-headers | awk '{print $1, $2, $3}'

note "streaming port ${TAK_STREAM_HOST}:${TAK_STREAM_PORT} at ${PASSTHROUGH_GATEWAY_IP}, server certificate (no client certificate offered)"
openssl s_client -connect "${PASSTHROUGH_GATEWAY_IP}:${TAK_STREAM_PORT}" -servername "${TAK_STREAM_HOST}" \
  </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -enddate -ext subjectAltName

note "API host ${TAK_API_HOST}:${TAK_API_PORT} by SNI on the passthrough gateway, server certificate"
openssl s_client -connect "${PASSTHROUGH_GATEWAY_IP}:${TAK_API_PORT}" -servername "${TAK_API_HOST}" \
  </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -ext subjectAltName

note "browser endpoints on the tenant gateway (public *.${DOMAIN} certificate, verified)"
for url in "https://sso.${DOMAIN}/realms/uds/.well-known/openid-configuration" "https://takserver.${DOMAIN}/" "${CLOUDTAK_URL}"; do
  curl -sS -o /dev/null -w "%{http_code} %{remote_ip} ${url}\n" "${url}" || fail=1
done

if [ -s "${dir}/client.pem" ]; then
  note "mutual TLS as ${identity}: TAK CA as the only trust root, server name ${TAK_CERT_NAME} checked"
  if openssl s_client -connect "${PASSTHROUGH_GATEWAY_IP}:${TAK_STREAM_PORT}" \
      -CAfile "${dir}/ca.pem" -verify_return_error -verify_hostname "${TAK_CERT_NAME}" \
      -cert "${dir}/client.pem" -key "${dir}/client.key" </dev/null 2>&1 |
      grep -E 'Verification: OK|Verified peername|Verify return code|^New, ' ; then :; else fail=1; fi
  note "the gateway host name is not in the certificate: a name-checking client must dial ${TAK_CERT_NAME}"
  if openssl s_client -connect "${PASSTHROUGH_GATEWAY_IP}:${TAK_STREAM_PORT}" \
      -CAfile "${dir}/ca.pem" -verify_return_error -verify_hostname "${TAK_STREAM_HOST}" \
      -cert "${dir}/client.pem" -key "${dir}/client.key" </dev/null 2>&1 | grep -E 'Verify return code'; then :; fi
  note "client-certificate API as ${identity}: SNI ${TAK_API_HOST} for the route, certificate name ${TAK_CERT_NAME} checked"
  printf 'GET /Marti/api/version HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n' "${TAK_API_HOST}" |
    openssl s_client -quiet -connect "${PASSTHROUGH_GATEWAY_IP}:${TAK_API_PORT}" -servername "${TAK_API_HOST}" \
      -CAfile "${dir}/ca.pem" -verify_return_error -verify_hostname "${TAK_CERT_NAME}" \
      -cert "${dir}/client.pem" -key "${dir}/client.key" 2>/dev/null | grep -v -i '^set-cookie' | tr -d '\r' | sed -n '1p;$p'
  note "wrong trust root is rejected (system roots do not include the TAK CA)"
  if openssl s_client -connect "${PASSTHROUGH_GATEWAY_IP}:${TAK_STREAM_PORT}" \
      -verify_return_error </dev/null >/dev/null 2>&1; then
    echo "FAIL: handshake succeeded without the TAK CA"; fail=1
  else
    echo "PASS: handshake refused without the TAK CA"
  fi

  note "from a ${RUNTIME_IMAGE} container on the default Docker bridge, with --add-host"
  docker run --rm --add-host "${TAK_CERT_NAME}:${PASSTHROUGH_GATEWAY_IP}" \
    -v "${dir}/ca.pem:/ca.pem:ro" "${RUNTIME_IMAGE}" bash -c "
      getent hosts ${TAK_CERT_NAME}
      openssl s_client -connect ${TAK_CERT_NAME}:${TAK_STREAM_PORT} -servername ${TAK_CERT_NAME} \
        -CAfile /ca.pem -verify_return_error -verify_hostname ${TAK_CERT_NAME} </dev/null 2>&1 |
        grep -E 'Verification|Verified peername|Verify return code|subject=' | head -4" || fail=1
fi

note "memory"
free -m | sed -n 1,2p
exit "${fail}"
