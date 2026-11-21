#!/bin/sh
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# The upstream startup line moves out of the active log at daily rotation.
# The API entrypoint clears this marker before starting a new JVM.
startup_marker=/tmp/takserver-api-started
if [ ! -f "$startup_marker" ]; then
    grep -q 'Tomcat started on port' /logs/takserver-api.log || exit 1
    touch "$startup_marker" || exit 1
fi

# Keep checking both live endpoints after startup rather than treating the
# marker as evidence of current readiness.
nc -z -w 3 127.0.0.1 8443 &&
    nc -z -w 3 "${POSTGRES_HOST:?}" "${POSTGRES_PORT:?}"
