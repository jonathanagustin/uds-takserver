<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->
# Desktop prototype: fresh TAKX 5.7 compile

This opt-in stage compiles and packages the official TAKX 5.7 sources at
`0b59a9bb2d0e90dc35c9edfff1c2056daab2b713` from scratch, with Temurin 17.0.21+9,
inside the `takx-desktop-proto:toolchain` image. Run it with
`takx/desktop-prototype/stages/client.sh [all|prepare|runtime-jars|wait-kernel|compile|package|lock]`.

It is compile and packaging only. It launches no GUI and loads no native
library. The Kernel on the classpath is **public Kernel 8.30.5 plus the project
patches** and CommonCommo is **1.17.2 patched**; neither becomes the private
Kernel 8.57.11 or CommonCommo 1.20.0 that TAKX 5.7 declares. WorldWind is the
NASA v2.2.1 interop profile, not the private `2.2.1-TAKX-4`.

## Relation to `takx/client-compiler`

Nothing built here is fed to `takx/client-compiler`, and that recipe and its
locks are not modified. Its two locks are read as a specification only:

- the source lists (1,815 Platform compiler inputs, 1,678 Client sources) and
  their digests;
- the classpath order (135 Platform entries, 137 Client entries) and the
  129-JAR processor path;
- the javac flags (`build.py:212-220`, `platform_producer.py:261-265`);
- the expected class-name sets (2,098 Platform, 8 aspect, 2,815 Client) and
  generated-source names.

A step is `ok` only with compiler exit 0, zero compiler errors and a class-name
set equal to the expected one. Class-hash equality with the frozen digests is
reported as information and is never required.

## How the eight prerequisite class families are satisfied

`takx/client-compiler/README.md` lists eight class families that recipe takes as
frozen, digest-pinned inputs. Here none of them is supplied; each is compiled.

| Family | Expected | How it is satisfied here |
| --- | ---: | --- |
| Bootstrap preferences | 2 | `prep`: javac of the 2 official `Bootstrap/src/preferences/java` sources |
| Bootstrap | 15 | `prep`: javac of the 13 official `Bootstrap/src/main/java` sources against the preferences classes and the JARs `Bootstrap/build.gradle` declares |
| ProcessManager | 5 | `prep`: javac of the 3 official sources against `slf4j-api` |
| Platform | 2,098 | `platform`: one javac of all 1,815 inputs with the real AutoValue and EventBus processors. The 70 XJC sources come from the `xjc` step and the 3 template sources from the `templates` step, not from frozen copies |
| Platform aspects | 8 | `aspects`: ajc 1.9.21.1 over the 4 official `.aj` files with `-XhasMember -Xlint:ignore -outxmlfile aop/aop.xml`, no inpath and no aspectpath, so nothing is woven at compile time |
| Kernel project profile | 15 | inside the composed Kernel JAR of the Kernel stage (`takx/kernel-client-interop` Java applied to Kernel 8.30.5); no separate class directory |
| Frozen context profile | 45 | inside the composed Kernel JAR, compiled from the **current** `takx/kernel-render-context` profile. It is not the frozen eight-source `a8a4fda8…` artifact and is not claimed to equal it; all 45 expected class names are present |
| Texture profile | 4 | inside the composed Kernel JAR (`takx/kernel-texture` applied to Kernel 8.30.5); real classes, not a compile-only overlay |

Because the last three families are inside the Kernel JAR, their three class
directories are left off the classpath; the remaining entries keep the order of
the specification. Each compile receipt records how many of the expected names
of those families the Kernel JAR holds (15, 45 and 4 in the recorded run).

The two WorldWind consumer postimages (`AOverlayLayer.java`,
`AbstractMapsforgeTiledImageLayer.java`) come from `P/consumers57` and must
equal the digests of the specification.

## Generators

- **XJC** runs JAXB XJC 3.0.1 from the pinned compile closure over the seven
  XSDs and `GlobalBindings.xjb`, with the options the official build applies
  through `buildSupport.configureXjc` and `com.github.bjornvester.xjc` 1.9.0
  (extension mode, `-quiet`, XML security features off). `-no-header` is added
  so the output carries no generation time.
