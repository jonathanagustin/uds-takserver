# 25. Qualify a Temurin runtime candidate

Date: 2026-11-18

## Status

Proposed. Keep this change in draft until native qualification passes.

## Context

Fresh native TAK image builds are blocked by a mismatch between requested RPM
fix versions and available package-manager results (#625). Read-only checks of
public UBI repository metadata have not supplied a correction. They do not
prove the cause of the original failure.

The builder already uses the official Temurin Java 17 JDK on Ubuntu 22.04.
The repository has no documented requirement for a Red Hat runtime. A runtime
using the same Java vendor and distribution family is an alternative worth
qualifying, rather than changing Copa's version checks.

## Proposal

Use the official Temurin 17.0.21+9 JDK Jammy image, pinned by its multi-platform
manifest digest. The index contains native Linux amd64 and arm64 images.
Keep the full JDK, including keytool and Java process diagnostics. Install
ncat, curl, CA certificates and process utilities explicitly; preserve the
config image's certificate and gettext tools and Ignite's unzip dependency.
Keep existing launch commands, permissions, source verification and TAK patches.

This changes the runtime distribution, package set and inherited image
entrypoint. Java native libraries, shell tools, certificate behavior and
application startup require real execution on both architectures. A successful
Dockerfile lint or an image index lookup is not that evidence.

## Acceptance

Before accepting this proposal, require both native Container builds with
Copa enabled and its strict package-version validation intact, all six TAK
image smoke tests, authenticated image comparisons, signature verification,
and the native install and upgrade matrix, including the airgap path.
Investigate failures rather than suppressing findings or weakening gates.

PR builds may publish input-bound candidate tags through the existing workflow.
Do not manually publish release aliases or dispatch a release publisher for this
experiment. Preserve earlier UBI failure evidence. If qualification fails, keep
the proposal in draft or close it; main retains its existing runtime until merge.

## First native qualification

Both architecture builds, including Copa and image attestations, completed on
candidate head 307dce8deb9551eebba0d1b0f4ee285980be1c5e. The authenticated scan
and both upgrade paths passed. All three fresh-install paths failed; on both
architectures the certificate Job completed but application pods could not
mount the missing CRL Secret. This observation does not establish why the
watcher did not produce that Secret.

The candidate adds fixed CA-readiness categories to the existing material
reconciliation callback: missing certificate, key or passphrase, or unreadable
material. No input values or crypto exceptions are logged. Loading, signing,
revocation and pod startup gates retain their existing behavior. Qualification
must identify and correct the fresh-install failure before acceptance.
