#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Name the gateway addresses on this host. Public DNS answers 127.0.0.1 for
# *.uds.dev and this sandbox's resolver returns nothing for those names, so the
# host needs /etc/hosts entries. k3d publishes the tenant gateway on host
# port 443; here each gateway has its own MetalLB address instead.
#
# The TAK server certificate names only "takserver" (see README.md), so that
# bare name also points at the passthrough gateway.
#
# Writes one marked block in /etc/hosts and ${SERVER_STATE}/endpoints.env,
# which later stages source. Run it again when a gateway address changes.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"

lb() { kubectl -n "$1" get svc "$2" -o jsonpath='{.status.loadBalancer.ingress[0].ip}'; }
admin="$(lb istio-admin-gateway admin-ingressgateway)"
tenant="$(lb istio-tenant-gateway tenant-ingressgateway)"
passthrough="$(lb istio-passthrough-gateway passthrough-ingressgateway)"
[[ -n "${admin}" ]] && [[ -n "${tenant}" ]] && [[ -n "${passthrough}" ]]

begin="# BEGIN takx-desktop-prototype"
end="# END takx-desktop-prototype"
tmp="$(mktemp)"
sed "/^${begin}\$/,/^${end}\$/d" /etc/hosts > "${tmp}"
cat >> "${tmp}" <<HOSTS
${begin}
${admin} keycloak.admin.${DOMAIN}
${tenant} sso.${DOMAIN} takserver.${DOMAIN} map.${DOMAIN}
${passthrough} takserver-stream.${DOMAIN} takserver-api.${DOMAIN} takserver
${end}
HOSTS
cat "${tmp}" > /etc/hosts
rm -f "${tmp}"

cat > "${SERVER_STATE}/endpoints.env" <<ENV
# Written by takx/desktop-prototype/server/45-hosts.sh. No secrets.
DOMAIN=${DOMAIN}
ADMIN_GATEWAY_IP=${admin}
TENANT_GATEWAY_IP=${tenant}
PASSTHROUGH_GATEWAY_IP=${passthrough}
TAK_STREAM_HOST=takserver-stream.${DOMAIN}
TAK_STREAM_PORT=8089
# The only DNS name in the TAK server certificate (CN and subjectAltName).
# A client that checks the server name must dial this name.
TAK_CERT_NAME=takserver
TAK_API_HOST=takserver-api.${DOMAIN}
TAK_API_PORT=443
TAK_ENROLL_HOST=takserver.${DOMAIN}
TAK_ENROLL_PORT=8446
CLOUDTAK_URL=https://map.${DOMAIN}/
ENV
sed -n "/^${begin}\$/,/^${end}\$/p" /etc/hosts
write_status server-hosts ok "gateway addresses admin=${admin} tenant=${tenant} passthrough=${passthrough} in /etc/hosts and ${SERVER_STATE}/endpoints.env"
