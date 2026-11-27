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
takx/desktop-prototype/stages/image.sh selftest start|wait|check|auth|browser|reconnect|stats|supervision|audit|linker|identity|stop
```

The unit tests of the token check (`test_web_auth.py`, `test_web_gate.py`) sign their tokens with the `openssl` command and are skipped where it is missing.

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
  --tmpfs /run/takx/web:rw,noexec,nosuid,nodev,size=1m,uid=10002,gid=10002,mode=0700 \
  --mount type=volume,src=takx-vnc,dst=/run/takx/vnc,readonly \
  -e TAKX_AUTH_MODE=none "$IMAGE" web
```

Then open `http://127.0.0.1:6080/`. `TAKX_AUTH_MODE=none` switches the bridge's token check off, and then anyone who reaches the port controls the desktop: publish it on loopback only. Without that variable the web mode wants the whole `TAKX_AUTH_*` configuration ([below](#the-bridges-own-sign-in-check)) and does not start without it. The chart always runs it with the check on.

The self-test does not publish a port. It runs with the check on, against a stand-in for the realm's key endpoint, and reaches the bridge with `docker exec`.

## Entrypoint modes

| Mode | User | Runs | Needs |
| --- | --- | --- | --- |
| `desktop` (default) | 10001:10001 | Xtigervnc on display `:1`, openbox, the TAKX JVM through `launch.sh` | writable `/tmp`, `/run/takx/session` (exec), `/run/takx/vnc` |
| `web` | 10002:10002 plus group 10001 | the gate `web_gate.py` on TCP 6080, which verifies the signed-in user's token; behind it, on a Unix socket, `websockify`: noVNC and the WebSocket to the RFB Unix socket | `/run/takx/vnc` (read-only is enough), writable `/run/takx/web`, the `TAKX_AUTH_*` settings |
| `identity` | 10001:10001 | `stage_identity.py`, then exits | `/run/takx/secret` (read-only), writable `/run/takx/identity` |

tini is PID 1. In `desktop` mode the container ends when Xtigervnc, openbox or the JVM ends. It exits with the JVM's status, or 70 or 71 for the other two. In `web` mode it ends when the gate or websockify ends.

### Writable paths

