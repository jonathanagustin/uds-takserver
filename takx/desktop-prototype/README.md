<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# TAKX 5.7 desktop prototype (opt-in)

This directory is an opt-in recipe that builds the official TAKX 5.7 desktop client (revision `0b59a9bb2d0e90dc35c9edfff1c2056daab2b713`) from public sources only and boots it on Linux `amd64` under Xvfb with Mesa `llvmpipe`.

It is a prototype, not a supported product build. The client runs on substitutes for every private artifact TAKX 5.7 declares:

| TAKX 5.7 declares (private) | The prototype uses (public) |
| --- | --- |
| TAK Kernel 8.57.11 | Kernel 8.30.5 (atak-civ `9f6893dd`) plus project patches (`takx/kernel-texture`, `takx/kernel-render-context`, feature and elevation JNI patches), unqualified, plus a Java overlay (two patches and a recipe refresh, below) |
| CommonCommo 1.20.0 | CommonCommo 1.17.2 with the project JNI patch |
| WorldWind 2.2.1-TAKX-4 | NASA WorldWind Java v2.2.1 with the interop profile of `takx/worldwind-java` |
| DockingFrames 1.1.3p1, colorchooser 1.1, swing-toast 1.0.3, NetBeans wizard, svgSalamander | source rebuilds by `takx/ui-java` |
| `tak-assets` 1.2 | only the files that exist in public atak-civ; the `png/` toolbar icons are absent |
| `border-shapefiles` 1.0.0, PostgreSQL bundle | absent (hsqldb is the default database) |
| bytedeco FFmpeg 4.1.3 natives (public, but "nonfree and unredistributable") | absent; video is a user-supplied add-on |
| `org.netbeans:wizard` | absent; no class refers to it |
| `net.sf.joost:joost`, `org.jogamp.jocl:jocl` | absent; no class refers to them |

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

`stages/assemble.sh` runs four steps. `TAKX_ASSEMBLE_NAME=NAME` assembles into `dist/NAME` and leaves `dist/takx57`, its overlay and its status files alone.

1. `assemble/kernel_overlay.py` applies `assemble/kernel-overlay/*.patch` with fuzz 0 to a copy of the Kernel Java sources the Kernel stage compiled, compiles only those files against the composed Kernel classes, and writes `P/kernel-overlay/kernel-8.30.5-prototype-java-overlay.jar` with a receipt. It also refreshes the Java sources of `takx/kernel-render-context` when that recipe's locked image is newer than the source the Kernel stage compiled (see below). The Kernel JAR is not changed, so the Kernel controls were run without the overlay.
2. The unmodified `takx/compat/build-system-clock.py` builds `libuds_desktop_clock.so`, which the Kernel build's `android.os.SystemClock` loads.
3. `assemble/derive_jar.py` writes the Kernel JAR of the tree as a derived copy of the JAR the Kernel stage built (below).
4. `assemble/assemble.py` copies everything into the tree and writes `manifest.json` with the source and SHA-256 of every file.

The tree mirrors the official `deployment/bin` that `gradle/binCreator.gradle` fills:

- `boot/`: `jboss-modules-1.12.0.Final.jar` and the preferences factory.
- `load/`: the bootstrap, platform, client and processmanager JARs from the client stage.
- `lib/`: the WorldWind interop JAR, named `worldwind-2.2.1-nasa-public-interop.jar`.
- `modules/`: the official `jdk` and `takx-bootstrap` descriptors, plus hand-written descriptors (below).
- `conf/`, `gazetteer/`, `resources/`, the log4j files and `template-64bit.vmoptions`: copied from the official tree.
- `resources/tak-assets/`: `txt/callsigns.txt`, `dbs/iconcache.sqlite`, `icons/` and the five `iconsets/*.zip` (Git LFS objects, verified against their pointers) from atak-civ.
- `native/linux64/`: `libcommoncommojni.so` and `libuds_desktop_clock.so`. The Kernel natives stay inside the Kernel JAR; its stock loader extracts them below `java.io.tmpdir`.
- `share/gdal/` and `share/proj/`: the data directories of GDAL 2.4.4 and PROJ 4.9.1. The libraries were built with the paths of their build directories, so `launch.sh` sets `GDAL_DATA` and `PROJ_LIB`; without them GDAL logs `Unable to open EPSG support file gcs.csv` wherever the build directories are absent.

