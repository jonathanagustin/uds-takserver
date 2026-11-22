<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Prebuilt TAKX libglvnd 1.7.0 OCI Cache

Builds and publishes genuine **libglvnd 1.7.0** (`libEGL.so.1`, `libGLESv2.so.2`, `libGLdispatch.so.0`, Khronos headers, and `gles-receipt.json`) from NVIDIA's official source as a minimal `scratch` OCI artifact:

```text
ghcr.io/jonathanagustin/uds-takserver/takx-native-gles:1.7.0-amd64
```

## Provenance & Controls

- Builds inside `ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64` with zero per-run `apt-get` installs.
- Verifies exact SHA-256 hashes of `libglvnd-1.7.0` and the pinned `meson-1.7.2` wheel matching `takx/native-gles/inputs.lock.json`.
- Links and executes `surfaceless_es3.c` directly against the built `libEGL.so.1` and `libGLESv2.so.2` dispatching to Mesa `llvmpipe`, confirming exact RGBA pixel readback and verifying all three built libraries appear in `dl_iterate_phdr`.
- Published tags carry Sigstore build provenance attestations (`actions/attest-build-provenance`).
- Scope remains explicitly bounded (`software_mesa_llvmpipe: true`, `hardware_gpu_accepted: false`, `sdk_accepted: false`, `security_accepted: false`).
