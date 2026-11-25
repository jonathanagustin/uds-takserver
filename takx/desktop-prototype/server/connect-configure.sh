#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Desktop prototype, connected run: give the running TAKX 5.7 client
# (connect.sh start) one TAK Server connection through its own user interface,
# as a user would: Home > TAK Server > Add, then the Connection Details form
# (TakServerConnectionDetailsPanel). Mouse and keyboard only, through xdotool.
#
# Usage: connect-configure.sh [TAKCLIENT] [--ca-from TAKCLIENT] [--ca-file FILE]
#        [--name NAME] [--address HOST] [--no-ok]
#   TAKCLIENT  directory name under $TAKX_PROTO_ROOT/secrets (default
#              takx-desktop); its client.p12 is the client certificate
#   --ca-from  the directory whose truststore.p12 (and password file) is the
#              CA file; default: the same directory. The negative checks mix
#              the two: a foreign CA with the real client certificate, and
#              the real CA with a foreign client certificate
#   --ca-file  the CA file's name in that directory (default truststore.p12,
#              the one in the TakClient's data package; truststore-java.p12
#              is the same CA written by JDK keytool)
#   --address  dial this name instead of the certificate name (used once, to
#              find out whether the client checks the server name)
#   --no-ok    fill the form but do not press OK
#
# Never run this while a recording is in progress: the form with its
# (masked) password fields is on screen. The password is typed inside the
# container straight from the mode 600 password file; it is not an argument,
# not an environment variable and is never printed. The 'eye' buttons that
# reveal a password field are not touched.
#
# Address: the certificate name of the TAK server (TAK_CERT_NAME in
# endpoints.env), port TAK_STREAM_PORT, protocol SSL (the form's default).
# 'Use Authentication' and 'Enroll for Client Certificate' stay off.
#
# The coordinates are those of the 1920x1080 session of this prototype with
# the default layout of a new project. They are not a general UI driver.
set -euo pipefail

root="${TAKX_PROTO_ROOT:-/var/lib/takx-proto}"
name=takx-connect
client=takx-desktop
cafrom=""
label="UDS TAK Server"
ok=1
address=""
cafile=truststore.p12
while (($#)); do
  case "$1" in
    --ca-from) cafrom="$2"; shift 2;;
    --ca-file) cafile="$2"; shift 2;;
    --name) label="$2"; shift 2;;
    --address) address="$2"; shift 2;;
    --no-ok) ok=0; shift;;
    -h|--help) sed -n '5,37p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0;;
    -*) echo "connect-configure.sh: unknown option '$1'" >&2; exit 2;;
    *) client="$1"; shift;;
  esac
done
# shellcheck disable=SC1091
source "${root}/server/endpoints.env"
secrets="${root}/secrets/${client}"
casecrets="${root}/secrets/${cafrom:-${client}}"
ca="${casecrets}/${cafile}"
[[ -s "${secrets}/client.p12" && -s "${ca}" && -s "${secrets}/password.txt" && -s "${casecrets}/password.txt" ]] || { echo "connect-configure.sh: incomplete identity ${secrets}" >&2; exit 1; }

# shellcheck disable=SC2154 # TAK_STREAM_PORT is set by ${root}/server/endpoints.env (written by server/45-hosts.sh), sourced above
docker exec -i -e DISPLAY=:99 -e LABEL="${label}" -e ADDRESS="${address:-${TAK_CERT_NAME}}" -e PORT="${TAK_STREAM_PORT}" \
  -e CA="${ca}" -e CERT="${secrets}/client.p12" -e CA_SECRET_FILE="${casecrets}/password.txt" -e SECRET_FILE="${secrets}/password.txt" -e OK="${ok}" "${name}" bash -s <<'DRIVE'
set -euo pipefail
click() { xdotool mousemove --sync "$1" "$2"; sleep 0.35; xdotool click 1; sleep 0.4; }
field() { click "$1" "$2"; xdotool key ctrl+a; xdotool type --delay 25 -- "$3"; sleep 0.3; }
secret() { click "$1" "$2"; xdotool key ctrl+a; tr -d '\r\n' <"$3" | xdotool type --delay 25 --file -; sleep 0.3; }

# Open the TAK Server dock if its Add button is not there yet, and widen it so
# that the whole form is visible (drag the divider between globe and dock).
if ! xdotool search --onlyvisible --name 'TAKX 5.7' >/dev/null; then echo "no TAKX window" >&2; exit 1; fi
if [[ ! -e /tmp/takx-connect-dock-open ]]; then
  click 1720 95; sleep 2.5
  xdotool mousemove --sync 1581 600; sleep 0.3; xdotool mousedown 1
  for _ in $(seq 20); do xdotool mousemove_relative --sync -- -15 0; sleep 0.03; done
  xdotool mouseup 1; sleep 1.5
  touch /tmp/takx-connect-dock-open
fi
click 1308 190; sleep 2.5                 # Add
field 1610 266 "$LABEL"                   # Name
field 1610 298 "$ADDRESS"                 # Address
field 1800 330 "$PORT"; xdotool key Tab   # Port (spinner); Protocol stays SSL
field 1610 428 "$CA"                      # CA File
secret 1600 460 "$CA_SECRET_FILE"         # CA Password
field 1620 499 "$CERT"                    # Client Cert File
secret 1610 531 "$SECRET_FILE"            # Client Password
if [[ "$OK" == 1 ]]; then click 1713 583; fi
DRIVE
echo "connect-configure.sh: form filled for '${client}', CA from '${cafrom:-${client}}' (${address:-${TAK_CERT_NAME}}:${TAK_STREAM_PORT}, SSL)$( ((ok)) && echo ', OK pressed')" >&2