| Path | Written by | Holds |
| --- | --- | --- |
| `/tmp` | desktop | X socket and lock file |
| `/run/takx/session` | desktop | `ProjectData/`, `home/`, `tmp/` (the JVM's temporary directory), `bin/` (links to `/opt/takx` plus a copy of `resources/`), `jvm.pid`. 226 to 270 MB in the first minutes. Needs `exec`: the Kernel loader extracts its nine native libraries below `tmp/` and maps them from there. **Holds key material once a TAK Server connection is added** (below). |
| `/run/takx/vnc` | desktop | `rfb.sock`, mode 0660, group 10001 |
| `/run/takx/web` | web | `websockify.sock`, mode 0600: the socket websockify listens on, behind the gate |
| `/run/takx/identity` | identity | the staged identity files, mode 0400 |

Everything else is read-only. In Kubernetes these are `emptyDir` volumes with `fsGroup: 10001`; all but `/tmp` are memory-backed in the chart.

**Where the client key is.** The identity stage writes `client.p12` and its password to `/run/takx/identity`. When a person adds the TAK Server connection, TAKX copies both PKCS#12 files into its project, `ProjectData/ProjectData/Globals/Files/SslCerts/` (mode 0644), and keeps their passwords in `ProjectData/ProjectData/Globals/kernel/databases/credentials.sqlite`: under `/run/takx/session`. So the session directory has to be memory too if the key is to stay off disk. The commands above and the chart's default do that. A volume there (the chart's `persistence.enabled`, or `session.memory: false`) puts the key and its password on disk.

### Environment

| Variable | Mode | Meaning | Default |
| --- | --- | --- | --- |
| `EXTRA_JVM_OPTS` | desktop | extra JVM options, as the official `runTakx` task reads them | none (`-Xmx4g` from the official template applies) |
| `TAKX_ARGS` | desktop | TAKX program arguments, space separated | `-create` when `ProjectData` is empty, otherwise none |
| `TAKX_GEOMETRY` | desktop | desktop size | `1920x1080` |
| `TAKX_DEPTH` | desktop | colour depth: 16, 24 or 32 | `24` |
| `TAKX_DISPLAY_NUMBER` | desktop, probes | X display number | `1` |
| `TAKX_HEALTH_TITLE` | probes | window title pattern of `started` and `ready` | `^TAKX 5\.7[^:]* : .` |
| `TAKX_LOCAL_INPUTS` | desktop | `off`: a new session starts with every default network input of TAKX switched off (below). `default`: the client's own defaults | `off` |
| `TAKX_WEB_LISTEN` | web | listen address of the gate | `0.0.0.0:6080` |
| `TAKX_WEB_HEARTBEAT` | web | seconds between WebSocket pings from websockify to the browser | `25` |
| `TAKX_AUTH_MODE` | web | `oidc`: verify the token of every request. `none`: no check (local use only) | `oidc` |
| `TAKX_AUTH_ISSUER` | web | `iss` the token must have | required |
| `TAKX_AUTH_AUDIENCE` | web | client id that must be in `aud` (and equal `azp` when that is present) | required |
| `TAKX_AUTH_GROUPS` | web | JSON list of group paths; the token must name at least one | required |
| `TAKX_AUTH_JWKS_URL` | web | `http` or `https` URL of the realm's key set | required |
| `TAKX_AUTH_GROUPS_CLAIM` | web | claim with the user's groups | `groups` |
| `TAKX_AUTH_ALGORITHMS` | web | comma-separated allow-list out of `RS256`, `RS384`, `RS512`, `ES256`, `ES384` | `RS256` |
| `TAKX_AUTH_LEEWAY_SECONDS` | web | tolerated clock difference for `exp`, `nbf`, `iat` (0 to 300) | `10` |
| `TAKX_AUTH_WS_MAX_SECONDS` | web | lifetime of a WebSocket after its upgrade; `0`: until the expiry of its token | `300` |
| `TAKX_AUTH_ORIGIN` | web | a WebSocket upgrade that names an `Origin` must name this one | not checked |
| `TAKX_AUTH_JWKS_REFRESH_SECONDS`, `TAKX_AUTH_JWKS_MAX_AGE_SECONDS`, `TAKX_AUTH_JWKS_TIMEOUT_SECONDS` | web | refresh interval of the keys, the age after which they are no longer used, fetch timeout | `300`, `900`, `5` |
| `TAKX_AUTH_JWKS_CA_FILE` | web | CA bundle for an `https` key URL, in place of the system's | system CAs |
| `TAKX_ADDON_VIDEO` | desktop | directory with FFmpeg natives the user supplies (below) | `/opt/takx-addons/video` |
| `TAKX_IDENTITY_SECRET_DIR` | identity | directory with `package.zip` and `password` | `/run/takx/secret` |
| `TAKX_IDENTITY_DIR` | identity | output directory | `/run/takx/identity` |
| `TAKX_IDENTITY_EXPECT_HOST` | identity | reject a package whose connect string names another host | not checked |
| `TAKX_IDENTITY_CONNECT_HOST` | identity | also write `takx-package.zip` with this host in the connect string | not written |

Heap dumps are always switched off after `EXTRA_JVM_OPTS`, and core dumps are disabled: either would put key material into a file.

The entrypoint sets `GDAL_DATA=/opt/takx/share/gdal` and `PROJ_LIB=/opt/takx/share/proj` and refuses to start without those directories. The libraries were built with the paths of their build directories; without the files GDAL logs `ERROR 4: Unable to open EPSG support file gcs.csv`, and `selftest.sh check` fails on that line.

### What the JVM listens on

TAKX's defaults make CommonCommo listen on TCP and UDP 4242 and 8087, UDP 10011 and six multicast groups, on every address. The list is the node `CommonCommo/DefaultInputs` of the program properties, each entry with an `enabled` switch; the same switches are in Preferences > Network. TAKX reads `ProjectData/LocalProgramProperties.xml`, which its own source calls "the file expected to be edited by users to make program configuration changes", and copies it from `conf/DefaultProgramProperties.xml` when it does not exist.

With `TAKX_LOCAL_INPUTS=off` the entrypoint writes that file for a new session: the defaults, with all eleven inputs set to `enabled="false"`. An existing file is left alone, and so is the tree. Nothing in a pod can use those inputs; the TAK Server connection is outbound.

- **Seen** (self-test of 2026-11-26): the file has 11 inputs and none enabled; the listeners of the network namespace are 6080 (the gate), one ephemeral TCP port of the JVM on every address, and one on loopback; 4242 and 8087 are closed from the host; no UDP socket is bound to any of the default ports. The globe renders and the log has no new error.
- **Cannot be bound to loopback.** CommonCommo takes only the port of a TCP input (`addTcpInboundInterface(port)`), so an input is on every address or off.
- **Stays open.** The ephemeral port is CommonCommo's HTTPS server for peer-to-peer mission packages. TAKX starts it unconditionally (`CommonCommoManager.configureTakServerUsage`, `setMissionPackageLocalHttpsParams`) with a throw-away certificate; there is no setting for it. In a cluster only NetworkPolicy and the mesh policy keep other pods from it; a process on the node reaches it.
- **Can be switched back on** by a person in Preferences > Network, for the life of the session.

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
| `web` | the gate answers `GET /healthz` on port 6080 with `ok` and websockify's socket exists | web container |

`/healthz` is the one path that needs no token. It says that the gate runs and nothing more: an `httpGet` probe without a token can see no other path, so the chart uses exec probes.

The title must name a project. A bare `TAKX 5.7 SNAPSHOT` frame is what TAKX shows behind a start-up error dialog, and `started` does not accept it. The probes do not look at pixels. `verify/selftest.sh` reads a frame for that.

### Identity stage

`identity` reads the two keys of a copied TakClient Secret, `package.zip` and `password`. It checks the zip entries, the connect string and the certificate validity dates, and writes `package.zip`, `client.p12`, `truststore.p12`, `truststore-java.p12` and `password.txt` with mode 0400. With no Secret mounted it stages nothing and exits 0. It logs names, sizes, dates and public fingerprints only.

The import into TAKX is done by a person in the window. No connection to a TAK Server was attempted or is claimed here.

## The bridge's own sign-in check

Reaching port 6080 is not enough. `web_gate.py` owns the port; websockify, which serves noVNC and carries the desktop, listens on a Unix socket of mode 0600 that only the web user can open. The gate passes a request on only after `web_auth.py` has accepted its token. Both are standard-library Python, under 900 lines together with their comments.

### What a request must carry

In a UDS cluster Authservice adds the OpenID Connect ID token of the signed-in session to every request as `Authorization: Bearer <JWT>` (UDS Core 1.16.0: `id_token: {preamble: Bearer, header: Authorization}`), and the waypoint's RequestAuthentication forwards it unchanged (`forwardOriginalToken: true`). The gate checks, in this order:

| Check | Rule | Answer otherwise |
| --- | --- | --- |
| Header | exactly one `Authorization` header, `Bearer` and one compact JWS of at most 16 KiB | 401 |
| Algorithm | `alg` is in `TAKX_AUTH_ALGORITHMS`. `none` and the HMAC algorithms cannot be put on that list | 401 |
| Key | `kid` names a signing key of the realm's key set, of the kind the algorithm needs. A key in the token itself (`jwk`, `jku`, `x5c`, `x5u`) is never read | 401; 503 when no keys are held |
| Signature | verifies over header and payload | 401 |
| `iss` | equals `TAKX_AUTH_ISSUER` | 401 |
| `aud`, `azp` | `aud` contains `TAKX_AUTH_AUDIENCE`; `azp`, when present, equals it | 401 |
| `exp`, `nbf`, `iat` | `exp` is present and not passed; the other two, when present, are not in the future; `TAKX_AUTH_LEEWAY_SECONDS` of tolerance | 401 |
| Groups | the claim `TAKX_AUTH_GROUPS_CLAIM` is a list that names at least one of `TAKX_AUTH_GROUPS` | 403 |

A cookie, a query parameter (`?token=`), another scheme (`Basic`), another header: none of them is a credential. Every refusal is a fixed answer of a few bytes and a closed connection; websockify is not contacted, so no byte of noVNC or of the desktop is sent. After the token, only `GET` and `HEAD` go on (405); a WebSocket upgrade must ask for `/websockify` (404) and, when it names an `Origin` and `TAKX_AUTH_ORIGIN` is set, come from that origin (403). The request websockify gets is written by the gate from five fields; the token, cookies and all other headers stay behind. One request per connection: each answer says `Connection: close`.

`GET /healthz` is answered `200 ok` without a token.

### Keys

The keys come from `TAKX_AUTH_JWKS_URL` (no proxy, no redirect, certificate verification always on for `https`, at most 256 KiB). They are fetched at start and again after `TAKX_AUTH_JWKS_REFRESH_SECONDS`. When a refresh fails, the keys held are used until they are `TAKX_AUTH_JWKS_MAX_AGE_SECONDS` old and never after that: a bridge without usable keys answers 503 to every token. A token with an unknown `kid` causes one fetch, at most every ten seconds: that is how a rotated key is picked up, and a withdrawn key stops working with the next refresh. Only RSA keys of 2048 bits or more and P-256 or P-384 keys with `use: sig` are taken.

### Sessions

- **An open WebSocket ends** `TAKX_AUTH_WS_MAX_SECONDS` after its upgrade (300 by default), with a connection reset: nothing still queued is delivered. With `0` it ends at the `exp` of the token it was opened with. The start page asks noVNC to reconnect after one second (`reconnect=1`), and the new upgrade has to pass the whole check again with a token that is valid then.
- **Why not `0` by default.** The UDS realm issues tokens for 60 seconds (`accessTokenLifespan`), so the screen would reconnect every minute. With 300 a desktop outlives the end of its session at Keycloak by at most five minutes.
- **Every other request** needs a token that is valid at that moment, so a page load after a session has ended gets nothing from the bridge.

### Tested

| What | How | Result on 2026-11-26 |
| --- | --- | --- |
| The rules above, one by one | `test_web_auth.py`, 35 tests: tokens signed by the OpenSSL command with RSA 2048, P-256 and P-384 keys made in the test; accepted, and rejected for a wrong signature, issuer, audience, expiry, not-before, group, `alg: none`, HMAC with the public key as the secret, unknown `kid`, oversized header, another scheme; fetch failure fails closed; rotation picks up a new `kid`; the two signature checks against OpenSSL's signatures, an exponent-3 forgery and an off-curve key | pass on Python 3.11, 3.12 and 3.13 |
| The gate with real sockets | `test_web_gate.py`, 24 tests: a stand-in websockify records what it is sent; no refusal contacts it; token in the query string, in a cookie, twice; request bodies and pipelined requests; request heads built to make a pattern backtrack; the WebSocket cut at expiry, also for a client that does not read | pass on the three Pythons |
| The image, no cluster | `selftest.sh auth` (`verify/auth_probe.py`): 20 request variants over HTTP and as a WebSocket upgrade against the running bridge, from inside the container and from the host's network namespace to the container's address | only the correct token got the page (200) and the RFB banner `RFB 003.008`; every other variant got 401, 403 or 431 and at most 32 bytes |
| Token expiry on an open WebSocket | a second bridge with `TAKX_AUTH_WS_MAX_SECONDS=0` and a 20-second token | 8.3 MB of screen received, connection reset 0.0 s after `exp`, the same token then refused with 401 |
| No keys | a third bridge whose key URL does not answer | 503 for every well-formed token, nothing of the desktop |
| A still screen | `auth_probe.py --hold`: a WebSocket that completes the RFB handshake and then asks for nothing | open after 65 seconds with the bridge's pings answered; one such WebSocket was left alone for the whole limit: 11 pings in 300 seconds, then ended by the bridge |
| A browser | `selftest.sh browser` and `reconnect`: Chromium through `exec_relay.py`, which adds the token as Authservice does | the desktop draws; with a 20-second limit noVNC reconnected by itself each time |

### What it does not do

- **Root on the node** can enter the pod's namespaces, read its memory and open the Unix sockets. The check closes the network path, not that one. `kubectl exec` and `port-forward` with cluster rights: `exec` is inside the pod, `port-forward` to 6080 meets the gate.
- **A valid token is a valid token** wherever it is sent from. The gate does not know the path a request took.
- **Not seen with Keycloak and Authservice.** The header and the claims were read from the UDS Core and identity-config sources (`groups` comes from the realm's `profile` scope with full paths). No token of a real realm has been through the gate yet. Whether Authservice has a fresh token ready when noVNC reconnects is the first thing to look at in a cluster.
- **One shared desktop.** The gate logs the `sub` of each request; TAKX knows nothing of it.
- At most 256 connections at once, 10 seconds for a request head. A flood from the node can keep others out for that long; it cannot get in.
- While Keycloak is slow to answer, a request that needs a key fetch waits for it, up to the fetch timeout (5 seconds), at most once in ten seconds.

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
- **Extra files.** `elf_audit.sh`, `verify/build_gates.sh`, `verify/scan_image.py`, `verify/exec_relay.py` and `verify/browser_probe.js` are not in the spec's file list. Nor are the bridge's check and its tests: `web_gate.py`, `web_auth.py`, `verify/token_tool.py`, `verify/auth_probe.py`, `verify/browser_reconnect.js`.
- **Login.** The spec left access control to the gateway. The bridge now checks the token itself, and TAKX's default inputs are off.

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

- Anything in a cluster: the chart, the UDS `Package`, SSO at the gateway, network policy, the Secret copy, and the bridge's check with tokens of a real realm.
- A TAK Server connection, and the data package import.
- More than one session, arm64, GPU rendering, runs longer than a few minutes.
- The known gaps of the prototype itself. See `../STATUS.md`.