### Left out or changed for its licence

`manifest.json` records each of these (`licence_exclusions`, `kernel_jar_derivation`), and `image/licence_gate.py` checks the result by content.

| Item | What the assembly does | Why | Effect seen |
| --- | --- | --- | --- |
| `ffmpeg-4.1.3-1.5.1-linux-x86_64.jar` | not copied | its binaries state "nonfree and unredistributable" | `MediaPlayerManager.startService` logs one `UnsatisfiedLinkError` when a project opens; no video. `TAKX_ADDON_LIBRARY_PATH` names a directory of natives the user supplies |
| `uds-takserver-ui-wizard-reference.jar` | not copied | CDDL-only | none: `gradle/clientDependencies.gradle` declares it, no class refers to it |
| `joost-0.9.1.jar` | not copied | MPL-1.1 only | none seen: a `runtimeOnly` declaration, and no class or service file of any JAR in the tree names `net.sf.joost`. Four STX sheets, the language joost runs, stay in `resources/export/`; no TAKX 5.7 source file opens them |
| `jocl-2.5.0.jar`, `jocl-main-2.5.0.jar`, `jocl-2.5.0-natives-linux-amd64.jar` | not copied | JogAmp terms with Apache-1.1 parts: under review next to GPL-3.0 code, not denied | none seen: `runtimeOnly` declarations, and no class of any other JAR names `com.jogamp.opencl`. JOGL and GlueGen, which the globe needs, stay with the same open question |
| 24 font atlases in the Kernel JAR (`assets/fonts/arial*`, `cour*`, `segoeui*`) | removed from the derived copy; `font_resources.json` lists no atlas | no licence, named after Microsoft fonts | none: Kernel 8.30.5 never opens them. `GlyphAtlas_open` has no caller outside `GlyphAtlas.cpp`, and nothing in the Kernel reads the option `default-font-atlas-uri` that TAKX sets to `asset://fonts`; glyphs are generated at run time since 8.13.0 (`takkernel/VERSION.md`). Labels render |
| `RUNPATH` of `libtakengine.so` and `libtakenginejni.so` | dynamic entry removed in the derived copy | it ends in an empty element, which glibc reads as the working directory | none |

The derived copy is named `kernel-8.30.5-linux-amd64-public-cpu-tree.jar`. The JAR the Kernel stage built, its receipt and its nine controls are unchanged and describe the built JAR; no control was rerun with the copy. The stock loader names its extraction directory after the MD5 of the JAR, so `prototype-launch.env` carries the MD5 of the copy. The atlases were not regenerated from an open font. Their JSON is the output format of `msdf-atlas-gen`, and atak-civ has no generator and no description of the format; since no code path opens an atlas, a regenerated set would be dead data that still had to be licensed and kept.

### Module graph

The official build generates one JBoss module per Maven artifact with a plugin that cannot run here. The prototype writes a coarser graph by hand:

- The names `ApplicationModuleFinder` asks the boot loader for exist exactly: `all-libs`, `jdk`, `log4j`, `org.slf4j.slf4j-api`, `org.aspectj`, `org.jboss.modules` (built into JBoss Modules), `jakarta.enterprise.jakarta.enterprise.cdi-api`, `jcommoncommo` and `gov.tak.kernel`.
- `gov.tak.kernel` holds the overlay JAR first, then the Kernel JAR and the Kernel-only dependency JARs. The GDAL and jassimp JARs are there because their JNI classes must share the class loader that maps the Kernel natives.
- `prototype.libs` holds every other third-party JAR in one module.
- `all-libs` re-exports all of them, and passes the `META-INF` path of the Kernel module through, because the Kernel build reads its resource catalog from there.
- Where the Kernel's dependency versions differ from the TAKX runtime closure (Jackson, Guava, Bouncy Castle, Kotlin, OkHttp, SLF4J and others), the TAKX closure version is used; `manifest.json` lists each case.

