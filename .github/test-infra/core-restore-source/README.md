# Core restore-policy source candidate

This manual, main-only experiment builds the reviewed two-file resource-policy
patch against Core v1.16.0. It produces a repository-owned derivative named
`1.16.0-restore-policy.1-upstream`; it does not change the deployed Core reference,
publish a registry package, or connect to a cluster.

The source lock binds 34 upstream files, the patch, AGPL license, CLI binary,
three native AMD64 image manifests, and the resulting CRD. The builder checks
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

The optional `run_api_smoke` input adds a separate AMD64 job with read-only
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
