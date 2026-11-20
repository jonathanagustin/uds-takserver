<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

### Counterbalanced PostgreSQL session repeats

Each native runner retains one database and shared data/filesystem-cache history. Every session resets the five GUCs and restarts PostgreSQL, then warms 50 live clients for at least 300 s before 1200 s measured. Cyclic configuration order balances ordinal position, not all carryover effects. These are session repeats, not independent installs, causal tuning effects or an ISA ranking. Incomplete or identity-invalid nine-session architecture blocks cannot enter the front.

| Architecture | Configuration | Valid repeats / planned | Received messages/s median [range] | Mean ping-pong ms median [range] |
| --- | --- | --- | --- | --- |
| amd64 | baseline | 3 / 3 | 495.199 [495.120, 495.395] | 433.419 [426.230, 448.134] |
| amd64 | postgis-buffers | 3 / 3 | 495.350 [495.131, 495.357] | 430.428 [401.518, 442.510] |
| amd64 | postgis-wal | 3 / 3 | 495.250 [494.951, 495.251] | 437.148 [436.243, 441.682] |

