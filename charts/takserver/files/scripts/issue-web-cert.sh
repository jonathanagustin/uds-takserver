#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# @lulaStart 1d49897a-a738-5e02-b561-715b77ff099b
# Issue the server certificate for the 8447 web connector and add it to
# ConfigMap cert-migration as takserver-web.jks. The tenant gateway connects
# to 8447 over TLS and verifies this certificate against the deploy-time CA,
# so its SANs are the in-cluster names of Service WEB_SERVICE.
# Runs in the cert-init Job after publish-cert-configmap.sh. On an upgrade
# the ConfigMap already exists, so the CA comes out of the ConfigMap. Reuse
# a keystore only while it matches that CA and Service and has 30 days left.

# Retry a refused or reset handshake. The k3s port-6443 tunnel refuses new
# connections for several seconds after this hook reads cert-migration, which
# is longer than five one-second tries. Twenty tries, two seconds apart.
# --retry-all-errors covers a TLS reset (curl 35 or 56). --retry-connrefused
# covers ECONNREFUSED. An HTTP status is not an error unless the caller
# passes -f, so 409 still returns to the caller. The config image is UBI9,
# whose curl is new enough for --retry-all-errors.
api_curl() {
  curl \
    --silent \
    --show-error \
    --retry 20 \
    --retry-delay 2 \
    --retry-all-errors \
    --retry-connrefused \
    --connect-timeout 5 \
    "$@"
}

set -euo pipefail

service="${WEB_SERVICE:?WEB_SERVICE is not set}"
key_name="${WEB_KEYSTORE:-takserver-web.jks}"
if [[ -z "${CAPASS:-}" ]]; then
  echo "CAPASS is not set. Secret takserver-keystore did not reach this Job." >&2
  exit 1
fi
pass="${CAPASS}"

# These overrides also let the executable tests use a local Kubernetes API.
sa="${SA_DIR:-/var/run/secrets/kubernetes.io/serviceaccount}"
ns="$(cat "${sa}/namespace")"
token="$(cat "${sa}/token")"
ca="${sa}/ca.crt"
base="${KUBE_API:-https://kubernetes.default.svc}/api/v1/namespaces/${ns}"
api="${base}/configmaps/cert-migration"
ca_key_file="${CA_KEY_FILE:-/opt/tak/certs/files/ca-do-not-share.key}"
fqdn="${service}.${ns}.svc.cluster.local"

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

api_curl -f --cacert "${ca}" -H "Authorization: Bearer ${token}" "${api}" > "${work}/cm.json"

# Pull one binaryData key out of the ConfigMap JSON. Base64 has no quotes.
field() {
  local name="${1//./\\.}" file="${2:-${work}/cm.json}"
  grep -o "\"${name}\": *\"[^\"]*\"" "${file}" | sed -n '1s/.*: *"\(.*\)"/\1/p'
}

field ca.pem | base64 -d > "${work}/ca.pem"
if [[ ! -s "${work}/ca.pem" ]]; then
  echo "ConfigMap cert-migration has no ca.pem" >&2
  exit 1
fi

# The ConfigMap takes precedence because split-key-material.sh copies its
# values over the Secret. On normal upgrades only the Secret has a keystore.
stored="$(field "${key_name}" || true)"
if [[ -z "${stored}" ]]; then
  secret_code="$(api_curl -o "${work}/certs.json" -w '%{http_code}' --cacert "${ca}" \
    -H "Authorization: Bearer ${token}" "${base}/secrets/takserver-certs")"
  if [[ "${secret_code}" == "200" ]]; then
    stored="$(field "${key_name}" "${work}/certs.json" || true)"
  elif [[ "${secret_code}" != "404" ]]; then
    echo "Reading Secret takserver-certs returned HTTP ${secret_code}" >&2
    exit 1
  fi
fi

