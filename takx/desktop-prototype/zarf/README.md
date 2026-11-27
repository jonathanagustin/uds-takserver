<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Deploy as a package

The TAKX 5.7 desktop prototype as a Zarf package (`takx-desktop`) and a UDS bundle (`takx-desktop-proto`): the client runs in one pod and is used from a browser at `https://takx.<domain>/`, behind the UDS tenant gateway and Keycloak.

**Deployed once, on one sandbox cluster, on 2026-11-26, and removed again.** [What the deploy showed](#what-the-deploy-showed) has the results, among them two things to know before using it: a process on the node itself reached the desktop without signing in, and deploying or removing the package restarts Authservice for every application behind it. The bundle has still not been deployed. [Still not shown](#still-not-shown) lists what is open.

**Changed since that deploy, and not deployed yet:** the bridge in the pod now verifies the signed-in user's token itself, TAKX starts with its default network inputs off, and the session directory, where TAKX keeps its own copy of the client key, is in memory. [What changed after the deploy](#what-changed-after-the-deploy) says what that is, what was tested without a cluster and what the next deploy has to show.

What the package carries is TAKX 5.7 (`0b59a9bb`) built from public sources on Kernel 8.30.5 + project patches, CommonCommo 1.17.2 patched and the NASA WorldWind interop build. It is not the official TAKX 5.7 distribution. The image is local only and is **not cleared for distribution**: its `SOURCES.json` says `"publishable": false` and lists why, and the licence gate still flags 44 items for review (issue #934; the `LICENCE-GATE` attachment lists them). This directory is opt-in; the TAK Server package, its bundles and CI do not use it.

## What is in it

| Path | Holds |
| --- | --- |
| `zarf.yaml` | Zarf v1beta1 package: names, descriptions, selection, values and attachments. It composes the four component files with `import.local`. [The definition in a built package](#the-definition-in-a-built-package) says what Zarf stores |
| `components/image.yaml` | the one image, ID `desktop` |
| `components/takclient.yaml`, `manifests/takclient.yaml` | a `TakClient` (group `tak.uds.dev`) in namespace `takclients` |
| `components/identity.yaml`, `manifests/identity-info.yaml` | the hand-over of the TAK identity to the desktop namespace |
| `components/desktop.yaml`, `image-values.yaml` | the chart `../chart` and its value mappings |
| `values.yaml`, `values.schema.json` | package values |
| `create-package.sh` | builds the package from a staged copy |
| `test_package.py` | checks without a cluster; helper modes for `../stages/package.sh` |
| `../chart` | Helm chart `takx-desktop`: Deployment (with the settings of the bridge's token check), Service, ServiceAccount, optional PersistentVolumeClaim, UDS `Package`, the ingress isolation (a NetworkPolicy and an Istio AuthorizationPolicy) and a DestinationRule for the bridge |
| `../bundle` | `uds-bundle.yaml`, `uds-config.yaml`, `create-bundle.sh` |
| `../stages/package.sh` | every build and check command of this page, step by step |
| `../stages/deploy.sh` | the deploy and its checks: baseline, deploy, admission, identity, a frame of the pod's display, the sign-in gate, isolation, pod replacement, removal |
| `../server/deploy_browser.cjs`, `deploy_probe.py`, `deploy_devusers.py` | the browser driver, the TCP probe and the test-user file writer that `deploy.sh` uses |

### Components

| Component | Selected | Does |
| --- | --- | --- |
| `takx-desktop-image` | always (amd64) | carries the image |
| `takx-desktop-takclient` | optional | creates `TakClient` `takx-browser` (callsign `takx-browser`, group `Operators`). The TAK Server package's `takserver-oidc` module issues the certificate and writes the device Secret |
| `takx-desktop-identity` | optional | waits for the `TakClient` to be `Ready`, then copies `package.zip` and `password` of its Secret to `takx-desktop/takx-desktop-identity` |
| `takx-desktop` | always | installs the chart and waits for the UDS `Package` and the Deployment |

Order matters: the identity is in place before the desktop starts, so the first start already has it.

### The pod

One Deployment, one replica, one image, three containers. The image's own [README](../image/README.md) describes the modes.

| Container | User | Does | Sees |
| --- | --- | --- | --- |
| `identity` (init) | 10001 | validates the copied Secret, stages `client.p12`, `truststore.p12`, `truststore-java.p12`, `password.txt`, `package.zip` and `takx-package.zip` | the Secret (read-only), the identity volume |
| `desktop` | 10001 | Xtigervnc on a Unix socket, openbox, the TAKX JVM. The display has no TCP listener. The JVM would listen on TCP 4242 and 8087 (CommonCommo's default inputs); a new session starts with those switched off (`takx.localInputs`). One ephemeral HTTPS port of CommonCommo stays. [Which ports can be reached](#which-ports-can-be-reached) says what keeps it closed | `/tmp`, the session volume (memory), the socket volume, the identity volume (read-only) |
| `web` | 10002 | the gate on TCP 6080, which verifies the token of every request; behind it, on a Unix socket, websockify: noVNC, bridged to the RFB socket | the socket volume (read-only), a volume of its own for websockify's socket, nothing else |

Every container has a read-only root filesystem, drops all capabilities and cannot gain privileges. The service account has no token and no RoleBinding.

**Reaching port 6080 is no longer enough.** The gate answers 401 or 403 unless the request carries the token Authservice adds after a sign-in: signed by the realm, issued for this package's client, not expired, and naming one of `sso.groups` ([The bridge's own check](#the-bridges-own-check)). The chart has no switch for that either. For pods and for users the way in is still the tenant gateway with Authservice; a process on the node gets as far as the gate.

The gate serves one request per connection and closes it. The chart's DestinationRule `takx-desktop-bridge` tells the waypoint the same ([The DestinationRule](#the-destinationrule)).

### Which ports can be reached

With the image of the deploy the pod listened on four TCP ports on every address: 6080 (the web bridge), and from the TAKX JVM 4242, 8087 and one ephemeral port; a fifth listener of the JVM is on loopback only. With the image of today the JVM's default inputs are off, and the self-test read two from `/proc/net/tcp`: 6080 (the gate) and the ephemeral port. Only 6080 is meant to be reached, and only along tenant gateway → Authservice → pod. The chart says so in three places, and each has its own enforcer:

| Object | What it says | Enforced by |
| --- | --- | --- |
| UDS `Package` (`templates/uds-package.yaml`) | `expose` names port 6080 on the tenant gateway behind Authservice; `allow` has egress rules only, so no ingress rule opens another port | the UDS operator, which generates a default-deny NetworkPolicy for the namespace, one ingress allowance for 6080 from the gateway path, and the ALLOW AuthorizationPolicy for that port |
| `NetworkPolicy` `takx-desktop-ingress-isolation` (`templates/ingress-isolation.yaml`) | selects the pod, policy type Ingress, no ingress rule: the pod is isolated for ingress whatever else exists in the namespace. NetworkPolicies only add up, so the operator's allowance for 6080 still applies | the cluster's NetworkPolicy controller (kube-router in k3s; this sandbox's k3s runs with it on) |
| `AuthorizationPolicy` `takx-desktop-deny-other-ports` (same file; `network.denyOtherPorts`, default on) | action DENY for every destination port that is not 6080, from any source | ztunnel: the namespace is in the ambient mesh, so every inbound connection to the pod passes it. DENY is evaluated before any ALLOW |

Kubelet probes are exec probes and need no ingress.

What the deploy of 2026-11-26 showed, port by port, with the image of that day, which had no check of its own and listened on 4242 and 8087 (`deploy.sh isolation`; "refused" means no byte came back):

| From | 6080 | 4242, 8087, the ephemeral port | Stopped by |
| --- | --- | --- | --- |
| a pod in another namespace, outside the mesh | refused | refused | NetworkPolicy: the connection is rejected |
| a pod in another namespace, in the mesh | refused | refused | its ztunnel sends everything for the pod to the pod's waypoint; NetworkPolicy rejects it there |
| another pod in `takx-desktop` | refused | refused | the same, and the namespace's default-deny egress |
| the same pods, with a temporary NetworkPolicy that let them in | Authservice answers at the waypoint (no session: no desktop); straight to the pod IP, ztunnel closes it: "explicitly denied by: takx-desktop/deny-all-except-waypoint-uds-takx-desktop-waypoint" | ztunnel closes it: "explicitly denied by: takx-desktop/takx-desktop-deny-other-ports" | the mesh layer alone |
| **the node** (a process in the host network namespace) | **reached: HTTP 200, the noVNC page, no sign-in** | **reached: the JVM accepts the connection** | nothing |

The last row is a limit of the platform, not a setting of this chart. kube-router adds, for every pod, "rule to permit the traffic to pods when source is the pod's local node", and Istio ambient rewrites the source of host traffic to `169.254.7.127` and keeps it away from ztunnel so that kubelet probes work. With that image a user with a shell on the node, or a pod with `hostNetwork` (which UDS Core refuses without an exemption), controlled the desktop without Keycloak.

No policy can close that row, so the image does: the bridge checks the token itself and TAKX's default inputs are off. In the image self-test, which reaches the container's address from the host's network namespace as a node reaches a pod, port 6080 answered 401 without a token and 4242 and 8087 were closed. **That row has not been repeated in a cluster.** What stays: the JVM's ephemeral HTTPS port is reachable from the node; `kubectl exec` is inside the pod; and root on the node can enter the pod's namespaces whatever the pod does.

The sign-in path through the gateway worked with the DENY policy in place, so `network.denyOtherPorts=false` was not needed.

### What the UDS `Package` declares

- **Sign-in:** Keycloak client `uds-takx-desktop`, Authservice in front of the pod, group `/UDS Core/Admin` (`sso.groups`). Everyone admitted shares the one session and the one TAK identity. Keycloak enforces the group when it issues the token; the bridge enforces it again when it reads the token.
- **Exposed:** Service `takx-desktop` port 6080 on the tenant gateway, host `takx`.
- **Egress, default:** TCP 8089 to the passthrough gateway pods, and TCP 8080 to the Keycloak pods, where the bridge fetches the realm's signing keys. Nothing else. DNS comes from the operator. A NetworkPolicy selects pods, not containers, so the desktop container can reach Keycloak's port as well.
- **Egress, opt-in:** TCP 443 to the same gateway (`tak.egress.api`) and TLS 443 to named hosts (`egress.hosts`, for example a tile server).
- **Never:** `Anywhere`, `KubeAPI` or `IntraNamespace` rules, policy exemptions.

In the deploy (before the Keycloak rule existed) the operator made of this: Keycloak client `uds-takx-desktop`, a waypoint (`uds-takx-desktop-waypoint`, one more pod in the namespace) that carries the Authservice check, eleven NetworkPolicies and four AuthorizationPolicies. From the desktop container, a TLS handshake to `takserver:8089` verified the server certificate against the staged CA. Nothing else answered: the Kubernetes API (ClusterIP, name and node address), the kubelet, `1.1.1.1:443`, `example.com:443`, `tak.gov:443`, the metadata address, the same gateway on 443 and 8446, the TAK Server Services directly, Keycloak, CloudTAK, the tenant gateway, the Zarf registry and Authservice. DNS resolves, external names included. For each of these the pod's ztunnel accepts the TCP connection and resets it, so a client sees "connection reset", not a timeout.

`tak.egress.api` is off because it would not help: TAKX 5.7 asks `https://<address>:8443` for missions and contacts, the gateway does not serve 8443, and on 443 it routes by an SNI name TAKX does not send ([STATUS.md](../STATUS.md), "HTTPS API from TAKX"). Without `egress.hosts` there are no online map tiles, only the built-in base map.

### The bridge's own check

[`../image/README.md`](../image/README.md), "The bridge's own sign-in check", has the rules, the settings and the tests. In the chart:

| Setting of the gate | Comes from | Default |
| --- | --- | --- |
| issuer | `auth.issuer`, or `https://sso.<domain>/realms/uds` | `https://sso.uds.dev/realms/uds` |
| audience | `sso.clientId` | `uds-takx-desktop` |
| required groups | `sso.groups`, the list the UDS `Package` gives Keycloak | `/UDS Core/Admin` |
| groups claim | `auth.groupsClaim` | `groups` |
| key set | `auth.jwksUrl` | `http://keycloak-http.keycloak.svc.cluster.local:8080/realms/uds/protocol/openid-connect/certs` |
| algorithms | `auth.algorithms` | `RS256` |
| allowed WebSocket origin | `https://<host>.<domain>` | `https://takx.uds.dev` |
| WebSocket lifetime | `auth.websocketMaxSeconds` | 300 |

**Why this header.** UDS Core 1.16.0 configures Authservice with `id_token: {preamble: "Bearer", header: "Authorization"}`, and the RequestAuthentication the operator generates for the waypoint has `forwardOriginalToken: true` (seen in the deploy: `uds-takx-desktop-jwt-authn`, issuer `https://sso.uds.dev/realms/uds`, audience `uds-takx-desktop`). So what reaches the pod is `Authorization: Bearer <ID token>`. The realm of identity-config 0.32.4 writes `groups` with full paths into the ID token (scope `profile`) and signs with RS256.

**Why the keys come from the Service.** The bridge asks Keycloak inside the cluster, over the mesh, not `https://sso.<domain>` through the gateway: no certificate of the gateway has to be trusted in the pod, and the answer cannot depend on what a public name resolves to. The realm publishes the same keys at both. Keycloak's own `Package` lets every namespace reach its port 8080 ("Keycloak backchannel access"), and the UDS operator talks to the same Service. Not tried in a cluster yet: if the fetch fails, the web container's log says `jwks: fetch failed` every ten seconds and every request with a token gets 503.

**Where the group is enforced now.** In the deploy the group lived only in Keycloak, and three ways around Authservice had not been tried: a request with an `Authorization` header that is not `Bearer` (such requests skip Authservice by rule and are judged by the waypoint's JWT policy alone), a token of the realm for another client, and HTTP/1.1 from a workload in the mesh to the waypoint. Whatever the waypoint does with them, the bridge refuses all three: it takes one `Bearer` token and nothing else, wants this client's audience, and reads the group itself. Tried against the image; in a cluster they are still to be tried.

**A token for a test.** Without a cluster, `../image/verify/token_tool.py` makes a key, a JWKS document and signed tokens with OpenSSL, and `../image/verify/auth_probe.py` sends them (`selftest.sh auth`). In a cluster the token has to come from the realm: run the authorization-code flow for the client `uds-takx-desktop` (redirect URI `https://takx.<domain>/login`, the client secret is in the Secret the operator writes for the client in namespace `takx-desktop`) with a user in the group, and use the `id_token` of the answer within its 60 seconds. `auth_probe.py` takes header lines, a `--connect` address and `--tls`, so the same variants can be sent to the pod address from the node and to the gateway.

### Sessions: how they end

As designed and as read from the sources; **not observed in a cluster**.

- **The token.** The realm issues ID tokens for 60 seconds (`accessTokenLifespan`); the Keycloak session lasts 10 minutes without use and 10 hours at most (`ssoSessionIdleTimeout`, `ssoSessionMaxLifespan`). Authservice keeps the session, with no time limits of its own (`idle_session_timeout` and `absolute_session_timeout` are "0" in UDS Core 1.16.0), and has to get a fresh token from Keycloak when the one it holds has expired.
- **A page load** after the session has ended at Keycloak gets a redirect to the sign-in from Authservice, and from the bridge nothing: it wants a token that is valid now.
- **An open WebSocket** is closed by the bridge `auth.websocketMaxSeconds` after it was opened, 300 by default. noVNC shows "Reconnecting" and tries again after a second. If the session is alive, Authservice adds a fresh token and the desktop is back; seen with a browser against the image with a 20-second limit, about 1.3 seconds each time. If the session has ended, the upgrade is redirected to the sign-in, which a WebSocket cannot follow: the page keeps trying until it is reloaded, and the reload leads to the sign-in.
- **So a logout or a disabled user loses an open desktop after at most five minutes**, and everything else when the token Authservice still holds has run out, 60 seconds at the most. With `auth.websocketMaxSeconds: 0` the open desktop goes with that token too, at the price of a reconnect every minute.
- **Logout.** `https://takx.<domain>/logout` is Authservice's logout path for the client; it ends the Authservice session and sends the browser to Keycloak's logout. The TAKX session in the pod is not touched: it is one desktop for everyone in the group.
- **An open desktop keeps the session alive.** Every reconnect makes Authservice refresh the token, which counts as use at Keycloak, up to the 10 hours.
- **Open question for the deploy.** Whether Authservice has a valid token ready on the reconnect, a request that cannot follow a redirect. If the web container logs `denied: expired` for requests of a signed-in browser, raise `auth.leewaySeconds` (the waypoint's own JWT filter tolerates 60 seconds) and note it.

### The DestinationRule

`templates/bridge-connections.yaml` has two settings and the reasons in full.

- `maxRequestsPerConnection: 1`: the gate closes every connection after one request, and Envoy is told so. It was added when websockify faced the waypoint and kept a process per pooled connection ([Findings](#findings)); the gate has removed that cause.
- `idleTimeout: 30s` closes connections without an active request. It is not a limit for a WebSocket, which for Envoy is one active request however quiet it is.
- So that a quiet session does not depend on that reading, websockify pings the browser every 25 seconds (`web.heartbeatSeconds`; the schema keeps it below 30). Against the image, with no Envoy on the path, a WebSocket that asked for no screen at all stayed open for the whole 300 seconds, 11 pings answered, until the bridge ended it at its limit (`auth_probe.py --hold`).
- Seen in a cluster so far: page loads of a few seconds with the rule; 14 and 43 minutes without it. Long and quiet sessions with the rule are for the next deploy.

### How the TAK identity reaches the pod

1. `TakClient` `takx-browser` in `takclients` (this package, or one that already exists).
2. The operator writes Secret `takclients/takx-browser` with five keys ([ADR 22](../../../adr/0022-takclient-operator.md)).
3. The `takx-desktop-identity` component copies two of them, `package.zip` and `password`, into `takx-desktop/takx-desktop-identity`. The person who deploys does this with their own access; nothing in the cluster may read Secrets of another namespace. The data goes from one `kubectl` to the next through a pipe and is not printed or written to a file.
4. The init container checks the package (entries, connect string, certificate dates) and stages the files under `/run/takx/identity`, in memory, mode 0400.
5. A person adds the connection in the TAKX window. See [After the deploy](#after-the-deploy).

The copy is not kept in step. `TakClient` certificates last 30 days by default; after a renewal or reissue, deploy `takx-desktop-identity` again. The copied Secret carries `uds.dev/pod-reload`, so UDS Core should then restart the pod; that restart was not exercised.

A reissue is not rare on this server. When the sandbox VM restarted on 2026-11-26, every `TakClient` got a new certificate as the TAK Server came back and the old ones were answered with "Attempt to use revoked certificate" (`../server/README.md`, "After a VM restart"). A desktop that was running then would hold a revoked identity until the component is deployed again.

### The server name

TAKX checks the name it dials against the TAK Server certificate, and that certificate names only `takserver`. The data package the operator issues says `takserver-stream.<domain>`, which TAKX refuses ([STATUS.md](../STATUS.md), "Rejected connections"). So:

- the pod gets a hosts entry `takserver` → the passthrough gateway. The address is the ClusterIP of Service `istio-passthrough-gateway/passthrough-ingressgateway`, read with a Helm `lookup` at install, or `tak.gateway.ip` when set;
- the init container also stages `takx-package.zip`, the same package with the connect string `takserver:8089:ssl`.

A render without a cluster (`helm template`, `zarf dev inspect manifests`) finds no Service and writes no hosts entry. Inside the Zarf deploy the lookup worked: the pod got `10.43.83.212 takserver`, the ClusterIP of the gateway Service.

## Build

The image comes first: `../stages/image.sh` builds `ghcr.io/jonathanagustin/uds-takserver/takx-desktop:<TAG>` into the local Docker image store. `<TAG>` ends in the first 12 digits of the assembled tree's manifest digest.

```bash
TAG=5.7-0b59a9bb-proto-<digest12>
takx/desktop-prototype/zarf/create-package.sh "$TAG" --confirm -o /var/lib/takx-proto/pkg/out
```

`create-package.sh` works on a staged copy. It sets the tag, reads the image from the Docker daemon (`TAKX_IMAGE_SOURCE=registry` for a published image) and writes the attachments out of the image itself, so the package cannot carry notices that differ from its image. It stops when the image label, `SOURCES.json` and the tag name different trees. No network is needed.

**The image is pinned by digest.** The tag only names the assembled tree; a rebuild of the image under the same tag has another digest. The staged `components/image.yaml` and the chart's default therefore name `<repository>:<TAG>@sha256:<digest>`. With the Docker daemon as the source the digest is the one the local image store has for the tag (with the containerd image store, the manifest digest, which is also the image ID); with `registry` the caller must give `TAKX_IMAGE_DIGEST`, and without a digest nothing is staged. `package.sh inspect` decompresses the built archive and fails unless the one manifest in its OCI layout has the digest `stages/image.sh` recorded.

### The definition in a built package

The sources are `zarf.dev/v1beta1` throughout, and Zarf 1.0.1 creates the package from them. The `zarf.yaml` inside the built archive nevertheless begins with `apiVersion: zarf.dev/v1alpha1`: Zarf 1.0.1 writes two YAML documents there, first its own conversion of the definition to `v1alpha1`, then the `v1beta1` definition. A reader that takes the first document sees `v1alpha1`; `uds zarf package inspect definition` prints the second. `zarf package create` of 1.0.1 has no option that leaves the first document out, so this is recorded, not changed. The build data in both documents says which CLI the package needs (`v0.88.0` or later, for the `v1beta1` deploy hooks). `package.sh inspect` keeps the stored file as `pkg/package/inspect/zarf.yaml-as-stored` and checks that it holds exactly these two documents.

Read the result back:

```bash
uds zarf package inspect definition <package>
uds zarf package inspect images <package>
uds zarf package inspect sbom <package> --output sbom/
uds zarf package inspect documentation <package> --output attachments/
```

The attachments are `README` (this file), `NOTICE`, `SOURCES` (source revisions, archive digests and the component of every file), `LICENCE-GATE` (the licence gate's report: every item it still flags), `LICENSES` (licence texts), `PATCHES` (the patches and lock files) and `DEBIAN-PACKAGES` (binary and source package versions).

`../stages/package.sh all` runs every check on this page and the build, and keeps a transcript of each step under `/var/lib/takx-proto/pkg/package/evidence/`.

## Deploy

Use the pinned CLI (`mise.toml`: uds 0.42.0, Zarf 1.0.1). The package says in its build data that it needs Zarf 0.88.0 or later.

### Onto a cluster that already runs UDS Core and TAK Server

The cluster needs:

- UDS Core with the tenant gateway, Keycloak and Authservice, and the passthrough gateway with TCP 8089 in its Service ports (`bundle/uds-bundle.yaml` of this repository sets that);
- the TAK Server package 1.4.0 or later, for the two identity components;
- an amd64 node with room for the requests (550m CPU, about 2.6 GiB) and the desktop's 5 GiB memory limit, and about 4 GiB of free disk for the image in the registry and on the node. The limit now covers the session files as well: the processes used 2.0 GiB, peak 2.1 GiB, in the deploy, a fresh session is 0.23 to 0.27 GiB of files, and the volume may grow to 1 GiB (`session.sizeLimit`), 3.2 GiB together at the most. In the image self-test, where the session has always been in memory, the container peaked at 1.8 and 2.0 GiB in two runs with both;
- `takx.<domain>` resolving to the tenant gateway;
- a Keycloak user in `/UDS Core/Admin`.

```bash
uds zarf package deploy zarf-package-takx-desktop-amd64-$TAG.tar.zst \
  --components takx-desktop-takclient,takx-desktop-identity --confirm
```

To use a `TakClient` that already exists, leave `takx-desktop-takclient` out and name it:

```bash
uds zarf package deploy zarf-package-takx-desktop-amd64-$TAG.tar.zst \
  --components takx-desktop-identity --set-values identity.name=takx-desktop --confirm
```

Do not run two clients on one certificate: stop any other client that uses that identity first.

Without `--components` the desktop starts with no identity.

`../stages/deploy.sh preflight deploy` does the same after it has checked memory and disk and recorded a baseline of the cluster. To take it out again:

```bash
uds zarf package remove takx-desktop --confirm
uds zarf tools kubectl delete namespace takx-desktop   # Zarf leaves it
```

Both the deploy and the removal restart Authservice ([Findings](#findings)).

Package values are set with `--set-values`: `global.domain`, `identity.name`, `identity.callsign`, `identity.group`, and every chart value under `desktop.` ([`../chart/values.yaml`](../chart/values.yaml)), for example `--set-values desktop.host=takx2` or `desktop.jvm.extraOpts=-Xmx3g`. `desktop.image` is ignored.

### As a bundle

`../bundle/uds-bundle.yaml` lists the whole stack for an empty cluster: Zarf init `v1.0.1`, `core-base` and `core-identity-authorization` `1.16.0-upstream` (the two layers of the `k3d-core-slim-dev` checkpoint), `takserver` `1.4.0-upstream` with CloudTAK, and this package with both optional components.

```bash
# Only this package: for a cluster that already has the rest. Nothing is pulled.
takx/desktop-prototype/bundle/create-bundle.sh /var/lib/takx-proto/pkg/out "$TAG" \
  --packages takx-desktop --confirm -o /var/lib/takx-proto/pkg/out
uds deploy uds-bundle-takx-desktop-proto-partial-amd64-$TAG.tar.zst --confirm

# The whole stack. Pulls about 2.7 GB; uds needs about 12 GB of free disk while it works.
takx/desktop-prototype/bundle/create-bundle.sh /var/lib/takx-proto/pkg/out "$TAG" --confirm
UDS_CONFIG=takx/desktop-prototype/bundle/uds-config.yaml \
  uds deploy uds-bundle-takx-desktop-proto-amd64-$TAG.tar.zst --confirm
```

From the whole bundle, `uds deploy ... --packages takx-desktop` deploys this package alone.

Bundle variables for the desktop: `DOMAIN`, `TAKX_HOST`, `TAKX_JVM_OPTS`, `TAKX_DESKTOP_MEMORY_LIMIT`, `TAKX_GATEWAY_IP`. A bundle cannot set package values with this CLI, so in a bundle the `TakClient` is always `takx-browser`.

The bundle does not make a cluster. Load balancer addresses for the three gateways and DNS for `<domain>` are the cluster's business; `server/README.md` records what this sandbox needed.

## After the deploy

Done in the pod on 2026-11-26, twice (first pod, and its replacement after a delete). The form was filled with `xdotool` inside the pod, not by hand in the browser.

1. Open `https://takx.<domain>/` and sign in. The page is noVNC; the TAKX window appears with a new project.
2. In TAKX: Home > TAK Server > Add. Address `takserver`, port `8089`, protocol SSL, CA file `/run/takx/identity/truststore.p12`, client certificate `/run/takx/identity/client.p12`. It connected on the first attempt both times.
3. Both files have the same password, in `/run/takx/identity/password.txt`. The browser session cannot read it and the clipboard is off. Click into a password field, then have someone with cluster access type it into the focused field, without it leaving the pod:

   ```bash
   uds zarf tools kubectl exec -n takx-desktop deploy/takx-desktop -c desktop -- sh -c \
     'export DISPLAY=:1 XAUTHORITY=/run/takx/session/home/.Xauthority
      tr -d "\r\n" </run/takx/identity/password.txt | xdotool type --delay 25 --file -'
   ```

4. The connection is saved in the project. With `persistence.enabled: false` (the default) the project is lost with the pod: the replacement pod started with a new project, a new callsign and no connection, and the connection had to be added again.

**Where the client key is afterwards.** TAKX copies both PKCS#12 files into its project (`ProjectData/ProjectData/Globals/Files/SslCerts/`, mode 0644) and keeps their passwords in a credential database next to them. The project is in the session volume:

| Values | Session volume | The key and its password |
| --- | --- | --- |
| default (`session.memory: true`) | `emptyDir`, memory, 1Gi | in memory only; gone with the pod |
| `session.memory: false` | `emptyDir` on the node's disk | on the node's disk until the pod is deleted. This is what the deploy of 2026-11-26 ran with |
| `persistence.enabled: true` | PersistentVolumeClaim | on the volume, at rest, for as long as the claim exists. Not deployed |

The staged files in `/run/takx/identity` are in memory in every case. The memory volume counts against the desktop container's limit; see [the requirements](#onto-a-cluster-that-already-runs-uds-core-and-tak-server).

Expect the toast "Failed to query Contact List" every 30 seconds while connected: TAKX asks `https://takserver:8443`, and neither the gateway nor the pod's egress rule offers that port.

## Checked without a cluster

`../stages/package.sh` ran these on 2026-11-26 with uds 0.42.0 (Zarf 1.0.1, Helm 4.3.2), on the image built from the tree `takx57-head`, last after the bridge's token check was added to the image and the chart. Transcripts are under `/var/lib/takx-proto/pkg/package/evidence/`. On that last run no cluster was up: `crds` used the CRDs it had read from the sandbox cluster earlier that day, and `lookup` was not run again; its row is the result of the earlier run.

| Step | What ran | Result |
| --- | --- | --- |
| `lint` | `yamllint --strict` with the repository config, `shellcheck` with the `hk.pkl` flags on the scripts and on the three action commands, `codespell` 2.4.3, licence headers (also `addlicense -check`, by hand) | no findings |
| `zarf-lint` | `uds zarf dev lint` on the staged tree and on this directory | no errors. The staged tree draws no warning: its image is pinned by digest, and the step fails if "not pinned with digest" appears there. This directory as tracked draws that one warning for the placeholder tag |
| `helm` | `helm lint --strict` and `helm template`, default values and every optional branch; two values outside the schema | 0 failed; both bad values refused |
| `render` | `uds zarf dev inspect manifests`, default and with package values set | 9 objects; the mappings are as intended; all three containers name the image by tag and digest; `desktop.image` is ignored |
| `crds`, `validate` | kubeconform 0.7.0 `-strict` against the Kubernetes 1.36.3 schemas and against the `Package`, `TakClient`, `AuthorizationPolicy` and `DestinationRule` CRDs read from the sandbox cluster (`kubectl get crd`) | every object valid, the NetworkPolicy, the AuthorizationPolicy and the DestinationRule included; a misspelt field is rejected |
| `test` | `test_package.py`, 57 tests | pass |
| lookup | `helm template --dry-run=server` (reads one Service, applies nothing) | hosts entry `takserver` with the gateway's ClusterIP |
| `create` | `uds zarf package create`, image from the Docker daemon, no network, 2 CPUs | package of about 0.7 GB in about a minute |
| `inspect` | `uds zarf package inspect`, then the decompressed archive | four components, one image, an SBOM that lists 315 Debian packages and more than 150 Java archives, seven attachments; the stored `zarf.yaml` holds the two documents described above; the OCI layout holds one manifest, whose digest is the image ID of the build |
| `upstream` | definition-only pull of the four upstream packages, anonymous, `--verify=always` with the bundle's identities | all four "Verified OK"; every optional component and override target exists |
| `bundle` | uds bundle schema (v0.42.0) on the tracked and staged definitions; `uds create` and `uds inspect` of the `takx-desktop`-only bundle | 0 schema errors; bundle created and read back |

The whole bundle was **not** created. It would pull about 2.7 GB and need about 12 GB of free disk, and the sandbox had 12 GB next to a running cluster. `package.sh bundle-full` does it where there is room.

### Admission policies, one by one

`test_package.py` writes each UDS Core v1.16.0 policy (`src/pepr/policies`) out as a predicate, applies it to the rendered pod, and shows that it rejects a pod changed to break it. The mutations of the same policies are not needed: the chart sets every field itself. The live webhooks agreed ([Admission](#admission)).

| UDS Core policy | The pod | Verdict |
| --- | --- | --- |
| `DisallowPrivileged` | `allowPrivilegeEscalation: false` on all three containers, no `privileged` | approve |
| `RequireNonRootUser` | `runAsNonRoot: true`, users 10001 and 10002, no supplemental group 0 | approve |
| `RestrictProcMount` | not set | approve |
| `RestrictSeccomp` | pod `RuntimeDefault`, containers not set | approve |
| `DisallowSELinuxOptions`, `RestrictSELinuxType` | not set | approve |
| `DropAllCapabilities` | `drop: ["ALL"]` on all three | approve |
| `RestrictCapabilities` | nothing added | approve |
| `DisallowHostNamespaces` | no `hostNetwork`, `hostPID`, `hostIPC` | approve |
| `RestrictHostPorts` | one container port, 6080, no host port | approve |
| `RestrictVolumeTypes` | `emptyDir`, `secret`, and `persistentVolumeClaim` when persistence is on | approve |
| `RestrictHostPathWrite` | no `hostPath` | approve |
| `RestrictIstioSidecarOverrides`, `RestrictIstioTrafficOverrides`, `RestrictIstioAmbientOverrides` | no pod annotation, no inject label | approve |
| `RestrictIstioUser` | no UID, GID or fsGroup 1337 | approve |
| `RestrictExternalNames`, `DisallowNodePortServices` | the Service is `ClusterIP` | approve |

The UDS operator's `Package` validator (`package-validator.ts`) was read for the rules a rendered `Package` can break: host set, no `domain` on a standard gateway, a remote on every rule, `remoteHost` only on egress with TLS and without a wildcard, `serviceAccount` only with `remoteHost`, unique rule names and client IDs, no colon in an Authservice client ID, redirect URIs present. The rendered `Package` keeps all of them. Rules that need cluster state (one `Package` per namespace, client ID unused elsewhere, no host collision) cannot be checked here.

This repository's own pod policy (`pepr/capabilities/tak-policy.ts`) is bound to namespaces `takserver` and `cloudtak`, so it does not judge this pod. The pod passes it anyway. Its `TakClient` webhook requires a callsign of 4 to 63 characters and group names without slashes; the package schema is narrower than that.

## What the deploy showed

One deploy on 2026-11-26, 19:27 to 20:13 UTC, on the sandbox cluster of [`../server/README.md`](../server/README.md): single-node k3s v1.36.3 (kube-router enforces NetworkPolicy), UDS Core 1.16.0 (`core-base`, `core-identity-authorization`, Istio ambient), the TAK Server package 1.4.0 with CloudTAK, uds 0.42.0 with Zarf 1.0.1. One machine, software rendering, 46 minutes: not acceptance. The steps are those of `../stages/deploy.sh`; the first run of each was by hand, and `admission`, `identity`, `boot`, `gate`, `isolation`, `lifecycle` and `remove` then ran from the script against the same deployment. Evidence is under `/var/lib/takx-proto/deploy/evidence/` and `deploy/run/`.

Two packages were deployed. The first (SHA-256 `9fb9a2b2...`) is the one the checks ran on. The second (`02b3925c...`) differs by the chart's DestinationRule; it went over the first as a Helm upgrade at 20:10 without restarting the pod, and the sign-in gate, the pod replacement and the removal ran on it. The package created after this page was written has the same image, objects and actions as the second and differs in this file and in comments of the chart; it has not been deployed.

### Zarf

- `uds zarf package deploy ... --components takx-desktop-takclient,takx-desktop-identity --confirm` took 64 seconds: image pushed to the cluster registry in 5 s, `TakClient` Ready within 2 s, Secret copied, chart installed, UDS `Package` Ready and Deployment Available 54 s after the Helm install.
- The agent accepted the `tag@sha256` reference and rewrote it to `127.0.0.1:31999/jonathanagustin/uds-takserver/takx-desktop@sha256:18c21b79...`: the tag is dropped, the digest kept. The kubelet pulled by that digest.
- Go templates in manifests and action commands, `./zarf tools kubectl` and `yq` in actions, both wait conditions, the Helm lookup and the namespace existing before the Secret copy all worked as written. The deploy log holds the action's command text and none of its data.

### Admission

No exemption exists for the namespace or the pod; the cluster's two exemptions are UDS Core's own (`istio-system`, `takserver/takserver-db-setup`). The admitted pod differs from the Deployment's template in this and nothing else:

| Who | Change |
| --- | --- |
| Zarf agent | the three image references rewritten to the cluster registry; `imagePullSecrets: private-registry`; label `zarf-agent: patched`; annotations `zarf.dev/original-image-*` |
| UDS Core policies (Pepr) | annotations `uds-core.pepr.dev/uds-core-policies: succeeded` and `uds-core.pepr.dev/mutated: ["require-non-root-user","drop-all-capabilities"]`. Both mutations found the values the chart already sets; no field of the security contexts changed |
| UDS operator | label `istio.io/use-waypoint: uds-takx-desktop-waypoint`; annotation `uds-core.pepr.dev/uds-core-operator: succeeded` |
| Istio | annotation `ambient.istio.io/redirection: enabled` |
| Kubernetes | node name, priority, the two default tolerations |

The `TakClient` passed the `takserver-oidc` webhook and carries its `succeeded` annotation. That module's pod policy is bound to other namespaces and did not see this pod.

### Identity

- `TakClient` `takx-browser`: `Ready`, reason `Issued`, one second after it was created.
- The copy `takx-desktop/takx-desktop-identity` has exactly `package.zip` and `password`, byte-identical to the source; `ca.pem`, `client.key` and `client.pem` stay in `takclients`. UDS Core added `uds.dev/pod-reload-cleanup-complete` to it.
- The init container staged six files, each mode 0400, owner 10001, on a memory volume that the desktop container mounts read-only. The directory itself is mode 3777 with group 10001: `fsGroup` on an `emptyDir` does that. The web container has neither the Secret nor the staged files.
- The init log names sizes, the connect string, the certificate subject and fingerprints. A search of the pod logs of all three containers, the waypoint log, the pod object, the events, the deploy logs and every evidence file for the PKCS#12 password, the key body and the two test users' passwords found nothing.

### The pod

- A frame read from the pod's own display (RFB on the Unix socket, through `kubectl exec`) two minutes after the start shows the TAKX window with a project in the title and the rendered globe.
- First pod, 43 minutes with light use: restarts 0 in all containers; desktop container 1,839 to 1,996 MiB, peak 2,118 MiB of the 5 GiB limit; 579 CPU-seconds, about 0.22 CPU on average; node never under pressure, 4.7 GiB the least memory available on the node.
- Exec probes, the socket shared between the two containers, `fsGroup` and the read-only root filesystem all behaved in Kubernetes as they did in Docker. Start to Available: 54 s for the first pod, 48 s for its replacement.

### The sign-in gate

- Without a session, `/`, `/vnc.html`, `/websockify`, `/core/rfb.js` and a WebSocket upgrade to `/websockify` each get `302` to Keycloak; a made-up bearer token gets `401`; port 80 redirects to HTTPS. The waypoint's access log has every one of them as `UAEX ext_authz_denied` with no upstream: the request ends at Authservice. A real browser with an empty profile lands on the Keycloak form, and a WebSocket it opens to `wss://takx.uds.dev/websockify` fails.
- `doug` (in `/UDS Core/Admin`) signs in and lands on noVNC; the canvas is 1920x1080 and live, the screenshot shows the TAKX desktop with the pod's clock. The bridge's log has one WebSocket for that session, from the waypoint's address, and none before it.
- `mapuser` (in `/CloudTAK/Users` only) is refused by Keycloak after the password: `403`, "You do not have access to this application". With that browser, `/vnc.html` is redirected to the form again and the WebSocket fails.
- An open desktop stayed connected for 14 minutes without input, and a reload after those 14 minutes needed no new sign-in. Session cookies copied into a new browser 11 minutes after they were issued were not accepted. What ends a session, and whether an open WebSocket is cut when it ends, was not established.

### The TAK Server connection

- TAKX in the pod connected to `ssl://takserver:8089` at the first attempt: "Stream connection ... is up!", protocol negotiation accepted, green row and status bar. The server name is checked: the project's CommonCommo patch enforces it, and the certificate names only `takserver`.
- Server side, asked as another `TakClient` through the passthrough gateway: `/Marti/api/subscriptions/all` lists `dn OU=UDS, O=TAK, CN=takx-browser`, protocol `tls`, group `Operators` in and out, `TAKX-CIV 5.7.4-SNAPSHOT`; `/Marti/api/clientEndPoints` shows the user `Connected`.
- Peer to TAKX: the scripted client `takx-peer` sent `SYNTHETIC-PEER-POD-1` (10 S 25 W); it is on the pod's globe with its label.
- TAKX to peer: Point Dropper placed `SYNTHETIC-TAKX-POD1 1` (30 S 15 W), Send > Broadcast at 19:43:20; `takx-peer` received it in that second as `a-n-G` at -30, -15 and the server stored it. `takx-outsider` (group `TakxIsolation`), connected the whole time, received only the server's protocol notice.
- CloudTAK was not the peer this time.

### Replacing and removing

- `kubectl delete pod`: the Deployment's replacement was Available 48 seconds later, restarts 0, identity staged again from the same Secret. The server dropped the subscription. The new pod has a new project, so no connection; added again, it connected, and the server lists the same certificate with a new callsign.
- `uds zarf package remove takx-desktop --confirm` took 7 seconds: the action deleted the copied Secret, the charts were uninstalled, the `TakClient` and its Secret disappeared from `takclients`. **Zarf leaves the namespace `takx-desktop` behind**; `deploy.sh remove` deletes it.
- Afterwards the namespaces, the `TakClient`s, `Package`s, exemptions and the Secrets in `takclients` were those of the baseline, and every pod outside the package had the same name, UID, readiness and restart count as before the deploy, with one exception: Authservice (next section). `https://takx.uds.dev/` answers 404.

### Findings

1. **The node reached the desktop without signing in.** See [Which ports can be reached](#which-ports-can-be-reached). Not fixable in the chart; since then the image checks the token itself, which has not been deployed.
2. **Deploying and removing the package each restart Authservice.** The UDS operator rewrites the Authservice configuration when a protected application appears or goes, and rolls the `authservice` Deployment (new pods at 19:27:37 and 20:12:35). Authservice keeps sessions in memory here, so everyone signed in to any application behind it has to sign in again. TAK Server, CloudTAK and Keycloak pods were untouched.
3. **The bridge kept a process for every connection the waypoint pooled.** After four page loads the web container held 44 `websockify` processes and 173 MiB of its 256 MiB limit, with every browser gone. Fixed in the chart with the DestinationRule `takx-desktop-bridge`: after it, three page loads left 1 process and 35 MiB, and the desktop still loaded through the gateway. The gate that now stands in front of websockify ends its connection with every request.
4. **The sign-in check of this page first broke the sign-in.** A second request with the cookie of a pending sign-in replaces its state at Authservice, and the sign-in then ends in "Oops, your session has expired". A user who opens the desktop in two tabs before signing in will meet it. `deploy_browser.cjs` probes with a separate profile now.
5. **Point Dropper needs an icon selected before a typed location is accepted.** OK with a typed location and no icon selected does nothing but log `[GLMapView]: Invalid lookAt`; with the icon selected the point is placed. Not seen in the earlier standalone runs, whose script selected the icon first.

## What changed after the deploy

Six points of the deploy and of its review were taken up in the image and the chart on 2026-11-26. **None of it has run in a cluster.**

| Finding | Change | Shown without a cluster |
| --- | --- | --- |
| The node reaches port 6080 without signing in; the desktop has no authentication of its own | the gate in front of websockify verifies the forwarded token on every request and on the WebSocket upgrade ([The bridge's own check](#the-bridges-own-check)) | 20 request variants from the host's network namespace to the container's address: only the correct token got the page and the RFB handshake |
| The node reaches the JVM's ports 4242 and 8087 | TAKX's default inputs are switched off in the program properties of a new session (`takx.localInputs`) | both ports closed from the host; no UDP socket on the default ports; the globe renders |
| The group is enforced only by Keycloak | the bridge reads the groups claim itself | a token without the group: 403 |
| The client key reaches the node's disk once a connection is added | the session volume is memory by default (`session.memory`) | the rendered volume; the self-test has always run with the session in memory |
| The DestinationRule is untested for long and quiet WebSockets | a ping every 25 seconds, below its 30-second idle timeout ([The DestinationRule](#the-destinationrule)) | a WebSocket that asked for nothing stayed open for 300 seconds and was pinged 11 times; no Envoy was on that path |
| What ends a session is unknown | the bridge ends a WebSocket after 300 seconds and noVNC reconnects through the sign-in ([Sessions](#sessions-how-they-end)) | cut at the limit and at token expiry; Chromium reconnected by itself |

**For the next deploy to show**, in this order:

1. The web container's log says `jwks: 1 signing key(s) loaded` (or more) and `ready: signing keys loaded`. If it says `fetch failed`, the egress rule to Keycloak or its waypoint is the place to look, and nobody gets in.
2. A user in the group reaches the desktop through the gateway as before, and the log has `200 GET /vnc.html sub=...` and `101 GET /websockify ... websocket open` for it.
3. From the node: `http://<pod-ip>:6080/` answers 401, `/healthz` answers `ok`, 4242 and 8087 are closed. With a real ID token of the client the same address answers 200: the token is what counts, not the path.
4. The three shapes around Authservice, at the gateway: an `Authorization: Basic ...` header, a token of the realm for another client, and HTTP/1.1 from a mesh workload to the waypoint. None may reach the desktop; note who refused each, the waypoint or the bridge.
5. A desktop left open for more than ten minutes: the bridge logs `session limit reached` every 300 seconds and the browser is back within seconds each time, without anyone touching it. Then the same with a still screen.
6. Sign out at Keycloak (or disable the user) with a desktop open: it is gone after at most 300 seconds and a reload leads to the sign-in. Also `https://takx.<domain>/logout`.
7. Memory of the desktop container with the session in memory, against the 2560Mi request and the 5Gi limit; `df /run/takx/session` in the pod after a connection was added, and that the mount is `tmpfs`.
8. Admission: the pod has one more `emptyDir` and the web container more environment; no exemption should be needed.

## Still not shown

- **The whole bundle.** `uds create` with the four upstream packages, and any `uds deploy`. Whether a bundle variable (`DOMAIN`) wins over the package value mapping (`global.domain`); they agree at the default.
- **Everything under [What changed after the deploy](#what-changed-after-the-deploy)** in a cluster, the token check with a real realm first of all.
- **Sessions.** Expiry and logout as they happen with Keycloak and Authservice. The design is written down; nothing of it was observed.
- **Renewal.** A reissued certificate, the redeploy of `takx-desktop-identity`, the pod-reload restart. A certificate revoked by the removal was not tried against the server.
- **Persistence.** `persistence.enabled: true`, and a start on an existing project.
- **Values.** Only the defaults were deployed: no `egress.hosts`, no `tak.egress.api`, `network.denyOtherPorts` on, no other host name or domain.
- **Other clusters.** Another NetworkPolicy controller, a sidecar mesh, more than one node, a CPU without AVX2, an arm64 node (the package is amd64 only).
- **CloudTAK as the peer of the pod**, missions, data packages and contacts (the API port, as before), video (no FFmpeg natives in the image), more than one browser on the session at once, and any run longer than 43 minutes.

## Limits

- One session, one identity, shared by everyone the SSO group admits. No per-user sessions.
- amd64 only, software rendering.
- Root on the node, and cluster rights for `kubectl exec`, are past the sign-in. A process on the node without a token, and `kubectl port-forward` to port 6080, meet the bridge's check (not yet shown in a cluster).
- A logout takes up to five minutes to close an open desktop.
- The session directory is memory: what TAKX writes there (logs, caches, 1 GiB at the most) is taken from the desktop's memory limit, and a session that outgrows the volume loses its pod. Not soaked.
- Installing and removing it interrupts every Authservice session in the cluster.
- The image and the package are local. Publishing either needs issue #934 closed first: the licence gate must be clean, and it is not.
- The whole-bundle path and the empty-cluster path were written from the repository's bundles and the sandbox recipe in `server/`; they have not run.

## Re-check of 2026-11-27: the bridge's token check on a live deploy

Image `takx-desktop:5.7-proto-b5385e92ddf1` (tree rebuilt with `TAKX_ASSEMBLE_FORCE=1`, so the denied font atlases are gone: licence gate 0 deny, 0 unknown, 44 review), package from `stages/package.sh create`, deployed with `--components takx-desktop-takclient,takx-desktop-identity` and removed again. The cluster had been restarted shortly before.

| Check | Result |
| --- | --- |
| Pod IP port 6080 from the node's host namespace, no token | 401 |
| Same, `Authorization: Bearer abc.def.ghi`, `Basic ...`, an `alg: none` token claiming the Admin group | 401 each |
| WebSocket upgrade on `/websockify` without a token | 401 |
| `/healthz` without a token | 200 |
| Pod IP ports 4242 and 8087 from the host namespace | connection refused |
| `selftest auth` (image, no cluster): quiet WebSocket 65 s, token expiry closing the socket, keys unreachable | pass |
| Browser, `doug` (/UDS Core/Admin) | noVNC page, 1920x1080 canvas, WebSocket with 218 frames |
| Browser, `mapuser` (CloudTAK users only) | Keycloak 403 "You do not have access to this application"; `/vnc.html` redirects to sign-in |
| Session volume | `emptyDir` with `medium: Memory`; `/run/takx/session` is tmpfs |

Not covered: a validly signed token with a wrong audience or the wrong group sent straight to the bridge (only the unit tests and the selftest cover those), HTTP/1.1 to the waypoint, renewal of a live TakClient.

Platform finding from this run: after a VM restart the `Package` of this, `cloudtak` and `takserver` stayed `Failed` ("Keycloak request ... 503"). The waypoint's HBONE connection to Keycloak's port 15008 was refused until `istio-cni-node` and `ztunnel` were restarted; the operator then needed its watcher restarted to retry. A deploy started before that waits in its health check and times out; this is not a fault of the package.
