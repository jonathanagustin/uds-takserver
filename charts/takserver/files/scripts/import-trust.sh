#!/bin/bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# trustAllCerts is false. Copy the JVM cacerts and add the certificates TAK
# needs for https://sso.<domain>.
#
# A populated uds-trust-bundle is imported when the operator wrote one.
# k3d-core-slim-dev does not set CA bundle variables, and the operator then
# deletes the ConfigMap instead of leaving it empty. The issuer certificate
# presented by the tenant gateway is imported in that case. ISSUER_HOST is
# the configurationUri host, and in-cluster DNS sends it to the gateway.
set -euo pipefail

store=/trust/cacerts
src=""
for candidate in \
  "${JAVA_HOME:-}/lib/security/cacerts" \
  /etc/pki/ca-trust/extracted/java/cacerts \
  /etc/pki/java/cacerts
do
  if [[ -n "${candidate}" ]] && [[ -f "${candidate}" ]]; then
    src="${candidate}"
    break
  fi
done
if [[ -z "${src}" ]]; then
  echo "JVM cacerts was not found" >&2
  exit 1
fi
cp "${src}" "${store}"
chmod 0644 "${store}"

imported=0

import_file() {
  local file="$1"
  local alias="$2"
  if [[ ! -s "${file}" ]]; then
    return 0
  fi
  if keytool -importcert -noprompt -alias "${alias}" \
    -keystore "${store}" -storepass changeit -file "${file}"; then
    imported=1
  fi
}

split_and_import() {
  local bundle="$1"
  local prefix="$2"
  rm -f /tmp/uds-ca-*.pem
  awk '
    /-----BEGIN CERTIFICATE-----/ { n++; file = sprintf("/tmp/uds-ca-%d.pem", n) }
    n > 0 { print > file }
  ' "${bundle}"
  local cert
  for cert in /tmp/uds-ca-*.pem; do
    [[ -f "${cert}" ]] || continue
    import_file "${cert}" "${prefix}-$(basename "${cert}" .pem)"
  done
}

bundle=/bundle/ca-bundle.pem
ready=0
for _ in $(seq 1 15); do
  if [[ -s "${bundle}" ]]; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" -eq 1 ]]; then
  echo "importing uds-trust-bundle"
  split_and_import "${bundle}" "uds"
else
  echo "uds-trust-bundle is empty or absent"
fi

issuer="${ISSUER_HOST:-}"
if [[ -n "${issuer}" ]] && command -v openssl >/dev/null 2>&1; then
  if ! command -v timeout >/dev/null 2>&1; then
    echo "timeout is required to fetch the issuer certificate" >&2
    exit 1
  fi
  echo "importing certificate presented by ${issuer}:443"
  issuer_output=$(mktemp /tmp/uds-issuer.XXXXXX)
  trap 'rm -f -- "${issuer_output}"' EXIT
  # Bound both an unresponsive TLS peer and its output. A failed or killed
  # fetch must not turn a partial certificate transcript into a trust source.
  if (
    ulimit -c 0
    ulimit -f 128
    echo | timeout --signal=TERM --kill-after=5s 15s \
      openssl s_client -connect "${issuer}:443" -servername "${issuer}" -showcerts
  ) > "${issuer_output}" 2>/dev/null; then
    if [[ -s "${issuer_output}" ]]; then
      split_and_import "${issuer_output}" "issuer"
    fi
  fi
  rm -f -- "${issuer_output}"
fi

if [[ "${imported}" -ne 1 ]]; then
  echo "no certificate was imported into the API trust store" >&2
  exit 1
fi
keytool -list -keystore "${store}" -storepass changeit >/dev/null
echo "API trust store ready"
