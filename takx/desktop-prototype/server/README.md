<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Live TAK Server for the TAKX desktop prototype

These scripts put a real TAK Server (published package `1.4.0-upstream`, with
CloudTAK) on UDS Core `1.16.0-upstream` in the Claude Code sandbox that runs the
desktop prototype, and export a client identity for TAKX. They record what was
run on 2026-11-25. They are a sandbox recipe, not an install guide: the
supported path is `uds run default` on a `k3d-core-slim-dev` cluster.

TAKX has connected to this server since: from a container on the host
([STATUS.md](../STATUS.md), "Connected run") and from a pod of the desktop
package ([zarf/README.md](../zarf/README.md), "What the deploy showed"). The
VM has also restarted once with the server deployed; see
[After a VM restart](#after-a-vm-restart).

## What ran, in order

| Step | Script | Result |
| --- | --- | --- |
| Host mounts for Istio ambient | `05-host-prep.sh` | root mount shared; private `/proc` view at `/run/takx-hostproc` |
| k3s `v1.36.3+k3s1` | `10-k3s.sh` (owner's `bootstrap-k3s.sh --verify`) | node Ready, pod ran and logs read back |
| UDS CLI `0.42.0`, Zarf `1.0.1` | `15-uds-cli.sh` | sha256 matched the release `checksums.txt` |
| MetalLB `v0.15.2` controller | `20-metallb.sh` | pool `10.44.0.10-10.44.0.19` |
| Zarf init, `coredns-custom` | `30-zarf-init.sh` | registry and agent Running |
| UDS Core base and identity | `40-core.sh`, `core/uds-bundle.yaml` | `core-base`, `core-identity-authorization` deployed |
| Host names | `45-hosts.sh` | `/etc/hosts` block, `endpoints.env` |
| TAK Server and CloudTAK | `50-tak.sh`, `tak/uds-bundle.yaml` | four UDS Packages Ready, all pods Ready |
| Checks | `60-verify.sh` | see [Evidence](#evidence) |
| Identities | `70-takclients.sh`, `export_takclient.py` | three `TakClient`s Ready, files exported |
| Keycloak test users | `80-keycloak-users.sh` | `doug`, `mapuser` |
| After a VM restart | `90-resume.sh` | k3s, admission, registry, mesh, workloads, the CloudTAK pod, the exported identities, then 45 and 60; redeploys nothing |
| Desktop package checks | `deploy_devusers.py`, `deploy_browser.cjs`, `deploy_probe.py` | used by `../stages/deploy.sh`; see [zarf/README.md](../zarf/README.md) |

`env.sh` holds shared settings. State is outside the repository under
`/var/lib/takx-proto`: `server/logs`, `server/endpoints.env`, `status/server-*.json`
and `secrets/<name>/` (directories 700, files 600).

All three packages were pulled anonymously from GHCR and passed keyless
signature verification (`Verified OK` in the create logs). Nothing was built.

## Adaptations from the repository's k3d path

Each one is a difference from `tasks.yaml` and `bundle/uds-bundle.yaml`.

1. **Native k3s, not k3d.** The `k3d-core-slim-dev` checkpoint is a k3d
   cluster image. The sandbox bootstrap supplies k3s with its four fixes
   (runc wrapper, `NO_PROXY`, cgroup v1, eviction thresholds). k3s is 1.36.3,
   not the 1.37.1 the checkpoint creates.
2. **servicelb off, MetalLB controller on.** The three gateway Services all
   use ports 80 and 443 and klipper servicelb can serve one. `disable:
   servicelb` goes in `/etc/rancher/k3s/config.yaml`, because the bootstrap
   takes no extra flags. Only the MetalLB controller runs. The speaker is
   deleted: on one node kube-proxy delivers the addresses to the host, to pods
   and to Docker bridge networks without any announcement.
3. **Zarf init and `coredns-custom` by hand.** The checkpoint ships both.
   The ConfigMap has the same two rewrites uds-k3d uses. The takserver watcher
   then added its own `00-takserver.override` key.
4. **Two Core layers instead of the checkpoint.** Monitoring, logging,
   runtime security and backup are separate layers and were never deployed.
   Envoy Gateway is not selected and `quic.enabled` stays false.
5. **Istio ambient on this kernel.**
   - `ztunnel` gets `IPV6_ENABLED=false` and the CNI chart `ambient.ipv6:
     false`, because the kernel boots with `ipv6.disable=1`.
   - The root mount is made shared.
   - `istio-cni` exits with `failed to Statfs "/host/proc/1/ns/net":
     permission denied`: PID 1 is the sandbox control process and its
     namespace links are unreadable. `05-host-prep.sh` mounts a private copy
     of `/proc` whose entry `1` is `/proc/2` (same network namespace), and the
     DaemonSet's `cni-host-procfs` volume points at it. The chart hardcodes
     `/proc`, so this is a patch on the live DaemonSet, and a Helm upgrade of
     the `cni` chart resets it. The real `/proc` is not changed.
6. **Keycloak dev admin Secret on**, as the checkpoint has it, so
   `setup:keycloak-user` works.
7. **Host names in `/etc/hosts`.** This sandbox's resolver returns nothing
   for `*.uds.dev`. Each gateway has its own address, so `sso`, `takserver`
   and `map` point at the tenant gateway, and `takserver-stream`,
   `takserver-api` and the bare name `takserver` at the passthrough gateway.
   `.uds.dev` is added to `NO_PROXY` for the scripts; the proxy stays set.
8. **CloudTAK basemap off.** nginx in the CloudTAK pod verifies the tile
   host's certificate. Pods see the sandbox proxy's certificate and do not
   trust it. Verification stays on, so there are no map tiles in CloudTAK.
9. **Bundle archives and the UDS layer cache are deleted after each deploy**
   to stay inside the disk allowance. A rerun pulls them again.

Run details that differ from the scripts as committed:

- `40-core.sh` was edited while its first run was in progress. `uds deploy`
  finished and both packages are listed as deployed, but the shell then failed
  on the changed file, and on that run the DaemonSet volume was repointed by
  hand. The patch loop now in the script has only been dry-run against the
  live DaemonSet.
- `40-core.sh` and `50-tak.sh` also had their bundle lookup rewritten for
  shellcheck afterwards. Neither has been run again end to end.
- `10-k3s.sh` gained the host-prep call and a lower disk floor for reruns
  after its first run; that version ran inside `90-resume.sh`.

## Evidence

From `logs/60-verify.log`, `logs/90-resume.log` and `logs/probe-*.json`:

- All TAK pods are Ready: api, messaging, config, plugins, Ignite, PostgreSQL,
  NATS, `tak-health`. UDS Packages `takserver`, `cloudtak`, `keycloak` and
  `authservice` are Ready.
- Streaming TLS answers on `10.44.0.12:8089` from the host. The server
  certificate is `CN=takserver`, issued by `CN=uds-tak-ca`.
- Mutual TLS with the `takx-desktop` certificate, the TAK CA as the only trust
  root and the server name checked: `Verify return code: 0 (ok)`, TLS 1.3.
  Without the TAK CA the handshake is refused.
- The client-certificate API answers `200` with `5.7-RELEASE-14-HEAD` at
  `/Marti/api/version` on port 443 with SNI `takserver-api.uds.dev`.
- The same handshake passes from a `takx-desktop-proto:runtime` container on
  the default Docker bridge with `--add-host takserver:10.44.0.12`.
- CloudTAK serves its page at `https://map.uds.dev/` (`200`, title
  `CloudTAK`), and its bootstrap log reports `connection live` to TAK Server.
  Nobody has signed in to it through a browser yet.
- Group routing, three probes connected at once (`cot_probe.py`):
  `takx-desktop` and `takx-peer` (both `Operators`) each received the other's
  position report five times. `takx-outsider` (`TakxIsolation`) received
  neither, only the server's protocol notice.
- Server-side record: during a probe, `/Marti/api/subscriptions/all` listed a
  TLS subscription with `dn` `OU=UDS, O=TAK, CN=takx-desktop`, user
  `takx-desktop`, groups `Operators`.

Memory with everything running: 8.4 GiB used, 7.6 GiB available of 15.7 GiB.
The TAKX JVM (`-Xmx4g`, 1 GiB direct memory) has not been started beside it.
Disk: 9.9 GiB of the allowance left.

## Connecting a client

| Purpose | Address | Notes |
| --- | --- | --- |
| Streaming | `10.44.0.12:8089` | routed by port; no SNI needed |
| Streaming, second route | `10.44.0.12:443` | SNI `takserver-stream.uds.dev`; a CoT stream starts after the handshake |
| Client-certificate API | `10.44.0.12:443` | SNI `takserver-api.uds.dev` |
| Enrollment (not used) | `10.44.0.12:8446` | SNI `takserver.uds.dev`; SNI `takserver` gets no certificate |
| CloudTAK, Keycloak | `10.44.0.11:443` | `map.uds.dev`, `sso.uds.dev`, public `*.uds.dev` certificate |
| TAKX desktop package, when deployed | `10.44.0.11:443` | `takx.uds.dev`; `45-hosts.sh` writes the name |

Read the current addresses from `/var/lib/takx-proto/server/endpoints.env`.

**The server certificate names only `takserver`** (common name and one DNS
subject alternative name). `takserver-stream.uds.dev` is not in it: a client
that checks the server name gets `hostname mismatch` for that name. So:

- Dial the name `takserver`, port 8089, protocol SSL, and map that name to
  the passthrough address. On 8089 the gateway does not look at SNI.
- In a container: `--add-host takserver:10.44.0.12` (run-in-toolchain.sh
  accepts `--docker-arg`), or append the line to `/etc/hosts` in the running
  container. The container needs no proxy setting and no host network.
- On port 443 the route needs SNI `takserver-api.uds.dev` while the
  certificate says `takserver`. A strict client cannot satisfy both, and curl
  fails there. OpenSSL can, with separate `-servername` and
  `-verify_hostname`.

### Identity files

`/var/lib/takx-proto/secrets/<name>/`, for `takx-desktop` (`Operators`),
`takx-peer` (`Operators`, a scripted peer) and `takx-outsider`
(`TakxIsolation`, for the negative check):

| File | Use |
| --- | --- |
| `client.p12` | client certificate and key; TAKX "Client Cert File" |
| `truststore.p12` | TAK CA from the device package; TAKX "CA File" |
| `truststore-java.p12` | the same CA written by the JDK's keytool |
| `password.txt` | protects all three PKCS#12 files |
| `package.zip` | device data package from the Secret, connect string `takserver-stream.uds.dev:8089:ssl` |
| `takx-package.zip` | the same package with connect string `takserver:8089:ssl` |
| `client.pem`, `client.key`, `ca.pem` | PEM forms for OpenSSL and the probe |
| `connection.json` | endpoints and public fingerprints; no secret |

JDK 17 `keytool` lists 0 entries in `truststore.p12`: its certificate bag has
no trusted-certificate attribute. OpenSSL reads the CA from it. Which of the
two TAKX needs has not been tested.

### What the TAKX 5.7 source says

Read at `release/5.7` (0b59a9b). None of this has been run against the server.

- **Editor.** `TakServerConnectionDetailsPanel`: name, address, protocol, port
  (default 8089), "CA File" and "Client Cert File" choosers for `.p12` with
  one password each, or "Enroll for Client Certificate" with a user name and
  password. It checks both files with the Java PKCS#12 key store.
- **Data package.** `PackageImporter` and `TakServerConnectionImporter` read a
  `cot_streams` preference with `connectString0`, `caLocation0`,
  `caPassword0`, `certificateLocation0` and `clientPassword0`, and accept only
  `ssl`. The package this deployment issues has exactly those keys.
- **Preferences.** A connection is a node
  `CommonCommo/TakServers/<uuid>` (keys `name`, `uri`, `enabled`,
  `certFileName`, `caCertFileName`, `useAuthentication`) in
  `ProjectData/LocalProgramProperties.xml`. Certificates are copied into the
  project's `Files/SslCerts`. `certPassword` and `caCertPassword` keys found
  on load are moved into TAKX's credential store.
- **Ports.** The secure API port is preference
  `CommonCommo/SecureServerApiPort`, default 8443, on the streaming host.
  Enrollment is `CommonCommo/CertEnrollmentApiPort`, default 8446.

## Limits and next experiments

- **TAKX has not connected.** Next: start the client with
  `--add-host takserver:10.44.0.12` and add the connection for `takserver:8089`.
  Keep the password dialog off any recording.
- **Missions and files from TAKX are not reachable as deployed.** TAKX would
  ask `takserver:8443`. The passthrough gateway exposes 443, 8446 and 8089,
  and a request to 443 with SNI `takserver` got no answer. Experiment: add
  8443 to the passthrough Service ports with a route by port, as 8089 has.
- **Isolation check.** The `admin` subscription CloudTAK holds is in every
  group, including `TakxIsolation`. Compare `takx-outsider` against
  `takx-desktop`, not against CloudTAK's admin view.
- **CloudTAK sign-in is untested**, and it has no basemap here.
- **A second VM restart has not happened.** The fixes below were run against
  reproductions of the two failures, not against another restart.
- Certificates from `TakClient` expire on 2026-12-26, since the restart
  reissued them.

## After a VM restart

The VM restarted on 2026-11-26 at about 18:14 UTC with everything deployed.
Docker was started again and `90-resume.sh` was run as it then was. What
happened, and what the scripts do about it now:

| What happened | Cause | Now |
| --- | --- | --- |
| `10-k3s.sh` ended with `FATAL: verify: pod create refused for 60s` on a cluster that was coming up | the bootstrap's `--verify` creates a pod. With UDS Core deployed that create passes the Pepr admission webhooks, which fail closed, and their pods were still starting | `10-k3s.sh` starts k3s without the proof, waits until a server-side dry run of a pod passes admission, then asks for the proof |
| gateway pods sat in `ErrImagePull` for a while | the Zarf registry pod had restarted and workloads were started before it answered | `90-resume.sh` waits for the registry and the Zarf agent, then the mesh, the gateways and Keycloak, before the TAK workloads |
| the CloudTAK pod stayed `0/3` (17 restarts of its init container in 66 minutes) | `merge-trust` writes `ca-bundle.pem` with mode 0444 into an `emptyDir`. After a restart the kubelet runs the init container again in the same pod, the file is still there, and the write fails with `EACCES` every time | `90-resume.sh` deletes a CloudTAK pod in that state once; the new pod has an empty volume. The cause is in `charts/cloudtak/files/merge-trust.mjs` of this repository and is not fixed there |
| every exported identity was revoked: `/Marti/api/version` answered `500`, "Attempt to use revoked certificate" | each `TakClient` got a new certificate when the TAK Server came back (new serial, `Ready` transition at 18:18:59 to 18:19:02). The API log shows the file users being created again; the operator reissues when its private enrollment state is lost. Not followed further | `90-resume.sh` compares each export's certificate with the fingerprint on its Secret and exports again when they differ; `60-verify.sh` now fails when the API does not answer `200` |

The rest came back by itself: node Ready, datastore, images, registry volume,
the three gateways with their addresses, Keycloak, the TAK Server with its
database.

How the fixes were checked, later the same day, without another restart
(`logs/90-resume-20261126-reproduction.log`):

- The four Pepr admission pods were deleted. A pod create was refused
  ("no endpoints available for service pepr-uds-core"); `10-k3s.sh` waited
  11 seconds and the bootstrap's proof then passed.
- The CloudTAK pod's sandbox was stopped with `crictl`, which makes the
  kubelet rerun the init container on the same volume. It failed as after
  the restart; `90-resume.sh` replaced the pod and CloudTAK became available.
- A second run exported the three stale identities again and the API
  answered `200`.

Not reproduced: the registry coming up late, and a k3s server that has to be
started (it was running throughout).

What a restart means for clients: every certificate a `TakClient` issued
before it is revoked. A device or a desktop that holds one has to be given the
new Secret.

## Removing it

Delete the `TakClient`s first so their certificates are revoked, then remove
the packages (`uds zarf package remove takserver --confirm`, and the two core
packages), stop k3s, unmount `/run/takx-hostproc/1` and `/run/takx-hostproc`,
remove the marked block from `/etc/hosts`, and delete
`/var/lib/takx-proto/secrets`.
