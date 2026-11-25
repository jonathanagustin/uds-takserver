#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Host mounts Istio ambient needs in this sandbox. They are not persistent:
# run this again after a VM restart, before k3s starts (10-k3s.sh does).
#
# 1. The root mount is private here. The Istio CNI agent needs it shared.
# 2. PID 1 is the sandbox control process and its /proc/1/ns/* links are
#    unreadable even for root. The Istio CNI agent opens /host/proc/1/ns/net
#    to find the node network namespace and exits when it cannot. A private
#    recursive bind of /proc is mounted at ${HOSTPROC}, and its entry "1" is
#    covered with /proc/2 (kthreadd), which lives in the same node network
#    namespace. The real /proc is untouched. 40-core.sh points the
#    istio-cni-node DaemonSet's host-procfs volume at ${HOSTPROC}.
set -euo pipefail
HOSTPROC="${HOSTPROC:-/run/takx-hostproc}"

if [ "$(findmnt -n -o PROPAGATION /)" != "shared" ]; then
  mount --make-rshared /
fi
mkdir -p "${HOSTPROC}"
if ! mountpoint -q "${HOSTPROC}"; then
  mount --rbind /proc "${HOSTPROC}"
  # Private, so the cover below does not propagate back onto the real /proc/1.
  mount --make-rprivate "${HOSTPROC}"
fi
if ! mountpoint -q "${HOSTPROC}/1"; then
  mount --bind /proc/2 "${HOSTPROC}/1"
fi
want="$(readlink /proc/self/ns/net)"
have="$(readlink "${HOSTPROC}/1/ns/net")"
if [ "${want}" != "${have}" ]; then
  echo "${HOSTPROC}/1 is in ${have}, the node network namespace is ${want}" >&2
  exit 1
fi
echo "host prep ok: / is $(findmnt -n -o PROPAGATION /), ${HOSTPROC}/1/ns/net -> ${have}"
