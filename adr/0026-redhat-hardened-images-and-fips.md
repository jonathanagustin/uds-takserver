# 26. Evaluate Red Hat Hardened Images and FIPS variants

Date: 2026-11-22

## Status

Proposed (tracking issue [#847](https://github.com/jonathanagustin/uds-takserver/issues/847)). Production images remain unchanged until full multi-arch qualification passes.

## Context

`uds-takserver` previously moved the TAK Server runtime from Red Hat UBI to the official Eclipse Temurin 17 JDK on Ubuntu 22.04 after UBI RPM package-version mismatches broke fresh native image builds in [#625](https://github.com/jonathanagustin/uds-takserver/issues/625) ([ADR 0025](0025-temurin-runtime-candidate.md)). Importantly for cryptographic compliance, ADR 0025 also normalized generated encrypted PEM keys to PKCS#8 PBES2/AES-256-CBC with PBKDF2-HMAC-SHA256 (100,000 iterations) and PKCS#12 without enabling an OpenSSL legacy provider.

Red Hat now publishes **Red Hat Hardened Images** (`registry.access.redhat.com/hi/*`, Project Hummingbird), a no-cost, freely redistributable catalog under UBI-style redistribution terms (downstream users require no paid subscription to run derived application containers; enterprise SLA support remains tied to RHEL or OpenShift subscriptions). These images provide minimal distroless runtime images (`-runtime`, `-runtime-fips`), builder images (`-builder`, `-fips-builder`), embedded SBOMs, and unauthenticated multi-architecture OCI image indexes.

On 2026-11-22, anonymous OCI v2 manifest index queries against `registry.access.redhat.com/hi/*` and `registry.access.redhat.com/ubi9/*` verified the following candidate bases:

| Repository & Tag | Verified Index Digest | Architectures | Assessment |
| :--- | :--- | :--- | :--- |
| `hi/nginx:1.30-fips` | `sha256:d13a65fb65b5710ad0ab4e4b4e7f36a1f1347d229b663ef65343f94b22a3f4be` | `amd64`, `arm64`, `s390x` | Strong candidate for CloudTAK static/proxy container |
| `hi/nodejs:24-fips` | `sha256:a2bed0655857924377b3a3c3a9c35b87c38b3067299c8baad7993e8f2e1cb3c8` | `amd64`, `arm64` | Strong candidate for CloudTAK API/SSO/bootstrap container |
| `hi/nodejs:22-fips` | `sha256:f295d7bc7bb0238bdf97a5ec5a001c3d141fa59c30379f8acfa5e8c46957098d` | `amd64`, `arm64` | LTS alternative for CloudTAK API container |
| `hi/nats:latest` | `sha256:4e24f3a04a413b6e60cc9f644dd71cd4c6414d8acf993215ca2b7331572ea43c` | `amd64`, `arm64`, `s390x` | Candidate replacement for `nats:2.15.3-alpine` |
| `hi/openjdk:21-fips-builder` | `sha256:86d44e3dd9bddc12670250000397bac92b24fa7ddefbab5df6316b9f4379ba07` | `amd64`, `arm64` | Streams 21 and 25 only (`hi/openjdk:17` returns HTTP 404) |
| `hi/openjdk:21-runtime-fips` | `sha256:a88c38024fcad1b690b58aa0aa868ccd03e24b4588fdca351dffecd4b72188af` | `amd64`, `arm64` | Distroless runtime (no shell) |
| `ubi9/openjdk-17:latest` | `sha256:7c71bf6a9eebdff3738453ec2833b542393e5be226b0af7a94eb7277d890b103` | `amd64`, `arm64`, `ppc64le`, `s390x` | UBI 9 option if TAK Server 5.7 requires Java 17 |
| `ubi9/openjdk-17-runtime:latest` | `sha256:0d5736bab543ab8fa6e18fc71581b279cf995e61ef5858986523dc1bdbe9e32d` | `amd64`, `arm64`, `ppc64le`, `s390x` | UBI 9 runtime alternative |
| `hi/postgresql:18` | `sha256:25718a4957508b51fad01c9183bed897d97d6f059553d32cbcc6d4b12c2a3304` | `amd64`, `arm64` | No PostGIS extension or PG15 `pg_upgrade` binary |
| `hi/postgresql:latest-fips` | `sha256:b0727fe3da0a472b2dde4b06ffd50a0ad9772be421dd87085645ff3bc471b3b2` | `amd64`, `arm64` | Same PostGIS and `pg_upgrade` limitation |
| `hi/velero:latest` | *(HTTP 404 Not Found)* | — | Not present in `/hi/` catalog; evaluate `dhi.io/velero` or keep upstream |

### Docker Hardened Images (`dhi.io`) for `/hi/` Gaps

Where `registry.access.redhat.com/hi/*` leaves a version or catalog gap (`hi/openjdk:17` HTTP 404, `hi/velero` HTTP 404, and Debian-based PostGIS packaging), **Docker Hardened Images (`dhi.io`)** provide Apache 2.0 community-tier Debian/Alpine minimal images with SBOMs, OpenVEX, SLSA Level 3 provenance, and near-zero CVEs:

- **TAK Server 5.7 (Java 17):** `dhi.io/eclipse-temurin` Java 17 (`-dev` / Debian/Alpine variants; see evaluation spike PR #856) fills the `hi/openjdk:17` gap without requiring a TAK Server Java 21 migration. Note that DHI FIPS variants require Docker's paid Select/Enterprise tiers, so `hi/openjdk:21-fips-builder` and `ubi9/openjdk-17` remain the free-FIPS track.
- **Velero:** `dhi.io/velero` fills the `hi/velero` 404 gap in `zarf.yaml`.
- **PostGIS:** Evaluate `dhi.io/postgres` (Debian-based) if a `-dev` or package-capable builder stage can layer checksum-pinned PGDG PostGIS 3.6.4 `.deb` packages for both PostgreSQL 15 and 18 without breaking [ADR 0018](0018-upgrade-postgres-in-place.md) in-place upgrades.

**Operational constraint:** Pulling from `dhi.io` requires Docker Hub authentication (`docker login dhi.io`). Any production adoption of `dhi.io` images must configure repository-scoped CI/Renovate pull credentials and preserve an unauthenticated fallback path for external fork PRs ([#652](https://github.com/jonathanagustin/uds-takserver/issues/652)).

Additional workload constraints shape how `/hi/`, DHI, and FIPS bases can be adopted safely:

1. **TAK Server embeds Tomcat** inside its Spring Boot WAR, so `hi/tomcat` is unnecessary. However, the upstream TAK cluster entrypoints and readiness scripts (`takserver-*-cluster.sh`, `enable_admin.sh`, `*-readiness.sh`, `keytool`, `curl`, `ncat`, `unzip`) currently require a shell and JDK tools at runtime. Distroless `-runtime` images cannot run those scripts directly without `-builder`/`-fips-builder` or replacing the shell entrypoints.
2. **CloudTAK currently bundles NGINX, Node.js, and bash** in a single container started by `start.sh`. Splitting the Pod into two hardened containers separates the TLS/static frontend from the Node API process and removes the runtime shell.
3. **PostGIS requires dual PostgreSQL 15 and 18 binaries** plus checksum-pinned PGDG PostGIS 3.6.4 packages for in-place `pg_upgrade` ([ADR 0018](0018-upgrade-postgres-in-place.md)). `hi/postgresql` does not provide PostGIS or dual major versions out of the box.

## Decision

Evaluate a hybrid strategy pairing **Red Hat Hardened Images (`registry.access.redhat.com/hi/*`)** (unauthenticated public pulls + free `-fips` variants) for covered workloads with **Docker Hardened Images (`dhi.io`)** for `/hi/` catalog gaps, keeping all production defaults unchanged until each phase passes full qualification:

1. **Phase 1 — CloudTAK (`hi/nginx:1.30-fips` + `hi/nodejs:24-fips`)**: Split the single-container CloudTAK deployment into two distroless/hardened containers within the same Pod (a static/reverse-proxy `nginx` container and a Node API/SSO/bootstrap container), replacing shell-based trust-bundle merging with a small Node helper so the Pod runs without `/bin/sh` and preserves read-only rootfs, SSO, basemap proxy egress, and mTLS behavior.
2. **Phase 2 — NATS (`hi/nats`) & Velero (`dhi.io/velero`)**: Prototype pinning `hi/nats` cleanly via unauthenticated multi-arch digest in `zarf.yaml` (`tests/nats-roundtrip.py`), and evaluate `dhi.io/velero` for the missing `hi/velero` image under explicit `dhi.io` auth/fork-CI rules.
3. **Phase 3 — TAK Server 5.7 (`dhi.io/eclipse-temurin` Java 17 vs `hi/openjdk:21-fips-builder` / `ubi9/openjdk-17`)**: Evaluate `dhi.io/eclipse-temurin` Java 17 (see evaluation spike PR #856) to shrink TAK Server's Debian/Temurin 17 CVE surface without a Java 21 migration, while keeping `hi/openjdk:21-fips-builder` and `ubi9/openjdk-17` as the free-FIPS track. Explicitly test JKS vs PKCS#12 keystores, Ignite cluster discovery, PostgreSQL JDBC TLS, and TAK client mTLS under RHEL FIPS crypto-policies.
4. **Phase 4 — PostGIS (`dhi.io/postgres` vs deferred)**: Evaluate `dhi.io/postgres` (Debian-based) for dual PostgreSQL 15 + 18 PostGIS packaging; otherwise retain `postgres:18-bookworm` with checksum-pinned PGDG PostGIS 3.6.4 `.deb` packages until ADR 0018 in-place upgrades are proven on a hardened base without regressing airgap packaging or CVE gates.

### Acceptance Criteria (Matching ADR 0025)

Before any candidate replaces a production default:

- Native multi-arch (`amd64` and `arm64`) builds succeed with strict package-version and CVE scan comparison against the current baseline.
- Multi-arch digest pinning, Renovate update tracking, Cosign signatures, and SBOM/provenance attestations remain intact.
- Full native install, regular upgrade, PostgreSQL 15 upgrade, airgap install, CloudTAK/TAK functional tests, and UDS Zero Trust east-west checks ([#849](https://github.com/jonathanagustin/uds-takserver/issues/849)) pass on the candidate images.

## Consequences

- **Lower CVE surface & stronger cryptography posture:** Moving CloudTAK, NATS, and eventually TAK Server onto minimal `/hi/` and `-fips` bases reduces OS package CVE noise while aligning runtime TLS and keystores with FIPS 140-3 crypto-policies.
- **Topology & debugging trade-offs:** Splitting CloudTAK adds a side-by-side container boundary (`localhost` loopback between `nginx` and `nodejs`) inside the CloudTAK Pod, and distroless `-runtime` / `-fips` images lack an interactive shell for ad-hoc container exec debugging (diagnostics rely on ephemeral debug containers or existing Pod logs/metrics).
- **Upstream Java & PostGIS constraints remain explicit:** No workarounds are introduced to fake Java 17 inside `/hi/openjdk` or skip dual-version PostGIS `pg_upgrade` guarantees.
