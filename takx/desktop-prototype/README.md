<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# TAKX 5.7 desktop prototype (opt-in)

This directory is an opt-in recipe that builds the official TAKX 5.7 desktop client (revision `0b59a9bb2d0e90dc35c9edfff1c2056daab2b713`) from public sources only and boots it on Linux `amd64` under Xvfb with Mesa `llvmpipe`.

It is a prototype, not a supported product build. The client runs on substitutes for every private artifact TAKX 5.7 declares:

| TAKX 5.7 declares (private) | The prototype uses (public) |
| --- | --- |
| TAK Kernel 8.57.11 | Kernel 8.30.5 (atak-civ `9f6893dd`) plus project patches (`takx/kernel-texture`, `takx/kernel-render-context`, feature and elevation JNI patches), unqualified, plus a three-patch Java overlay (below) |
| CommonCommo 1.20.0 | CommonCommo 1.17.2 with the project JNI patch |
| WorldWind 2.2.1-TAKX-4 | NASA WorldWind Java v2.2.1 with the interop profile of `takx/worldwind-java` |
| DockingFrames 1.1.3p1, colorchooser 1.1, swing-toast 1.0.3, NetBeans wizard, svgSalamander | source rebuilds by `takx/ui-java` |
| `tak-assets` 1.2 | only the files that exist in public atak-civ; the `png/` toolbar icons are absent |
| `border-shapefiles` 1.0.0, PostgreSQL bundle | absent (hsqldb is the default database) |

Nothing here is relabelled as the private version, and none of it has renderer, SDK or client acceptance. [STATUS.md](STATUS.md) records what was run, every fix made to get the client to boot, and what does not work.

## Stages

Every stage runs in the `takx-desktop-proto` images through `run-in-toolchain.sh`, keeps its outputs under the work root (`TAKX_PROTO_ROOT`, default `/var/lib/takx-proto`), writes `status/<stage>.json` when it finishes and skips work that is already `ok`. A failed output directory is moved to `failed/`, never deleted.

| Order | Script | What it does |
| --- | --- | --- |
| 0 | `Dockerfile` | `toolchain` image (Debian trixie, GCC 14.2.0, CMake 3.31.6, SWIG 4.3.0, Temurin 17.0.21+9) and `runtime` image (adds Xvfb, openbox, xdotool, ffmpeg, Mesa) |
| 1 | `fetch.sh` | fetches and verifies every pinned input |
| 2 | `stages/lane1.sh`, `lane2.sh`, `lane3.sh` | native providers (SQLCipher, PROJ, GEOS, Assimp, LAS, MDB, Draco, Cesium, GDAL, curl, crypto, CommonCommo and others) |
| 3 | `stages/lane4.sh` | Java libraries (GIS, Kernel Java, GDAL JNI, WorldWind, UI) |
| 4 | `stages/kernel.sh` | composed Kernel: sources, Java, natives, package, controls |
| 5 | `stages/client.sh` | compiles and packages TAKX 5.7; resolves the runtime JAR closure |
| 6 | `stages/assemble.sh` | builds the runnable tree `dist/takx57` |
| 7 | `stages/boot.sh` | boots the tree and collects evidence |
| 8 | `stages/capture.sh` | records the booted client |

## Assembly

`stages/assemble.sh` runs three steps:

1. `assemble/kernel_overlay.py` applies `assemble/kernel-overlay/*.patch` with fuzz 0 to a copy of the Kernel Java sources the Kernel stage compiled, compiles only those files against the composed Kernel classes, and writes `P/kernel-overlay/kernel-8.30.5-prototype-java-overlay.jar` with a receipt. The Kernel JAR is not changed, so the Kernel controls were run without the overlay.
2. The unmodified `takx/compat/build-system-clock.py` builds `libuds_desktop_clock.so`, which the Kernel build's `android.os.SystemClock` loads.
3. `assemble/assemble.py` copies everything into `dist/takx57` and writes `manifest.json` with the source and SHA-256 of every file.

The tree mirrors the official `deployment/bin` that `gradle/binCreator.gradle` fills:

- `boot/`: `jboss-modules-1.12.0.Final.jar` and the preferences factory.
- `load/`: the bootstrap, platform, client and processmanager JARs from the client stage.
- `lib/`: the WorldWind interop JAR, named `worldwind-2.2.1-nasa-public-interop.jar`.
- `modules/`: the official `jdk` and `takx-bootstrap` descriptors, plus hand-written descriptors (below).
- `conf/`, `gazetteer/`, `resources/`, the log4j files and `template-64bit.vmoptions`: copied from the official tree.
- `resources/tak-assets/`: `txt/callsigns.txt`, `dbs/iconcache.sqlite`, `icons/` and the five `iconsets/*.zip` (Git LFS objects, verified against their pointers) from atak-civ.
- `native/linux64/`: `libcommoncommojni.so` and `libuds_desktop_clock.so`. The Kernel natives stay inside the Kernel JAR; its stock loader extracts them below `java.io.tmpdir`.

