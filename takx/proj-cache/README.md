<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Prebuilt TAKX PROJ 4.9.1 Static PIC OCI Cache

Builds and publishes the ATAK-pinned **PROJ 4.9.1** static PIC library (`libproj.a` + headers + `COPYING` + `proj-receipt.json`) as a minimal `scratch` OCI artifact:

```text
ghcr.io/jonathanagustin/uds-takserver/takx-native-proj:4.9.1-amd64
```

## Provenance & Controls

- Uses the digest-pinned GCC 14.2.0 compiler image shared with `takx/geos-cache/inputs.lock.json`.
- Verifies exact SHA-256 hashes of `proj.tar.gz` and `proj-4.9.1/COPYING` matching `takx/native-proj/inputs.lock.json` with zero source patches (`--without-jni --disable-shared --enable-static --with-pic`).
- Compiles and runs `proj_control.c` (forward + inverse Spherical Mercator projection + `PJ_VERSION == 491`), verifies whole-archive shared linking (`--no-undefined`) with zero `TEXTREL`, and re-qualifies the extracted `/proj` prefix inside `ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64` where no host PROJ is installed.
- Published tags carry Sigstore build provenance attestations (`actions/attest-build-provenance`).
- Scope remains explicitly bounded (`static_pic_host_abi: true`, `sdk_accepted: false`, `security_accepted: false`).
