<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Matched counterbalanced PostgreSQL study on AMD64 and ARM64

[Run 44856472301](https://github.com/jonathanagustin/uds-takserver/actions/runs/44856472301) completed all 18 sessions (nine AMD64 and nine ARM64) on attempt 1 at exact source `83c1262b9059de6fa512f357eff87d16f9adf93d`. [sources.json](sources.json) binds the workflow, jobs, verified GitHub artifact ZIP digests, all 18 original record hashes, and validation code. [records-amd64.zip](records-amd64.zip), [records-arm64.zip](records-arm64.zip), and [bench-front.zip](bench-front.zip) contain the original GitHub artifacts. Both architecture blocks pass `bench.postgis_study.complete` with zero recorded collection caveats. [report.md](report.md) contains the generated summary table.

Each configuration ran three counterbalanced sessions at 50 clients on one retained PostgreSQL 18.6/PostGIS 3.6.4 database per architecture (300 s warmup, 1200 s measured).

Across the matched revision, received throughput overlaps across both architectures and all three configurations (`494.977`–`495.427` msg/s medians). Observed mean ping-pong latency medians were lower on AMD64 (`438.340`–`445.814` ms) than ARM64 (`463.528`–`474.632` ms), while observed TAK container startup was faster on ARM64 (`40.311` s messaging / `47.656` s API vs. `59.439` s / `70.298` s on AMD64). Within each architecture, throughput and latency ranges across the three PostgreSQL configurations remain close; keep the current production defaults. Representative-load validation remains open in [#49](https://github.com/jonathanagustin/uds-takserver/issues/49) and cross-architecture Pareto tracking in [#51](https://github.com/jonathanagustin/uds-takserver/issues/51).