### Module graph

The official build generates one JBoss module per Maven artifact with a plugin that cannot run here. The prototype writes a coarser graph by hand:

- The names `ApplicationModuleFinder` asks the boot loader for exist exactly: `all-libs`, `jdk`, `log4j`, `org.slf4j.slf4j-api`, `org.aspectj`, `org.jboss.modules` (built into JBoss Modules), `jakarta.enterprise.jakarta.enterprise.cdi-api`, `jcommoncommo` and `gov.tak.kernel`.
- `gov.tak.kernel` holds the overlay JAR first, then the Kernel JAR and the Kernel-only dependency JARs. The GDAL and jassimp JARs are there because their JNI classes must share the class loader that maps the Kernel natives.
- `prototype.libs` holds every other third-party JAR in one module.
- `all-libs` re-exports all of them, and passes the `META-INF` path of the Kernel module through, because the Kernel build reads its resource catalog from there.
- Where the Kernel's dependency versions differ from the TAKX runtime closure (Jackson, Guava, Bouncy Castle, Kotlin, OkHttp, SLF4J and others), the TAKX closure version is used; `manifest.json` lists each case.

### Kernel Java overlay

Each patch fixes one place where booting the real client failed. All three are candidates to move into the recipes that own the code.

| Patch | Problem found at boot | Change |
| --- | --- | --- |
| `01-render-surface-identity.patch` | TAKX casts `getRenderContext().getRenderSurface()` to its own component types; the project render-context binding returned a forwarding wrapper, so the globe component was never created (`ClassCastException` in `ApplicationGlobe.makeWwd`) | the binding returns the client's surface object when it implements the legacy interface |
| `02-glutils-buffer-position.patch` | the upstream desktop `GLUtils` uploads a pixel buffer at the position `Bitmap.copyPixelsToBuffer` left it; the `takx/compat` Bitmap advances the position as Android does, so every widget texture upload failed | `rewind()` before the upload |
| `03-asset-root-module-loader.patch` | the `takx/compat` `AssetManager` serves assets only from `URLClassLoader` URLs or `java.class.path`; under JBoss Modules the Kernel JAR is neither | the JAR that holds the desktop classes is a declared root |

## Boot

```sh
takx/desktop-prototype/stages/boot.sh start                    # offline: map tile requests fail TLS verification
takx/desktop-prototype/stages/boot.sh start --trust-sandbox-ca # trust the sandbox proxy CA as well
takx/desktop-prototype/stages/boot.sh check                    # evidence into status/boot.json
takx/desktop-prototype/stages/boot.sh stop
```

`assemble/launch.sh` is the command line of the official `runTakx` task: working directory = the tree, class path `boot/*` and `conf`, every line of `template-64bit.vmoptions`, `java.library.path`, then `org.jboss.modules.Main -mp modules takx-bootstrap -create`. It adds only `-Dprojectdata.dir`, `-Djava.io.tmpdir` and `-Duser.home`, all inside the attempt's run directory. `assemble/session.sh` starts Xvfb 1920x1080x24, openbox and `LIBGL_ALWAYS_SOFTWARE=1` around it.

Each attempt gets `runs/boot-<n>/` and `logs/boot-<n>.log`. The run directory links to the tree and copies only `resources/`, because TAKX writes `resources/tak-assets/wmm_cof` at start.

Network behaviour to know about:

- The client contacts public endpoints by itself at start: `tak.gov` for map source definitions, the selected imagery server for tiles, the TAK elevation server, and `time.nist.gov`. No TAK Server is configured and no credential is entered.
- In a sandbox with a TLS-intercepting proxy those requests fail certificate verification unless `--trust-sandbox-ca` is given. Verification is never disabled.
- The default imagery layer, USGS Imagery Topo, answered HTTP 404 when this was written; USGS Imagery works.

## Capture

`stages/capture.sh all` boots a fresh attempt, records the X display losslessly with `ffmpeg x11grab` while `xdotool` drives the real UI, encodes H.264 `yuv420p` 1920x1080 30 fps files into the git-ignored `demo/out/takx/`, verifies them (`ffprobe`, `signalstats`, `freezedetect`, `blackdetect`) and extracts review frames. Media is never committed. `demo/out/takx/PROVENANCE.md` states what the footage shows and does not show.

## Files in this stage

| File | Purpose |
| --- | --- |
| `stages/assemble.sh`, `stages/boot.sh`, `stages/capture.sh` | stage drivers |
| `assemble/assemble.py` | tree assembler and manifest |
| `assemble/kernel_overlay.py`, `assemble/kernel-overlay/*.patch` | Kernel Java overlay |
| `assemble/launch.sh`, `assemble/session.sh` | launcher and X session |
