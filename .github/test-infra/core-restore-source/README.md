# Core restore-policy source candidate

This manual, main-only experiment builds the reviewed three-file resource-policy
patch against Core v1.16.0. It produces a repository-owned derivative named
`1.16.0-restore-policy.2-upstream`; it does not change the deployed Core reference,
publish a registry package, or connect to a cluster.

The source lock binds 34 upstream files, the patch, AGPL license, CLI binary,
three native image manifests per selected platform, and the resulting CRD. The builder checks
its recipe against the workflow commit, uses a fresh output directory, and
bounds disk use, command output, and execution time. Default invocation only
checks the local contract.

The workflow requests a Sigstore identity token only for package signing.
Verification requires the exact main-branch workflow identity, GitHub issuer,
and transparency-log verification. The signed definition binds the source
commit, run ID, and attempt. Unsigned local builds cannot emit a provider receipt.
Only the six public artifact files are uploaded; credentials and private build
logs stay outside that upload list. Corresponding source and the upstream AGPL
license accompany the derivative.

Run the offline guards with:

```sh
uv run --no-project --with pyyaml==6.0.3 python -B -m unittest discover \
  -s .github/test-infra/core-restore-source -p 'test_*.py' -v
```

A successful package build establishes package provenance and structure only.
Core-owned API upgrade testing, actual restore behavior, ARM qualification, and
owner acceptance remain separate requirements for the restore-policy PR.

The optional `run_api_smoke` input adds a separate native job with read-only
GitHub permissions. It downloads only the same attempt's candidate, repeats
provider/signature/source verification, and refuses a remote Docker daemon or
an existing test cluster. The established checkpoint helper creates the fresh
cluster; its run/source/cluster marker is checked before every API observation
and package deployment.

The job pulls the official Core 1.16.0 backup package by the pinned index digest
`sha256:e3728d6a8430dbd9a1127cbce7cf474026f8e393135ecb24cbe0616d70e69fa6`,
requiring the exact upstream release signer and transparency-log verification.
Separate single-package UDS bundles deploy the official baseline and then the
signed source candidate, with node agents disabled and schedules paused. Both
use normal package signature verification and the Core-owned Helm chart.

The baseline must reject `spec.resourcePolicy`. After the upgrade, the same
server dry run must succeed, while an unknown field and missing required policy
fields must still fail. Both phases require the exact expected CRD schema and
Core Helm owner. The CRD UID must survive the upgrade, and no Restore object may
persist. The only public evidence is fixed schema proof and hashed identities;
raw API responses, manifests, and command logs are excluded from artifacts.
This is API schema evidence only, with restore and owner acceptance explicitly
false. No chart in a normal application bundle or deployment default changes.

Tool-manager CLI paths are resolved before passing the executable to the
builder; the resolved regular file must still match the exact pinned SHA256.
On failure, the builder prints only a fixed guard category, an allowlisted
exception type, and its own source hash/callsite lines. Arbitrary exception
messages, paths, HTTP bodies, credentials, and private command output are omitted.

A failed API smoke retains fixed failure categories, its command role, and
hash-bound callsite lines from the known helpers. At baseline/candidate failures,
a read-only diagnostic repeats the trial-cluster guard before inspecting the
fixed Velero Deployment, Pods and Events. Public proof contains only replica and
phase counts plus allowlisted error-pattern counts. Object names, raw reasons,
messages, command logs, credentials and manifests are omitted. Diagnostic failure
cannot turn the original failure into acceptance. This does not change CPU
requests, admission policy, signature requirements, or the schema assertions.

CRD diagnostics expose only fixed ownership predicate booleans, the normalized
specification hash, and whether the v1 schema contains `resourcePolicy`. They do
not publish observed owner/manager names or alter the required Core Helm owner.
These facts distinguish a metadata mismatch from a schema replacement while
preserving the same acceptance rules.

The API-only bootstrap can reconcile missing Helm tracking metadata by deploying
the same verified official baseline bundle once more through its normal Core
hooks and charts. Before that mutation, the live CRD must have the exact locked
baseline schema/component, a stable valid UID, no deletion or owner references,
and only correct or absent tracking fields. Conflicting values and existing
Backup/Restore objects refuse reconciliation. The final full Core owner/schema
check and unchanged UID are mandatory before any baseline API evidence is taken.
No direct metadata patch, force-conflict flag or ownership bypass is introduced.
This qualifies an upgrade from the reconciled baseline, not fresh derivative
installation or actual restore behavior.

The manual workflow also accepts `api_mode: fresh` with `run_api_smoke: true`.
This mode verifies that no Velero CRDs or namespace exist immediately before
one candidate bundle deployment. It does not pull, install or reconcile the
baseline. The first deployment must retain the complete candidate schema and
Core Helm owner, pass all four strict server dry-run controls, preserve the CRD
UID during observation and leave no Restore objects. Public evidence records
`mode: fresh` separately from upgrade results. A passing API check still does
not qualify actual data restoration, ownership transfer or native ARM behavior.
The default remains `upgrade`; all signature, source, cluster and quota guards
apply to both modes.

