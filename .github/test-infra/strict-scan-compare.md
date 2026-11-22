# Strict image comparison adapter

The Scan workflow uses `../scripts/strict-scan-compare.py` for the acquisition
and iteration portion formerly handled by `uds-pk scan compare`. A fresh hosted
qualification remains separate work. It retains the pinned
`uds-pk 0.2.0 compare-scans` implementation for vulnerability comparison and
Markdown rendering; it does not add a fail-on-CVE threshold or turn the report
into a security acceptance claim.

The upstream combined command overwrites earlier same-flavor component image
lists and discovers packages through an organization-only API. Missing released
flavors can produce a successful empty report, and unmatched images compare
against themselves. See issues #666 and #667 and the upstream
[scan command](https://github.com/defenseunicorns/uds-pk/blob/v0.2.0/src/cmd/scan.go),
[SBOM fetcher](https://github.com/defenseunicorns/uds-pk/blob/v0.2.0/src/utils/sbom_fetch.go),
and [comparison implementation](https://github.com/defenseunicorns/uds-pk/blob/v0.2.0/src/compare/compare.go).
The adapter invokes only the unchanged comparison operation.

## Interface

For canonical registry inputs, from the repository root:

```sh
python3 .github/scripts/strict-scan-compare.py \
  --zarf-yaml-path zarf.yaml --source-root . \
  --flavor upstream --architecture amd64 \
  --output-directory "$RUNNER_TEMP/strict-scan-receipt" \
  --markdown comment.md
```

The output directory and Markdown file must not already exist. The adapter
selects the latest published stable release by default. `--release-tag vX.Y.Z`
explicitly selects an existing public stable release for a controlled replay;
it never falls back to an older release after a failure.

Candidate integration additionally supplies:

- `--zarf-yaml-path "$RUNNER_TEMP/candidate-scan.yaml"`; imports remain relative
  to `--source-root`, and `zarf.yaml` there remains the canonical inventory.
- One `--image-name-override FULL_CANONICAL_REF=FULL_LOOPBACK_DIGEST_REF` for
  every flattened image in the consumer state. Basenames are insufficient.
- `--candidate-state PATH --candidate-state-sha256 SHA256`, using the bytes
  captured after the caller's successful canonical consumer validation.

Loopback mode refuses execution until the canonical
`.github/scripts/candidate-consumer.py` is available. It replays that module's
`validate_state(path)` without a supplied context, preserving its real event,
checkout, producer, registry and complete-state policy. The state bytes must
match the caller's hash before and after replay and decode to the returned
validated object. This shares the consumer's verification authority; it is
not an independent reimplementation of producer verification. Partial
checkpoint state cannot replace a ready consumer state.

The inventory takes the union of all selected-flavor and common component
images, including exact local component imports. Duplicate image references
are deduplicated. Ambiguous components, unsupported selectors, remote imports,
cycles, escaping paths and unresolved imports fail explicitly. The rewritten
inventory must map exactly back to the original canonical inventory.

## Release and scan boundaries

The release reader reuses the existing release-readiness and release-binding
policies. It checks the public release ID/tag and peeled Git source, requires
that source to be reachable from main, verifies the original publisher
invocation, and cryptographically verifies the OCI release-binding predicate
with the exact repository workflow/source/issuer. The public release asset
provides candidate selectors only; it does not authenticate itself. In this
first adapter, a historical backfill with different workflow and application
commits is explicitly unsupported rather than assumed equivalent.

The authenticated index selects the requested native architecture. Native
manifest and unique `sboms.tar` layer hashes are checked before use. The tar
reader accepts bounded regular JSON files, rejects links, sparse entries,
traversal, duplicate names/repositories/JSON keys and incomplete tar framing,
and never performs filesystem extraction. Full image references and decoded
image configuration bind each SBOM to its repository and Linux architecture.
The signed SBOM's image-ID/config hash must agree.

Each current input resolves to the native digest the selected architecture
will consume. The supported pinned registry `manifest` command returns bytes
that must match that digest; its configuration descriptor then binds a bounded
public configuration-blob read and Linux/native-platform check. This initial
reader supports Docker Hub, GHCR and validated numeric loopback; other registry
hosts fail explicitly. Public scoped tokens stay in memory, with authorization
removed before allowed HTTPS blob-CDN redirects. Only an exact,
consumer-validated numeric-loopback digest permits HTTP, scoped to that
individual registry/configuration or scan call. Other calls explicitly keep
HTTP and skipped TLS verification disabled. Mutable tags are re-resolved before
report publication; changing a sibling architecture while retaining the
selected native digest does not invalidate that selection. Release identity,
tag source and signed index, manifest inputs, consumer state and tool identities
are also rechecked before publication.

The adapter pins Grype 0.120.1, UDS CLI 0.42.0 with Zarf 1.0.1, and
`uds-pk compare-scans` 0.2.0. Their actual executable hashes are recorded; the
workflow's existing checked installers remain the binary-source authority.
It updates the Grype database once, records its official source/checksum and
the complete database-file hashes, then disables automatic database updates.
File identity checks between scans and final hashes reject database drift.
The supported database bound is four GiB, accommodating the actual v6 database.

Every selected image gets independent released-SBOM and current-image Grype
scans with the original CPE-generation and CycloneDX output settings. Missing,
failed, identity-mismatched or package-catalog-free results fail. A real package
catalog with zero CVEs is valid. OS/file-only output cannot prove a package scan.
New or ambiguous repositories have no automatic baseline policy and fail
explicitly; the adapter never invents a self-comparison or empty release scan.

The complete Markdown and bounded `receipt.json` contain only selected public
image/release/source/digest/tool/database identities and result hashes/counts.
Checkout identity and adapter/policy file hashes are recorded separately from
the consumer's verified PR-head identity; a merge checkout is not assumed equal
to its PR head. GitHub API calls explicitly select github.com, and all other
GitHub CLI operations override inherited host/repository configuration.
Raw child output, image configuration, environment values, credentials and
temporary source SBOMs/scans are excluded. Temporary acquisition/scanning data
is removed on success or failure. Source and parser errors produce fixed reason
codes rather than command arguments, upstream bodies or raw exceptions.

## Validation scope

Offline controls cover same-flavor union/common imports, exact mappings and
state hashes, unsafe/truncated archives, source/platform identity, empty or
wrong scanner output, database changes, current native-payload changes, release
selection/source/index changes and crypto-before-SBOM ordering. Mocked crypto
processes test ordering only; they do not establish cryptographic acceptance.

An actual controlled NATS comparison uses an authenticated v0.16.7 AMD64 SBOM,
an independently resolved current registry image, pinned tools and a frozen
database. The source-SBOM and live-image catalogs can differ even for the same
named image, so their reported differences are not themselves proof of a
security fix. A separate real zero-CVE package control confirms Grype retains
package evidence when no vulnerabilities match. These controls do not establish
complete TAK scanning, an AMD64 runtime result, or hosted workflow acceptance.