- **Templates** follow `Platform/build.gradle`: `SupportedPlatformVersion`
  constants for 3.0–3.5 and 5.0–5.7, and the two javadoc tables of
  `gov.takx.bom`. The tables state the versions actually used (for example
  Kernel 8.30.5 plus project patches) rather than the versions Gradle declares.
  `package-info.java` is javadoc only and yields no class.

## Packaging

`package` writes deterministic JARs named like a local, non-release Gradle build
(`<platformVersion>-SNAPSHOT`):

- `package/load/bootstrap-*.jar` with `Agent-Class`, `Premain-Class`,
  `Can-Redefine-Classes` and `TAKX-Flavor`, signed with the developer key that
  is tracked in the official tree, as the official local build does. The
  unsigned JAR is kept in `package/unsigned/`.
- `package/load/platform-*.jar`: Platform classes, the eight aspect classes,
  `aop/aop.xml` and `Platform/src/main/resources`.
- `package/load/client-*.jar`: Client classes and `Client/src/main/resources`.
- `package/load/processmanager-*.jar` and `package/boot/preferences-factory-*.jar`.

The official tree has no Git LFS pointer among these resources; the step fails
if one appears. Manifests carry `X-Prototype-*` attributes naming the real
Kernel and CommonCommo.

## Runtime JAR closure

`resolve_runtime.py` reads the dependency declarations from the official Gradle
files and lets Apache Maven 3.9.9 (pinned by SHA-512) resolve the runtime scope
from Maven Central and the JogAmp repository only, with strict checksums, into
`P/takx57-runtime-jars`. `manifest.json` lists every JAR with coordinates,
SHA-256, repository and whether the same bytes are in the compile closure.

- Nothing is requested from `artifacts.tak.gov`. Private coordinates are listed
  as `private-with-public-substitute` with the prototype artifact that stands in.
- `svgSalamander` is on JitPack only; it is excluded and listed with its
  `takx/ui-java` substitute.
- Versions of artifacts in the compile closure are managed to the closure
  version. Other versions are mediated by Maven (nearest wins), which is not
  Gradle's rule; the manifest records what was selected.

## Lock

`inputs.lock.json` records every input digest the run consumed, by logical
root. Locally rebuilt inputs (the composed Kernel JAR, `jcommoncommo.jar`, the
GDAL and jassimp JARs) carry the digest of the run that wrote the lock, so a
rebuild of any of them makes `client.sh lock` fail until
`TAKX_CLIENT_RECORD_LOCK=1` records the new digests deliberately. Output
digests and the comparison summary are in `P/takx57/result.json`; they are not
tracked because JAR manifests carry the build date.

## Architecture

The compile is the same on Linux `amd64` and `arm64`; `../arch.sh` holds the
names that differ. On arm64 it has run on GitHub-hosted `ubuntu-24.04-arm`
runners only (workflow runs 45702964185 and 45714468510, 2026-11-25): compile
and packaging with zero compiler errors and the expected class names. No GUI
was started on arm64.

- **JDK identity.** The specification lock pins four files of the x64 Temurin
  17.0.21+9. On arm64 `build.py` checks the same four files of the aarch64
  archive against `../inputs.arm64.lock.json`; the x64 pins are not reused.
- **Tracked lock.** amd64 compares with `inputs.lock.json`. arm64 compares with
  `inputs.lock.arm64.json`. That file is the lock the two workflow runs above
  wrote, byte for byte the same in both (SHA-256 `35cb6fe7...e4b2ac`), although
  the second run rebuilt the composed Kernel JAR
  (`kernel-8.30.5-linux-arm64-public-cpu.jar`, SHA-256 `8ee97f9a...fa7c4` in
  both). It is the record of that arm64 build, not a validation of it.
- **Native classifier JARs.** The official `gradle/binCreator.gradle` declares
  only `natives-linux-amd64` (JOGL, GlueGen, JOCL) and `linux-x86_64`
  (FFmpeg). On arm64 `resolve_runtime.py` requests `natives-linux-aarch64` and
  `linux-arm64` of the same artifacts and versions, checks them against
  `../inputs.arm64.lock.json`, and says in `manifest.json` that this is a
  prototype substitution.

## Not covered

No Gradle task runs, so nothing here is evidence about the official Gradle
build. Load-time weaving, the JBoss module graph, CDI discovery, JNI and
rendering are exercised only by the assembly and boot stages.
