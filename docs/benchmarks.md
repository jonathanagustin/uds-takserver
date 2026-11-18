# Benchmarks

The Benchmark workflow measures how much processor time and memory TAK Server uses under a Team Awareness Kit (TAK) client load, and how long it takes to become ready, for several resource configs. The nightly run on `main` has completed on both architectures since November 2, 2026, for example [run 41613491855](https://github.com/jonathanagustin/uds-takserver/actions/runs/41613491855). Those runs use 10 clients for 60 seconds, once per config. Treat the front as a first look, not a sizing decision. The results are in the run's `bench-front` artifact and its job summary. This page describes the harness.

## When it runs

The [retained ten-client snapshot](../bench/results/42361079121/README.md)
contains matched baseline, Postgres buffers and Postgres write-ahead log (WAL) records from
November 7, 2026, on both architectures, with source hashes and a corrected
report. It records why those observations do not justify a tuning change,
architecture default, or a 200-client load.

[`.github/workflows/benchmark.yaml`](../.github/workflows/benchmark.yaml) runs:

- Mondays at 09:41 Coordinated Universal Time (UTC)
- by hand (`workflow_dispatch`)
- on a pull request labeled `benchmark`

It runs on amd64 and arm64 runners. The weekly run uses client count 10. To run more, set the workflow input `clients` to a comma-separated list such as `10,50,200`, or to `all` for every count a config lists.

Manual runs also accept `profile`: `all` (the default) keeps the full sweep;
`baseline` measures only the unchanged install, without tuning rollouts.
With `clients=10,50`, baseline produces two points per architecture instead of
fourteen across the seven enabled profiles. Installation and its tests still
run on both architectures. Scheduled and labeled runs keep the full sweep.
This option does not establish 50-client headroom or relax the gate on 200 clients.
The 60-second window includes clients starting one second apart. Requested
clients and observed connection events do not establish sustained concurrency.
New records identify their cumulative parent-snapshot counters; older interval
reports could omit late clients and final partial intervals. See the
[counter provenance](../bench/SCHEMA.md#counter-window-and-provenance) before
comparing results.

## What one run does

1. Installs the current branch with `uds run test-install`, with this commit's TAK, CloudTAK, and PostGIS images.
2. Fetches the upstream TAK load tester (`src/testing/load_test` from the same commit as the `upstream/takserver` submodule, `5.7-RELEASE-14`) and installs its pinned Python requirements from `bench/load-test-requirements.txt`.
3. Runs `bench/run.py`. For each config in `bench/configs/` that is not marked `skip: true`, and for each selected client count, it applies the config to the running cluster, runs the load, and writes `bench/out/<arch>/<config>.json`. A second or later client count adds `-c<count>` to the file name.
4. Uploads the records as the `bench-<arch>` artifact. If the install or the sweep fails, it also uploads the cluster debug output and logs as `debug-log-bench-<arch>-<run>-<attempt>`, with passwords masked by `.github/scripts/redact-logs.py`.
5. The `benchmark-summary` job runs `bench/report.py` on all the records it finds, so one failed architecture still leaves a front for the other. It fails when neither architecture uploaded a record. It writes `front.md` and `front.svg` and uploads them as the `bench-front` artifact. The report is also shown in the job summary.

Client certificates for the load are built at run time from `tests/extract-certs.sh`. They are not written into the repository or an image.

## Configs

Each file in [`bench/configs/`](../bench/configs/) changes memory settings, Java virtual machine (JVM) flags, or Postgres settings on the running cluster. Before the next config, `bench/run.py` puts the previous config's changes back. Each lists client counts 10, 50, and 200 and a 60-second load.

| Config | What it changes |
| --- | --- |
| `baseline` | Nothing. The test bundle's current requests, limits, and JVM flags. Its cold time-to-ready is the install itself, with no rollout restart |
| `heap-768` | Sets `JDK_JAVA_OPTIONS` to `-Xms768m -Xmx768m` on the application programming interface (API), messaging, and config containers |
| `postgis-memory` | Raises the CloudTAK PostGIS memory request to 512Mi and limit to 1Gi |
| `postgis-buffers` | TAK Postgres `shared_buffers` 128MB, `work_mem` 4MB, `effective_cache_size` 384MB, `max_wal_size` 256MB, `checkpoint_timeout` 5min, through `ALTER SYSTEM` and a restart |
| `postgis-wal` | A smaller cache and fewer checkpoints than `postgis-buffers`: `shared_buffers` 64MB, `effective_cache_size` 256MB, `max_wal_size` 128MB, `checkpoint_timeout` 15min |
| `small` | Memory requests and limits from the `small` [values profile](profiles.md) |
| `production` | Memory requests and limits from the `production` [values profile](profiles.md) |
| `gc-serial` | Uses the serial collector for garbage collection (GC) on the API, messaging, and config containers. Skipped: the image already passes `UseG1GC`, and a second collector makes the JVM exit |

The configs set memory, JVM flags, and Postgres settings only. Central processing unit (CPU) allocation stays at whatever the install set. Beside the load, each record holds the Postgres counters (`pg_stat_database`, `pg_stat_checkpointer`, `pg_stat_io`) and the `postgresql` container working set. The harness resets those counters before each config. Earlier runs, such as run 41613491855, did not reset the checkpoint counters or the `cot` database counters. In those runs the values add up from one config to the next.

## The record and the report

[`bench/SCHEMA.md`](../bench/SCHEMA.md) defines every field in a record and how it is collected. A field that could not be measured is `null`, with the reason in `errors`. It is never stored as zero.

The report compares configs on three views: footprint against throughput, footprint against mean latency, and footprint against warm rollout duration. The initial cold install age is copied into every config as a shared reference, so it stays in the evidence table and never ranks swept configs. Cold and warm readiness measurements do not compete. Each view is split into cohorts with the same source revision, client count and load duration. Records missing one of those fields are isolated, so an unknown workload cannot dominate another record. A config is on the Pareto front for a view when no other config in the same cohort and on the same architecture is at least as good on both axes and better on one. Dominated configs are drawn as open circles. Both architectures share one scale on each cohort's plot, but dominance is decided inside one architecture. See [architecture decision record (ADR) 19](../adr/0019-compare-arches-on-one-scale.md).

The markdown report includes the observed CPU, working set, throughput, latency, startup phases, Postgres counters and record caveats. Missing timings stay "not measured"; negative or non-finite timings are shown as invalid and never treated as durations. The source records are unchanged. A startup-phase row describes the pod present when that config was collected. A Postgres-only rollout can reuse an earlier TAK pod, so repeated rows are not independent cold and warm TAK startup measurements.

A setting is copied into the package only after a report puts it on the front. The nightly records are one 10-client run per config. Do not treat any config, or either [values profile](profiles.md), as a sizing result yet.

## Collect an exploratory PostgreSQL comparison

The manual Benchmark choice `study=synthetic-postgis` runs the existing
`baseline`, `postgis-buffers` and `postgis-wal` configurations on one native
runner per architecture. It fixes the client count at 50 and ignores the
short-sweep `profile` and `clients` selections. The scheduled run, default
short sweep and `synthetic-baseline` retain their existing behavior. This
option prepares the controlled measurements for issues #49 and #51; no
PostgreSQL experiment or tuning result is supplied by adding it.

After reviewing and merging the protocol, a maintainer can dispatch
`benchmark.yaml` on `main` with this body:

```json
{"ref":"main","inputs":{"study":"synthetic-postgis","jvm_start_metadata":false}}
```

Both native jobs use the same source and tester. Each retains its own run,
attempt, context, cluster, node-container and marker identity. The disposable
cluster guard refuses an existing `uds` cluster before installation and checks
the same proof before every session. Each session's file and metadata identify
its configuration, repeat, ordinal position and sequence number. Existing
session files cause refusal before work begins.

Each repeat rotates configuration order:

| Repeat | First session | Second session | Third session |
| --- | --- | --- | --- |
| 1 | baseline | buffers | WAL |
| 2 | buffers | WAL | baseline |
| 3 | WAL | baseline | buffers |

Before every session, including baseline, the harness resets `ALTER SYSTEM`
overrides, applies that point's five settings and restarts PostgreSQL. The
postmaster must change while the database system identifier and database OID
remain stable. The normalized settings, durability, database identity and
resource limits are read back before load and at the measured boundaries.
Across sessions, the retained complete container inventory must keep loaded
images and memory limits unchanged. Application Pod/container identities and restart
counts remain fixed; only PostgreSQL changes incarnation intentionally.
Expected source defaults are PostgreSQL 18.6/PostGIS 3.6.4 with buffers 128MB,
work memory 4MB, effective cache 4GB, maximum WAL 1GB and checkpoint timeout
5min; a different runtime requires reviewing the protocol before acceptance.

The two candidate presets already exist. Buffers keeps 128MB shared buffers
and 4MB work memory, changes the planner's cache hint to 384MB, and limits WAL
to 256MB with a 5min checkpoint timeout. WAL uses 64MB shared buffers, 4MB
work memory, a 256MB cache hint, 128MB maximum WAL and a 15min timeout. These
are exploratory planning/checkpoint hypotheses. The retained baseline's full
cache-hit ratio and zero temporary files do not establish a cache or work-memory
bottleneck. A longer timeout and smaller maximum WAL can have competing
effects on checkpoint frequency.

Neither preset raises shared buffers or work memory above the recorded
baseline, but this does not establish a worst-case memory bound: work memory
can be used by multiple operations, and other PostgreSQL allocations remain.
The database must keep its actual 512Mi memory and 500m CPU limits, with
`fsync`, `full_page_writes` and `synchronous_commit` all on. The existing
readiness, restart, OOM and container-sample checks remain required. The
CloudTAK `postgis-memory` preset and other resource/JVM configurations are
excluded. No production configuration, database image or durability setting
is changed by the protocol source.

Each session holds 50 observed clients through at least 300s of steady warmup
and 1200s measured, with the existing ramp, workload-fidelity, counter-delta,
sample and SQL-bracket checks. Nine sessions require 225 steady workload
minutes per architecture, plus ramp and setup. This manual mode alone has a
360min job ceiling, a 90min install deadline, and a 120s PostgreSQL rollout
wait with a 130s child-process deadline. The first invalid session stops that
architecture without an automatic retry; its raw record remains available.

Database contents, host/filesystem caches and the application processes are
shared across the nine sessions. A PostgreSQL restart resets its process and
buffer-pool state, while database contents and operating-system caches persist.
The protocol records database size and system/postmaster identity; it does
not restore a common data snapshot. Rotating each configuration through each
ordinal position balances order while retaining possible carryover effects.
These are repeated client sessions in a shared history. They do not establish
independent cluster replication, individual parameter causality, production
capacity or an architecture ranking.

The report groups medians/ranges by architecture and configuration. A complete
architecture block needs all nine distinct accepted source-bound sessions;
partial or identity-invalid blocks remain inspectable and cannot enter the
front. Earlier baseline-only and short-window records keep their report format
and separate cohorts. Retain the raw files, original failures, artifact ZIP and
file hashes, source and run/attempt metadata when curating results. No preset
is adopted automatically; keeping the defaults is an allowed outcome. Upgrade
and backup/restore qualification remain separate requirements.

## Collect one startup pair without load

The manual-only [Startup pair workflow](../.github/workflows/startup.yaml) runs
this procedure from `main`, with a 60-minute ceiling per selected native runner.
Its `architecture` input defaults to `amd64`; select `arm64` to qualify ARM
without repeating AMD, or `both` for independent pairs on both architectures.
ARM uses `ubuntu-22.04-arm`; AMD retains the `TEST_RUNNER` override. A native
machine check precedes setup and cluster work. Each architecture has its own
cohort and artifact, and a failed architecture cannot count as a qualified pair.
It requires an already published package matching the keyed application inputs;
it fails before cluster work when that package is missing. It has no schedule,
pull-request trigger, load test, or automatic retry. An active run is preserved.

Rejected startup observations report a fixed failure kind and the collector's
module, source line and source-file hash. Exception values and resource bodies
are omitted. This diagnostic identifies the failed collector callsite; it is
not an accepted startup measurement or a reason to relax identity/timing checks.

`python3 -m bench.startup_trials` records one application install and one
subsequent rollout on the same disposable cluster. It does not install,
restart, remove, or tune anything. It adds no scheduled or pull-request run.
Use it between existing deployment steps, after Core is ready but before the
TAK application is installed. Recreating Core between observations changes
the node identities and invalidates the pair.

Create an owner-only output directory with `mkdir -m 700 startup-evidence`.
Place `bindings.json` there with a `source` SHA-256 binding and any other
named input hashes, for example `config`. These are caller-supplied bindings,
not an attestation that the checkout or running images belong to that source.
Use the same file, context, and cohort for both observations.

```sh
python3 -m bench.startup_trials begin --context k3d-uds --cohort startup-a \
  --bindings startup-evidence/bindings.json --output startup-evidence/cold-begin.json
# Perform the existing application deployment and wait for its readiness checks.
python3 -m bench.startup_trials finish --begin startup-evidence/cold-begin.json \
  --mode cold --output startup-evidence/cold.json
python3 -m bench.startup_trials begin --context k3d-uds --cohort startup-a \
  --bindings startup-evidence/bindings.json --output startup-evidence/warm-begin.json
# Restart both application Deployments using the existing rollout procedure.
python3 -m bench.startup_trials finish --begin startup-evidence/warm-begin.json \
  --mode warm --output startup-evidence/warm.json
python3 -m bench.startup_trials pair --cold startup-evidence/cold.json \
  --warm startup-evidence/warm.json --output startup-evidence/pair.json
```

The cold boundary must contain neither application Pods nor Deployments.
The warm boundary must match the accepted cold result. Both new application
Pods and Java virtual machines (JVMs) must have new identities, with unchanged
application, sidecar, and init-container image digests, deployment configuration,
referenced configuration resources, and node platform. Startup log, numeric JVM-start, init, and Ready timestamps
must join exactly; missing evidence rejects the pair. `warm_s` is the span from
the earliest new application Pod creation to the latest application Ready
transition, not the time taken by the rollout command.

Records contain numeric telemetry, typed identities, timestamps, and hashes.
Secret reads request only their unique identifiers and resource versions;
configuration values and raw logs are not stored. Kubelet image inventory and
pull-event projections describe observed image-cache evidence. Pull duration is
the sum of observed complete event pairs; missing pairs stay unmeasured and do
not prove zero pull time. Every reported phase duration is recomputed from its
typed evidence. Numeric samples must fall inside the finish observation window,
with ordered brackets and consistent wall-clock/monotonic elapsed times.
The image observations do not prove that filesystem or operating-system caches
were cold. Class counts are
observed after startup, not class-loading durations. A valid pair alone does
not justify class data sharing, probe tuning, or a performance improvement;
repeated measurements and observer-overhead checks remain necessary.

The [repeat-series protocol](startup-series.md) describes how to retain and
validate three to five original pairs per selected architecture, including
failed and missing observations. Its offline checks do not run a study or
qualify class-loading duration, instrumentation overhead or tuning benefits.

## Check capacity before a larger load

New records retain `capacity.containers` from the same five-second kubelet
polls as the aggregate working set. Each row has the namespace, pod UID and
container name, sampled memory/CPU peaks and last values, source timestamps,
and before/after memory limits, readiness, restart counts and termination
reasons. Older records do not have this evidence. See the
[record schema](../bench/SCHEMA.md#per-container-capacity-evidence).

For the 50-client point, inspect `takserver-messaging` on both architectures
before attempting 200 clients. Compare its sampled working-set peak with its
memory limit, require headroom, and check that its UID, limit, healthy state and
restart count stayed stable. Review both termination-reason fields for OOMs.
Do not infer messaging's headroom from the total TAK and CloudTAK working set.
Missing data, a changed pod or limit, restarts, or insufficient samples leave
the gate unresolved. Polling can miss a transient peak, so a small observed
margin is not evidence that a larger load fits.

The collector bounds its stored identities and retains aggregates, not a
time-series dump. It changes neither client-count defaults nor workload limits.

Startup command-failure diagnostics publish only a fixed Kubernetes operation,
a fixed error-pattern category and bounded numeric exit status. The collector
keeps at most 4 KiB of stderr in memory for classification and discards its raw
bytes. Arguments, resource names, URLs, command output and exception values are
not published. The original request, stream, process-group and total deadline
bounds remain enforced. Pattern categories are observations, not causal proof or
accepted startup timing.

When the startup pilot rejects an unavailable JVM observation or phase join,
its failure diagnostic retains the fixed application, observation stage and
allowlisted observer reason. For example, `marker_after_ready` remains a timing
rejection; the collector does not round Ready timestamps or accept a pair from
that failure. Unknown reasons become `unsupported_schema`, and raw observer
payloads, log lines and exception values are not published. These diagnostics
identify the rejected check, not the cause of a historical pilot failure.

The startup configuration snapshot honors an explicitly boolean `optional: true`
on ConfigMap references, including the admission module's optional
`uds-trust-bundle`. A successful named lookup with `--ignore-not-found` may bind
absence; present objects retain UID, resource-version and content hashes. Required
references take precedence over optional references to the same name. Permission,
transport and other command failures still fail, and appearance or disappearance
between cold and warm snapshots changes the configuration binding.


## Additional November 15 startup execution

The [retained AMD64 repeat](https://github.com/jonathanagustin/uds-takserver/tree/main/bench/results/startup-repeat-20261115)
adds one source-qualified cold/warm application-start pair: Pod spans 170/130
seconds, compared with the earlier AMD64 pair's 140/100 seconds. The restored
Core namespace UID matches the earlier execution; run/cohort and application
Pod/JVM identities establish additional execution. ARM64 failed Core restoration
before measurement. The report retains original records and metadata and does
not infer a tuning benefit, cache effect, startup distribution or ISA ranking.
