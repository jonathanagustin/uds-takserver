<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Prebuilt TAKX Native OCI Caches

Builds, qualifies, and publishes minimal `scratch` OCI artifacts for foundational TAKX static PIC C/C++ dependencies in parallel via `.github/workflows/takx-native-caches.yaml`:

- **Layer 0:**
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib:1.3.1-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-expat:2.10.0-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-quictls:3.0.14-quic1-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-openssl:3.5.9-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-nghttp2:1.62.1-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-libmicrohttpd:1.0.1-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-protobuf:3.21.0-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-uriparser:0.9.8-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-fmt:10.2.1-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-zstd:1.5.6-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-meshoptimizer:0.20-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-asyncplusplus:1.1-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-tinyxml2:10.0.0-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-libjpeg-turbo:3.0.3-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-libwebp:1.4.0-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-draco:1.5.7-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-zlib-ng:2.1.5-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-libiconv:1.15-amd64`
- **Layer 1 (composed from Layer-0 GHCR caches):**
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-curl:8.23.0-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-sqlcipher:4.5.7-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-laszip:3.4.3-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-assimp:4.0.1-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-ngtcp2:1.6.0-amd64`
  - `ghcr.io/jonathanagustin/uds-takserver/takx-native-spdlog:1.14.1-amd64`

Every component compiles inside the digest-pinned GCC 14.2.0 image, verifies exact upstream archive and license SHA-256 hashes from the existing `takx/native-*/inputs.lock.json` manifests, runs a real C consumer control, is extracted and re-verified inside `ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64`, and receives a Sigstore build provenance attestation on `main`.
