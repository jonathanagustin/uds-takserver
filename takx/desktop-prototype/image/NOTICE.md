<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# TAKX 5.7 desktop prototype image: notice

This image is a prototype built by the `uds-takserver` project from public sources. It is not the official TAKX 5.7 distribution, it is not qualified, and it is not published.

## What is inside

| Part | What this image contains | What official TAKX 5.7 declares |
| --- | --- | --- |
| Client | TAKX 5.7, revision `0b59a9bb`, compiled from the public repository | the same sources |
| Kernel | Kernel 8.30.5 + project patches (public `atak-civ` `9f6893dd`) | a private Kernel build |
| CommonCommo | CommonCommo 1.17.2 patched (public `atak-civ` `9f6893dd`) | a private CommonCommo build |
| WorldWind | NASA WorldWind Java v2.2.1 with an interop profile | a private WorldWind build |
| UI libraries | source rebuilds by `takx/ui-java` | private artifacts |
| Other JARs | unmodified public Maven artifacts | the same coordinates where public |
| Assets | public `atak-civ` icons and icon sets; no private `png/` directory, no border shapefiles | private asset sets |
| Runtime | Eclipse Temurin 17.0.21+9 JDK; Debian trixie packages: Xtigervnc, openbox, websockify, noVNC, Mesa | not part of TAKX |

The tree under `/opt/takx` is byte-identical to the assembled tree: `manifest.json` there names the origin and SHA-256 of each file, and the image build checks all of them.

## Licences

This project is GPL-3.0-only. TAKX and `atak-civ` carry the text of the GNU General Public License version 3 as their `LICENSE.md`. The other components keep their own licences; the texts are collected here as found, without a legal review.

| Where | What |
| --- | --- |
| `LICENSES/GPL-3.0-only.txt` | the licence of this project |
| `LICENSES/takx-5.7/`, `LICENSES/atak-civ/` | licence files of TAKX and `atak-civ`, with its `THIRDPARTY.md` |
| `LICENSES/worldwind/` | licence and third-party notice files of NASA WorldWind Java |
| `LICENSES/providers/` | licence files of the native libraries built into the Kernel and CommonCommo |
| `LICENSES/jars/` | every licence and notice file found inside the bundled JARs |
| `LICENSES/INDEX.tsv` | all of the above with SHA-256 and origin |
| `/usr/share/doc/*/copyright` | Debian packages, including `novnc` (unpacked, not installed) |
| `$JAVA_HOME/legal/` | the JDK |

## Corresponding source

`SOURCES.json` identifies the source of every file in the tree: repositories and revisions, every fetched archive with URL and SHA-256, the build receipts, and each file mapped to a component. `patches/` holds the patches the builds applied; those named by a build receipt are checked against the digest in that receipt. `locks/` holds the two lock files. `debian-packages.tsv` lists binary and source package versions of the operating system layer.

This is a manifest, not the source itself. `SOURCES.json` says `"publishable": false` and lists the reasons, among them: unmodified Maven binaries whose source archives are not recorded here, the open licence review of the bundled FFmpeg binaries, and a repository working tree that had uncommitted changes. Anyone who passes this image on must provide the complete corresponding source with it, or a written offer, as GPL-3.0 section 6 requires.

## Limits

One machine, software rendering, a few minutes of use. No renderer, SDK or client acceptance is claimed. See `takx/desktop-prototype/STATUS.md` in the repository for what was and was not tested.
