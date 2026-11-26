<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Package reference

Operational reference moved out of the top-level README: supported platforms, automated test suites, repository layout, and current known limitations.

## Supported architectures

| Architecture | Images | Zarf package | Tested in CI |
| --- | --- | --- | --- |
| `amd64` (x86_64) | yes | yes | install and upgrade |
| `arm64` (aarch64) | yes | yes | install and upgrade |

The images are multi-arch: one tag serves both. Zarf packages are per architecture, and `uds run create-dev-package` builds for the machine you run it on. For a cross-architecture build, generate the Pepr admission chart before calling Zarf so package creation can include it.

```bash
bash pepr/build.sh
uds zarf package create . --flavor upstream --architecture arm64 --confirm
```

The ATAK test device is amd64 only and needs KVM on the node.

## Testing

`uds run test-install` runs the full suite against a new `k3d-core-slim-dev` cluster. The checks are in [`tasks/test.yaml`](../tasks/test.yaml) and [`tests/`](../tests/):

- Pod health, Istio mesh, UDS policy, and gateway checks.
- `tests/api-smoke.sh` exercises the Marti API on 8443, 8447, and 8446, and streams a Cursor on Target (CoT) event on 8089.
- `tests/oidc-render.sh` checks that every TAK pod rendered the OIDC client secret.
- Playwright tests sign in through Keycloak, check the admin role and mapped groups, enroll a client certificate on 8446, and open the admin UI with a client certificate.
- The CloudTAK tests go from `map.<domain>` to the Keycloak login and back as that user, and require a live TAK connection with that user's certificate.

In GitHub Actions, the Test workflow runs the install test on amd64 and arm64. It runs the upgrade test from the last release on both architectures too. Full Core deploys the full UDS Core layers and checks monitoring, logging, Falco, Portal, and a Velero backup and restore. It runs nightly, on relevant pushes to `main`, and on pull requests that touch those areas or carry the `full-core` label. The ATAK e2e workflow deploys the KubeVirt device and checks that it connects.

The EKS, RKE2, and AKS workflows are manual. They initialize and validate the OpenTofu scaffolds in `.github/test-infra/` without creating cloud resources. Cloud credentials are not configured. [CI workflows](ci.md) lists every workflow and when it runs.

## Repository layout

| Path | Purpose |
| --- | --- |
| `zarf.yaml`, `common/zarf.yaml` | Package metadata, variables, images, and the `takserver` and `cloudtak` components |
| `charts/takserver/` | Config chart: UDS `Package`, ConfigMaps, standard TAK ports, and the certificate Job |
| `charts/cloudtak/` | CloudTAK chart: Deployment, PostGIS, and its UDS `Package` |
| `values/` | Values overlays for the upstream chart and the Pepr module |
| `pepr/` | `takserver-oidc` Pepr module. It renders CoreConfig, sets the TAK startup order, writes the CoreDNS rewrite, and copies certificates. See [Configuration](configuration.md#the-takserver-oidc-module) |
| `bundle/` | Test bundle (UDS Core passthrough gateway plus this package), `uds-config.yaml`, and `test-values.yaml` |
| `atak/` | Optional ATAK test device: KubeVirt package, VM, viewer, and Android application package (APK) build |
| `upstream/` | Git submodules: TAK Server, CloudTAK, and ATAK-CIV source at pinned tags |
| `tasks.yaml`, `tasks/` | `uds run` tasks, built on [uds-common](https://github.com/defenseunicorns/uds-common) |
| `tests/` | API smoke scripts, upgrade and backup checks, Python unit tests, and Playwright tests |
| `bench/` | Load benchmark harness, its configs, and the report. See [Benchmarks](benchmarks.md) |
| `demo/` | Scripts that record and assemble the demo videos |
| `docs/`, `adr/` | Documentation and architecture decision records |
| `.github/` | CI workflows, image builds, and test infrastructure |

The layout follows the [UDS package guide](https://docs.defenseunicorns.com/core/how-to-guides/packaging-applications/create-uds-package/) and [uds-packages/reference-package](https://github.com/uds-packages/reference-package).

## Known limitations

- On k3d, host ports 8446 and 8089 are not mapped, and host port 443 is the tenant gateway. Reach the passthrough gateway with a port-forward or a load balancer.
- Ports 8444, 9000, and 9001 are not exposed through a gateway.
- The upstream chart renders the `postgres-configuration` ConfigMap with empty `pg_hba.conf` and `postgres.conf` keys.
- The upstream chart does not mount the `uds-trust-bundle` ConfigMap. The API and messaging init containers import it into a copy of the Java virtual machine (JVM) trust store instead. See [Single sign-on](sso.md).
- CloudTAK has no OpenID Connect route of its own yet ([upstream issue #661](https://github.com/dfpc-coe/CloudTAK/issues/661)), so this package adds the Keycloak sign-in route in the CloudTAK pod. CloudTAK logout ends the Keycloak session. Its default basemap is OpenStreetMap through a tile proxy in its pod; set `basemap.enabled: false` where the cluster cannot reach it. Features that need S3 are not available. See [CloudTAK](cloudtak.md).
- Resource requests are sized for a small development node, and NATS runs as a single replica. Production sizing and storage are not tuned yet.