### Kernel Java overlay

Each patch fixes one place where booting the real client failed. Both are candidates to move into the recipes that own the code.

| Patch | Problem found at boot | Change |
| --- | --- | --- |
| `02-glutils-buffer-position.patch` | the upstream desktop `GLUtils` uploads a pixel buffer at the position `Bitmap.copyPixelsToBuffer` left it; the `takx/compat` Bitmap advances the position as Android does, so every widget texture upload failed | `rewind()` before the upload |
| `03-asset-root-module-loader.patch` | the `takx/compat` `AssetManager` serves assets only from `URLClassLoader` URLs or `java.class.path`; under JBoss Modules the Kernel JAR is neither | the JAR that holds the desktop classes is a declared root |

The former `01-render-surface-identity.patch` is gone: `takx/kernel-render-context` now returns the client's own surface object itself (and checks the real current GL context when a client rebinds after a GL context re-creation).

Recipe refresh: a composed Kernel built before that recipe change still holds the old `RenderContextBinding` and `GLMapView`. `kernel_overlay.py` compares the Java images the recipe's lock pins with the sources the Kernel stage compiled. For each one that differs it runs the recipe's `prepare.py`, takes the locked image from its output and compiles it into the overlay JAR; the receipt lists these files under `recipe_refresh`. It stops, asking for a Kernel lane rebuild, if the Kernel stage had changed such a file after the recipe or if a regenerated JNI header differs from the one the native libraries were built with. Once the Kernel lane is rebuilt from the current recipe the refresh finds nothing to do.

## Boot

```sh
takx/desktop-prototype/stages/boot.sh start                    # offline: map tile requests fail TLS verification
takx/desktop-prototype/stages/boot.sh start --trust-sandbox-ca # trust the sandbox proxy CA as well
takx/desktop-prototype/stages/boot.sh check                    # evidence into status/boot.json
takx/desktop-prototype/stages/boot.sh stop
```

`assemble/launch.sh` is the command line of the official `runTakx` task: working directory = the tree, class path `boot/*` and `conf`, every line of `template-64bit.vmoptions`, `java.library.path`, then `org.jboss.modules.Main -mp modules takx-bootstrap -create`. It adds only `-Dprojectdata.dir`, `-Djava.io.tmpdir` and `-Duser.home`, all inside the attempt's run directory, and the environment variables `GDAL_DATA` and `PROJ_LIB`. `assemble/session.sh` starts Xvfb 1920x1080x24, openbox and `LIBGL_ALWAYS_SOFTWARE=1` around it.

Each attempt gets `runs/boot-<n>/` and `logs/boot-<n>.log`. The run directory links to the tree and copies only `resources/`, because TAKX writes `resources/tak-assets/wmm_cof` at start.

Network behaviour to know about:

- The client contacts public endpoints by itself at start: `tak.gov` for map source definitions, the selected imagery server for tiles, the TAK elevation server, and `time.nist.gov`. No TAK Server is configured and no credential is entered.
- In a sandbox with a TLS-intercepting proxy those requests fail certificate verification unless `--trust-sandbox-ca` is given. Verification is never disabled.
- The default imagery layer, USGS Imagery Topo, answered HTTP 404 when this was written; USGS Imagery works.

## Capture

`stages/capture.sh all` boots a fresh attempt, records the X display losslessly with `ffmpeg x11grab` while `xdotool` drives the real UI, encodes H.264 `yuv420p` 1920x1080 30 fps files into the git-ignored `demo/out/takx/`, verifies them (`ffprobe`, `signalstats`, `freezedetect`, `blackdetect`) and extracts review frames. Media is never committed. `demo/out/takx/PROVENANCE.md` states what the footage shows and does not show.

