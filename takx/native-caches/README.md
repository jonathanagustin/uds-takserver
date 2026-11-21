<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Prebuilt TAKX Native Layer-0 OCI Caches

Builds, qualifies, and publishes minimal `scratch` OCI artifacts for foundational TAKX static PIC C/C++ dependencies in parallel via `.github/workflows/takx-native-caches.yaml`:

- `ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib:1.3.1-amd64`
- `ghcr.io/jonathanagustin/uds-takserver/takx-native-expat:2.10.0-amd64`
- `ghcr.io/jonathanagustin/uds-takserver/takx-native-quictls:3.0.14-quic1-amd64`
- `ghcr.io/jonathanagustin/uds-takserver/takx-native-openssl:3.5.9-amd64`

Every component compiles inside the digest-pinned GCC 14.2.0 image, verifies exact upstream archive and license SHA-256 hashes from the existing `takx/native-*/inputs.lock.json` manifests, runs a real C consumer control, is extracted and re-verified inside `ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64`, and receives a Sigstore build provenance attestation on `main`.
