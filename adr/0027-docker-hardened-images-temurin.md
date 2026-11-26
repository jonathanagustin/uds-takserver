# 27. Qualify Docker Hardened Images Temurin 17 as the TAK Server base

Date: 2026-11-22

## Status

Adopted for TAK Server in [#871](https://github.com/jonathanagustin/uds-takserver/pull/871).
The digest-pinned DHI builder and runtime are on `main`. The outstanding
qualification and evaluation items remain tracked in
[#852](https://github.com/jonathanagustin/uds-takserver/issues/852).

## Context

Red Hat Hardened Images have no Java 17 stream ([ADR 0026](0026-redhat-hardened-images-and-fips.md)).
`dhi.io/eclipse-temurin` does. It keeps the same JDK vendor and major version as
[ADR 0025](0025-temurin-runtime-candidate.md) and moves the OS from Ubuntu 22.04 to
Debian 13. The Community tier is free, but pulls need a Docker login, and FIPS
variants are a paid tier.

The spike in #856 built `takserver-api` on `17-jdk-debian13-dev` on native amd64
and arm64 and ran a toolchain smoke (`java`, `keytool`, `curl`, `nc`, `ps`,
`openssl`). The later qualification run is described below.

## Decision

Use `dhi.io/eclipse-temurin:17-jdk-debian13-dev`, pinned by index digest, for the
builder and runtime stages. The `-dev` variant keeps a shell and apt, which the
entrypoints and the runtime package installs need. The build job logs in with the
`DHI_USERNAME` and `DHI_TOKEN` repository secrets.

## Acceptance

Same bar as ADR 0025: native amd64 and arm64 builds with Copa and its strict
package-version validation intact, all six TAK images through native deployment,
authenticated image comparisons, signature verification, and the native install
and upgrade matrix including airgap. Additionally: confirm the PKCS#8 key
normalization still interoperates with the Pepr runtime on Debian 13 OpenSSL, and
decide how fork pull requests (no secrets) and airgap mirroring obtain DHI bases.
The outstanding checks use the same acceptance criteria.

## Not established

The first qualification run includes fresh install, upgrade, airgap and Copa
results. Dedicated Pepr key interoperability evidence, Full Core and PostgreSQL
15 upgrade results, and the fork and airgap mirroring decision remain open.
Whether the free Community catalog stays free is outside our control.

## First native qualification

Run [45078250033](https://github.com/jonathanagustin/uds-takserver/actions/runs/45078250033) on the candidate head:

- Both native builds with Copa, `images`, and package jobs passed.
- Fresh install passed on amd64, arm64 and amd64 airgap, and the amd64 upgrade passed.
- The first arm64 upgrade attempt failed in "Upgrade from the released package and
  check retained state". GitHub returned no log for that job, so the cause is
  unknown. Re-running only that job passed. One pass does not explain the first
  failure, so it counts as an unresolved flake until another run shows otherwise.
- `validate-full-core` and the PostgreSQL 15 upgrade were skipped in the PR. The
  fresh installs exercise certificate generation and the controller that loads
  the PKCS#8 keys, but there is no separate Pepr key interoperability control for
  Debian 13 OpenSSL.

Outstanding qualification after adoption: a second clean arm64 upgrade run, the full-core
run, signature verification evidence, and the fork pull request and airgap
mirroring decision.
