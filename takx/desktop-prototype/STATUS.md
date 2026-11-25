<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Desktop prototype status

Date of the runs below: 2026-11-25, Linux `amd64`, 4 vCPU, software rendering only.

## Result

The official TAKX 5.7 client (`0b59a9bb`), built from public sources, boots to its real main window and renders the globe under Xvfb with Mesa `llvmpipe`. It stays up, responds to the mouse and keyboard, and was recorded.

It runs on Kernel 8.30.5 plus project patches (not the private 8.57.11), CommonCommo 1.17.2 patched (not 1.20.0), NASA WorldWind 2.2.1 interop (not 2.2.1-TAKX-4) and `takx/ui-java` rebuilds. None of this is renderer, SDK or client acceptance: it is one machine, one software renderer, and a few minutes of use.

| Stage | Status | Evidence |
| --- | --- | --- |
| assemble | ok | `status/assemble.json`, `dist/takx57/manifest.json` (547 files, 270 MB) |
| kernel-overlay | ok | `status/kernel-overlay.json`, `P/kernel-overlay/receipt.json` (3 patches, 7 classes) |
| compat-system-clock | ok | `status/compat-system-clock.json` |
| boot | ok | `status/boot.json`, `logs/boot-10.log` |
| capture | see `status/capture.json` | `demo/out/takx/capture.json` (git-ignored) |

Paths are relative to the work root `/var/lib/takx-proto` unless they start with `demo/`.

## Success criteria

| Criterion | Result |
| --- | --- |
| JVM up for more than 120 s with no fatal exception | Yes. Attempts 5, 8 and 10 were checked at 123 s, 133 s and 131 s with the window still mapped; attempt 6 ran for about 3 minutes of manual probing. No JVM crash, `SIGSEGV` or `OutOfMemoryError` in any log. Non-fatal errors remain (below). |
| Real TAKX top-level window | Yes. `xdotool` reports a visible 1920x1080 window titled `TAKX 5.7 SNAPSHOT : <project> (Developer's Edition)`. |
| Non-blank screenshots, looked at | Yes. `signalstats` luma range 16 to 235. I viewed the frames; the description is below. |
| First-run dialogs | None appeared. `-create` makes a new project with the default database (hsqldb). No credential or server dialog was opened. |

### What the screen shows

- Title bar `TAKX 5.7 SNAPSHOT : 2026_11_25_... (Developer's Edition)`; "Developer's Edition" because the bootstrap JAR is signed with the developer key in the official tree.
- Ribbon tabs Home, Maps, Data, Draw, View, Plugins, Range & Bearing, Help, and the Home toolbar (Navigation Lock, New Globe, Overlays, Maps & Favorites, Elevation Tools, Select, ALERT, Point Dropper, Drawing Tools, Routes, Go to Location, City Search, Chats, Contacts, Playback, Devices, Files, Properties, Sensor Feeds, Media, TAK Server, Data Packages).
- Ten toolbar buttons show a "?" placeholder instead of their icon: the private `tak-assets` `png/` directory is absent.
- The docked "Primary Globe" tab with the Kernel globe rendered: Earth imagery, star field, compass, zoom buttons, tilt and scale widgets, the red "NO GPS" self-location notice, and a status line with eye altitude, heading, MGRS position and elevation.
- Side docks (Overlays, Maps, MultiSelector Tool, TAK Server, Contacts) and the status bar with MGRS, a System Messages counter, the UTC clock and "TAK Network Status" (disconnected).

### What worked when driven with xdotool

- Left-drag pans the globe, the wheel zooms, right-drag rotates the heading (the compass and the heading readout follow).
- Maps & Favorites opens the Maps panel; switching the imagery layer to USGS Imagery loads live tiles.
- Point Dropper places a CoT 2525C marker, by clicking the globe or by a typed location; the marker and its label render on the globe and appear under Recently Added.
- The ribbon tabs switch.

## Fixes made to boot

Each row is one real failure, with the attempt whose log holds the stack trace.

| # | Attempt | Failure | Cause | Fix |
| --- | --- | --- | --- | --- |
| 1 | assembly | duplicate file `jaxb-core-3.0.1.jar` | two Maven groups publish that file name | the second keeps its group in the name |
| 2 | 1 (headless) | `IOException: Asset JAR is outside declared classpath roots` in `CertificateManagerBase` | the `takx/compat` `AssetManager` accepts only `URLClassLoader` URLs and `java.class.path`; under JBoss Modules the Kernel JAR is neither | overlay patch 03. A first workaround, the Kernel JAR on the class path, was wrong (row 8) |
| 3 | 1 (headless) | `Resources$NotFoundException: Resource identifier is absent or has the wrong type: 0` for `wmm_cof`; the launch event failed and the JVM exited | the Kernel resource catalog is in `META-INF` of the Kernel JAR, and a JBoss Modules dependency exports only `META-INF/services` | `all-libs` imports and exports the `META-INF` path of `gov.tak.kernel` |
| 4 | 3 | `ClassCastException: RenderContextBinding$Surface cannot be cast to java.awt.Component` in `ApplicationGlobe.makeWwd`; no globe component, then a chain of `NullPointerException`s | the project render-context patch returns a forwarding surface; TAKX casts the surface to its own types in six places | overlay patch 01 |
| 5 | 4 | `UnsatisfiedLinkError: no uds_desktop_clock in java.library.path`, then `NoClassDefFoundError: android.os.SystemClock` on every frame | the `takx/compat` SystemClock needs its JNI library, which no earlier stage built | `assemble.sh` runs the unmodified `takx/compat/build-system-clock.py`; the library goes to `native/linux64` |
| 6 | 4 | `IndexOutOfBoundsException: Required 6400 remaining bytes in buffer, only had 0` in `GLUtils.texSubImage2D` on every frame; map widgets missing | upstream desktop `GLUtils` uploads at the buffer position the `takx/compat` Bitmap advanced | overlay patch 02 |
| 7 | 5, 6 | every map tile request failed with `SSLHandshakeException: PKIX path building failed` | the sandbox proxy intercepts TLS; the JVM does not trust its CA, correctly | optional `boot.sh start --trust-sandbox-ca`; verification stays on |
| 8 | 6 | `IncompatibleClassChangeError: ...memory.FeatureCursor does not implement the interface com.atakmap.map.layer.feature.FeatureCursor ... of loader 'app'`; placed markers did not render | with the Kernel JAR on the class path, JNI `FindClass` on native threads loaded a second copy of the Kernel classes through the system class loader | Kernel JAR removed from the class path; the class path is the official one again |

