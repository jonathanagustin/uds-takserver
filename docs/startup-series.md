# Repeated startup observations

Use `python3 -m bench.startup_series` to check a bounded series of original
startup-pair artifacts. A series requires three to five cold/warm pairs per
selected native architecture. It does not collect observations, install or
restart an application, dispatch workflows, or change JVM/probe settings.

This prepares the repeated-observation part of [#48](https://github.com/jonathanagustin/uds-takserver/issues/48).
No repeat series has been executed or accepted by this change. Class-loading
duration, effects of instrumentation on startup, and measured tuning tradeoffs
remain separate empirical requirements.

## Collect the planned repetitions

1. Choose one `main` source revision, the native architectures (`amd64`,
   `arm64`, or both), and three to five expected repetitions per architecture.
   Keep that source and the workflow/collector bytes fixed for the series.
   The matching published application package must already exist. The existing
   [manual Startup pair workflow](../.github/workflows/startup.yaml) still
   produces one cold/warm pair per selected architecture and builds no images.
2. Use the normal manual workflow for each planned repetition. Wait for the
   previous execution to finish before starting another. Stop further
   repetitions for an architecture when an observation fails; preserve its
   failed slot and every remaining missing slot. A later successful attempt
   must not silently replace a failed observation. No automatic retries are
   added.
3. Retain each original `startup-pair-<arch>-<run>-<attempt>` artifact ZIP and
   its GitHub run/attempt, job, source and artifact metadata. Verify successful
   candidate jobs against their exact source and attempt; an overall cancelled
   run does not establish whether a particular native job passed. Check the
   API archive digest and size before treating a download as that artifact.
   Artifact names alone are insufficient. An older attempt requires that
   attempt's metadata, rather than the latest run summary.
4. Fill every expected slot in the plan below, including failed and missing
   observations. Preserve the original ZIPs. Do not edit historical records to
   make them eligible for a new series.

The existing workflow has a 60-minute limit per selected native job. Three
pairs have ceilings of 180 runner-minutes for one architecture, or 360 for
both, plus planning-job overhead. Five pairs have ceilings of 300 or 600
runner-minutes. These are bounds, not measured costs. Artifact retention stays
at 14 days, and an active experiment is preserved.

## Plan and acquisition metadata

The plan is a JSON object with these fields. Unknown fields and duplicate JSON
keys are rejected. File paths are relative to the plan's directory.

| Field | Value |
| --- | --- |
| `schema` | Integer `1` |
| `kind` | `startup-series-plan` |
| `source_sha` | Exact 40-character experiment source commit |
| `workflow_sha256` | SHA-256 of that source's `.github/workflows/startup.yaml` |
| `repetitions` | Integer from `3` through `5` |
| `architectures` | Unique selected values from `amd64` and `arm64` |
| `entries` | One entry for every architecture/repetition slot |

Every entry has exactly these fields:

| Field | Value |
| --- | --- |
| `arch` | A selected architecture |
| `repetition` | Its one-based planned repetition number |
| `status` | `candidate`, `failed`, or `missing` |
| `run_id`, `run_attempt` | Positive integer GitHub source identities, when available |
| `artifact_id` | Positive integer artifact identity, when available |
| `artifact_name` | `startup-pair-<arch>-<run_id>-<run_attempt>`, when available |
| `archive_sha256` | Verified original ZIP digest, when available |
| `archive_path` | Relative path to that ZIP, when available |

Candidates require all acquisition fields. Failed or missing entries may use
`null` for unavailable acquisition fields. Do not invent IDs, hashes or data to
fill a failed slot. A normal `both` execution can share a run/attempt across
architectures; one architecture cannot reuse that same run/attempt as another
repetition. Artifact IDs cannot be reused.

The plan's API metadata is supplied by the operator. Offline validation checks
its consistency with the provided bytes; it does not authenticate GitHub API
responses or provide a cryptographic provenance attestation. Retain and inspect
the independently acquired source/run/artifact metadata with the report.

## Validate the original artifacts

Use a new private output directory:

```sh
mkdir -m 700 series-report
python3 -m bench.startup_series --plan study/plan.json \
  --output series-report/report.json
```

Each candidate ZIP must contain exactly the original six typed JSON files:
`bindings.json`, `cold-begin.json`, `cold.json`, `warm-begin.json`, `warm.json`,
and `pair.json`. Symlinks, escaped paths, duplicate members and oversized inputs
are rejected. The separate begin and binding files must match their copies in
the finish records; `pair.json` must match the pair validator's recomputed
result. Existing timing, configuration, image and incarnation checks are
preserved by calling the unchanged startup-pair validator.

New startup artifacts record `bindings.package_archive`: the SHA-256 of the
actual native application archive acquired before deployment. They also record
`bindings.package_manifest`, computed by the pinned command
`uds zarf package inspect digest <archive>`. That command verifies the package
checksums and derives a canonical manifest from its contents, definition and
provenance files. A series requires the same canonical package identity within
each architecture and retains every acquisition's archive byte hash.

These values differ from `bindings.package`, which hashes the package input
key. OCI pull reconstructs a local archive, whose filesystem metadata can
affect wrapper bytes without changing package contents. Wrapper-byte equality
is therefore not a substitute for canonical package equality. Older artifacts
without both recorded content and byte bindings are not eligible; an input key
cannot reconstruct either. Source, workflow and collector bindings must also
match.

Each pair and repetition must have fresh measured application Pod/container/JVM
identities. Resolved container image content and platform properties are
compared within an architecture. Per-install registry authorities and node
UIDs are not substitutes for content or platform equality. Original record
fingerprints remain available; matching source/package bindings do not prove
that secret values, generated configuration, physical hosts or caches were
identical across installations. A restored Core namespace UID may repeat.

The report retains every expected slot and fixed rejection reasons. Failed,
missing or invalid observations make the series incomplete; they are not
dropped to produce a passing distribution. Descriptive raw observations,
counts, minima, medians and maxima stay separate for each architecture. They
are not an architecture ranking, causal effect, production capacity estimate,
confidence interval or tuning recommendation.

## Keep timing questions distinct

The existing collector runs synchronously before and after application startup.
Cold finish follows normal deployment and validation; warm finish follows
rollout completion and old-Pod deletion. JVM metric reads in accepted records
occur after Ready. The existing JMX exporter and ordinary monitoring remain
part of the baseline environment.

Metric-read brackets can describe a limited part of collection cost. They do
not measure startup class-loading duration or establish zero indirect observer
effect. Measuring either requires a separately bounded startup experiment and
appropriate controls. This series adds no JFR recording, JVM attachment,
class-data-sharing archive, probe change or instrumentation toggle.
