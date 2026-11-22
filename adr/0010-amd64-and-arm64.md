# 10. Build, package, and test for amd64 and arm64

Date: 2026-10-20

## Status

Accepted. The Postgres image decision is replaced by [ADR 13](0013-build-the-postgis-image-here.md).

## Context

The six TAK images were built for `linux/amd64` only, and the Zarf package, the test bundle, and the CI deploy all ran on amd64. Arm64 hosts are common for both development (Apple silicon laptops running k3d) and deployment (Graviton and other arm64 cloud nodes). UDS Core publishes `core-base` and the `k3d-core-slim-dev` checkpoint for both architectures, so the platform this package sits on already supports arm64.

Zarf packages are per architecture. A package carries one image per reference, pulled for the package's architecture, so an arm64 package needs every image it lists to have an arm64 variant.

The images and their bases:

- The builder (Temurin 17 on Ubuntu 22.04) and the runtime base (UBI9 OpenJDK 17) are manifest lists that include arm64.
- TAK's Gradle build already pulls the `linux-aarch_64` Netty epoll, tcnative, and QUIC natives, and the Node and protoc downloads have arm64 builds.
- NATS and its config reloader are multi-arch.
- `docker.io/postgis/postgis:15-3.4`, the image the upstream chart names, is amd64 only.

Building arm64 under QEMU on an amd64 runner emulates the whole Gradle and Node compile, which is many times slower than a native build. GitHub provides native `ubuntu-24.04-arm` runners for public repositories.

## Decision

- The container workflow builds each architecture on a native runner (`ubuntu-24.04` and `ubuntu-24.04-arm`) and pushes `<tag>-amd64` and `<tag>-arm64`. A final job joins them with `docker buildx imagetools create` into one index under `<tag>`, the tag `zarf.yaml` and the values reference. The merge refuses to write `<tag>` unless every architecture's tag exists, so it cannot replace a multi-arch index with fewer platforms.
- `wait-for-tak-images.sh` treats a tag as published only when its index lists `linux/amd64` and `linux/arm64`.
- Postgres uses `docker.io/imresamu/postgis:15-3.4`, the multi-arch build of the same `postgis/docker-postgis` Dockerfiles published by one of that project's maintainers. It has the same PostgreSQL 15.8 and PostGIS 3.4.3 as the amd64-only image, on Debian bookworm instead of bullseye.
- The package workflow creates the package on both architectures. The install test (k3d, slim UDS Core, the test bundle, SSO, and the full smoke and Playwright suite) runs on both. The release workflow publishes one package per architecture, each created on its own native runner.

## Consequences

- One image tag and one package reference work on either architecture. `zarf package deploy` and `uds deploy` pick the package for the cluster, or take `--architecture`.
- Every image change costs two native builds instead of one. They run in parallel.
- The Postgres image comes from a maintainer's namespace rather than the `postgis` organization. Switch back if `postgis/postgis` starts publishing arm64.
- The upgrade test ran on amd64 only until a released arm64 package existed. Every release from 0.6.0 on publishes both, so the upgrade jobs run on amd64 and arm64.
- Any image added to `zarf.yaml` must have an arm64 variant, or the arm64 package create fails. That failure is intended.

## Measurement note: ten-client workload

Date: 2026-11-07.

[Run 42361079121's retained evidence](../bench/results/42361079121/README.md)
contains baseline, Postgres buffers and Postgres WAL observations on both
architectures at the same source revision, 10 clients and 60-second duration.
The corrected report shares axes across architectures and keeps the source
records and collection caveats inspectable. Both source jobs failed the
baseline cold-age check because CloudTAK had restarted; the uploaded load
records do not turn that into a successful cold-install measurement.

This light-load, single-observation sweep does not justify a performance-based
architecture default. The Postgres-config points have nearly identical
throughput across architectures, latency varies, and there was no checkpoint
pressure in the measurement window. No messaging per-container headroom or
class-loading profile was recorded.

Keep native development on the host's architecture, native CI on both
architectures, and deployment on the target platform with a representative
capacity test. Both architectures remain supported. No Postgres defaults,
JVM flags or probe settings change from this evidence. Issues 48, 49 and 51
remain open for the heavier load and controlled startup comparisons.

## Measurement note: matched 50-client counterbalanced study

Date: 2026-11-21.

[Run 44856472301's retained evidence](../bench/results/44856472301/README.md)
(and [generated summary table](../bench/results/44856472301/report.md))
compares `amd64` and `arm64` on the same Pareto-front axes across three
PostgreSQL 18.6/PostGIS 3.6.4 configurations (`baseline`, `postgis-buffers`,
and `postgis-wal`) at exact source `83c1262b9059de6fa512f357eff87d16f9adf93d`.
Each architecture completed three counterbalanced repeats per configuration
(18 sessions total) at 50 clients with at least 300 seconds warmup and
1200 seconds measured, passing `bench.postgis_study.complete` with zero
recorded collection caveats.

Observed results on the GitHub hosted native runners:

- **Throughput:** Received message rates overlap across both architectures and
  all three configurations (`494.977`–`495.427` messages/s medians).
- **Mean ping-pong latency:** Medians were lower on `amd64`
  (`438.340`–`445.814` ms) than on `arm64` (`463.528`–`474.632` ms).
- **Container startup:** Observed TAK container startup was faster on `arm64`
  (`40.311` s messaging / `47.656` s API vs. `59.439` s / `70.298` s on `amd64`).

These are hosted-runner session repeats sharing one retained database per
architecture, not independent installs or an isolated ISA ranking. Both
architectures remain supported with no change to production PostgreSQL
defaults: use the host's native architecture for local development, keep both
architectures in CI, and choose deployment architecture based on target
infrastructure and representative workload testing. This satisfies the matched
three-configuration comparison and ADR update for issue 51; issues 48 and 49
remain open for startup tuning and representative-load validation.

