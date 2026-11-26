<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Deploy as a package

The TAKX 5.7 desktop prototype as a Zarf package (`takx-desktop`) and a UDS bundle (`takx-desktop-proto`): the client runs in one pod and is used from a browser at `https://takx.<domain>/`, behind the UDS tenant gateway and Keycloak.

**Nothing here has been deployed.** The package and a bundle were built and checked without a cluster. [What a deploy still has to prove](#what-a-deploy-still-has-to-prove) lists what that leaves open.

What the package carries is TAKX 5.7 (`0b59a9bb`) built from public sources on Kernel 8.30.5 + project patches, CommonCommo 1.17.2 patched and the NASA WorldWind interop build. It is not the official TAKX 5.7 distribution. The image is local only and is **not cleared for distribution**: its `SOURCES.json` says `"publishable": false` and lists why (issue #375). This directory is opt-in; the TAK Server package, its bundles and CI do not use it.

## What is in it

| Path | Holds |
| --- | --- |
| `zarf.yaml` | Zarf v1beta1 package: names, descriptions, selection, values and attachments. It composes the four component files with `import.local` |
| `components/image.yaml` | the one image, ID `desktop` |
| `components/takclient.yaml`, `manifests/takclient.yaml` | a `TakClient` (group `tak.uds.dev`) in namespace `takclients` |
| `components/identity.yaml`, `manifests/identity-info.yaml` | the hand-over of the TAK identity to the desktop namespace |
| `components/desktop.yaml`, `image-values.yaml` | the chart `../chart` and its value mappings |
| `values.yaml`, `values.schema.json` | package values |
| `create-package.sh` | builds the package from a staged copy |
| `test_package.py` | checks without a cluster; helper modes for `../stages/package.sh` |
| `../chart` | Helm chart `takx-desktop`: Deployment, Service, ServiceAccount, optional PersistentVolumeClaim, UDS `Package` |
| `../bundle` | `uds-bundle.yaml`, `uds-config.yaml`, `create-bundle.sh` |
| `../stages/package.sh` | every command of this page, step by step |

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
| `desktop` | 10001 | Xtigervnc on a Unix socket, openbox, the TAKX JVM. The display has no TCP listener; the JVM itself listens on TCP 4242, 8087 and one ephemeral port (CommonCommo), which only the network policy keeps unreachable | `/tmp`, the session volume, the socket volume, the identity volume (read-only) |
| `web` | 10002 | websockify: noVNC on TCP 6080, bridged to the socket | the socket volume (read-only), nothing else |

Every container has a read-only root filesystem, drops all capabilities and cannot gain privileges. The service account has no token and no RoleBinding. There is no login in the pod: **whoever reaches port 6080 controls the desktop**, so the only way in is the tenant gateway with Authservice, and the chart has no switch for that.

### What the UDS `Package` declares

- **Sign-in:** Keycloak client `uds-takx-desktop`, Authservice in front of the pod, group `/UDS Core/Admin` (`sso.groups`). Everyone admitted shares the one session and the one TAK identity.
- **Exposed:** Service `takx-desktop` port 6080 on the tenant gateway, host `takx`.
- **Egress, default:** TCP 8089 to the passthrough gateway pods. Nothing else. DNS comes from the operator.
- **Egress, opt-in:** TCP 443 to the same gateway (`tak.egress.api`) and TLS 443 to named hosts (`egress.hosts`, for example a tile server).
- **Never:** `Anywhere`, `KubeAPI` or `IntraNamespace` rules, policy exemptions.

`tak.egress.api` is off because it would not help: TAKX 5.7 asks `https://<address>:8443` for missions and contacts, the gateway does not serve 8443, and on 443 it routes by an SNI name TAKX does not send ([STATUS.md](../STATUS.md), "HTTPS API from TAKX"). Without `egress.hosts` there are no online map tiles, only the built-in base map.

### How the TAK identity reaches the pod

1. `TakClient` `takx-browser` in `takclients` (this package, or one that already exists).
2. The operator writes Secret `takclients/takx-browser` with five keys ([ADR 22](../../../adr/0022-takclient-operator.md)).
3. The `takx-desktop-identity` component copies two of them, `package.zip` and `password`, into `takx-desktop/takx-desktop-identity`. The person who deploys does this with their own access; nothing in the cluster may read Secrets of another namespace. The data goes from one `kubectl` to the next through a pipe and is not printed or written to a file.
4. The init container checks the package (entries, connect string, certificate dates) and stages the files under `/run/takx/identity`, in memory, mode 0400.
5. A person adds the connection in the TAKX window. See [After the deploy](#after-the-deploy).

The copy is not kept in step. `TakClient` certificates last 30 days by default; after a renewal or reissue, deploy `takx-desktop-identity` again. The copied Secret carries `uds.dev/pod-reload`, so UDS Core should then restart the pod.

### The server name

TAKX checks the name it dials against the TAK Server certificate, and that certificate names only `takserver`. The data package the operator issues says `takserver-stream.<domain>`, which TAKX refuses ([STATUS.md](../STATUS.md), "Rejected connections"). So:

- the pod gets a hosts entry `takserver` → the passthrough gateway. The address is the ClusterIP of Service `istio-passthrough-gateway/passthrough-ingressgateway`, read with a Helm `lookup` at install, or `tak.gateway.ip` when set;
- the init container also stages `takx-package.zip`, the same package with the connect string `takserver:8089:ssl`.

A render without a cluster (`helm template`, `zarf dev inspect manifests`) finds no Service and writes no hosts entry.

## Build

The image comes first: `../stages/image.sh` builds `ghcr.io/jonathanagustin/uds-takserver/takx-desktop:<TAG>` into the local Docker image store. `<TAG>` ends in the first 12 digits of the assembled tree's manifest digest.

```bash
TAG=5.7-0b59a9bb-proto-<digest12>
takx/desktop-prototype/zarf/create-package.sh "$TAG" --confirm -o /var/lib/takx-proto/pkg/out
```

`create-package.sh` works on a staged copy. It sets the tag, reads the image from the Docker daemon (`TAKX_IMAGE_SOURCE=registry` for a published image) and writes the attachments out of the image itself, so the package cannot carry notices that differ from its image. It stops when the image label, `SOURCES.json` and the tag name different trees. No network is needed.

Read the result back:

```bash
uds zarf package inspect definition <package>
uds zarf package inspect images <package>
uds zarf package inspect sbom <package> --output sbom/
uds zarf package inspect documentation <package> --output attachments/
```

The attachments are `README` (this file), `NOTICE`, `SOURCES` (source revisions, archive digests and the component of every file), `LICENSES` (licence texts), `PATCHES` (the patches and lock files) and `DEBIAN-PACKAGES` (binary and source package versions).

`../stages/package.sh all` runs every check on this page and the build, and keeps a transcript of each step under `/var/lib/takx-proto/pkg/package/evidence/`.

## Deploy

Use the pinned CLI (`mise.toml`: uds 0.42.0, Zarf 1.0.1). The package says in its build data that it needs Zarf 0.88.0 or later.

### Onto a cluster that already runs UDS Core and TAK Server

The cluster needs:

- UDS Core with the tenant gateway, Keycloak and Authservice, and the passthrough gateway with TCP 8089 in its Service ports (`bundle/uds-bundle.yaml` of this repository sets that);
- the TAK Server package 1.4.0 or later, for the two identity components;
- an amd64 node with room for the requests (550m CPU, about 2.1 GiB) and the desktop's 5 GiB memory limit;
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

None of this has been done in a pod.

1. Open `https://takx.<domain>/` and sign in. The page is noVNC; the TAKX window should appear with a new project.
2. In TAKX: Home > TAK Server > Add. Address `takserver`, port `8089`, protocol SSL, CA file `/run/takx/identity/truststore.p12`, client certificate `/run/takx/identity/client.p12`. These are the settings with which the same client connected outside the cluster ([STATUS.md](../STATUS.md), "Connected run").
3. Both files have the same password, in `/run/takx/identity/password.txt`. The browser session cannot read it and the clipboard is off. Click into a password field, then have someone with cluster access type it into the focused field, without it leaving the pod:

   ```bash
   uds zarf tools kubectl exec -n takx-desktop deploy/takx-desktop -c desktop -- sh -c \
     'export DISPLAY=:1 XAUTHORITY=/run/takx/session/home/.Xauthority
      tr -d "\r\n" </run/takx/identity/password.txt | xdotool type --delay 25 --file -'
   ```

   `server/connect-configure.sh` types the password the same way in its container.
4. The connection is saved in the project. With `persistence.enabled: false` (the default) the project is lost with the pod and the connection has to be added again. With it on, the volume holds TAKX's own copies of the PKCS#12 files and their passwords.

## Checked without a cluster

`../stages/package.sh` ran these on 2026-11-25 with uds 0.42.0 (Zarf 1.0.1, Helm 4.3.2). Transcripts are under `/var/lib/takx-proto/pkg/package/evidence/`.

| Step | What ran | Result |
| --- | --- | --- |
| `lint` | `yamllint --strict` with the repository config, `shellcheck` with the `hk.pkl` flags on the scripts and on the three action commands, `codespell` 2.4.3, licence headers (also `addlicense -check`, by hand) | no findings |
| `zarf-lint` | `uds zarf dev lint` on the staged tree and on this directory | no errors; one warning, "Image not pinned with digest" (a local image has no registry digest) |
| `helm` | `helm lint --strict` and `helm template`, default values and every optional branch; two values outside the schema | 0 failed; both bad values refused |
| `render` | `uds zarf dev inspect manifests`, default and with package values set | 6 objects; the mappings and the image reference are as intended; `desktop.image` is ignored |
| `crds`, `validate` | kubeconform 0.7.0 `-strict` against the Kubernetes 1.36.3 schemas and against the `Package` and `TakClient` CRDs read from the sandbox cluster (`kubectl get crd`) | every object valid; a misspelt field is rejected |
| `test` | `test_package.py`, 48 tests | pass |
| lookup | `helm template --dry-run=server` (reads one Service, applies nothing) | hosts entry `takserver` with the gateway's ClusterIP |
| `create` | `uds zarf package create`, image from the Docker daemon, no network, 2 CPUs | package of about 0.7 GB in about a minute |
| `inspect` | `uds zarf package inspect` | four components, one image, an SBOM with 492 entries (315 Debian packages, 160 Java archives), six attachments |
| `upstream` | definition-only pull of the four upstream packages, anonymous, `--verify=always` with the bundle's identities | all four "Verified OK"; every optional component and override target exists |
| `bundle` | uds bundle schema (v0.42.0) on the tracked and staged definitions; `uds create` and `uds inspect` of the `takx-desktop`-only bundle | 0 schema errors; bundle created and read back |

The whole bundle was **not** created. It would pull about 2.7 GB and need about 12 GB of free disk, and the sandbox had 12 GB next to a running cluster. `package.sh bundle-full` does it where there is room.

### Admission policies, one by one

`test_package.py` writes each UDS Core v1.16.0 policy (`src/pepr/policies`) out as a predicate, applies it to the rendered pod, and shows that it rejects a pod changed to break it. The mutations of the same policies are not needed: the chart sets every field itself.

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

## What a deploy still has to prove

- **Admission.** The live UDS Core and `takserver-oidc` webhooks, their mutations, and the Zarf agent (image rewrite to the cluster registry, pull Secret) have not seen these objects. The table above is a reading of the sources.
- **The UDS operator.** Reconcile of the `Package` to `Ready`: the Keycloak client, the Authservice chain for an ambient workload, the VirtualService, the NetworkPolicies.
- **The browser path.** Keycloak redirect, the noVNC page and its WebSocket through the tenant gateway and Authservice, behaviour on session expiry and logout.
- **The pod in Kubernetes.** `fsGroup` on the volumes and the Secret, the socket shared between two containers, the exec probes, start-up inside ten minutes and memory on a shared node. The image ran with Docker only.
- **Zarf at deploy.** Go templates in manifests and in action commands, `./zarf tools` in actions, the wait conditions, the Secret copy itself, `onRemove`, the namespace being there before the copy, and the image push from this package. The yq expression of the copy was run on a synthetic Secret; the `kubectl` around it was not.
- **The Helm lookup inside a Zarf deploy.** It was run with `helm template --dry-run=server` only.
- **The route to TAK Server.** Pod → hosts entry → gateway ClusterIP:8089 under ambient mesh and the network policy; TAKX connecting from the pod; the manual steps under [After the deploy](#after-the-deploy).
- **Precedence.** Whether a bundle variable (`DOMAIN`) wins over the package value mapping (`global.domain`). They agree at the default.
- **Renewal.** A reissued certificate, the redeploy of `takx-desktop-identity`, the pod-reload restart.
- **Persistence.** `persistence.enabled: true`, and a start on an existing project: whether the window title then satisfies the start-up probe is unknown.
- **The whole bundle.** `uds create` with the four upstream packages, and any `uds deploy`.
- **Removal.** `uds zarf package remove takx-desktop`: the `TakClient` is deleted (certificate revoked), the copied Secret is deleted by an action. Namespaces stay.

## Limits

- One session, one identity, shared by everyone the SSO group admits. No per-user sessions.
- amd64 only, software rendering.
- The image and the package are local. Publishing either needs the review in issue #375 first.
- The whole-bundle path and the empty-cluster path were written from the repository's bundles and the sandbox recipe in `server/`; they have not run.
