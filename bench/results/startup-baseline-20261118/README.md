<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->
# AMD64 startup baseline after the runtime and trust changes

[Run 44398702032](https://github.com/jonathanagustin/uds-takserver/actions/runs/44398702032), attempt 1, source `d044a34b3278f388014e599f88253d0097c83633`, completed one accepted cold/warm application pair in job 135135600561. The source includes the accepted Temurin runtime and bounded trust initialization. JVM, probe and load settings were not tuned for this run.

The source pair validator accepted the original records and was independently replayed with matching helper bytes before curation. The timestamp replay below derives durations from original timestamps and official JVM start epochs. The four application rows belong to one pair, not four independent trials.

| Architecture | Boundary | Application | Paired pull (s) | Wait deps | Trust | OIDC | Container→JVM | JVM→log | Log→Ready |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| amd64 | cold | takserver-api | 0.000 | 32.000 | 43.000 | 0.000 | 1.202 | 65.251 | 6.546 |
| amd64 | cold | takserver-messaging | 0.000 | 32.000 | 43.000 | 0.000 | 1.274 | 56.725 | 5.001 |
| amd64 | warm | takserver-api | 0.000 | 4.000 | 44.000 | 0.000 | 0.343 | 66.305 | 1.352 |
| amd64 | warm | takserver-messaging | 0.000 | 5.000 | 44.000 | 0.000 | 0.338 | 54.385 | 4.276 |

| Architecture | Cold Pod span (s) | Warm Pod span (s) |
| --- | --- | --- |
| amd64 | 151.000 | 120.000 |

Pod span runs from the earliest measured application Pod creation to the latest application Ready timestamp in that episode. It is not deployment-command wall time. Phases overlap across applications; do not sum the columns. Init timestamps have whole-second resolution; zero OIDC duration does not mean zero work. Zero paired pull time does not establish cache state, absent network pulls or zero actual pull time.

Trust initialization occupied 43–44 seconds in these four observations. The pair does not isolate why, establish a startup improvement, or support a causal comparison with earlier sources. Several runtime inputs changed, and the earlier samples were different executions. This is a source-bound baseline, not a controlled repeat distribution, ISA ranking, class-loading-duration result or observer-overhead experiment. No CDS/probe benefit, memory/bytes tradeoff or production-capacity claim follows.

## Evidence and replay

The original six JSON files are unchanged under `records/amd64/`, with the exact downloaded `artifact.zip`. Its SHA256 matches GitHub artifact metadata for 14472627384. `sources.json` binds every file, API metadata snapshot, original source commit and four frozen workflow/collector/observer source files. Both matching source package jobs completed before dispatch; the workflow acquired only the matching published application package. Bindings retain the acquired archive byte hash separately from canonical package content identity.

Cold and warm records retain distinct application Pod and JVM identities, matching within-pair image/configuration bindings, and independently checked timestamp order. Their incarnations also differ from the earlier curated AMD64 pairs. A restored Core namespace UID can repeat across executions; these records do not prove distinct physical hosts, a fresh cache, or configuration/secret equivalence across installs. Earlier failed qualifications retain their original status and source scope; no ARM measurement was requested in this run.

From the repository root:

```sh
python3 -B bench/results/startup-baseline-20261118/check.py
python3 -B bench/results/startup-baseline-20261118/check.py --table
```

The checker reuses the hash-pinned historical independent timing reader, validates exact ZIP/record bytes, source/run/workflow bindings, successful real execution steps, fresh incarnations and timestamp calculations. Frozen source snapshots are evidence and are not executed by this checker. Offline replay checks the retained provenance; it does not independently authenticate GitHub or establish future source/runtime acceptance.

Refs #48. Controlled repeats, class-loading and overhead evidence, and a measured safe tuning tradeoff remain open.