The `.2` candidate sets `serverSideApply: "false"` only on the upstream Velero
chart. Helm 4 then creates its bundled CRDs and skips CRDs already installed by
the preceding Core configuration chart, rather than applying the older bundled
schema over them. The Core configuration chart retains its normal apply mode.
The package inspector and corresponding-source verifier require these exact
strategies and source bytes. This is a candidate fix; only a successful separate
fresh-mode run can qualify the first installation. Upgrade and actual restore
qualification remain separate.


The manual `architecture` choice defaults to `amd64` and also accepts `arm64`.
Both producer and API jobs use the corresponding native Linux runner, exact CLI
binary hash and three image manifests. Signing requires a matching GitHub runner
architecture; building and signature consumption also require the matching native
Linux host. Neither emulation nor architecture-check bypass flags are used.
Artifact names include the selected platform and the same run/attempt binding.

Source-lock, provider and API-proof schema 2 make the architecture explicit. A reader
rejects missing, mismatched or relabelled provider platforms, even if the version
matches. Schema 1 artifacts retain their original source-specific verifier;
they are not silently upgraded. The `.2` source patch, Core base, license and
image indexes are unchanged. ARM native build/API acceptance requires successful
protected ARM runs; source pins and offline tests alone do not establish it.

The optional `run_owner_trial` input (default false) runs the existing native
fresh/handoff/reversal driver only after the same run's signed build and API
smoke succeed. The trial job has read-only permissions and consumes exactly the
six-file provider for its own source, run, attempt and architecture. It verifies
the pinned native CLI, package signature, transparency log, corresponding source
and payloads before creating a disposable cluster, then rechecks the captured
provider hashes before bundle binding, each restore and receipt publication.
The source-mode schema 2 plan also binds the provider's canonical package manifest
digest to all three built archives. It checks every OCI blob, the retained
`uds-bundle.yaml` package path/ref and exact keyless policy, embedded source
annotations, version and native platform. This inspection repeats when loading
the plan, restoring reviewed task defaults, and immediately before each bundle
deployment. A stale, unrelated or weakened-policy archive cannot be labelled as
the selected source candidate by merely updating its archive hash.

Canonical digest inspection uses the pinned UDS0.41 CLI only after successful
signature verification. That subcommand does not accept verification flags;
supported `--log-level=error --no-color` flags suppress its unverified-signature
warning so bounded merged output contains exactly the digest. A nonzero exit,
unexpected output or changed log refuses the binding. Digest inspection itself
is not a signature check. The exact certificate, issuer and transparency-log
checks remain mandatory in the preceding provider verifier and normal UDS
bundle creation/deployment.

The provider receipt includes a closed inventory of 26 runtime helper files,
including policy YAML, controller readback, bundle reader, driver and staged
receipt helpers. Every receipt compares those bytes to the exact workflow source
commit with Git replacement and lazy fetching disabled. Source/helper drift
before deployment, restore or final receipt verification fails closed; the
constructor's initial helper check is not the final check.
`tasks.yaml` retains its existing separate strict pin/restoration guard: its
temporary image-tag transform is validated and restored after archive binding,
then the driver requires exact reviewed task bytes before deployment. The pin
script and restoration guard remain in the closed inventory.

Only the experimental bundle copies select this local signed backup/restore
package. The trial uses `tests/backup-restore-controller-trial.sh`, carrying the
exact seven-resource restore policy and controller readback from draft #597.
Ordinary restore tasks and published Core dependencies retain their defaults.
The private journal and public acceptance receipt bind the provider and restore
helper hashes. A failed trial or failed receipt validation fails the job; raw
cluster journals and command logs are not uploaded.

This wiring is not restore or owner-transfer acceptance. Both native trials
must pass real data/client restore, controller regeneration, fresh ownership,
handoff and reversal before the draft restore behavior can become ready.

## Optional encrypted owner failure evidence

For a future manual main-branch dispatch with `run_owner_trial: true`, an operator
may supply `diagnostic_recipient`, an age X25519 **public** recipient (`age1…`).
The empty default disables this channel and acquires no encryption tool. This
channel does not generate age keys. Keep the private identity outside repositories,
workflow inputs and logs; supply only the existing public recipient.

The owner job first publishes and uploads its existing sanitized evidence with
14-day retention. Only a failed staged-trial step and a nonempty recipient then
invoke the existing reviewed encryption helper. It authenticates the failed
owner journal's source/run/attempt and encrypts only its bounded private failure
capture. Invalid or unavailable input or encryption failure can omit this
diagnostic; it does not change the trial failure or any acceptance gate.

Only successful encryption with the helper's `ready=true` postcheck enables the
separate `core-owner-encrypted-first-failure-…` artifact. Its sole file is
`first-failure.age`, retained for seven days. Raw captures, keys and decrypted
contents are not uploaded or printed. Keep any later decryption private, as
described in [the diagnostic guidance](../../../docs/ci.md#optional-encrypted-private-failure-capture).

Both optional steps have configured two-minute timeouts and continue on error.
Artifact transport may ignore a step timeout, so this is best-effort delivery;
the existing 230-minute owner-job ceiling remains the fallback. Sanitized public
uploads take priority. A retry with an empty recipient omits this channel again;
decide whether to supply a public recipient before dispatching another trial.
This wiring cannot recover a previous runner's discarded capture, establish the
cause of an earlier failure or fix the underlying deployment/restore behavior.
