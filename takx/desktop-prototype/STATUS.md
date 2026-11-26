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
| boot | ok | `status/boot.json`, `logs/boot-11.log`, `runs/boot-11/` |
| capture | ok | `status/capture.json`, `capture/boot-11/master.mkv`, `demo/out/takx/capture.json` and `PROVENANCE.md` (git-ignored, never committed) |

Paths are relative to the work root `/var/lib/takx-proto` unless they start with `demo/`.

The sections down to "Connected run with CloudTAK" describe the tree `dist/takx57` of 2026-11-25. The image and the package are built from a newer tree, `dist/takx57-head`; see [Image, package and licences](#image-package-and-licences-2026-11-26) at the end.

## Success criteria

| Criterion | Result |
| --- | --- |
| JVM up for more than 120 s with no fatal exception | Yes. Attempts 5, 8, 10 and 11 were checked at 123 s, 133 s, 131 s and 124 s with the window still mapped; attempt 6 ran for about 3 minutes of manual probing. Nothing ran longer than that. No JVM crash, `SIGSEGV` or `OutOfMemoryError` in any log. Non-fatal errors remain (below). |
| Real TAKX top-level window | Yes. `xdotool` reports a visible 1920x1080 window titled `TAKX 5.7 SNAPSHOT : <project> (Developer's Edition)`. |
| Non-blank screenshots, looked at | Yes. `signalstats` luma range 16 to 235. I viewed the frames; the description is below. |
| First-run dialogs | None appeared. `-create` makes a new project with the default database (hsqldb). No credential or server dialog was opened. |

### What the screen shows

- Title bar `TAKX 5.7 SNAPSHOT : 2026_11_25_... (Developer's Edition)`; "Developer's Edition" because the bootstrap JAR is signed with the developer key in the official tree.
- Ribbon tabs Home, Maps, Data, Draw, View, Plugins, Range & Bearing, Help, and the Home toolbar (Navigation Lock, New Globe, Overlays, Maps & Favorites, Elevation Tools, Select, ALERT, Point Dropper, Drawing Tools, Routes, Go to Location, City Search, Chats, Contacts, Playback, Devices, Files, Properties, Sensor Feeds, Media, TAK Server, Data Packages).
- Eight of the visible Home toolbar buttons show a "?" placeholder instead of their icon: the private `tak-assets` `png/` directory is absent.
- The docked "Primary Globe" tab with the Kernel globe rendered: Earth imagery, star field, compass, zoom buttons, tilt and scale widgets, the red "NO GPS" self-location notice, and a status line with eye altitude, heading, MGRS position and elevation.
- Side docks (Overlays, Maps, MultiSelector Tool, TAK Server, Contacts) and the status bar with MGRS, a System Messages counter, the UTC clock and "TAK Network Status" (disconnected).

### What worked when driven with xdotool

- Left-drag pans the globe, the wheel zooms, right-drag rotates the heading (the compass and the heading readout follow).
- Maps & Favorites opens the Maps panel; switching the imagery layer to USGS Imagery loads live tiles.
- Point Dropper places a CoT 2525C marker, by clicking the globe or by a typed location; the marker and its label render on the globe and appear under Recently Added.
- The ribbon tabs switch.

## Footage

`stages/capture.sh all` recorded attempt 11 and produced, in the git-ignored `demo/out/takx/`:

| File | Length | Content |
| --- | --- | --- |
| `takx57-raw-take-1080p30.mp4` | 30.27 s | one uncut take: empty desktop, main window, project load, globe, pan, heading rotation, zoom |
| `takx57-clip-1-globe-pan-zoom-rotate.mp4` | 9.50 s | pan, rotation and zoom |
| `takx57-clip-2-maps-imagery-layer.mp4` | 10.00 s | Maps panel, imagery layer switched to USGS Imagery |
| `takx57-clip-3-point-dropper-synthetic-marker.mp4` | 9.50 s | Point Dropper placing `SYNTHETIC-DEMO 1` at 30 S 15 W |
| `takx57-session-full-1080p30.mp4` | 93.63 s | the whole recording, including the ribbon tabs |

All five are H.264 High, `yuv420p`, 1920x1080, 30 fps, and passed the `ffprobe`, `signalstats` and `freezedetect` checks in `capture.json`. I viewed the extracted frames. The footage has long still stretches (14.5 s of the raw take, mostly the project loading), so it needs trimming before any public use. It shows a standalone client: no TAK Server, no CloudTAK, no credentials. `PROVENANCE.md` next to the files has the digests, commands, renderer string and caveats. The files are unpublished; this session has no way to upload release assets.

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
- The recipe tests of the overlay patches' owners (`takx/compat`, `takx/kernel-render-context`) and `takx/compat/test-system-clock.py`. Of the desktop-prototype tests only `test_scripts.py` was rerun after this stage (62 run, 2 skipped, all passed).

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

## Connected run (2026-11-25)

The same TAKX 5.7 prototype (main tree `dist/takx57`) connected to the TAK Server 5.7-RELEASE-14 that `server/` deploys in this sandbox (package `takserver` 1.4.0-upstream on UDS Core 1.16.0, native k3s), exchanged markers with a second real client in both directions, and was recorded. The peer was a scripted streaming client, not CloudTAK. Missions, data packages and the contact list did not work. One day, one machine, three short sessions: not acceptance.

Paths are under `/var/lib/takx-proto` unless they start with `demo/` or `server/`.

### How it was connected

- `server/connect.sh start` runs the boot session with one extra host entry: `takserver`, the only name in the server certificate, mapped to the passthrough gateway `10.44.0.12`. The JVM keeps the stock JDK trust store.
- `server/connect-configure.sh` drives TAKX's own form with the mouse and keyboard (Home > TAK Server > Add): address `takserver`, port 8089, protocol SSL, CA file `secrets/takx-desktop/truststore.p12`, client certificate `secrets/takx-desktop/client.p12`. Both passwords are typed from the mode 600 file inside the container. "Use Authentication" and "Enroll for Client Certificate" stay off.
- It connected on the first attempt (`connect-1`). No client or server change was needed.
- The package's own `truststore.p12` works as the CA file, although JDK keytool lists no entries in it: the form accepts an empty key store and CommonCommo reads the CA with OpenSSL. `truststore-java.p12` was not needed and not tried.

### Evidence of the connection

| Evidence | Result | Where |
| --- | --- | --- |
| TAKX interface | Green check on the "UDS TAK Server ssl://takserver:8089" row and green "TAK Network Status"; I viewed the frames | `connect/connect-1/shots/05-after-ok.png`, `demo/out/takx/frames/takx57-connected-*` |
| Client log | `Stream connection to ssl:takserver:8089 is up!`, then `Proto Negotiate: ... Protocol negotiation request accepted` (TAK protocol version 1) | `logs/connect-1.log` 20:43:28, `logs/connect-2.log` 20:54:33 |
| Server | `/Marti/api/subscriptions/all` lists `dn OU=UDS, O=TAK, CN=takx-desktop`, protocol `tls`, groups `Operators` IN and OUT, `takClient TAKX-CIV`, `takVersion 5.7.4-SNAPSHOT`; `/Marti/api/clientEndPoints` shows the same user `Connected` | `connect/connect-1/evidence/stream-takx-desktop.json`, `connect/connect-2/evidence/capture-stream.json` |

`server/connect_evidence.py` asks the server as a different TakClient (`takx-peer`), with mutual TLS through the gateway.

### Marker exchange (recorded run, `connect-2`)

| Direction | Result | Where |
| --- | --- | --- |
| TAKX to peer | Point Dropper placed `SYNTHETIC-TAKX 2` (neutral, 30 S 15 W); the marker's radial menu > Send > Broadcast sent it. `takx-peer` (group `Operators`) received it at 20:55:09 as `a-n-G` at -30.0, -15.0. The server stored it | `connect/connect-2/capture/peer.json`, `evidence/capture-takx-marker.json` |
| Peer to TAKX | `takx-peer` sent `SYNTHETIC-PEER-2` (`a-u-G`, 10 S 25 W) from 20:54:39. It appears on the TAKX globe as a yellow unknown-affiliation marker; hovering shows the label and the status line reads `(SYNTHETIC-PEER-2)` | clip 2 and its frames, `evidence/capture-peer-marker.json` |
| Unrelated group | `takx-outsider` (group `TakxIsolation`), connected for the whole take, received only the server's protocol notice: neither marker, nor TAKX's own position report | `connect/connect-2/capture/outsider.json` |

The first session (`connect-1`) gave the same result for `SYNTHETIC-TAKX 1` (peer received it, outsider did not: `connect/connect-1/evidence/try1-listen-*.json`) and showed `SYNTHETIC-PEER-0` on the globe (`shots/12-peer0.png`, `13-crop.png`).

Two things differ from the plan:

- The marker is named `SYNTHETIC-TAKX 2`, with a space. Point Dropper builds names as prefix, space, index; I did not rename it.
- TAKX does not send a marker when it is placed. Nothing reached the server until Send > Broadcast.

### Rejected connections

| Case | Client | Server | Where |
| --- | --- | --- | --- |
| Foreign CA as the CA file, real client certificate | `Error trying to make network connection ... (server certificate failed verification - check truststore)`, `CONN_SSL_PEER_CERT_NOT_TRUSTED`; red row; retried every 15 s | subscription added and removed within about 20 ms, no identity | `connect/connect-2/evidence/neg-client-log.txt`, `neg-server-messaging-log.txt` |
| Real CA, client certificate from a foreign CA with the same subject `CN=takx-desktop` | "Connected" for about 85 ms, then `SSL Read fatal error`, `Commo Interface Error ... IO`, toast "Network Error, TAK Server Not Connected"; repeats every 15 s | `SSLHandshakeException ... Certificate error: peer not verified` at the same seconds; the subscription list has no entry for the identity | same files, `neg-foreign-client-cert-subscriptions.json` |
| No client certificate (OpenSSL, TAKX's form does not allow SSL without one) | chain verifies, then `tlsv13 alert certificate required` | | `neg-openssl.txt` |
| Address `takserver-stream.uds.dev` (not in the certificate), real CA and client certificate (`connect-3`) | `(server certificate does not match configured endpoint)`, `CONN_SSL_PEER_CERT_NOT_TRUSTED` | no subscription | `connect/connect-3/evidence/name-check-client-log.txt`, `name-check-stream.json` |

The brief "Connected" in the second row is TLS 1.3: the client finishes its handshake before the server has judged the client certificate. TAKX shows a success toast and then an error toast each time.

The last row means the project's CommonCommo patch (`takx/patches/0101`) enforces the server name in this build. It also means a TAKX user of this package cannot dial the documented gateway name `takserver-stream.uds.dev`: the server certificate names only `takserver`.

A certificate revoked by deleting its TakClient was not tested.

### What did not work

- **HTTPS API from TAKX.** TAKX calls `https://<address>:8443` (preference `CommonCommo/SecureServerApiPort`) for missions and the contact list. The passthrough gateway exposes 443, 8446 and 8089, so every call fails with `ConnectException: Connection refused`. The log has `Failed to fetch mission list from server` once per connection, and the toast "Failed to query Contact List from: UDS TAK Server" appears every 30 s, also in the footage. Moving the preference to 443 would not help: that route needs SNI `takserver-api.uds.dev`, and TAKX sends the address it dials. Mission sync, data packages, contacts and chat are therefore untested.
- **CloudTAK as the peer.** CloudTAK v13.112.3 is deployed, but `POST /api/login` answers 403 "Username/Password login is disabled - Please use SSO", and I drove no browser. The test user `mapuser` is in `Operators_READ` only, so a CloudTAK-to-TAKX marker would also need a writable group.

### Footage

`stages/capture-connected.sh all` recorded `connect-2` into the git-ignored `demo/out/takx/`. The second session below recorded `connect-7` with CloudTAK as the peer and replaced these files; the `connect-2` master and evidence are still under `connect/connect-2/`. The credential form was filled and closed before the recording started; the connection was then disabled so the take could start red.

| File | Length | Content |
| --- | --- | --- |
| `takx57-connected-raw-take-1080p30.mp4` | 51.57 s | the whole master, uncut |
| `takx57-connected-clip-1-status-goes-green.mp4` | 7.0 s | the "enabled" box is ticked; row and status bar turn green |
| `takx57-connected-clip-2-peer-marker-arrives.mp4` | 10.0 s | `SYNTHETIC-PEER-2` appears on the globe; hover shows its label |
| `takx57-connected-clip-3-takx-marker-placed.mp4` | 10.0 s | Point Dropper places `SYNTHETIC-TAKX 2` |
| `takx57-connected-clip-4-takx-marker-broadcast.mp4` | 10.0 s | radial menu > Send > Broadcast |

All five are H.264 High, `yuv420p`, 1920x1080, 30 fps and passed the checks in `capture-connected.json`. I viewed the 20 extracted frames. About 36 s of the raw take are still. `PROVENANCE.md` there has versions, trust, digests and caveats. Unpublished.

### Issue #374 acceptance

| Item | State |
| --- | --- |
| Record versions, OS, plugin set, endpoint configuration, date | Partly. Versions, endpoint, trust and date are in `PROVENANCE.md`. The workstation is a container on a sandbox VM, and the plugin set was not inspected |
| Streaming mutual TLS with a dedicated TakClient, validation on, unrelated trust and revoked credentials rejected | Partly. Mutual TLS with `takx-desktop`, a foreign CA, a foreign client certificate and a wrong server name have evidence. Revocation was not tested |
| Synthetic marker with CloudTAK in both directions, negative check for an unrelated group | Not with CloudTAK. Done in both directions with a second streaming client, with the unrelated-group check |
| Mission synchronization and data-package transfer | No. Blocked by the API port; the limit is recorded above |
| Credential renewal, re-import, deletion, revocation, reconnect | No. Only disable and re-enable of the connection was exercised |
| Streaming and HTTPS endpoints checked separately | Yes as a finding: streaming works, HTTPS on 8443 is refused |
| Repeatable checklist and bounded evidence without secrets | Partly. The scripts and evidence files exist; the client guide was not touched |
| Short TAKX-to-CloudTAK demo linked from the client guide | No. The footage has no CloudTAK and is unpublished |

### Where the secrets are

Nothing secret is in the repository or in `demo/out`. TAKX keeps its own copies of the PKCS#12 files and the passwords in each run's project directory (`runs/connect-<n>/ProjectData/.../SslCerts` and `kernel/databases/credentials.sqlite`); those directories and `connect/connect-<n>/` (private screenshots, including the filled form with masked passwords) are mode 700. `secrets/untrusted/` holds the throwaway CA and client certificate of the rejected-connection checks.

### Not verified

- `stages/capture-connected.sh record` ran once, before a lint comment was added. `server/connect-configure.sh` gained `--ca-from` and `--address` after the recorded run; the later sessions used the new version.
- A first connection of a brand-new client on camera, a reconnect after a server restart, and any run longer than a few minutes.
- The fixed render-context tree `dist/takx57-rcfix` was not used.

### Next experiments

1. Expose 8443 on the passthrough gateway by port, or add the gateway names to the server certificate, then test missions, data packages and contacts from TAKX.
2. Drive CloudTAK through SSO in a browser (`google-chrome` is on the host) with a user in a writable group, and repeat the exchange with CloudTAK as the peer.
3. Delete the `takx-desktop` TakClient and watch the running client and a reconnect.

## Connected run with CloudTAK (2026-11-25, second session)

Second session the same day, 21:05 to 22:25 UTC, same TAKX tree (`dist/takx57`), same server. CloudTAK is now the peer: the TAKX 5.7 prototype and CloudTAK 13.112.3 exchanged named synthetic markers in both directions through the TAK Server, and both screens were recorded. Deleting the TakClient disconnected TAKX and the server refused the revoked certificate; a re-issued credential connected again. Missions and data packages are still untested. One machine, software rendering, about 80 minutes: not acceptance.

Attempts: `connect-4` exploration, `connect-5` rehearsal (a complete take, superseded), `connect-6` stopped before recording (a driver command was missing), `connect-7` the recorded take and everything after it. Paths are under `/var/lib/takx-proto` unless they start with `demo/` or `server/`.

### CloudTAK as the peer

- `server/connect_cloudtak_user.py` creates the Keycloak user `takx-cloudtak` in `/CloudTAK/Users`, `/TAK/Operators_READ` and `/TAK/Operators_WRITE` with a random password (mode 600 file). The repository's `mapuser` has the read direction only.
- `server/connect-cloudtak.sh start` runs a headed Chrome 153 (Chrome for Testing) on its own Xvfb display; `signin` goes through the real Keycloak form. `server/connect_cloudtak.cjs` drives the page with Playwright over the DevTools protocol.
- CloudTAK's map needs WebGL2. Without a GPU the page stops at "Website Error: Maximum call stack size exceeded" (console: "WebGL2 is required"); Chrome's SwiftShader flags fix it.
- After sign-in the server lists a second TLS subscription: `dn CN=takx-cloudtak, OU=UDS, O=TAK`, groups `Operators` IN and OUT, `takClient CloudTAK`, `takVersion 13.112.3` (`connect/connect-7/evidence/capture-stream-cloudtak.json`). CloudTAK's own `GET /api/marti/group` shows only `Operators:OUT` for the same user; sending works all the same.
- CloudTAK does not send a point when it is created either. It needs the feature's Share > Broadcast To All Users.

### Evidence of the connection (`connect-7`)

| Evidence | Result | Where |
| --- | --- | --- |
| TAKX interface | Row "UDS TAK Server ssl://takserver:8089" and "TAK Network Status" turn from red to green, toast "TAK Server Connected"; I viewed the frames | `demo/out/takx/frames/takx57-connected-clip-1-*`, `takx57-connected-raw-take-*` |
| Client log | `Stream connection to ssl:takserver:8089 is up!` at 21:49:05 (form) and 21:49:46 (recorded re-enable), each followed by `Protocol negotiation request accepted` | `connect/connect-7/evidence/capture-stream.json`, `client-log-until-restart.log` |
| Server | `/Marti/api/subscriptions/all`: `dn OU=UDS, O=TAK, CN=takx-desktop`, `tls`, `Operators` IN and OUT, `TAKX-CIV 5.7.4-SNAPSHOT`; `/Marti/api/clientEndPoints`: user `takx-desktop`, callsign `IVANHOE`, `Connected` | `connect/connect-7/evidence/capture-stream.json` |

### Marker exchange with CloudTAK (`connect-7`)

| Direction | Result | Where |
| --- | --- | --- |
| CloudTAK to TAKX | CloudTAK's Coordinate Input created `SYNTHETIC-PEER-7` at 10 S 25 W; Share > Broadcast To All Users was clicked at 21:50:07. It is on the TAKX globe within the next seconds; hovering shows the label and the status line reads `(SYNTHETIC-PEER-7)`. The server stored it at 21:50:07 as `a-f-G-E-V-C`, uid `b33e9fd8-...` | `capture/cloudtak-place.json`, clip 2 and its frames, `evidence/capture-peer-marker.json` |
| TAKX to CloudTAK | Point Dropper placed `SYNTHETIC-TAKX 7` at 30 S 15 W; the marker's details dialog renamed it `SYNTHETIC-TAKX-7`; radial menu > Send > Broadcast at 21:50:53. The server stored it at 21:50:54 as `a-n-G`, uid `b06ee7b7-...`. CloudTAK shows a green square there; its search box lists `SYNTHETIC-TAKX-7` under MAP FEATURES at 21:51:06 and its details panel reads type Ground, location -30, -15, creator `IVANHOE` (TAKX's callsign) | clips 3 to 5, browser clips 3 and 4, `capture/cloudtak-find.json`, `evidence/capture-takx-marker.json` |
| Third client, same group | `takx-peer` (silent, `Operators`) received both markers at 21:50:07 and 21:50:54 | `capture/peer.json` |
| Unrelated group | `takx-outsider` (`TakxIsolation`), connected from 21:49:34 to 21:52:05, received only the server's protocol notice | `capture/outsider.json` |

Each client also sees the other as a contact: TAKX's "Send As Cot" dialog lists `takx-cloudtak`, and CloudTAK's Share dialog lists `IVANHOE`. The rehearsal `connect-5` gave the same results for `SYNTHETIC-PEER-5` and `SYNTHETIC-TAKX-5`.

### Rejected connections

- OpenSSL against the stream port, rerun at 22:04 (`connect/connect-7/evidence/neg-openssl.txt`): no client certificate gives `tlsv13 alert certificate required`; a foreign CA as the only trust root gives `certificate verify failed`; a client certificate from a foreign CA gives a TLS alert, with `SSLHandshakeException` in the server log.
- The four cases driven in TAKX itself (foreign CA, foreign client certificate, wrong server name, and the name check) are those of the first session; they were not repeated.

### Revocation and re-issue (`connect-7`, after the take)

| Step | Result |
| --- | --- |
| `kubectl delete takclient takx-desktop` at 22:04:57 | Returned in 1.3 s; the device Secret is gone. TAKX lost the stream in the same second (`SSL Read fatal error`, toast "Network Error, TAK Server Not Connected") |
| TAKX retries every 15 s with the old certificate | The TLS handshake completes, then the server closes: `RevokedException: Attempt to use revoked certificate : OU=UDS,O=TAK,CN=takx-desktop` at 22:05:13, 22:05:28 and later. The row stays red; a "Connected" toast flashes for about 40 ms on each retry (TLS 1.3, as in the first session) |
| Server subscription list, four reads from 22:05:09 to 22:05:42 | No entry for `takx-desktop` |
| The old credential from another client (`connect_peer.py`) | Connection closed in the same second, nothing delivered, not even the protocol notice |
| `server/70-takclients.sh takx-desktop:Operators` | New certificate, serial `77754EC8` (old `73FA609F`) |
| TAKX: delete the connection row, Add again with the new files | `Stream connection ... is up!` at 22:07:02, protocol negotiated, green row; the server lists the subscription again |
| The old credential again at 22:07:22 | Subscription added and removed within 10 ms, nothing delivered |
| `server/connect.sh restart` (same project) | The saved connection came up by itself at 22:13:52 and the markers were still on the globe |

Evidence: `connect/connect-7/evidence/revoke-timeline.txt`, `revoke-*.json`, `reissue-*.json`, `shots/20-revoked.png` and `22-reissued.png` (I viewed both). This is deletion and re-issue. Renewal of a certificate while its TakClient stays was not tested.

### HTTPS API: the port is the obstacle (experiment, not the deployed path)

To learn whether port 8443 is the only obstacle, the certificate name `takserver` was pointed at the workstation (`TAKX_CONNECT_SERVER_IP=172.17.0.1`), where a transparent TCP relay forwarded 8089 to the gateway and `kubectl port-forward` exposed `takserver-api-service` 8443. TLS was not terminated anywhere. This bypasses the gateway for the API, so it says nothing about the deployed route.

- OpenSSL with the TakClient certificate: the API presents `CN=takserver`, the name verifies, `GET /Marti/api/version` answers 200.
- Before: "Failed to query Contact List" 51 times, every 30 s, the last at 22:13:04; `ApiException: Connection refused` from the mission list call.
- After the restart through the relay at 22:13:52, with the same connection and the package's `truststore.p12`: no contact-list failure and no `ApiException` in the next ten minutes; the only warning in the log is a time-server timeout. The forward handled four connections from TAKX.
- JDK lists 0 entries in the package's `truststore.p12`, which could have broken TAKX's Java HTTPS client. It did not show as a failure. Changing the CA file to `truststore-java.p12` at 22:22 made no visible difference; the stream accepts that file too.

So the failures stop once 8443 is reachable under a name in the server certificate. That is the absence of errors, not a positive result: I have no record of the responses, and missions and data packages were not exercised in the interface. Records: `connect/api-experiment/` (its first count blamed certificate-path errors on the API; the correction is in the same file: those came from requests made at boot without the sandbox CA).

### Footage

`stages/capture-connected.sh all` recorded both displays of `connect-7` into the git-ignored `demo/out/takx/`. The Keycloak form and the TAKX connection form were closed before the recording started.

| File | Length | Content |
| --- | --- | --- |
| `takx57-connected-raw-take-1080p30.mp4` | 113.03 s | TAKX display, the whole master, uncut |
| `takx57-connected-clip-1-status-goes-green.mp4` | 7.0 s | the "enabled" box is ticked; row and status bar turn green |
| `takx57-connected-clip-2-cloudtak-marker-arrives.mp4` | 10.0 s | `SYNTHETIC-PEER-7` from CloudTAK appears on the globe; hover shows its label |
| `takx57-connected-clip-3-takx-marker-placed.mp4` | 10.0 s | Point Dropper places the marker |
| `takx57-connected-clip-4-takx-marker-named.mp4` | 10.0 s | radial menu > details; the name becomes `SYNTHETIC-TAKX-7` |
| `takx57-connected-clip-5-takx-marker-broadcast.mp4` | 10.0 s | radial menu > Send > Broadcast |
| `takx57-connected-cloudtak-raw-take-1080p30.mp4` | 112.97 s | browser display, the whole master, uncut |
| `takx57-connected-cloudtak-clip-1-peer-marker-created.mp4` | 10.0 s | CloudTAK Coordinate Input: name and position typed, point created |
| `takx57-connected-cloudtak-clip-2-peer-marker-broadcast.mp4` | 9.0 s | details panel, Share Features, Broadcast To All Users |
| `takx57-connected-cloudtak-clip-3-takx-marker-arrives.mp4` | 10.0 s | the TAKX marker appears on the CloudTAK map |
| `takx57-connected-cloudtak-clip-4-takx-marker-found.mp4` | 10.0 s | the search box opens, `SYNTHETIC-TAKX-7` is typed and listed under MAP FEATURES (about second 7), the map flies to the marker, its radial menu opens |

All are H.264 High, `yuv420p`, 1920x1080, 30 fps and passed the checks in `capture-connected.json`. I viewed contact sheets of the extracted frames. About 94 s of each raw take are still. `PROVENANCE.md` there has versions, trust, digests and caveats. Unpublished.

### Issue #374 acceptance

| Item | State |
| --- | --- |
| Record versions, OS, plugin set, endpoint configuration, date | Partly. Versions, endpoint, trust and date are in `PROVENANCE.md`. The workstation is a container on a sandbox VM, and the plugin set was not inspected |
| Streaming mutual TLS with a dedicated TakClient, validation on, unrelated trust and revoked credentials rejected | Has evidence: mutual TLS with `takx-desktop`; foreign CA, foreign client certificate and wrong server name rejected (first session); revoked certificate rejected (this session) |
| Synthetic marker with CloudTAK in both directions, negative check for an unrelated group | Has evidence: `SYNTHETIC-PEER-7` and `SYNTHETIC-TAKX-7`, with a third same-group client and the unrelated-group listener |
| Mission synchronization and data-package transfer | No. The limit is recorded: port 8443 is not exposed under the certificate name. With that worked around on the workstation the API calls stop failing; missions and data packages were not driven |
| Credential renewal, re-import, deletion, revocation, reconnect | Partly. Deletion, revocation, reconnect refusal, re-issue and re-import have evidence. Renewal of a live TakClient was not tested |
| Streaming and HTTPS endpoints checked separately | Yes as a finding: streaming works through the gateway; HTTPS on 8443 is refused there and stops failing when the port is reached another way |
| Repeatable checklist and bounded evidence without secrets | Partly. The scripts and evidence files exist; the client guide was not touched |
| Short TAKX-to-CloudTAK demo linked from the client guide | Partly. The footage now shows TAKX and CloudTAK, each on its own screen; it is unpublished and not linked |

### Where the secrets are

Nothing secret is in the repository or in `demo/out`. `secrets/cloudtak-user/` holds the CloudTAK user's password and the Chrome profile with its session (mode 700). `secrets/takx-desktop-revoked/` is a copy of the revoked credential, kept for the rejection check. Run directories `runs/connect-4` to `connect-7` and `connect/connect-*/` are mode 700.

### Not verified

- `stages/capture-connected.sh record` as it stands now: the take was recorded before three later edits (the result-anchored cut of browser clip 4, the "listed" event line, and an external restyle of the file). The "listed" line of `connect-7` was added to `events.tsv` afterwards from `cloudtak-find.json`; `events.as-recorded.tsv` is the original. `finish` ran on the final file.
- The rehearsal `connect-5` was cut with lead times estimated by hand; `connect-7` used the script's own measurement.
- A positive result for missions, data packages, contacts or chat from TAKX.
- CloudTAK with a basemap, a real GPU, or a user's own browser.
- The fixed render-context tree `dist/takx57-rcfix` was not used.

### Next experiments

1. Decide the deployment change for TAKX's HTTPS API: 8443 by port on the passthrough gateway, or the gateway names in the server certificate. Then drive missions and data packages and look for a positive result.
2. Rotate a live TakClient certificate and re-import it without deleting the TakClient.
3. Record both screens side by side on a machine with a GPU, with a basemap in CloudTAK.

## Image, package and licences (2026-11-26)

The image and the Zarf package were rebuilt from a tree assembled from the branch as it is now, with the findings of the packaging review fixed and the licence blockers of issue #934 removed or recorded. The image boots with nothing mounted and renders the globe; the licence gate passes the build and is **not clean**: 44 items are under review, so the image stays `publishable: false`. Nothing was pushed, and nothing was deployed in that session. One machine, software rendering, sessions of two to five minutes: not acceptance.

| Artifact | Value |
| --- | --- |
| Tree | `dist/takx57-head`, 645 files, 259 MB, `manifest.json` SHA-256 `47e84e568bba2c8c1d73ac5c44cd0e452fb57ff50d2c17a3fec54c9dcb3af390` |
| Image | `ghcr.io/jonathanagustin/uds-takserver/takx-desktop:5.7-0b59a9bb-proto-47e84e568bba` (also `takx-desktop:5.7-proto-47e84e568bba`), ID and manifest digest `sha256:18c21b79a9050d74603ac0232e43d9cd357e485df1d30672669489bdca7c861c`, 2.11 GB, local Docker store only |
| Package | as built in this session: 681,558,019 bytes, SHA-256 `9fb9a2b237f2a927d499e89e45d744151dca038d8a70a1dd00930c57e53ab916`. Replaced under the same name after the deploy; the current file is in the table of [Deployed in a pod](#deployed-in-a-pod-2026-11-26) |
| Bundle | as built in this session (this package only): SHA-256 `1135fc53e371926964e05f78d9299308fbf5048fa9700ea7eb51587f77464ae5`. Replaced with the package |
| Evidence | `pkg/evidence/r5/` (final image), `pkg/package/evidence/` (final package), logs in `pkg/r5/`. Earlier builds of this session, superseded: `pkg/evidence/r4/` and `r4-control/` (same tree; the image differed in the spelling of one pattern in the policy file), `pkg/evidence/r3/` and `r3-addon/` (the tree before JOCL was left out) |

The repository was at commit `f43ba66` with uncommitted changes when the notices were generated; `SOURCES.json` in the image says so (`repository.dirty`, with the paths). A rebuild after those changes are committed keeps the tag, because the tag names the tree, and gets another image digest; the package must then be created again, which the digest pin enforces.

### What the tree contains

- **Kernel.** The JAR the Kernel stage built (Kernel 8.30.5 + project patches, compiled before the render-context source fix), shipped as a derived copy (below). In front of it in the same module sits the overlay JAR with 40 classes, so these win: `RenderContextBinding` (4 classes) and `GLMapView` (34), compiled from the current `takx/kernel-render-context` sources because they are newer than what the Kernel stage compiled; `AssetManager` (2, overlay patch 03); `GLUtils` (2, overlay patch 02). Overlay patch 01 no longer exists. `manifest.json` (`kernel_java_overlay`) lists every class and both source digests.
- **Same content as the earlier unreviewed tree.** A first assembly from the branch was file for file identical to `dist/takx57-r2`, which an interrupted run had left. The final tree differs from it by the three JOCL JARs.
- **Not connected.** This tree and this image were booted and driven locally. No TAK Server connection was made with them; the connected runs above used `dist/takx57`.
- **Controls.** The nine Kernel controls describe the built JAR. None was rerun with the overlay or with the derived copy.

### Packaging review findings

| # | Finding | What was done | Evidence |
| --- | --- | --- | --- |
| 1 | GDAL and PROJ data missing | The assembly copies `share/gdal` (93 files) and `share/proj` (11); `launch.sh` and the image entrypoint set `GDAL_DATA` and `PROJ_LIB`; the entrypoint refuses to start without them; `selftest.sh check` fails on `Unable to open EPSG support file` | No such line in the image's log. Control, on the previous build of the same tree: with the data directory hidden and the entrypoint guard cut out, GDAL logs the error and the check fails on exactly these two checks (`pkg/evidence/r4-control/selftest.control.json`) |
| 2 | "No listening TCP port" was false | Texts corrected. The chart renders a NetworkPolicy that isolates the pod for ingress and an Istio AuthorizationPolicy that denies every port but 6080; the UDS `Package` has no ingress rule. `zarf/README.md`, "Which ports can be reached", names the enforcer of each | Listeners read from `/proc/net/tcp` in the self-test: 6080, 4242, 8087 and one ephemeral port on every address, one more on loopback (`pkg/evidence/r5/jvm.json`). Objects rendered and schema-valid. **Not exercised in a cluster** |
| 3 | Image pinned by tag | The staged package and chart name `<tag>@sha256:<digest>`; without a digest nothing is staged | `package.sh inspect`: both definition documents carry the digest, and the one manifest in the package's OCI layout has it |
| 4 | RUNPATH ending in an empty element | The derived Kernel JAR has `DT_RUNPATH` removed from `libtakengine.so` and `libtakenginejni.so`. The built JAR and its receipt are untouched; the manifest records both digests of each library | `elf_audit.sh`: 0 run paths with an empty or relative element in 21 libraries. `selftest.sh linker` under `LD_DEBUG=libs`: 0 search paths with an empty or relative element, 0 files tried by a relative name, no Kernel `RUNPATH`, nothing below `/var/lib/takx-proto` |
| 5 | Built package says `zarf.dev/v1alpha1` | Nothing to change. Zarf 1.0.1 creates the package from the v1beta1 sources and stores two documents in the archive's `zarf.yaml`: its own v1alpha1 conversion first, then the v1beta1 definition. It has no option to omit the first | `pkg/package/inspect/zarf.yaml-as-stored`; `package.sh inspect` checks for exactly these two documents |

### Left out or changed for its licence

| Item | What was done | Visible effect | How it was checked |
| --- | --- | --- | --- |
| `ffmpeg-4.1.3-1.5.1-linux-x86_64.jar` (bytedeco natives, "nonfree and unredistributable") | not in the tree; the gate denies any file that contains that string | **Video is lost**: no playback, recording or media length analysis. When a project opens, `MediaPlayerManager.startService` logs one `UnsatisfiedLinkError: no jniavutil`; the other services start. The Media dock opens and offers to add a file or a URL | Boot of the image, one such error in its log; I viewed the frames `pkg/evidence/r3/ui-media-add.png`, `r4/ui-media.png` and `r5/browser-novnc.png` |
| Video as an add-on | a directory of natives the user supplies at `/opt/takx-addons/video` is appended to `java.library.path` | With the 16 libraries of the excluded JAR mounted read-only, the error is gone and the JVM maps `libavutil.so.56`, `libavcodec.so.58`, `libavformat.so.58`, `libswresample.so.3` and four JNI libraries from the mount (`pkg/evidence/r3-addon/`). **Playing a video was not tried.** The chart has no value for the mount | one boot on 2026-11-26 |
| `uds-takserver-ui-wizard-reference.jar` (CDDL-only) | not in the tree | none seen. No class is required at boot or later: no TAKX source file imports `org.netbeans`, and no class of any JAR in the tree names `org.netbeans.api.wizard`, `spi.wizard` or `modules.wizard`. TAKX's own wizards are `gov.takx.commons.wizard` | scan of every class and service file in the tree; boot; File menu and Point Dropper opened |
| `joost-0.9.1.jar` (MPL-1.1 only) | not in the tree | none seen. A `runtimeOnly` declaration; no class or service file names `net.sf.joost`. Four STX sheets (`resources/export/*.stx`), the language joost runs, stay in the tree, and no TAKX 5.7 source file opens them. The Data ribbon is empty in this build, so no export path could be driven | same scan; `grep` of the TAKX sources |
| JOCL (`jocl-2.5.0.jar`, `jocl-main-2.5.0.jar`, natives JAR; JogAmp terms with Apache-1.1 parts) | not in the tree | none seen. `runtimeOnly` declarations; no class outside JOCL names `com.jogamp.opencl` | same scan; the image without them boots and renders the globe (`pkg/evidence/r4/`, `r5/`) |
| 24 font atlases named after Arial, Courier and Segoe UI in the Kernel JAR | removed from the derived copy; the index lists no atlas. Not regenerated | none. Labels and widget text render: I viewed a placed marker with its label, the compass and the "NO GPS" notice (`pkg/evidence/r5/browser-novnc.png`), and the status line (`r3/ui-marker.png`) | see below |

**How the Kernel uses the atlases.** It does not. The only reader is `GlyphAtlas_open` / `GlyphAtlas_loadBitmap` in `renderer/core/GlyphAtlas.cpp`, which no other file of `takkernel` calls. TAKX sets the option `default-font-atlas-uri` to `asset://fonts`, and nothing in Kernel 8.30.5 reads that option. Since 8.13.0 `GLGlyphAtlas2` generates glyphs at run time (`takkernel/VERSION.md`). The JSON files are in the output format of `msdf-atlas-gen`; atak-civ has no generator and no description of the format. Regenerating them from an open font was therefore not done: the result would be data that nothing loads.

**Kept, although flagged.** XPP3 is compiled into `jboss-modules`, which parses the module descriptors with it at boot. AspectJ is referenced by the woven platform classes and by the bootstrap. The four Jakarta API JARs are referenced by Weld, Hibernate and the TAKX client and platform classes. JOGL and GlueGen draw the globe. `jai-imageio-core` registers image readers through service files, so the class scan cannot show it unused.

### What the licence gate still flags

`image/licence_gate.py` judged 582 items of the tree: 538 allowed, 0 denied, 0 unknown, 44 under review. The report is `licence-gate.json` in the image and the `LICENCE-GATE` attachment of the package. On the unfiltered tree `dist/takx57` the same gate denies 45 items and reports 18 unknown, and the build stops.

| Count | What | Open question |
| --- | --- | --- |
| 10 | `worldCities.csv`, `TAKX_legacy.zip`, three `.wav` files, five icon set archives | no statement of their own; covered only by the repository `LICENSE.md` |
| 9 | `jogl-all` and its natives JAR, seven native libraries inside | Apache-1.1 parts next to GPL-3.0 code |
| 6 | `aspectjrt`, `aspectjweaver`, four Jakarta API JARs | EPL-2.0 next to GPL-3.0-only code (the Jakarta JARs also offer GPL-2.0 with the Classpath exception) |
| 4 | the four TAKX JARs | ten TAKX source files carry Proprietary, NDA or EULA headers; upstream has not confirmed the GPL covers them |
| 3 | `gluegen-rt`, its natives JAR and library | BSD-4-Clause advertising clause |
| 3 | three symbol fonts in `JavaRendererUtils` | no font-specific notice inside an Apache-2.0 artifact |
| 2 | `libgdal.so`, `libgdalalljni.so` | the strings MRSID and ECW; that no proprietary codec is linked is the recipe's statement |
| 2 | `share/gdal` (93 files), `share/proj` (11 files) | tables derived from EPSG, ESRI and IGNF data, terms not checked one by one |
| 1 | `libtakengine.so` | KTX notices of the Cesium Native dependency carry ETC and Kodak terms; whether such code is compiled in is unconfirmed |
| 1 | the Kernel JAR | eleven CA certificates and data files without their own statement |
| 1 | `jboss-modules` | XPP3 acknowledgement clause (the acknowledgement is in `NOTICE.md`) |
| 1 | `jai-imageio-core` | nuclear-facility acknowledgement wording |
| 1 | `spacemusic.au` in the WorldWind JAR | origin not stated |

Beyond the gate, `SOURCES.json` lists why the image is not publishable: no source archives are recorded for the Maven and Kernel dependency binaries, two fetched inputs have no verified digest, the export classification is open, and the image carries a source manifest, not the source.

### Notices and counts

- `LICENSES/` in the image holds 269 files, and `INDEX.tsv` has 269 rows. An earlier run indexed four SQLCipher provider files twice and reported 273; the generator now writes one row per file and fails when the two numbers differ.
- It holds one text per licence identifier the policy allows or keeps under review (21 files; 19 identifiers are in use), the licence files of libmicrohttpd, nghttp2, ngtcp2, protobuf, curl and libglvnd that the review missed, and every licence and notice file found inside the JARs (64 of the 144 JARs carry one; the other 80 are covered by the identifier texts).
- Fetched inputs are counted by what is recorded for each: 451 with a URL and a SHA-256, 99 with a SHA-256 only, 11 built by the prototype, 3 git checkouts, 2 unverified. The earlier "566 with URL and SHA-256" is gone.
- The OCI label lists the licence identifiers in use, not `GPL-3.0-only`.

### Verified on the final image

`stages/image.sh` and `image/verify/selftest.sh`, run step by step on `takx-desktop:5.7-proto-47e84e568bba`:

- **Nothing mounted.** Two containers on `--network none`, tmpfs and one tmpfs volume only, no bind mount; `/var/lib/takx-proto` does not exist inside.
- **Read-only root, non-root.** Read-only root filesystem, uid 10001 and 10002, no capabilities, no-new-privileges.
- **Boots.** The `started` probe passed after about 40 s; all 15 checks passed with the JVM 130 s old; peak memory 2.27 GB with `-Xmx2g`.
- **Globe rendered.** I viewed the frame: title `TAKX 5.7 SNAPSHOT : 2026_11_26_19_00_36 (Developer's Edition)`, Earth imagery, star field, compass, a marker placed with Point Dropper and its label, the Media dock, clock 19:02:38 UTC.
- **Browser bridge live.** A raw RFB client through `/websockify` read two frames five seconds apart that differ. Headless Chromium loaded noVNC and its canvas showed the desktop with the marker placed a minute earlier; that screenshot is the frame described above (`pkg/evidence/r5/browser-novnc.png`).
- **Supervision.** Killing websockify ends only the web container; killing the JVM fails all three probes and ends the desktop container.
- **Layer scan.** 15 layers, 18,957 files: no sandbox CA, no private key outside an unmodified Debian library, no key store, no build path.
- **Unit tests.** 62 image, gate and derive tests and 55 package tests pass on Python 3.11, 3.12 and 3.13.

### Not verified

- **Any deploy**, in that session. The package was deployed later the same day: see [Deployed in a pod](#deployed-in-a-pod-2026-11-26).
- **Video with the add-on**, beyond the libraries being mapped.
- **A TAK Server connection** with this tree or this image, in that session. The deploy made one from the pod.
- **Import and export paths.** The Data ribbon is empty in this build, so nothing that might have used the wizard JAR or joost could be clicked; the conclusion rests on the class scan.
- **arm64.** The image and the package are amd64 only; `derive_jar.py` and the image were not run on arm64 here.
- **Soak.** The longest session of this image was about four minutes.

## Deployed in a pod (2026-11-26)

The `takx-desktop` package was deployed to the sandbox cluster of `server/` (single-node k3s, UDS Core 1.16.0, TAK Server package 1.4.0 with CloudTAK), used through the tenant gateway from a real browser, connected to the TAK Server from inside the pod, and removed again. 19:27 to 20:13 UTC, one machine, software rendering: not acceptance. The cluster is back at its baseline and **the package is not deployed now**. [zarf/README.md](zarf/README.md), "What the deploy showed", has every result with its numbers; this is the summary. Paths are under `/var/lib/takx-proto/deploy` unless they start with `demo/`, `server/`, `stages/` or `pkg/`.

| Artifact | Value |
| --- | --- |
| Package deployed first | SHA-256 `9fb9a2b237f2a927d499e89e45d744151dca038d8a70a1dd00930c57e53ab916` (the build of the section above). The checks ran on it |
| Package deployed over it | SHA-256 `02b3925c698bf7db0176b2e3ebdb247bab7527ff1b6323a611ee97b030088315`: the same plus the chart's DestinationRule. The sign-in gate was rerun on it, and the pod replacement and the removal ran on it |
| Package in `pkg/out` now | 681,482,841 bytes, SHA-256 `9eb3f7538e69b166c272a8439955b38cc6eea88f2e2f3056c354bec071eb2c38`. Same image; its rendered objects are identical to the second package's, and it carries the README and chart comments written after the deploy. **Not deployed** |
| Bundle in `pkg/out` now | this package only, SHA-256 `1027f7daec09e9bc02298c6ad00d9382ee98e9746cee3cb6b0065eef52524234`. Never deployed |
| Image | unchanged: `sha256:18c21b79a9050d74603ac0232e43d9cd357e485df1d30672669489bdca7c861c`, local, `publishable: false` |

### What was shown

| Check | Result | Evidence |
| --- | --- | --- |
| Admission | The pod passed UDS Core's Pepr policies and the Zarf agent with no exemption. Mutations: image references to the cluster registry (tag dropped, digest kept), pull Secret, the waypoint label, annotations. No security field changed | `evidence/04-admission-mutations.txt`, `03-pod.yaml` |
| Identity | `TakClient` `takx-browser` Ready in one second; two keys copied, byte-identical; six staged files mode 0400 on a read-only memory volume; no secret value in any log or evidence file | `evidence/05-identity.txt`, `17-secret-scan.txt` |
| Boot in the pod | Globe rendered; I viewed the frame read from the pod's display. 43 minutes, restarts 0; desktop container 1.8 to 2.0 GiB, peak 2.1 GiB; about 0.22 CPU on average | `evidence/06-pod-frame-1.png`, `07-monitor.tsv` |
| Sign-in gate | Without a session `/` and `/websockify` (plain and as a WebSocket upgrade) get `302` to Keycloak and end at Authservice (`ext_authz_denied`, no upstream). `doug` (`/UDS Core/Admin`) reaches the live desktop in Chromium; I viewed the screenshot. `mapuser` (not in the group) gets `403` from Keycloak and no bridge | `evidence/08-gate-unauthenticated.txt`, `09-gate-doug/`, `10-gate-mapuser/`, `11-waypoint-access.log` |
| Isolation | From a pod in another namespace and from a pod in the namespace, 6080, 4242, 8087 and the ephemeral port are refused. With NetworkPolicy opened for a test, ztunnel's log names the chart's DENY policy for the other ports and the operator's policy for 6080. Egress: only `takserver:8089` answers; the Kubernetes API, the internet and ten other destinations do not | `evidence/13-isolation-ingress.txt`, `13-isolation-ztunnel-mesh-layer.log`, `14-isolation-egress.txt` |
| Connection from the pod | Connected to `ssl://takserver:8089` at the first attempt with the staged identity, server name checked. The server lists `CN=takx-browser`, `tls`, `Operators`, `TAKX-CIV 5.7.4-SNAPSHOT`. One synthetic marker each way with the scripted peer `takx-peer`; `takx-outsider` in another group received neither | `evidence/15-connection/` |
| Lifecycle | Pod deleted: replacement Available in 48 s with a new project and no connection; added again, it connected. Package removed in 7 s; namespace (deleted by `stages/deploy.sh`, Zarf leaves it), `TakClient`, its Secret and the copy are gone; everything else as in the baseline except Authservice | `evidence/20-lifecycle/`, `22-removal-comparison.txt` |

### What was found

- **The node is inside the trust boundary.** A process in the node's host network namespace reaches `pod-IP:6080` without signing in (HTTP 200) and the JVM's ports 4242 and 8087. kube-router and Istio ambient both let the node's own traffic through to its pods. The chart cannot close this; `zarf/README.md` and the chart say so now. Evidence: `evidence/13-isolation-ingress.txt` section D, `13-isolation-node-rules.txt`.
- **Authservice restarts on deploy and on removal** (UDS operator; new pods at 19:27:37 and 20:12:35), which ends the sessions of every application behind it. TAK Server, CloudTAK and Keycloak pods kept their UIDs and restart counts. Evidence: `evidence/21-authservice-replicasets.txt`.
- **The bridge kept one process per pooled connection**: 44 processes and 173 of 256 MiB after four page loads. Fixed with a DestinationRule in the chart (1 process, 35 MiB after three more); package rebuilt and deployed over the running one. Evidence: `evidence/18-bridge-processes/`.
- **A TAK Server restart reissues every `TakClient` certificate** and revokes the old ones (seen after the VM restart earlier that day). The desktop's Secret copy would then be stale; the identity component has to be deployed again. `server/README.md`, "After a VM restart".
- **CloudTAK of package 1.4.0 does not survive a node restart by itself**: its init container cannot rewrite a mode 0444 file in its `emptyDir`. `server/90-resume.sh` replaces the pod; the cause, `charts/cloudtak/files/merge-trust.mjs`, is outside this directory and unchanged.
- Smaller: a second request with the cookie of a pending sign-in makes that sign-in end in "your session has expired"; Point Dropper ignores a typed location unless an icon is selected first; the toast "Failed to query Contact List" repeats while connected (the API port, as before).

### Footage

One take of the desktop used through the browser, in the git-ignored `demo/out/takx/`, with `PROVENANCE-pod-browser.md`. The sign-in was made in a separate, unrecorded browser, and the connection form was filled before the take.

| File | Length | Content |
| --- | --- | --- |
| `takx57-pod-browser-raw-take-1080p30.mp4` | 56.6 s | the whole take |
| `takx57-pod-browser-clip-1-desktop-opens-status-goes-green.mp4` | 18.4 s | a Chromium window opens `takx.uds.dev`, the desktop appears, the "enabled" box is ticked, row and status bar turn green |
| `takx57-pod-browser-clip-2-peer-marker-label.mp4` | 9.5 s | hover on the peer's marker `SYNTHETIC-PEER-POD-2` |
| `takx57-pod-browser-clip-3-globe-drag-and-zoom.mp4` | 32.6 s | two drags turn the globe, the wheel zooms in and out |

All four are H.264 High, `yuv420p`, 1920x1080, 30 fps, with no black interval (`*.ffprobe.json`, `*.detect.txt`). I viewed frames of the master at 2.6, 6, 20, 26, 45 and 55 s and of the encoded raw take at 12 s. noVNC scales the desktop to the browser window (about 0.92), and no mouse pointer is drawn. Unpublished.

### Not verified

- **The bundle**: never deployed, and the whole bundle never created.
- **Sessions**: expiry, logout, and whether an open WebSocket is cut when a session ends. One open desktop stayed connected for 14 minutes and reloaded without a sign-in; cookies moved to another browser after 11 minutes were refused. Not explained.
- **Renewal and revocation** in the pod, **persistence**, **`egress.hosts`**, any value other than the defaults.
- **CloudTAK as the pod's peer**, missions, data packages, contacts, video.
- **`stages/deploy.sh preflight` and `deploy`**: the first deploy was made by hand with the same command, before the script existed; its baseline files were written from the baseline taken by hand. The other steps ran from the script.
- **A second VM restart** with the corrected `server/90-resume.sh`: its fixes were run against reproductions of the failures only.
- **The final package** (`9eb3f753...`) in a cluster. It differs from the second deployed package in documentation and comments.
- **Soak**: the pod ran 43 minutes, its replacement 2.

