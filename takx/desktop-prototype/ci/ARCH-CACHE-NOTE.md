<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Architecture port: notes for the owners of files it did not edit

The arm64 port left `provider_cache.py`, `providers.json`, `assemble/`, `stages/assemble.sh`, `stages/boot.sh`, `stages/capture.sh`, `server/`, `image/`, `README.md` and `STATUS.md` untouched. This note lists what each of them needs, or does not need, for arm64. The section below records what has run on arm64 since; the rest of the note was written before that and is marked where a run has answered it.

## Results on GitHub-hosted runners (2026-11-25 and 2026-11-26)

Runs 45702964185 to 45730520989 of `.github/workflows/takx-desktop-prototype.yaml`, on `ubuntu-24.04-arm` (arm64) and `ubuntu-24.04` (amd64). Nothing below is a validation: Kernel 8.30.5 + project patches and CommonCommo 1.17.2 patched, built from the override tree, unqualified. The only arm64 GUI start is the unattended one of the boot step described below.

- **arm64 providers.** All 24 passed on the first attempt with the 16 override patches as written: each recipe exited 0, its own tests and receipt checks passed, and the output was pushed to `takx-desktop-proto-ci/cache`. The second run restored all 24. Every ELF object in the 17 native provider directories is `aarch64` (`status/ci-<group>.json`, field `elf`).
- **arm64 Kernel and client.** `kernel.sh all` and `client.sh all` passed in both runs: 815 native translation units, the ELF audit, the nine Kernel controls in the runtime image, and the TAKX 5.7 compile with zero compiler errors. Both runs wrote the same Kernel JAR (`kernel-8.30.5-linux-arm64-public-cpu.jar`, SHA-256 `8ee97f9a...fa7c4`) and the same client lock, now tracked as `client/inputs.lock.arm64.json`.
- **arm64 assembly.** Failed as predicted below in the first five runs: `assemble: missing input .../P/kernel-composed/package/kernel-8.30.5-linux-amd64-public-cpu.jar`. Since run 45727946867 the arm64 tree copy carries `overrides/arm64/prototype/assemble.patch` and `assemble.sh` passes there: 547 files, 265,775,057 bytes, with `kernel-8.30.5-linux-arm64-public-cpu.jar`, the `natives-linux-aarch64` JARs of GlueGen, JOGL and JOCL and `ffmpeg-4.1.3-1.5.1-linux-arm64.jar`; the five JOCL native JARs of other platforms are listed under `left_out`. The amd64 tree of run 45730520989 has the same 547 files (269,855,653 bytes).
- **Boot step, both architectures (run 45730520989).** With the lock per lineage and the control fix (below) the amd64 leg reached assembly and boot for the first time in CI, and the arm64 leg did too. `boot.sh check` wrote `ok` on both after 298 s of JVM time: a mapped 1920x1080 window titled `TAKX 5.7 SNAPSHOT : ... (Developer's Edition)`, a non-blank frame (YMIN 16, YMAX 235) and no fatal JVM marker. The arm64 log says `OS : Linux, version 7.0.0-1017-azure, arch aarch64` and `llvmpipe (LLVM 19.1.7, 128 bits)`; amd64 says `256 bits`. The frames kept as `ci-receipts/boot/boot-1-check.png` show the main window with the globe on both, and the two logs have the same error lines (the absent `tak-assets/png` toolbar icons and nine KMZ layer definitions that are not ZIP files). This is one unattended start per architecture in a container under Xvfb with software GL: nothing was clicked, no TAK Server was connected, no physical display or GPU was involved, and it qualifies neither the renderer nor the client on arm64 or amd64. The boot group had to wait before its check (`boot.sh check` needs a JVM older than 120 s); in run 45727946867 it checked at once and failed with "JVM age 0s".
- **amd64 images and cache.** `GITHUB_TOKEN` cannot use the hand-built images or the hand-pushed cache: the pull of the reference toolchain digest answered `manifest unknown`, and `provider_cache.py` classified the registry's answer for `takx-desktop-proto-cache` as unauthorized. amd64 therefore built its own images and all 24 providers once under `takx-desktop-proto-ci`, and restored them in the second run.
- **amd64 Kernel and client (resolved below).** In the second and third run (45721036467) the six amd64 Kernel controls that load the native engine failed (`stock-basic`, `stock-database`, `stock-logger`, `composed`, both `coexist-*`), each with `java.lang.AssertionError: real GDAL JNI unavailable` at `KernelRuntimeSmoke.java:74`, after `EngineLibrary.initialize()` had passed its own checks and without the binding's "Native library load failed." message. The same controls pass on arm64 in CI and on amd64 in the development sandbox. The nine native libraries of the amd64 CI Kernel have the same SHA-256 as the sandbox ones (`libgdalalljni.so` `490d9762...`, `libtakengine.so` `f24e8800...`), so the difference is on the Java side or in the run-time environment: the locally built `gdal-2.4.4-mod-java.jar`, `assimp-4.0.1-mod-jassimp-java.jar` and `jcommoncommo.jar` have different digests in the sandbox, in amd64 CI and in arm64 CI. The cause is not known. The workflow uploads each control's log, the tree the stock loader unpacked, and the `gdalJNI.class` the controls ran with, and records the processor of every job (the failing amd64 job ran on an Intel Xeon 6975P-C). The client compiled and packaged, but its lock differs from the tracked `client/inputs.lock.json` in those three JARs and in the composed Kernel classes JAR; the latter is the same in amd64 CI and arm64 CI, so the tracked amd64 lock predates the current Kernel Java sources.
- **amd64 Kernel controls, cause found (run 45726536612).** `ci/diagnose-controls.sh` showed the stock loader extracting into `.../takkernel/engine/4d88bf97.../lib/linux64` while the controls had put `.../takkernel/engine/04d88bf97.../lib/linux64` on `java.library.path`. `EngineLibrary.getRuntimeDirectory()` names the directory `HashingUtils.md5sum(jar)`, and that method of Kernel 8.30.5 removes one leading zero from the MD5 hex string; `kernel/controls.py` and `kernel/compose.py` used Python's full digest. The amd64 CI Kernel JAR is the first one whose MD5 starts with `0` (one JAR in sixteen does). The natives still load, by absolute path, but the GDAL binding's own `System.loadLibrary("gdalalljni")` finds nothing on the library path, and `EngineLibrary.initialize()` silences standard error around that first use, which is why no message was printed. Both files now apply the loader's rule (`stock_loader_directory_name`); the compiled `HashingUtils` of the composed JAR gives the same names for digests with no, one and two leading zeros. **The same prediction is in `takx/sdk-package/build.py:303` and `takx/sdk-package/run_hosted_controls.py:695,750`, which this port does not edit**: a Kernel JAR whose MD5 starts with `0` will fail `--phase qualify` there in the same way.
- **amd64 client lock, per build lineage.** The four differing entries are JARs built by the provider stages (their manifests and entries carry the build time) and the composed Kernel classes JAR. The amd64 leg wrote the same lock in three runs (SHA-256 `adbb4402...96c1`); it is tracked as `client/inputs.lock.amd64.ci.json` and selected by `TAKX_CLIENT_LOCK_LINEAGE=ci`, which `ci/pipeline.py` sets whenever the hand-built images were not usable. `client/inputs.lock.json` is unchanged and still applies to the hand-built providers. The arm64 lock was renamed to `client/inputs.lock.arm64.ci.json` for the same reason.
- **Inputs after a key change (runs 45724029364, 45726536612).** A JSON file added below `takx/` gave the inputs a new key, the amd64 inputs job fetched everything again and Maven Central answered HTTP 429 for 150 required files. A fetch for a new key now starts from `cache/` and `jars/runtime-extras/` of the newest stored inputs (`ci/inputs.sh seed`), and `fetch_inputs.py` asks the Google-hosted Maven Central mirror once after a 429 (`fetched_from` in `status/fetch.json`). In the next run both architectures seeded 435 cache files and verified 553 of 566 records, as a full fetch does, without a 429.
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

