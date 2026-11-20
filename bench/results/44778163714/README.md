<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Counterbalanced PostgreSQL study on ARM64

[Run 44778163714](https://github.com/jonathanagustin/uds-takserver/actions/runs/44778163714) completed all nine ARM64 sessions on attempt 1 at source `1d2fe18c68ddc5600c21d020e737708f2630e595`. [sources.json](sources.json) binds the workflow, job, verified artifact ZIP digest, original record bytes and validation code. [records-arm64.zip](records-arm64.zip) contains the original GitHub artifact. The repository's `bench.postgis_study.complete` check accepts the full block. The three validation and tester fixture files match the measured source byte for byte. [report.md](report.md) contains the generated results.

Each configuration ran three counterbalanced sessions at 50 clients on one retained PostgreSQL 18.6/PostGIS 3.6.4 database. Sessions warmed up for at least 300 seconds and measured 1200 seconds. They share database and cache history. The counterbalanced order distributes position effects across configurations.

Throughput and mean ping-pong latency ranges overlap across all three configurations. Keep the current production defaults. Representative-load validation remains open in [#49](https://github.com/jonathanagustin/uds-takserver/issues/49). The [earlier AMD64 study](../44339321591/README.md) used a different source and runtime. A matched comparison remains open in [#51](https://github.com/jonathanagustin/uds-takserver/issues/51).
