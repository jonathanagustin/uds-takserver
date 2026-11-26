<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# TAKX 5.7 desktop prototype image

One image that holds the assembled TAKX 5.7 tree and everything it needs to run and to be reached from a browser. It runs with nothing mounted from the host, as a non-root user, on a read-only root filesystem.

What is inside is TAKX 5.7 (`0b59a9bb`) built from public sources on Kernel 8.30.5 + project patches, CommonCommo 1.17.2 patched and the NASA WorldWind interop build. It is not the official TAKX 5.7 distribution. The image is local only and is never pushed. This directory is opt-in: nothing here is used by the TAK Server build or its CI.

## Build and test

`../stages/image.sh` records the exact commands. Each step is bounded, so they can be run one at a time.

```bash
export TAKX_ASSEMBLE_NAME=takx57-head             # the tree to build from; default takx57 (see ../stages/assemble.sh)
takx/desktop-prototype/stages/image.sh context    # copy dist/<tree>, compare, check the manifest
takx/desktop-prototype/stages/image.sh notices    # licence texts, NOTICE.md, SOURCES.json, patches, licence gate report
takx/desktop-prototype/stages/image.sh gate       # the licence gate alone
takx/desktop-prototype/stages/image.sh test       # unit tests, no Docker, on python3.11, 3.12 and 3.13
takx/desktop-prototype/stages/image.sh build      # docker buildx build with the build gates
takx/desktop-prototype/stages/image.sh inspect    # size, layers, history, layer scan, notices in the image
takx/desktop-prototype/stages/image.sh selftest start|wait|check|browser|stats|supervision|audit|linker|identity|stop
```

Tags, with `<digest12>` the first 12 hex digits of the SHA-256 of the tree's `manifest.json`:

- `takx-desktop:5.7-proto-<digest12>`
- `ghcr.io/jonathanagustin/uds-takserver/takx-desktop:5.7-0b59a9bb-proto-<digest12>`, the name the Zarf package uses. It exists only in the local Docker image store.

The build uses `--provenance=false --sbom=false`, so the image is a single manifest. `zarf package create` needs that to read the image from the Docker daemon.

## Run without a cluster

Two containers of the same image share one tmpfs volume for the RFB socket. Nothing is bind-mounted.

```bash
IMAGE=takx-desktop:5.7-proto-<digest12>
docker volume create --driver local --opt type=tmpfs --opt device=tmpfs \
  --opt o=size=1m,uid=10001,gid=10001,mode=2770 takx-vnc
docker run -d --name takx-desktop --hostname takx-desktop --add-host takx-desktop:127.0.0.1 \
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 10001:10001 \
  --tmpfs /tmp:rw,nosuid,nodev,size=1g,mode=1777 \
  --tmpfs /run/takx/session:rw,exec,nosuid,nodev,size=2g,uid=10001,gid=10001,mode=0750 \
  --tmpfs /run/takx/identity:rw,noexec,nosuid,nodev,size=8m,uid=10001,gid=10001,mode=0750 \
  --mount type=volume,src=takx-vnc,dst=/run/takx/vnc \
  -p 127.0.0.1:6080:6080 -e EXTRA_JVM_OPTS=-Xmx2g "$IMAGE" desktop
docker run -d --name takx-web --network container:takx-desktop \
  --read-only --cap-drop ALL --security-opt no-new-privileges --user 10002:10002 --group-add 10001 \
  --mount type=volume,src=takx-vnc,dst=/run/takx/vnc,readonly "$IMAGE" web
```

Then open `http://127.0.0.1:6080/`. The bridge has no login of its own: anyone who reaches the port controls the desktop. Publish it on loopback only. In a cluster, access control belongs to the gateway in front of it.

The self-test does not publish a port. It uses `--network none` and reaches the bridge with `docker exec`.

## Entrypoint modes

