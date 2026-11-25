### Counterbalanced PostgreSQL session repeats

Each native runner retains one database and shared data/filesystem-cache history. Every session resets the five GUCs and restarts PostgreSQL, then warms 50 live clients for at least 300 s before 1200 s measured. Cyclic configuration order balances ordinal position, not all carryover effects. These are session repeats, not independent installs, causal tuning effects or an ISA ranking. Incomplete or identity-invalid nine-session architecture blocks cannot enter the front.

| Architecture | Configuration | Valid repeats / planned | Received messages/s median [range] | Mean ping-pong ms median [range] |
| --- | --- | --- | --- | --- |
| arm64 | baseline | 3 / 3 | 495.177 [495.010, 495.393] | 456.096 [442.257, 486.973] |
| arm64 | postgis-buffers | 3 / 3 | 495.480 [495.241, 495.616] | 460.747 [452.476, 474.246] |
| arm64 | postgis-wal | 3 / 3 | 495.345 [495.240, 495.373] | 466.547 [440.264, 471.179] |
