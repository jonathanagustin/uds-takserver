<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Architecture port: notes for the owners of files it did not edit

The arm64 port left `provider_cache.py`, `providers.json`, `assemble/`, `stages/assemble.sh`, `stages/boot.sh`, `stages/capture.sh`, `server/`, `image/`, `README.md` and `STATUS.md` untouched. This note lists what each of them needs, or does not need, for arm64. The section below records what has run on arm64 since; the rest of the note was written before that and is marked where a run has answered it.

## Results on GitHub-hosted runners (2026-11-25)

Runs 45702964185 and 45714468510 of `.github/workflows/takx-desktop-prototype.yaml`, on `ubuntu-24.04-arm` (arm64) and `ubuntu-24.04` (amd64). Nothing below is a validation: Kernel 8.30.5 + project patches and CommonCommo 1.17.2 patched, built from the override tree, unqualified. No arm64 GUI was started.

- **arm64 providers.** All 24 passed on the first attempt with the 16 override patches as written: each recipe exited 0, its own tests and receipt checks passed, and the output was pushed to `takx-desktop-proto-ci/cache`. The second run restored all 24. Every ELF object in the 17 native provider directories is `aarch64` (`status/ci-<group>.json`, field `elf`).
- **arm64 Kernel and client.** `kernel.sh all` and `client.sh all` passed in both runs: 815 native translation units, the ELF audit, the nine Kernel controls in the runtime image, and the TAKX 5.7 compile with zero compiler errors. Both runs wrote the same Kernel JAR (`kernel-8.30.5-linux-arm64-public-cpu.jar`, SHA-256 `8ee97f9a...fa7c4`) and the same client lock, now tracked as `client/inputs.lock.arm64.json`.
- **arm64 assembly.** Fails as predicted below: `assemble: missing input .../P/kernel-composed/package/kernel-8.30.5-linux-amd64-public-cpu.jar`. The workflow keeps that step visible but does not fail the arm64 leg on it.
- **amd64 images and cache.** `GITHUB_TOKEN` cannot use the hand-built images or the hand-pushed cache: the pull of the reference toolchain digest answered `manifest unknown`, and `provider_cache.py` classified the registry's answer for `takx-desktop-proto-cache` as unauthorized. amd64 therefore built its own images and all 24 providers once under `takx-desktop-proto-ci`, and restored them in the second run.
- **amd64 Kernel and client, open.** In the second run the amd64 Kernel controls that load the native engine failed (`stock-basic`, `stock-database`, `stock-logger`, `composed`, both `coexist-*`; exit 1 after about two seconds, no marker printed), while the same controls passed on arm64 and pass in the development sandbox. The cause is not known yet; the workflow now uploads each control's log and records the processor of every job. The client compiled and packaged, but its lock differs from the tracked `client/inputs.lock.json` in four locally built JARs, because the providers were rebuilt in CI images instead of restored from the hand-pushed cache.
- **Inputs.** In the first run every job repeated the whole fetch and Maven Central answered the last amd64 job with HTTP 429. The inputs are now fetched once per architecture by the `inputs` job, stored as `takx-desktop-proto-ci/inputs:<arch>-<key>` (`ci/inputs.sh`) and restored by every other job.

## Provider cache (`provider_cache.py`, `providers.json`)

No change is required, and `providers.json` was not edited. All 24 amd64 keys are the same before and after the port (compared with `provider_cache.py key`).

On arm64 the stages run from a patched copy of the tree, `<work root>/overrides/arm64/tree` (`arch_tree.py`), and that copy holds its own `provider_cache.py` and `providers.json`. The key of an arm64 provider therefore already contains:

- the digests of the patched recipe files (`recipe_files`), so a changed override patch gives a new key;
- the path of the copy as `repo`, which no amd64 checkout has;
- the arm64 toolchain image identity.

With the amd64 toolchain identity and the same work root, none of the 24 keys derived from the arm64 tree equals an amd64 key.

Two things to know when changing the cache:

- **The arm64 key does not name the architecture.** It differs from amd64 only through the three inputs above. If you want it explicit, add `'arch'` to `key_document` only when it is not `amd64`; adding it unconditionally changes every amd64 key and the `GOLDEN` vectors.
- **`status --write-index` on arm64 writes into the tree copy**, not into the repository, because it defaults to the directory of the script. Pass a file name (for example `cache-index.arm64.tsv`) if an arm64 index is wanted.
- **`push-cache.sh` and `restore-cache.sh` have no hand-over.** They call the `provider_cache.py` next to them. On arm64 run the copies inside the tree (`<work root>/overrides/arm64/tree/takx/desktop-prototype/`), or give them the three lines `fetch.sh` has (`. "$here/arch.sh"` and `takx_arch_enter "${BASH_SOURCE[0]}" "$@"`); from the tracked tree they would derive keys of unpatched recipes that no arm64 build produces.

The workflow writes to a separate registry prefix, `takx-desktop-proto-ci/cache`, and only reads the hand-pushed `takx-desktop-proto-cache` as a second source (`ci/pipeline.py`, `TAKX_CI_CACHE_FALLBACK`).

## Assembly (`assemble/assemble.py`): blocks arm64 today

The arm64 leg of the workflow is expected to fail in its "Assemble and boot" step until these two lines know the architecture. Both names are available from `arch.sh` (`arch.sh get jogamp`, `arch.sh get kernel_jar`).

| Line | Today | Needed on arm64 |
| --- | --- | --- |
| `assemble.py:70` | `FOREIGN_NATIVES = re.compile(r'natives-(?!linux-amd64$)')` drops the aarch64 JogAmp jars | keep `natives-linux-aarch64`, drop the others (line 277 is the matching reason text) |
| `assemble.py:301` | reads `kernel-8.30.5-linux-amd64-public-cpu.jar` | the Kernel stage writes `kernel-8.30.5-linux-arm64-public-cpu.jar` on arm64; `status/kernel-composed.json` (`jar.path`) and `P/kernel-composed/receipt.json` (`outputs.jar.path`) name the file on both |

`native/linux64` (`assemble.py:368,373`, `launch.sh:30`) can stay: the stock Kernel loader selects `libs/linux64` for any 64-bit `os.arch`.

On arm64 the workflow starts `assemble.sh` and `boot.sh` from the tree copy, so their `$repo` is the copy and `run-in-toolchain.sh` mounts the base repository's `upstream/` and `.git/` into it.

## Tests that fail inside the arm64 tree by design

`takx/kernel-native/test_gdal_history.py` (4 tests) fails when run from the tree copy. It checks a frozen historical GDAL producer against the digests of the unpatched sibling recipes, which the patched copy cannot have. The prototype does not use that path (`kernel-native/build.py` is not run; fresh providers are never "historical"), and the lanes do not replay kernel-native tests. The offline tests of the other 15 patched recipes give the same result in the tree as in the tracked tree.

## Not ported

- `stages/kernel.sh kernel-baseline` (optional, not part of `all`): `kernel-native/build.py` and `toolchain.py` keep their x86_64 checks.
- `server/15-uds-cli.sh`, `30-zarf-init.sh`, `40-core.sh`, `50-tak.sh`: `amd64` asset and bundle names.
- `image/`: not reviewed for arm64.
- Image tags `takx-desktop-proto:toolchain` and `:runtime` are local, single-architecture tags. The workflow pushes `takx-desktop-proto-ci/toolchain:<arch>-<Dockerfile digest>` and pulls by digest.

## Known risks for the first arm64 run

- `kernel-scene/build.py` gives GDAL 2400 s at one job (`build.py:363,385`); the amd64 build used about 2090 s. If arm64 overruns, an override patch has to raise both literals; the lock value `gdal_stage_seconds` (`build.py:260`) must stay 2400.
- `sse2neon.h` of Kernel 8.30.5 has not been compiled with GCC 14 on aarch64.
- The aarch64 `ninja` wheel is assumed to report `1.11.1.git.kitware.jobserver-1`; the Dockerfile fails closed if it does not.
- Whether `GITHUB_TOKEN` can pull the hand-built amd64 images and the hand-pushed cache was not verified. If it cannot, the amd64 leg builds its own images and every provider once.
- A fresh amd64 CI build may not reproduce the tracked `client/inputs.lock.json` byte for byte; the client stage then stops at `lock` and prints the differing inputs.