| Mode | User | Runs | Needs |
| --- | --- | --- | --- |
| `desktop` (default) | 10001:10001 | Xtigervnc on display `:1`, openbox, the TAKX JVM through `launch.sh` | writable `/tmp`, `/run/takx/session` (exec), `/run/takx/vnc` |
| `web` | 10002:10002 plus group 10001 | `websockify`: noVNC on TCP 6080, WebSocket to the RFB Unix socket | `/run/takx/vnc` (read-only is enough) |
| `identity` | 10001:10001 | `stage_identity.py`, then exits | `/run/takx/secret` (read-only), writable `/run/takx/identity` |

tini is PID 1. In `desktop` mode the container ends when Xtigervnc, openbox or the JVM ends. It exits with the JVM's status, or 70 or 71 for the other two.

### Writable paths

| Path | Written by | Holds |
| --- | --- | --- |
| `/tmp` | desktop | X socket and lock file |
| `/run/takx/session` | desktop | `ProjectData/`, `home/`, `tmp/` (the JVM's temporary directory), `bin/` (links to `/opt/takx` plus a copy of `resources/`), `jvm.pid`. About 270 MB after start. Needs `exec`: the Kernel loader extracts its nine native libraries below `tmp/` and maps them from there. |
| `/run/takx/vnc` | desktop | `rfb.sock`, mode 0660, group 10001 |
| `/run/takx/identity` | identity | the staged identity files, mode 0400 |

Everything else is read-only. In Kubernetes these are `emptyDir` volumes with `fsGroup: 10001`. Put `session` on a volume to keep projects across restarts.

### Environment

| Variable | Mode | Meaning | Default |
| --- | --- | --- | --- |
| `EXTRA_JVM_OPTS` | desktop | extra JVM options, as the official `runTakx` task reads them | none (`-Xmx4g` from the official template applies) |
| `TAKX_ARGS` | desktop | TAKX program arguments, space separated | `-create` when `ProjectData` is empty, otherwise none |
| `TAKX_GEOMETRY` | desktop | desktop size | `1920x1080` |
| `TAKX_DEPTH` | desktop | colour depth: 16, 24 or 32 | `24` |
| `TAKX_DISPLAY_NUMBER` | desktop, probes | X display number | `1` |
| `TAKX_HEALTH_TITLE` | probes | window title pattern of `started` and `ready` | `^TAKX 5\.7[^:]* : .` |
| `TAKX_WEB_LISTEN` | web | listen address | `0.0.0.0:6080` |
| `TAKX_ADDON_VIDEO` | desktop | directory with FFmpeg natives the user supplies (below) | `/opt/takx-addons/video` |
| `TAKX_IDENTITY_SECRET_DIR` | identity | directory with `package.zip` and `password` | `/run/takx/secret` |
| `TAKX_IDENTITY_DIR` | identity | output directory | `/run/takx/identity` |
| `TAKX_IDENTITY_EXPECT_HOST` | identity | reject a package whose connect string names another host | not checked |
| `TAKX_IDENTITY_CONNECT_HOST` | identity | also write `takx-package.zip` with this host in the connect string | not written |

Heap dumps are always switched off after `EXTRA_JVM_OPTS`, and core dumps are disabled: either would put key material into a file.

The entrypoint sets `GDAL_DATA=/opt/takx/share/gdal` and `PROJ_LIB=/opt/takx/share/proj` and refuses to start without those directories. The libraries were built with the paths of their build directories; without the files GDAL logs `ERROR 4: Unable to open EPSG support file gcs.csv`, and `selftest.sh check` fails on that line.

### Video add-on

The image ships no FFmpeg natives. The build TAKX declares (`org.bytedeco:ffmpeg:4.1.3-1.5.1:linux-x86_64`) states "nonfree and unredistributable" in its own binaries, so the assembly leaves it out and the licence gate denies it by that string.

- **Effect.** When a project opens, `MediaPlayerManager.startService` fails with `UnsatisfiedLinkError: no jniavutil in java.library.path`. The error is logged once and the other project services start. The Media dock opens; playback, recording and media length analysis cannot work.
- **Add-on.** Mount a directory of natives for JavaCPP FFmpeg 4.1.3-1.5.1 (`libavutil.so.56`, `libjniavutil.so` and the rest, as they lie under `org/bytedeco/ffmpeg/linux-x86_64/` of a natives JAR) at `/opt/takx-addons/video`, read-only. The entrypoint appends it to `java.library.path` and JavaCPP loads from there. Whoever supplies them is responsible for their licence.
- **Tested.** With such a directory mounted read-only (the 16 libraries of the natives JAR, on 2026-11-26) the error is gone and the JVM maps `libavutil.so.56`, `libavcodec.so.58`, `libavformat.so.58`, `libswresample.so.3` and their four JNI libraries from it. Playing a video was not tested. The chart has no value for this mount.

### Probes

`/opt/takx-desktop/healthcheck.sh MODE` exits 0 when the condition holds and prints one line otherwise.

| Mode | Passes when | Use |
| --- | --- | --- |
| `started` | the JVM runs and a visible window of at least 1024x700 has a title like `TAKX 5.7 SNAPSHOT : <project> ...` | startup probe |
| `ready` | `started`, and the RFB socket exists | readiness probe |
| `live` | the JVM runs and the X server answers | liveness probe |
| `web` | the bridge serves `vnc.html` on port 6080 | web container (or `httpGet /vnc.html`) |

The title must name a project. A bare `TAKX 5.7 SNAPSHOT` frame is what TAKX shows behind a start-up error dialog, and `started` does not accept it. The probes do not look at pixels. `verify/selftest.sh` reads a frame for that.

### Identity stage

`identity` reads the two keys of a copied TakClient Secret, `package.zip` and `password`. It checks the zip entries, the connect string and the certificate validity dates, and writes `package.zip`, `client.p12`, `truststore.p12`, `truststore-java.p12` and `password.txt` with mode 0400. With no Secret mounted it stages nothing and exits 0. It logs names, sizes, dates and public fingerprints only.

The import into TAKX is done by a person in the window. No connection to a TAK Server was attempted or is claimed here.

## How the native libraries resolve

The image build copies the tree byte for byte: it patches no binary and repacks no JAR, so `manifest.json` still describes every file. `verify_tree.py` checks that at build time. The one repacked JAR is made earlier, by the assembly: `../assemble/derive_jar.py` writes the Kernel JAR of the tree as a derived copy and records every difference in `manifest.json` (`kernel_jar_derivation`).

- **Kernel libraries.** The nine libraries sit in the Kernel JAR under unversioned names, while `libtakengine.so` and `libtakenginejni.so` ask for SONAMEs such as `libassimp.so.4`. The stock loader extracts them and maps each one by absolute path in dependency order, so every later request finds an SONAME that is already loaded. The dynamic linker searches no directory.
- **No RUNPATH in the Kernel libraries.** As built, `libtakengine.so` and `libtakenginejni.so` carry a `RUNPATH` of build directories below `/var/lib/takx-proto/P` that ends in an empty element. glibc reads an empty element as the working directory, and the JVM's working directory (`/run/takx/session/bin`) must stay writable for the application user, because TAKX writes there. The derived copy has the `DT_RUNPATH` entry removed from both libraries: 109 and 101 bytes of the dynamic section differ, nothing else, and both digests of each library are in the manifest. The built JAR, its receipt and its controls are untouched; no control was rerun with the copy.
- **Linker trace.** `selftest.sh linker` boots the client under `LD_DEBUG=libs` and fails unless: no Kernel library brings a `RUNPATH`, no search path of any process has an empty or relative element, no file is tried by a relative name, and no line names `/var/lib/takx-proto`. `elf_audit.sh` fails the build on any `RPATH` or `RUNPATH` with an empty or relative element.
- **Gates.** `check_native_closure.py` resolves every `DT_NEEDED` of every bundled Linux x86-64 library to a bundled SONAME, the JDK or `ldconfig`. `elf_audit.sh` runs `ldd -r` on each of them with its siblings visible under their SONAMEs. Both run in the build; `selftest.sh audit` runs the second one again in the finished image.
- **One exception.** `libnewt_head.so` from the JOGL 2.5.0 Maven artifact has 21 undefined symbols (Broadcom VideoCore and EGL helpers, `ceil`, `floor`). That is how JOGL publishes it. The audit counts them on their own line and allows them for that file only.

## Deviations from the design spec

- **Tree file modes.** The spec asked for `--chmod=a-w` on the tree. TAKX copies `conf/Default*.xml` into a new project together with their mode, and with read-only templates the first start ends in an "Access Denied" dialog instead of the globe. The tree keeps its assembled modes (owner root, 0644 and 0755). The client runs as uid 10001 on a read-only root and cannot write there either way.
- **Started probe.** The spec's pattern `^TAKX 5\.7` also matched the bare frame behind that error dialog. The probe now requires a project in the title.
- **X access.** The X server gets an MIT cookie and `-nolisten local`. Without the second, the abstract X socket would be reachable from every container of the pod.
- **Extra files.** `elf_audit.sh`, `verify/build_gates.sh`, `verify/scan_image.py`, `verify/exec_relay.py` and `verify/browser_probe.js` are not in the spec's file list.

## Licences and corresponding source

`gen_notices.py` writes the notices context, which the image carries at `/usr/share/takx-desktop/`: `NOTICE.md`, `LICENSES/` with an index, `SOURCES.json`, `licence-gate.json`, `patches/`, `locks/` and `debian-packages.tsv`. See `NOTICE.md` for what each holds.

`SOURCES.json` identifies the source of every file of the tree, but it is a manifest, not the source. It says `"publishable": false` and lists why. The image must not be distributed until those points are closed (issue #934). Its counts are computed per category: of the fetched inputs, how many have a URL and a SHA-256, how many only a SHA-256, how many were built here, how many are git checkouts without a digest. `LICENSES/INDEX.tsv` has one row per file; the generator fails when the two numbers differ.

### Licence gate

`licence_gate.py` walks the tree, opens every JAR and judges every JAR, native library and asset against `licence-policy.json`. An entry or a loose file that is itself a ZIP archive (the icon sets, an archive inside a JAR) is opened as well, three levels deep, and its entries get the same deny checks. It is not a legal review; it enforces a recorded policy seeded from the compliance work behind issue #934.

- **Denied or unknown fails the build.** `gen_notices.py` runs the gate and fails; `build.sh` refuses to build on such a report; and the image build runs the gate again on the tree inside the image (`verify/build_gates.sh`, gate 6).
- **Deny-list.** Any file that contains the string "nonfree and unredistributable" or `--enable-nonfree`, whatever its POM says; CDDL-only and MPL-1.1-only artifacts; font files and font atlases named after Microsoft fonts.
- **Unknown.** A Maven artifact whose exact version is not in the policy, a native library or a font inside a JAR or a nested archive without a rule of its own, a file without an origin in `manifest.json`, an archive that cannot be read or is nested deeper than three levels, a licence in use whose text is missing from `LICENSES/texts`.
- **Review.** A known licence that permits redistribution but leaves a question for somebody qualified (EPL-2.0 or BSD-4-Clause next to GPL-3.0 code, assets with no statement of their own). These do not fail the build. They keep `"clean": false`, and the image stays `publishable: false`.

`licence-gate.json` in the image lists what is still flagged. On the tree of 2026-11-26 that is 44 items, all under review, none denied or unknown; [STATUS.md](../STATUS.md) groups them. The licence texts in `LICENSES/texts` come from the SPDX license list at a pinned tag, each checked against the SHA-256 in the policy; they are fetched once into `pkg/licence-texts`. The identifiers for POM names such as "BSD License" are a normalisation; the policy says where no text was compared.

Patches named by a build receipt are checked against the SHA-256 in that receipt. When the working tree no longer has that version, the generator takes it from the git history and says so in `SOURCES.json`.

## Not covered here

- Anything in a cluster: the chart, the UDS `Package`, SSO at the gateway, network policy, the Secret copy.
- A TAK Server connection, and the data package import.
- More than one session, arm64, GPU rendering, runs longer than a few minutes.
- The known gaps of the prototype itself. See `../STATUS.md`.