## Deploy as a package

`stages/image.sh` puts the assembled tree into one self-contained image ([image/README.md](image/README.md)), and `stages/package.sh` turns that image into a Zarf v1beta1 package, `takx-desktop`, and a UDS bundle, `takx-desktop-proto`. The image build fails when the licence gate finds a denied or unknown JAR, native library or asset; the package pins the image by digest. The gate is not clean: it flags 44 items for review, so the image is labelled `publishable: false` and nothing is pushed ([STATUS.md](STATUS.md), "Image, package and licences"). The package runs the client in one pod and serves it to a browser at `https://takx.<domain>/` behind the UDS tenant gateway and Keycloak; a `TakClient` supplies its TAK identity. Both are opt-in and separate from the TAK Server package.

The bridge in the pod does not rely on the gateway alone: a small gate in front of noVNC verifies the signed-in user's token on every request (signature against the realm's keys, issuer, audience, expiry, group) and answers 401 or 403 otherwise, so reaching its port is not enough. TAKX starts with its default network inputs off, and the session directory, where TAKX keeps its own copy of the client key once a connection is added, is in memory. [image/README.md](image/README.md), "The bridge's own sign-in check", has the rules and the tests.

```bash
TAKX_ASSEMBLE_NAME=takx57-head takx/desktop-prototype/stages/assemble.sh   # a tree from the current sources
TAKX_ASSEMBLE_NAME=takx57-head takx/desktop-prototype/stages/image.sh all  # notices, licence gate, image, self-test
takx/desktop-prototype/stages/package.sh all      # lint, render, validate, test, build, inspect; deploys nothing
uds zarf package deploy /var/lib/takx-proto/pkg/out/zarf-package-takx-desktop-amd64-<TAG>.tar.zst \
  --components takx-desktop-takclient,takx-desktop-identity --confirm
```

The package and a bundle with only this package were built and checked without a cluster, last on 2026-11-26 from the tree `takx57-head`. An earlier build of the package was deployed once to the sandbox cluster of `server/`, used through the gateway from a browser, connected to the TAK Server from the pod, and removed again ([STATUS.md](STATUS.md), "Deployed in a pod"; `stages/deploy.sh`). The token check, the switched-off inputs and the session in memory came after that deploy and were tested against the image only ([STATUS.md](STATUS.md), "The bridge checks the token itself"): **the package as it is now has not been deployed.** The bundle has not been deployed, and the bundle with Zarf init, UDS Core and TAK Server was written and checked against its upstream packages but not created. [zarf/README.md](zarf/README.md) has the components, the values, the deploy commands, what was checked, what the deploy showed, what changed after it and what is still open.

## Files in this stage

| File | Purpose |
| --- | --- |
| `stages/assemble.sh`, `stages/boot.sh`, `stages/capture.sh` | stage drivers |
| `assemble/assemble.py` | tree assembler and manifest |
| `assemble/derive_jar.py`, `assemble/test_derive_jar.py` | derived Kernel JAR of the tree and its tests |
| `image/licence_gate.py`, `image/licence-policy.json`, `image/test_licence_gate.py` | licence gate, its policy and its tests |
| `image/web_gate.py`, `image/web_auth.py`, `image/test_web_gate.py`, `image/test_web_auth.py` | the bridge's gate, its token check and their tests |
| `image/verify/token_tool.py`, `image/verify/auth_probe.py`, `image/verify/browser_reconnect.js` | test keys and tokens, the probe that tries them against a running bridge, and the browser check of the reconnect |
| `assemble/kernel_overlay.py`, `assemble/kernel-overlay/*.patch` | Kernel Java overlay |
| `assemble/launch.sh`, `assemble/session.sh` | launcher and X session |