Rows 2, 4 and 6 are the Kernel Java overlay. The Kernel JAR and its receipt are unchanged, and the nine Kernel controls were not rerun with the overlay. The three patches should move into `takx/compat` (02 needs a decision there or upstream, 03) and `takx/kernel-render-context` (01), each with its own tests; patch 01 in particular bypasses that patch's forwarding of size listeners and is correct only while the client keeps one surface object per renderer.

## What does not work or was not tested

Known failures, all non-fatal, present in every GUI attempt:

- **MIL-STD-2525D and 2525E point-dropper panels.** `NullPointerException: Failed to convert 'SUGP-----------' to DELTA` (and `to ECHO`) in `AMil2525IconSelectionPanel.convertToTarget2525(AMil2525IconSelectionPanel.java:351)`, called from `lateInit` on the event thread. The Kernel 8.30.5 symbol table returns no 2525D symbol for the converted code. The 2525C panel works. Not investigated further.
- **Kernel typefaces.** `NoSuchFieldException: sSystemFontMap` in `MapTextFormat.getSSystemFontMap(MapTextFormat.java:324)`: the Kernel reads a private Android field that the desktop `Typeface` does not have. Logged once; labels still render.
- **Marker icon URI.** `[TAKEngineJNI]: failed to handle URI file://` and `IO2.cpp@1467 code=8` once per placed marker. The marker renders as its affiliation square.
- **Toolbar icons.** Each missing `tak-assets/png/*.png` logs `File not found`; the client shows its default image.
- **Border layers.** `Shape file not found ... resources/ShapeFiles/World_Political_Borders.shp` and the state borders: `border-shapefiles` is private and absent.
- **DockingFrames.** `ClassNotFoundException: bibliothek.util.workarounds.Java9Workaround` is printed once by the `takx/ui-java` rebuild and ignored by the library.
- **Default imagery.** USGS Imagery Topo answers HTTP 404 from the USGS server; the low-resolution Kernel base map shows until another layer is chosen.
- **tak.gov layer sync.** With the proxy CA trusted, the client downloads map source definitions from `tak.gov`; the Kernel logs `KmzLayerInfoSpi: Failed to find document KML file` while probing each one. The layers are listed anyway.

One slow path was seen once and not reproduced: in attempt 8 a right-drag rotation after zooming in froze the display for about 16 s, then completed.

Not tested at all:

- Any TAK Server connection, CommonCommo traffic, chat, contacts, data packages, missions or plugins.
- Drawing tools, routes, range and bearing, elevation tools, KML or shapefile import, 3D models, video, playback.
- 124 native method names in five Kernel classes have no exported symbol (`GLES10` 107, `GLLabelManager` 6, `NativeRenderSurface` 6, `RenderContextInterop` 4, `SystemClock` 1 before the clock library was added). None was hit in these runs; any code path that calls one would throw `UnsatisfiedLinkError`.
- GL context destruction and recreation (closing and reopening the globe, a second globe), which is what the render-context patch is for.
- A hardware GPU, a real X server, another distribution, or a run longer than a few minutes.
- The recipe tests of the overlay patches' owners; the existing desktop-prototype tests were not rerun after this stage.

## Deviations from the official layout and launch

- JBoss Modules descriptors are hand-written and coarser than the generated graph (README, "Module graph").
- Only Linux `amd64` natives are shipped; `jaxb-runtime` and `jboss-logging-annotations` are left out as the official scripts do.
- `java.library.path` is `native/linux64` plus the Kernel stock loader's extraction directory under `java.io.tmpdir`. How the official build lays out `nativeLibPath` is not public.
- The Kernel natives carry build-time `RUNPATH` entries below `/var/lib/takx-proto/P`. The stock loader maps each library by absolute path first, so they were not used, but the tree is not proven relocatable.
- JARs are named `5.7.4-SNAPSHOT`.
- `--trust-sandbox-ca` and `TAKX_BOOT_DELAY_S` exist only for this sandbox and for recording.

## Next experiments

1. Rerun the Kernel controls (`binding-state` above all) with the overlay classes, then move patch 01 into `takx/kernel-render-context` and patches 02 and 03 into `takx/compat` with tests.
2. Exercise GL context recreation: undock and redock the globe tab, open "New Globe", and watch for the crash recorded in `takx/kernel-render-context/QUALIFICATION.md`.
3. Find why `MilStd2525.get2525DFrom2525C("SUGP-----------")` plus `getSymbol` yields nothing on 8.30.5.
4. Connect to a local TAK Server from this repository to test CommonCommo 1.17.2 in the client.
5. Run `boot.sh start` in a second image without `/var/lib/takx-proto/P` mounted to prove the tree is self-contained.
