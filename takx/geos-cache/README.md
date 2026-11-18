# Local GEOS C-API cache prerequisite

This separate opt-in profile builds historical GEOS 3.4.3 and prepares a bounded
local OCI component for [#482](https://github.com/jonathanagustin/uds-takserver/issues/482).
The default build and PR workflow remain read-only. Registry publication is a
separate protected-main opt-in described below; it has not yet been qualified.
It adds no automatic SDK consumer. Draco and the historical `native-geos`
profile retain their existing behavior. An old
producer receipt is never accepted as fresh input or upgraded into this schema.
The existing profile's GCC 14 configure-feature diagnosis is tracked separately
in [#680](https://github.com/jonathanagustin/uds-takserver/issues/680); this change
does not rewrite that profile or its historical evidence.

The exact pinned GCC 14.2 image contains GNU Make 4.3, binutils 2.40 and Python
3.11.2. This new profile records their actual identities; it does not substitute
those observations into the older GEOS profile's Make 4.4.1/binutils 2.44 receipt.
The fixed historical C configure probes require the explicit GNU89 language
contract with GCC 14; C++ library compilation remains C++11 and the genuine C
consumer remains C11. The profile requires agreeing active 64-bit feature
definitions in both generated headers, then compiles and runs the configured
type's size/arithmetic control before library compilation. Missing features
fail; there is no cached-result override or warning suppression fallback.
See GCC's [Autoconf compatibility guidance](https://gcc.gnu.org/gcc-14/porting_to.html).
This language choice does not modernize the historical source or establish
security suitability.
The initial local execution is Linux AMD64 **emulated on an ARM64 Podman host**.
It does not establish native AMD64 performance or hosted acceptance.

The five-file projection is deliberately small:

- `lib/libgeos_c.a` and `lib/libgeos.a`;
- `include/geos_c.h` and its unconditional `include/geos/export.h` dependency;
- the exact upstream `COPYING` license.

It excludes C++ headers, `geos-config`, absolute-prefix `.la`/`.pc` files,
executables, source/build trees and diagnostics. This is a Linux C-API component;
it does not promise a C++ development package or an MSVC header contract.

## Fresh build and local restoration

The engine must already have the immutable image in `inputs.lock.json`. The
launcher checks its exact configuration digest, Linux OS and AMD64 architecture;
it does not acquire images implicitly. Locked source acquisition is about two
MiB and requires only the public archive and original three-file ATAK patch.
Use Python 3.11 or newer for the host launcher and offline checks.
Use canonical external paths; outputs must be new, outside the repository and
separate from source caches or OCI inputs. Symlink, filesystem-identity and
Unicode/case aliases are rejected. Case-distinct Linux path pairs are also
conservatively rejected when their normalized tails overlap.

```sh
python3 -B takx/geos-cache/producer.py \
  --engine /path/to/podman \
  --source-cache /private/geos-source-cache \
  --output /private/new-geos-producer

python3 -B takx/geos-cache/cache.py \
  --layout /private/new-geos-producer/oci \
  --subject sha256:REVIEWED_LOCAL_MANIFEST_DIGEST \
  --input-key geos-capi-local-v1-REVIEWED_INPUT_HASH \
  --output /private/new-restored-prefix

python3 -B takx/geos-cache/qualify.py \
  --engine /path/to/podman \
  --layout /private/new-geos-producer/oci \
  --subject sha256:REVIEWED_LOCAL_MANIFEST_DIGEST \
  --input-key geos-capi-local-v1-REVIEWED_INPUT_HASH \
  --output /private/new-two-prefix-controls
```

Read the exact local subject/key from the completed fresh `result.json`; no
mutable lookup tag is used. The local digest is an integrity pin selected by the
caller, **not publisher authentication**. Restore verifies all OCI descriptor,
manifest, capsule and five-file hashes before creating a prefix. It never
executes code. Qualification separately executes the genuine C geometry/error
probe and strict whole-archive PIC link at two unrelated prefixes in another
container. That container receives only the local OCI input and public recipes;
the original producer/source trees and host GEOS are unavailable. Header/link
traces, actual initialized providers and payload hashes bind both consumers.

## Identity, bounds and remaining gates

The component-specific `geos-capi-local-v1-*` key binds:

- exact archive, license and three patch pre/post images, plus complete patched
  and unpatched source-inventory digests;
- every consumed executable recipe/helper/probe/lock, fixed container-relative
  configure/build commands, generated configuration identities and whitelist
  environment; documentation and tests are excluded;
- immutable image/configuration, observed host/target/emulation context, actual
  GCC backends, selected binutils, Python, shells, Make, patch and ELF tools;
- target pointer/byte order, glibc and C++ ABI/features and hashed tool providers.

Both archives must contain ordinary real ELF64 little-endian x86_64 REL objects.
Closure records preserve member order and occurrence, including repeated
basenames, and must match actual freshly compiled objects and capsule bytes.
Tested headers, payloads, source, build configuration, tools and providers are
rechecked before success. No dependency OCI subject is invented for this leaf.

Builds use one job, one container CPU and three GiB memory, with networking off,
a read-only root and a fixed environment containing no caller credentials.
The new profile allows 1,200 seconds for compilation and 1,800 seconds overall;
these limits accommodate an explicit emulation experiment and are not speed
claims. Retained owned output is bounded to one GiB, each command log to eight
MiB. Polling bounds are not a filesystem quota. Cleanup targets only its UUID
container with an independent 15-second bound, including after budget failure.
Failed/stopped runs retain private diagnostics without a success receipt; a
changed recipe requires a fully fresh run.

Capsules allow at most 32 MiB compressed and 64 MiB expanded, two MiB JSON and
exactly five regular payload files plus the manifest. Canonical USTAR headers,
zero padding, one canonical gzip member, bounded framing, exact OCI
directories/blobs and unique JSON keys
exclude unrecorded metadata, links, sparse entries, traversal and extras.

Offline parser, key, alias, privacy, payload-lease and cleanup controls run with:

```sh
uv run --no-project --with pyyaml==6.0.3 python -B -m unittest discover -s takx/geos-cache -p 'test_*.py' -v
```

These synthetic fixtures never establish native compilation. Fresh build and
two-prefix execution receipts must be assessed separately. Registry push/pull,
protected publisher/source trust, authenticated and anonymous readback,
immutable-key promotion, SDK/renderer/TAKX acceptance and security suitability
remain unqualified. Native local hosted evidence is recorded below. The schema keeps registry,
publisher, SDK and security acceptance explicitly false. Issue #482 stays open.

## Read-only hosted trial

`GEOS local qualification` runs the same fresh producer and two-prefix consumer
commands on a native Ubuntu 24 AMD64 runner for changes to this profile or its
historical GEOS helpers. The compiler image is pulled by its existing immutable
reference and its configuration is verified by the launcher. The original
network, filesystem, CPU, memory, source and timeout constraints remain active.
Docker runs as the invoking numeric UID/GID so the container can use private
runner-owned bind mounts with all capabilities dropped. Rootless Podman retains
its existing mapping. No directory permissions or capabilities are relaxed.
Checkout credentials are not persisted; the read-only `qualify` job has only
`contents: read`, without registry writer, signing or publisher authentication.

A successful job retains the exact capsule and OCI layout, fresh producer and
two-prefix consumer records, plus checkout/tree/run/attempt bindings and record
hashes. On failure, only selected complete command logs with matching recorded
sizes and hashes pass through the existing whole-stream redactor. Incomplete,
changed, oversized or invalid logs are omitted; failed output never becomes a
success receipt. Original failed/emulated trials remain historical evidence.

This job supplies a way to collect native local evidence.
Its execution alone does not qualify cache publication, security
suitability, the SDK or TAKX. Assess the exact successful run's bytes separately;
registry, publisher, SDK and security acceptance remain false in local receipts.

## Opt-in protected registry qualification

After reviewing and merging the workflow, a maintainer can manually select
`publish=true` in **GEOS local qualification** on `main`. The default is false.
PRs and non-main dispatches cannot reach the publisher or its job-scoped package,
OIDC and attestation grants. A fresh native build and two-prefix qualification
must finish first; the publisher downloads that exact artifact ID from its own
run, then verifies its checkout/tree/run/attempt, recipe and receipt hashes,
native identity, five-file capsule and OCI subject before staging any bytes.

The fixed namespace is
`ghcr.io/jonathanagustin/uds-takserver/native-cache/geos-capi`. The existing
`geos-capi-local-v1-*` key still describes the unchanged local payload format;
registry qualification does not rewrite its local claims. Staging uses an
attempt-specific tag. An existing input key with another immutable subject is a
collision, never an overwrite or a reason to rebuild a published object.
Authorization failures, transport failures and generic HTTP 404 responses are
not cache misses. The workflow serializes main dispatches; other writers must
not be granted permission to race this namespace.

The publisher attests SLSA provenance and a distinct GEOS predicate binding the
exact source/run/attempt and native evidence. Readback requires successful
`gh attestation verify` with that exact protected workflow/source and hosted
runner policy before processing a remote capsule. It fetches the fixed subject
and bounded blobs with an empty registry credential configuration, rechecks all
digests and the original strict capsule/OCI contract, then runs genuine geometry
and whole-archive PIC controls at two new unrelated prefixes in the isolated
compiler container. Only successful readback, a second attestation check and
these consumers permit input-key promotion and an accepted registry receipt.

Command output has independent time/size bounds and stays private; only compact
typed qualification, readback, consumer and accepted receipts are uploaded.
Failure emits no accepted receipt. The original producer/local relocation
records keep all four wider acceptance flags false. A successful registry
receipt can establish component transport/publisher acceptance only; SDK,
renderer, client and security suitability remain false. No cache is wired into
automatic consumers by this experiment. Until an actual protected run and its
immutable bytes are independently reviewed, registry acceptance remains open
under #482; mocked transport tests establish no live publication or native proof.

### Accepted native local evidence

The fresh native AMD64 trial [44357847600](https://github.com/jonathanagustin/uds-takserver/actions/runs/44357847600)
passed on source `df1eb30b32aa6311e3523726c2e75b30a3075f33`. Both restored
prefixes passed genuine geometry/error and whole-archive PIC controls, with
matching tools, providers, headers and payload. The exact records and independent
integrity review are retained in [the qualification checkpoint](qualification/2026-11-18/README.md).
This closes the missing native local build/relocation evidence only; the
remaining registry, publisher, SDK and security gates above stay open.