## Assembly (`assemble/assemble.py`): amd64-only, patched in the arm64 tree copy

The tracked assembler cannot assemble arm64 until these two lines know the architecture. Both names are available from `arch.sh` (`arch.sh get jogamp`, `arch.sh get kernel_jar`).

| Line | Today | Needed on arm64 |
| --- | --- | --- |
| `assemble.py:70` | `FOREIGN_NATIVES = re.compile(r'natives-(?!linux-amd64$)')` drops the aarch64 JogAmp jars | keep `natives-linux-aarch64`, drop the others (line 277 is the matching reason text) |
| `assemble.py:301` | reads `kernel-8.30.5-linux-amd64-public-cpu.jar` | the Kernel stage writes `kernel-8.30.5-linux-arm64-public-cpu.jar` on arm64; `status/kernel-composed.json` (`jar.path`) and `P/kernel-composed/receipt.json` (`outputs.jar.path`) name the file on both |

Until then the arm64 tree copy is given both changes by `overrides/arm64/prototype/assemble.patch`, which `arch_tree.py` applies to the copy of `assemble.py` only (the tracked file is not touched, and amd64 never sees the patch). It is deliberately not a condition of the tree: if `assemble.py` changes so that a hunk no longer applies with fuzz 0, the copy keeps the tracked file, the tree receipt and the job summary say `NOT applied`, and only the arm64 assembly step fails, as it did before. Delete the patch when the two lines read `arch.sh`.

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

Written before any run; the results section above answers each of them (GDAL took 698 s of its 2400 s, `sse2neon.h` compiled, the ninja wheel reported the expected version, `GITHUB_TOKEN` cannot read the hand-built packages, and the amd64 lock is now per lineage).

- `kernel-scene/build.py` gives GDAL 2400 s at one job (`build.py:363,385`); the amd64 build used about 2090 s. If arm64 overruns, an override patch has to raise both literals; the lock value `gdal_stage_seconds` (`build.py:260`) must stay 2400.
- `sse2neon.h` of Kernel 8.30.5 has not been compiled with GCC 14 on aarch64.
- The aarch64 `ninja` wheel is assumed to report `1.11.1.git.kitware.jobserver-1`; the Dockerfile fails closed if it does not.
- Whether `GITHUB_TOKEN` can pull the hand-built amd64 images and the hand-pushed cache was not verified. If it cannot, the amd64 leg builds its own images and every provider once.
- A fresh amd64 CI build may not reproduce the tracked `client/inputs.lock.json` byte for byte; the client stage then stops at `lock` and prints the differing inputs.
