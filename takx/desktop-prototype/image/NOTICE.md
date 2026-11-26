<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# TAKX 5.7 desktop prototype image: notice

This image is a prototype built by the `uds-takserver` project from public sources. It is not the official TAKX 5.7 distribution, it is not qualified, and it is not published. `SOURCES.json` says `"publishable": false` and lists why.

## What is inside

| Part | What this image contains | What official TAKX 5.7 declares |
| --- | --- | --- |
| Client | TAKX 5.7, revision `0b59a9bb`, compiled from the public repository | the same sources |
| Kernel | Kernel 8.30.5 + project patches (public `atak-civ` `9f6893dd`), as a derived copy of the built JAR (below) | a private Kernel build |
| CommonCommo | CommonCommo 1.17.2 patched (public `atak-civ` `9f6893dd`) | a private CommonCommo build |
| WorldWind | NASA WorldWind Java v2.2.1 with an interop profile | a private WorldWind build |
| UI libraries | source rebuilds by `takx/ui-java` | private artifacts |
| Other JARs | unmodified public Maven artifacts | the same coordinates where public |
| Assets | public `atak-civ` icons and icon sets; no private `png/` directory, no border shapefiles | private asset sets |
| Data | the data directories of GDAL 2.4.4 and PROJ 4.9.1 (`/opt/takx/share`) | not stated |
| Runtime | Eclipse Temurin 17.0.21+9 JDK; Debian trixie packages: Xtigervnc, openbox, websockify, noVNC, Mesa | not part of TAKX |

`/opt/takx/manifest.json` names the origin and SHA-256 of each file of the tree, and the image build checks all of them.

## What was left out or changed for its licence

| Item | What was done | Effect |
| --- | --- | --- |
| `ffmpeg-4.1.3-1.5.1-linux-x86_64.jar` (bytedeco FFmpeg natives) | not shipped: the binaries state "nonfree and unredistributable" | no video playback, recording or media length analysis; one error in the log when a project opens |
| `uds-takserver-ui-wizard-reference.jar` | not shipped: CDDL-only | none seen; no class refers to it |
| `joost-0.9.1.jar` | not shipped: MPL-1.1 only | none seen; no class refers to it. Four STX sheets in `resources/export/` stay; nothing in TAKX 5.7 opens them |
| `jocl-2.5.0.jar`, `jocl-main-2.5.0.jar`, `jocl-2.5.0-natives-linux-amd64.jar` (JogAmp OpenCL binding) | not shipped: flagged for review (Apache-1.1 parts) and unused | none seen; no class refers to `com.jogamp.opencl`. JOGL and GlueGen stay |
| 24 font atlas files in the Kernel JAR (`assets/fonts/arial*`, `cour*`, `segoeui*`) | removed from the copy in this image; the index `font_resources.json` lists no atlas | none seen; Kernel 8.30.5 does not open them |
| RUNPATH of `libtakengine.so` and `libtakenginejni.so` in the Kernel JAR | dynamic entry removed from the copies in this image | none; the libraries are mapped by absolute path |

The Kernel JAR as built, its receipt and its controls are unchanged outside the image. `manifest.json` (`kernel_jar_derivation`) records both digests of every changed entry. Video works only with FFmpeg natives the user supplies in a directory named by `TAKX_ADDON_LIBRARY_PATH`; none are part of this image.

## Licences

This project is GPL-3.0-only. TAKX and `atak-civ` carry the text of the GNU General Public License version 3 as their `LICENSE.md`. The other components keep their own licences. The image is an aggregate: the OCI label lists the licence identifiers in use, not one licence. The texts are collected as found, without a legal review.

| Where | What |
| --- | --- |
| `LICENSES/GPL-3.0-only.txt` | the licence of this project |
| `LICENSES/texts/` | one text for every licence identifier the licence gate knows |
| `LICENSES/takx-5.7/`, `LICENSES/atak-civ/` | licence files of TAKX and `atak-civ`, with its `THIRDPARTY.md` |
| `LICENSES/worldwind/` | licence and third-party notice files of NASA WorldWind Java |
| `LICENSES/providers/` | licence files of the native libraries built into the Kernel and CommonCommo |
| `LICENSES/jars/` | every licence and notice file found inside the bundled JARs |
| `LICENSES/INDEX.tsv` | all of the above with SHA-256 and origin |
| `licence-gate.json` | the licence gate's verdict on every JAR, native library and asset, and what it still flags |
| `/usr/share/doc/*/copyright` | Debian packages, including `novnc` (unpacked, not installed) |
| `$JAVA_HOME/legal/` | the JDK |

## Acknowledgements

- This product includes software developed by the Indiana University Extreme! Lab (http://www.extreme.indiana.edu/).
- This product includes software developed by the Apache Software Foundation (http://www.apache.org/).
- Map styles in the Kernel JAR: © OpenMapTiles (https://openmaptiles.org/), design under CC-BY 4.0; map data © OpenStreetMap contributors.
- JogAmp (GlueGen, JOGL) parts are under BSD-2-Clause, BSD-3-Clause, BSD-4-Clause, Apache-1.1, Apache-2.0, SGI Free Software License B and the Ubuntu Font Licence; see `LICENSES/texts/`.
- The Apache-2.0 JARs keep their `NOTICE` files inside them; copies are in `LICENSES/jars/`.

## Cryptography

This image contains cryptographic software: OpenSSL 3.5.9 and OpenSSL 3.0.14+quic and SQLCipher 4.5.7, statically linked into the bundled native libraries, Bouncy Castle 1.84, and the JDK's providers. Their sources are named in `SOURCES.json`.

No export classification of this build is stated or implied. tak.gov makes a statement about ATAK-CIV only: "which advises this item is classified as EAR99. Other TAK-CIV Product Lines are currently being evaluated for commodity determination." Whoever passes this image on is responsible for the export and sanctions rules that apply to them.

## Corresponding source

`SOURCES.json` identifies the source of every file in the tree: repositories and revisions, every fetched input with its category (URL and SHA-256, SHA-256 only, built here, git checkout), the build receipts, and each file mapped to a component and a licence. `patches/` holds the patches the builds applied; those named by a build receipt are checked against the digest in that receipt. `locks/` holds the two lock files. `debian-packages.tsv` lists binary and source package versions of the operating system layer.

This is a manifest, not the source itself. Anyone who passes this image on must provide the complete corresponding source with it, or a written offer, as GPL-3.0 section 6 and the LGPL require.

## Limits

One machine, software rendering, a few minutes of use. No renderer, SDK or client acceptance is claimed. See `takx/desktop-prototype/STATUS.md` in the repository for what was and was not tested.
