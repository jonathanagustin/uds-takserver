# 25. Qualify a Temurin runtime candidate

Date: 2026-11-18

## Status

Accepted on November 18, 2026, in [#726](https://github.com/jonathanagustin/uds-takserver/pull/726),
merged at `ea145739a46d786df3277bd9196b6170e7512bde`.

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
images through native deployment acceptance, authenticated image comparisons, signature verification,
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

## Private-key interoperability correction

The exact published candidate supplies OpenSSL 3.0.2. Its req command emits
PBES2 private keys using 3DES. The published Pepr 2.0.7 AMD controller runtime
is Node 26.12.1 with OpenSSL 4.0.3, which rejects that key cipher with
ERR_OSSL_EVP_UNSUPPORTED. A synthetic CA generated with the candidate's actual
binary, libraries and scripts reproduces that failure in the controller's
actual Node runtime and libraries. The host's Node 24 accepted the same key,
so host-only certificate tests had not exposed the mismatch.

Normalize generated encrypted PEM keys to PKCS#8 PBES2/AES-256-CBC with
PBKDF2-HMAC-SHA256 and 100,000 iterations before publishing them. Root keys keep
CAPASS; application keys keep PASS. Files are replaced only after successful
conversion and stay mode 0600. This preserves public-key identity and existing
PKCS#12 format selection. It enables no legacy provider in the consumer.
The same real-runtime control passes after normalization. The local witness established this interoperability correction. The later
hosted qualification below accepts the combined change; historical failures
retain their original outcomes.

## Generated backup aliases

The same certificate-producer review found an upstream backup name for the
CA PEM key that the application-bundle filters did not recognize (#727).
Fresh publication and migration now omit both canonical and backup names.
Application keys, required signing keystores, public backup certificates and
custom filenames remain, and the migration leaves the dedicated CA Secret
unchanged. Controls execute the real serialization block and migration entry
point against synthetic files and a local API fixture; both fail before the
filter correction and pass after it. Hosted qualification applies to this
combined producer and migration change.

## Accepted qualification

Exact head `82e4e56a2fac75ae4a0e7ac05290fe4557af7c1f` and the merged main
commit have identical full trees (`249efc18b32876483a648bb807f0e6678842e444`).

| Required evidence | Accepted run |
| --- | --- |
| Both native builds with Copa, attestations, scans, SBOMs and manifest publication | [44338245110](https://github.com/jonathanagustin/uds-takserver/actions/runs/44338245110) |
| Authenticated complete-inventory image comparisons | [44338245020](https://github.com/jonathanagustin/uds-takserver/actions/runs/44338245020) |
| AMD and ARM fresh installs, AMD airgap, and both regular upgrades | [44338245380](https://github.com/jonathanagustin/uds-takserver/actions/runs/44338245380) |

Native deployment acceptance exercised all six TAK images. The PostgreSQL 15
row was intentionally skipped in the PR; its separate main-push policy remains.
All other applicable PR checks passed. The correction resolves build blocker
#625 and backup-alias issue #727. Earlier failed heads and UBI observations
remain recorded. Fork candidate qualification in #652/#696 remains separate.
