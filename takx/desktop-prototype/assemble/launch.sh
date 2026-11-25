#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
#
# Start TAKX 5.7 from an assembled tree with the command line of the official
# runTakx task (deployment/build.gradle): working directory = the tree, class
# path boot/* and conf, every line of template-64bit.vmoptions as a JVM
# option, java.library.path, main class org.jboss.modules.Main with
# '-mp modules takx-bootstrap', then the program arguments.
#
# Usage: launch.sh TREE RUNDIR [takx arguments...]
#
# TREE is the assembled tree or a run directory that links to it. RUNDIR
# receives ProjectData, tmp and home, so nothing is written outside it.
# EXTRA_JVM_OPTS is honoured as the official task does.
set -euo pipefail

tree="$1"; rundir="$2"; shift 2
cd "$tree"
mkdir -p "$rundir"/{ProjectData,tmp,home}

mapfile -t vmoptions < <(grep -v '^[[:space:]]*$' template-64bit.vmoptions)
# prototype-launch.env (written by assemble.py) names two facts about the
# public Kernel build that the official command line does not need:
#   KERNEL_STOCK_LOADER_SUBDIR  where the stock loader extracts the natives,
#                               relative to java.io.tmpdir
#   KERNEL_ASSET_ROOT_JAR       the Kernel JAR; its desktop AssetManager
#                               serves assets only from java.class.path roots
# shellcheck disable=SC1091
source ./prototype-launch.env
library_path="$PWD/native/linux64:$rundir/tmp/$KERNEL_STOCK_LOADER_SUBDIR"

# shellcheck disable=SC2086
exec java "${vmoptions[@]}" \
  -Djava.library.path="$library_path" \
  -Dprojectdata.dir="$rundir/ProjectData" \
  -Djava.io.tmpdir="$rundir/tmp" \
  -Duser.home="$rundir/home" \
  ${EXTRA_JVM_OPTS:-} \
  -cp "$(printf '%s:' boot/*.jar)conf:$KERNEL_ASSET_ROOT_JAR" \
  org.jboss.modules.Main -mp modules takx-bootstrap "$@"
