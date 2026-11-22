#!/bin/bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Start CloudTAK as a non-root user. Upstream api/start writes
# /etc/nginx/nginx.conf, starts nginx as root, and runs `npm run prod` in a
# loop. This renders the same nginx.conf into /tmp via render-nginx-conf.mjs,
# and runs the API once. NODE_OPTIONS carries --import for the 8089 handshake
# gap. That flag is not visible on process.execArgv; the preload installs
# because it was loaded. A reset is held only until the pool listens, and a
# missing listener still crashes the process.
set -euo pipefail

node /home/etl/uds/render-nginx-conf.mjs /tmp/nginx
nginx -e /dev/stderr -c /tmp/nginx/nginx.conf

export NODE_OPTIONS="${NODE_OPTIONS:+${NODE_OPTIONS} }--import /home/etl/api/uds-tak-error-guard.mjs"
exec node dist/index.js