current_keystore() {
  printf '%s' "${stored}" | base64 -d > "${work}/existing.jks" || return 1
  # A trusted-certificate entry without a private key cannot serve TLS.
  keytool -J-Duser.language=en -J-Duser.country=US -list -v -alias takserver-web \
    -keystore "${work}/existing.jks" -storepass "${pass}" > "${work}/entry.txt" 2>/dev/null || return 1
  grep -F 'PrivateKeyEntry' "${work}/entry.txt" >/dev/null || return 1
  keytool -exportcert -rfc -alias takserver-web -keystore "${work}/existing.jks" \
    -storepass "${pass}" > "${work}/existing.pem" 2>/dev/null || return 1
  openssl verify -CAfile "${work}/ca.pem" -purpose sslserver -verify_hostname "${fqdn}" \
    "${work}/existing.pem" >/dev/null 2>&1 || return 1
  openssl x509 -in "${work}/existing.pem" -noout -checkend 2592000 >/dev/null || return 1
  # Istio checks SANs, so a matching CN alone is insufficient.
  openssl x509 -in "${work}/existing.pem" -noout -ext subjectAltName \
    | tr ',' '\n' | sed 's/^[[:space:]]*//' | grep -Fx "DNS:${fqdn}" >/dev/null
}

if [[ -n "${stored}" ]] && current_keystore; then
  echo "${key_name} already has a current certificate for ${fqdn}"
  exit 0
fi
echo "Issuing ${key_name}: the web keystore is missing, unreadable, expires soon, or does not match the CA and Service"

# The CA key is not left in the ConfigMap TAK pods mount. A fresh install
# still has it on local disk. An older ConfigMap, or Secret takserver-ca-key,
# covers the upgrade and a Job retry.
if [[ -s "${ca_key_file}" ]]; then
  cp "${ca_key_file}" "${work}/ca.key"
else
  key_b64="$(api_curl --cacert "${ca}" -H "Authorization: Bearer ${token}" \
    "${base}/secrets/takserver-ca-key" \
    | grep -o '"ca-do-not-share.key": *"[^"]*"' | head -n 1 | sed 's/.*: *"\(.*\)"/\1/' || true)"
  if [[ -n "${key_b64}" ]]; then
    printf '%s' "${key_b64}" | base64 -d > "${work}/ca.key"
  else
    field ca-do-not-share.key | base64 -d > "${work}/ca.key"
  fi
fi
if [[ ! -s "${work}/ca.pem" ]] || [[ ! -s "${work}/ca.key" ]]; then
  echo "Could not read ca.pem and the CA key for the web certificate" >&2
  exit 1
fi

cat > "${work}/ext.cnf" <<CNF
basicConstraints = CA:FALSE
keyUsage = critical, digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:${fqdn}, DNS:${service}.${ns}.svc, DNS:${service}.${ns}, DNS:${service}
CNF

openssl req -new -newkey rsa:2048 -nodes \
  -keyout "${work}/web.key" -out "${work}/web.csr" \
  -subj "/CN=${fqdn}/O=${ORGANIZATION:-TAK}/OU=${ORGANIZATIONAL_UNIT:-UDS}"
openssl x509 -req -in "${work}/web.csr" -days 730 -sha256 \
  -CA "${work}/ca.pem" -CAkey "${work}/ca.key" -passin "pass:${pass}" \
  -set_serial "0x$(openssl rand -hex 16)" \
  -extfile "${work}/ext.cnf" -out "${work}/web.pem"
openssl verify -CAfile "${work}/ca.pem" "${work}/web.pem"

openssl pkcs12 -export \
  -in "${work}/web.pem" -inkey "${work}/web.key" -certfile "${work}/ca.pem" \
  -name takserver-web -out "${work}/web.p12" -passout "pass:${pass}"
keytool -importkeystore -noprompt \
  -srckeystore "${work}/web.p12" -srcstoretype PKCS12 -srcstorepass "${pass}" \
  -destkeystore "${work}/web.jks" -deststoretype PKCS12 \
  -deststorepass "${pass}" -destkeypass "${pass}" -alias takserver-web

printf '{"binaryData": {"%s": "%s"}}' "${key_name}" "$(base64 -w 0 "${work}/web.jks")" > "${work}/patch.json"
code="$(api_curl -o "${work}/resp" -w '%{http_code}' --cacert "${ca}" \
  -H "Authorization: Bearer ${token}" \
  -H "Content-Type: application/merge-patch+json" \
  -X PATCH --data-binary @"${work}/patch.json" "${api}")"
if [[ "${code}" != "200" ]]; then
  echo "ConfigMap cert-migration patch returned HTTP ${code}" >&2
  cat "${work}/resp" >&2 || true
  exit 1
fi
echo "Added ${key_name} for ${fqdn} to ConfigMap cert-migration"
# @lulaEnd 1d49897a-a738-5e02-b561-715b77ff099b
