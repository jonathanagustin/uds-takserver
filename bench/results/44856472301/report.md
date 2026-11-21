### Counterbalanced PostgreSQL session repeats

Each native runner retains one database and shared data/filesystem-cache history. Every session resets the five GUCs and restarts PostgreSQL, then warms 50 live clients for at least 300 s before 1200 s measured. Cyclic configuration order balances ordinal position, not all carryover effects. These are session repeats, not independent installs, causal tuning effects or an ISA ranking. Incomplete or identity-invalid nine-session architecture blocks cannot enter the front.

| Architecture | Configuration | Valid repeats / planned | Received messages/s median [range] | Mean ping-pong ms median [range] |
| --- | --- | --- | --- | --- |
| amd64 | baseline | 3 / 3 | 494.977 [494.968, 495.494] | 445.814 [436.416, 468.674] |
| amd64 | postgis-buffers | 3 / 3 | 495.329 [495.095, 495.443] | 444.004 [435.540, 454.316] |
| amd64 | postgis-wal | 3 / 3 | 495.244 [495.178, 495.262] | 438.340 [432.944, 438.462] |
| arm64 | baseline | 3 / 3 | 495.427 [495.421, 495.596] | 474.632 [470.507, 476.548] |
| arm64 | postgis-buffers | 3 / 3 | 495.427 [495.136, 495.791] | 463.528 [457.217, 475.913] |
| arm64 | postgis-wal | 3 / 3 | 495.287 [495.284, 495.398] | 464.159 [457.785, 470.235] |
