#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Create the repository's standard Keycloak test users so CloudTAK can be the
# browser peer: the same three steps tasks.yaml create-deploy-test-bundle runs
# after a deploy. User names and passwords are the public development
# defaults of uds-common (tasks/setup.yaml) and tests/ensure-map-user.py; this
# script does not print them, and its log is kept private.
#
#   doug     /UDS Core/Admin and /TAK/Operators_READ  (TAK group Operators)
#   mapuser  /CloudTAK/Users                          (may sign in to CloudTAK)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "${here}/../../.." && pwd)"
# shellcheck source=takx/desktop-prototype/server/env.sh
. "${here}/env.sh"
umask 077
log="${LOGS}/80-keycloak-users.private.log"

# Run from the work directory so the task runner leaves nothing in the repo.
cd "${WORK}"
"${UDS}" run -f "${repo}/tasks.yaml" setup:keycloak-user --with group="/UDS Core/Admin" --no-progress >"${log}" 2>&1
python3 "${repo}/tests/ensure-tak-group.py" >>"${log}" 2>&1
python3 "${repo}/tests/ensure-map-user.py" >>"${log}" 2>&1
chmod 600 "${log}"
echo "keycloak test users ensured (log: ${log}, private)"
write_status server-keycloak-users ok "doug (/UDS Core/Admin, /TAK/Operators_READ) and mapuser (/CloudTAK/Users) ensured with the repository's default development credentials"
