#!/usr/bin/env bash
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Called from the checked-out workflow source without GitHub API credentials.
set -euo pipefail

: "${RUNNER_TEMP:?GitHub runner temporary directory is required}"
tools="${RUNNER_TEMP}/lula-tools"
mkdir -p "${tools}"
cp .github/lula/package.json .github/lula/package-lock.json .github/lula/crawl.mjs "${tools}/"
cp .github/lula/official/LICENSE "${tools}/LULA-LICENSE"
cp .github/lula/README.md "${tools}/LULA-MODIFICATIONS.md"
archive="${tools}/lula2-0.9.5.tgz"
curl --fail --silent --show-error --location --max-time 60 --max-filesize 4194304 \
  https://registry.npmjs.org/lula2/-/lula2-0.9.5.tgz -o "${archive}"
python3 - "${archive}" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("patch", ".github/lula/patch_crawl.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.verify_archive(pathlib.Path(sys.argv[1]))
PY
npm ci --ignore-scripts --no-bin-links --no-audit --no-fund --prefix "${tools}"
python3 .github/lula/patch_crawl.py --archive "${archive}" --extract-crawl \
  "${tools}/crawl.official.js" "${tools}/crawl.safe.mjs"
