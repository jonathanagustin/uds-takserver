<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->
# Native startup pairs from run 44702615950

[Workflow run 44702615950](https://github.com/jonathanagustin/uds-takserver/actions/runs/44702615950), attempt 1, ran at source `d10aaae3974fb4934f16e8f157548bd0ad528132`. Both native jobs passed. Each archive contains one cold/warm application pair.

| Architecture | Job ID | Artifact ID | Cold Pod span (s) | Warm Pod span (s) |
| --- | --- | --- | --- | --- |
| AMD64 | 136174427822 | 14615046366 | 150 | 121 |
| ARM64 | 136174427795 | 14614821160 | 130 | 101 |

Pod span runs from the earliest application Pod creation to the latest application Ready timestamp within the episode. These pairs record observed startup timing. Controlled repeats, observer overhead, class-loading duration, and tuning tradeoffs remain open in [#48](https://github.com/jonathanagustin/uds-takserver/issues/48).

The archives match GitHub's published SHA-256 digests. Run the pair validator from the repository root to reproduce both acceptance records.

```sh
set -eu
records=bench/results/44702615950
sha256sum -c "$records/SHA256SUMS"
for arch in amd64 arm64; do
  data=$(mktemp -d)
  unzip -q "$records/startup-$arch.zip" -d "$data"
  python3 -B -m bench.startup_trials pair --cold "$data/cold.json" --warm "$data/warm.json" --output "$data/recheck.json"
  cmp "$data/recheck.json" "$data/pair.json"
done
```

## Same-source repeat

[Attempt 2](https://github.com/jonathanagustin/uds-takserver/actions/runs/44702615950/attempts/2)
completed on both architectures at the same source commit. Each architecture
has the same workflow, package input key, and package manifest digest as in
attempt 1. Each archive retains the checksum of the package wrapper that its
job actually acquired.

| Architecture | Artifact ID | Cold Pod span (s) | Warm Pod span (s) |
| --- | --- | --- | --- |
| AMD64 | 14659014309 | 161 | 130 |
| ARM64 | 14659925279 | 130 | 111 |

The ZIPs match their published Actions digests. To replay these pairs, use the
command above with `startup-$arch-attempt-2.zip` as the archive name. The two
pairs per architecture show observed variation across independent jobs.
Observer overhead, isolated class-loading duration, and tuning remain open.
