# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG MODP_URL="https://github.com/client9/stringencoders/archive/e1448a9415f4ebf6f559c86718193ba067cbb99d.tar.gz"
ARG MODP_SHA256="8c26f89672c6a5343ba8ed58200207e67530bc5a318130442069204068b1585c"
ARG MODP_SHA512="68c9b9a9eb09075c792cfc0a8ce1959c60a86e5256de8568b0cb6934f748fd9e95c5f1801a8982fecac65850a8f2d633a64dc98e4505ee8e6914bd0b0fb996cf"
ARG LICENSE_SHA256="1fecc108ae7078aab437014074370f3a8a58bcb231596e5c4e59f6cc4083b10d"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY modp_base64_control.c /build/modp_base64_control.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, os, tarfile, urllib.request
from pathlib import Path

dest = Path("/build/modp-base64.tar.gz")
urllib.request.urlretrieve(os.environ["MODP_URL"], dest)
raw = dest.read_bytes()
got256 = hashlib.sha256(raw).hexdigest()
if got256 != os.environ["MODP_SHA256"]:
    raise SystemExit(f"modp-base64 SHA-256 mismatch: {got256}")
got512 = hashlib.sha512(raw).hexdigest()
if got512 != os.environ["MODP_SHA512"]:
    raise SystemExit(f"modp-base64 SHA-512 mismatch: {got512}")
with tarfile.open(dest, "r:gz") as tf:
    tf.extractall("/build")
lic = hashlib.sha256(
    Path("/build/stringencoders-e1448a9415f4ebf6f559c86718193ba067cbb99d/COPYING").read_bytes()
).hexdigest()
if lic != os.environ["LICENSE_SHA256"]:
    raise SystemExit(f"modp-base64 COPYING SHA-256 mismatch: {lic}")
PY

RUN set -x; \
    src="/build/stringencoders-e1448a9415f4ebf6f559c86718193ba067cbb99d"; \
    mkdir -p /modp-base64/lib /modp-base64/include; \
    gcc -O2 "${src}/src/modp_b64_gen.c" "${src}/src/arraytoc.c" -o /build/modp_b64_gen; \
    /build/modp_b64_gen > "${src}/src/modp_b64_data.h"; \
    gcc -std=c99 -O2 -fPIC -march=x86-64 -mtune=generic \
      -I"${src}/src" -c "${src}/src/modp_b64.c" -o /build/modp_b64.o; \
    ar rcs /modp-base64/lib/libmodp_b64.a /build/modp_b64.o; \
    cp "${src}/src/modp_b64.h" "${src}/src/extern_c_begin.h" "${src}/src/extern_c_end.h" "${src}/src/modp_stdint.h" /modp-base64/include/; \
    cp "${src}/COPYING" /modp-base64/COPYING; \
    gcc -std=c99 -Wall -Wextra -Werror -O2 \
      -I/modp-base64/include /build/modp_base64_control.c /modp-base64/lib/libmodp_b64.a \
      -o /build/modp_base64_control; \
    /build/modp_base64_control; \
    gcc -shared -o /build/libmodp_b64_whole.so \
      -Wl,--whole-archive /modp-base64/lib/libmodp_b64.a -Wl,--no-whole-archive \
      -Wl,--no-undefined; \
    if readelf -d /build/libmodp_b64_whole.so | grep -q TEXTREL; then \
      echo "unexpected TEXTREL in libmodp_b64_whole.so" >&2; \
      exit 1; \
    fi; \
    lib_sha="$(sha256sum /modp-base64/lib/libmodp_b64.a | awk '{print $1}')"; \
    hdr_sha="$(sha256sum /modp-base64/include/modp_b64.h | awk '{print $1}')"; \
    gcc_ver="$(gcc -dumpfullversion -dumpversion)"; \
    printf '{\n  "schema": "uds-takx-native-cache-v1",\n  "component": "modp-base64",\n  "version": "2020-09-26",\n  "arch": "x86_64",\n  "gcc_version": "%s",\n  "source_archive_sha256": "%s",\n  "license_sha256": "%s",\n  "artifacts": {\n    "lib/libmodp_b64.a": "%s",\n    "include/modp_b64.h": "%s",\n    "COPYING": "%s"\n  },\n  "controls": {\n    "static_pic_control": true,\n    "whole_archive_no_undefined": true,\n    "no_textrel": true\n  },\n  "scope": {\n    "static_pic_host_abi": true,\n    "sdk_accepted": false,\n    "security_accepted": false\n  }\n}\n' \
      "${gcc_ver}" "${MODP_SHA256}" "${LICENSE_SHA256}" "${lib_sha}" "${hdr_sha}" "${LICENSE_SHA256}" \
      > /modp-base64/cache-receipt.json

FROM scratch
COPY --from=builder /modp-base64 /modp-base64
