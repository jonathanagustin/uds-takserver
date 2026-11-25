#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, connected run: CloudTAK in a real Chrome as the browser
# peer of the TAKX client (README.md of this directory deploys CloudTAK next
# to the TAK Server).
#
#   start       Xvfb display :97 (1920x1080x24) and a headed Chrome on it,
#               DevTools on 127.0.0.1:9222 only. WebGL2 comes from Chrome's
#               software renderer (SwiftShader): without it CloudTAK's map
#               does not start on a machine with no GPU ("WebGL2 is
#               required", then "Maximum call stack size exceeded").
#               No proxy: the browser can reach the cluster gateways and
#               nothing else. Certificate checks stay on.
#   signin      create the Keycloak user of the peer if needed
#               (connect_cloudtak_user.py: takx-cloudtak, read and write in
#               the TAK group Operators) and sign in to CloudTAK through
#               Keycloak. NOT while the browser display is being recorded:
#               the Keycloak form is on screen.
#   stop        stop Chrome and the display
#   CMD ARGS    anything else goes to connect_cloudtak.cjs
#               (whoami, shot, view, place, find, remove)
#
# The browser profile holds the CloudTAK session, so it lives under
# $TAKX_PROTO_ROOT/secrets/cloudtak-user/ (mode 700) with the password file.
# Nothing secret is printed.
#
# Environment: TAKX_PROTO_ROOT, TAKX_CLOUDTAK_DISPLAY (default :97),
# NODE_PATH (default: the global node modules, where 'playwright' is).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
display="${TAKX_CLOUDTAK_DISPLAY:-:97}"
secrets="${root}/secrets/cloudtak-user"
state="${root}/connect/cloudtak"
user=takx-cloudtak
# shellcheck disable=SC1091
source "${root}/server/endpoints.env"
export CLOUDTAK_URL NODE_PATH="${NODE_PATH:-$(npm root -g)}"
# The Keycloak admin calls of 'signin' must not go through the sandbox proxy.
# shellcheck disable=SC2154 # DOMAIN is set by ${root}/server/endpoints.env (written by server/45-hosts.sh), sourced above
export NO_PROXY="${NO_PROXY:-},.${DOMAIN}" no_proxy="${no_proxy:-},.${DOMAIN}"

driver() { node "${here}/connect_cloudtak.cjs" "$@"; }

case "${1:-}" in
  start)
    mkdir -p "${state}" "${secrets}/chrome-profile"; chmod 700 "${secrets}" "${secrets}/chrome-profile"
    if ! pgrep -f "Xvfb ${display} " >/dev/null; then
      setsid nohup Xvfb "${display}" -screen 0 1920x1080x24 -nolisten tcp >"${state}/xvfb.log" 2>&1 </dev/null &
      sleep 2
    fi
    if ! curl -fsS --noproxy '*' -o /dev/null http://127.0.0.1:9222/json/version 2>/dev/null; then
      DISPLAY="${display}" setsid nohup google-chrome --no-sandbox --no-proxy-server --no-first-run \
        --no-default-browser-check --disable-features=Translate,MediaRouter --password-store=basic \
        --use-angle=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist \
        --window-position=0,0 --window-size=1920,1080 \
        --remote-debugging-address=127.0.0.1 --remote-debugging-port=9222 \
        --user-data-dir="${secrets}/chrome-profile" about:blank >"${state}/chrome.log" 2>&1 </dev/null &
      for _ in $(seq 30); do
        curl -fsS --noproxy '*' -o /dev/null http://127.0.0.1:9222/json/version 2>/dev/null && break
        sleep 1
      done
    fi
    curl -fsS --noproxy '*' http://127.0.0.1:9222/json/version | python3 -I -c 'import json,sys; print("connect-cloudtak:", json.load(sys.stdin)["Browser"])' >&2;;
  signin)
    python3 "${here}/connect_cloudtak_user.py" "${secrets}" >/dev/null
    driver signin "${user}" "${secrets}/password.txt";;
  stop)
    pkill -f 'remote-debugging-port=9222' || true
    pkill -f "Xvfb ${display} " || true;;
  -h|--help|"") sed -n '5,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//';;
  *) driver "$@";;
esac
