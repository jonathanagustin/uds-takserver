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
