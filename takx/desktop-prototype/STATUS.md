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

`stages/capture-connected.sh all` recorded `connect-2` into the git-ignored `demo/out/takx/`. The credential form was filled and closed before the recording started; the connection was then disabled so the take could start red.

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
