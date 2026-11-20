# Building the images

TAK Server connects Team Awareness Kit (TAK) clients. This page explains where its image source comes from, how to run the build, and how continuous integration (CI) tags and publishes the six TAK images for both architectures.

`uds run build-image` and `mise run build-image` both run [`.github/container-and-chart/docker/build.sh`](../.github/container-and-chart/docker/build.sh). GitHub Actions runs that same script from [`.github/workflows/container.yaml`](../.github/workflows/container.yaml), and only when files under `.github/container-and-chart/`, that workflow, or the `upstream/takserver` pin change.

## Where the source comes from

The TAK Server source is a git submodule at [`upstream/takserver`](../upstream/takserver), pinned to commit `5187abd46d827d37cfc5708805eced197a837e49`, which is the public tag `5.7-RELEASE-14` of [TAK-Product-Center/Server](https://github.com/TAK-Product-Center/Server). `.gitmodules` marks it shallow, so a clone fetches that one commit and no history.

```bash
git submodule update --init --depth 1 upstream/takserver
```

`build.sh` runs that command when the directory is empty. It refuses to build when the submodule is checked out at a different commit than the one this repository pins, or when it has local changes.

`build.sh` passes the submodule work tree to BuildKit as the `takserver-src` build context, with the commit and its tree id as build arguments.

The builder stage copies the files, removes the submodule's `.git` pointer file, and commits them again as one commit tagged with the release. TAK's `src/build.gradle` reads its version from `git describe` and the branch name, so the war still reports `5.7-RELEASE-14-HEAD`, the same as a detached clone of the tag. The stage fails unless the tree id of that commit equals the upstream commit's tree id, which proves the files are exactly the pinned commit. The Ignite image copies `ignite.sh` from the same context instead of downloading it.

Renovate's `git-submodules` manager is on. `.gitmodules` sets `branch = 5.7-RELEASE-14`, so Renovate looks for newer `X.Y-RELEASE-N` tags and moves the pinned commit, the tag in `.gitmodules`, and the other `5.7-RELEASE-14` strings in one pull request.

## Running the build

The host needs a container runtime. Docker Buildx is the supported one. It builds for the machine's architecture unless you set `PLATFORMS`.

```bash
./.github/container-and-chart/docker/build.sh
PLATFORMS=linux/amd64,linux/arm64 ./.github/container-and-chart/docker/build.sh
PUSH_IMAGES=true ./.github/container-and-chart/docker/build.sh
```

`CONTAINER_RUNTIME=podman` is a best-effort override. Podman is not what this repository tests. If `podman buildx` is missing, the script falls back to `podman build` for a single platform and stops if you asked for more than one.

You do not need a Java Development Kit (JDK), Gradle, protoc, or Node on the host. The Dockerfile's builder stage is Temurin 17.0.20, pinned by the image digest. Gradle is the upstream wrapper, 8.10.2. protoc 3.25.8 and the web user interface (UI) build's Node downloads come from Gradle. The Gradle cache is a BuildKit cache mount at `/gradle`, so a rebuild on the same builder does not download everything again. In GitHub Actions that cache is also stored with the Actions cache.

The runtime stages use the official Temurin 17.0.21+9 JDK on Ubuntu 22.04, pinned by a manifest-list digest covering amd64 and arm64. The full JDK retains Java diagnostic and certificate tools. Generated encrypted PEM keys use explicit AES-256-CBC PKCS#8 encoding so Pepr's current crypto runtime can load them; configured passwords, key identities and the PKCS#12 format option are preserved. Runtime packages are installed with apt; Copa and the existing scan, signature, image smoke, install and upgrade gates still apply. The Temurin builder digest is a manifest list with both as well. The pin is the `RUNTIME_IMAGE` argument in [`Dockerfile`](../.github/container-and-chart/docker/Dockerfile), which Renovate moves to each new tag.

When Copa was added (#212), Trivy 0.76.1 reported 17 fixable OS vulnerabilities (1 critical, 7 high) in the base of that time, `1.24-3.1791779556`, and Copa 0.15.0 cleared all 17. The TAK image adds more packages, so [`.github/scripts/copa-patch.sh`](../.github/scripts/copa-patch.sh) runs after a single-architecture build and replaces the tag with the patched image before the push. A multi-arch push does not load an image Copa can patch. CI builds one architecture per runner. The PostGIS image is built here from the official `postgres` image and goes through the same Copa step (#222). NATS stays on its upstream tags, and this repository does not publish a patched copy of it.

The Container workflow retains `copa-rpm-evidence-<arch>-<run>-<attempt>` even
when patching fails. These bounded JSON files contain the scan report hash,
image ID, native architecture, source/run binding, OS family/version and only
the vulnerability IDs and installed/fixed versions for `krb5-libs`, `libpcap`,
`rpm` and `rpm-libs` (see #625). Raw reports, image tags, descriptions and
repository credentials are not retained. Local callers can opt in with
`COPA_EVIDENCE_DIR` pointing to an output directory. Diagnostic failure does not
change the patch result or scan policy. These scan observations do not establish
package-manager candidates, repository publication state or a successful patch.


The builder stays on Java 17. TAK Server 5.7-RELEASE-14, commit `5187abd46d827d37cfc5708805eced197a837e49`, is still the newest public tag. Its Gradle wrapper is 8.10.2, and that Gradle cannot run on Java 25: Groovy stops in the build script with `Unsupported class file major version 69`. TAK 5.7 targets Java 17, and the runtime image is OpenJDK 17, so `mise.toml` pins Temurin 17 as well. Moving the builder means a newer upstream wrapper, or a wrapper bump in this repository that still emits Java 17 bytecode and passes the install and upgrade tests.

The six tags match `zarf.yaml`:

- `ghcr.io/jonathanagustin/uds-takserver/takserver-config:5.7-RELEASE-14`
- `ghcr.io/jonathanagustin/uds-takserver/takserver-messaging:5.7-RELEASE-14`
- `ghcr.io/jonathanagustin/uds-takserver/takserver-api:5.7-RELEASE-14`
- `ghcr.io/jonathanagustin/uds-takserver/takserver-plugins:5.7-RELEASE-14`
- `ghcr.io/jonathanagustin/uds-takserver/takserver-database-setup:5.7-RELEASE-14`
- `ghcr.io/jonathanagustin/uds-takserver/takserver-ignite:5.7-RELEASE-14`

Pull requests scan the loaded images with Grype and Trivy. A push of these files also writes SPDX SBOMs and can push the tags. A vulnerability report does not fail the workflow.

`takserver-tool-ui` uses `react-scripts` 5.0.1. Its ESLint setup names a `jest/globals` environment that the installed plugin does not provide, so the UI build stops before it writes the bundle. The builder sets `DISABLE_ESLINT_PLUGIN=true`. The upstream `package.json` is not changed.

## Docker Hub rate limits

The Zarf package pulls two images from `docker.io`: NATS (`library/nats:2.15.3-alpine`) and the NATS config reloader (`natsio/nats-server-config-reloader:0.25.0`). Postgres is the PostGIS image built in this repository. Docker Hub limits anonymous pulls per Internet Protocol (IP) address, so a shared runner or sandbox can fail package creation with `429 toomanyrequests`. Pull them through a mirror:

```bash
uds run create-dev-package --set CREATE_OPTIONS="--registry-override docker.io=mirror.gcr.io"
```

The package still records the `docker.io` image names. Only the place Zarf pulls from changes.

## Architectures

The images are published for `linux/amd64` and `linux/arm64`. The container workflow does not use QEMU. It builds each architecture on a native GitHub runner (`ubuntu-24.04` and `ubuntu-24.04-arm`), each with its own Gradle and BuildKit cache. It pushes `<tag>-<key>-amd64` and `<tag>-<key>-arm64`. A last job runs [`merge-image-manifests.sh`](../.github/scripts/merge-image-manifests.sh), which joins them with `docker buildx imagetools create` into one index under `<tag>-<key>`. It writes nothing unless both per-architecture tags exist. `wait-for-tak-images.sh` checks that each index lists both platforms before a package or test job uses it.

### Tags keyed by the build inputs

- The inputs are the pinned `upstream/takserver` commit plus the git tree of `.github/container-and-chart/docker`, recorded in the `dev.uds-takserver.inputs` label. `<key>` is the first 12 hex digits of their SHA-256. `build.sh --tag` prints `5.7-RELEASE-14-<key>` for the current commit.
- The container workflow builds only when `5.7-RELEASE-14-<key>` is not already published for both architectures. A pull request that does not change those inputs reuses the images built for them before.
- `5.7-RELEASE-14` and `5.7-RELEASE-14-<arch>`, the tags in the committed files and in released packages, are written only from `main`, from the keyed images.
- `wait-for-tak-images.sh` waits for the keyed index, and `.github/scripts/pin-image-tags.sh` points `zarf.yaml`, `tasks.yaml` and `bundle/uds-config.yaml` at it before CI creates a package. A keyed tag only ever holds one set of inputs, so a pull request deploys its own TAK images even when another branch builds different ones at the same time.

The TAK, CloudTAK and PostGIS tag helpers prefer `sha256sum` and use `shasum -a 256` when it is unavailable. Both hash the same input bytes and retain the first 12 hex digits. A failed Git lookup or hash stops the helper before it prints a tag or invokes a build or registry command. The helpers are themselves recipe inputs, so committing a change to one correctly produces a new key.

Each helper also exposes `--inputs`, which prints its exact image input label without fetching a submodule, building, or contacting a registry. Recipe edits produce the existing `-dirty` label; candidate export rejects these labels and rejects initialized source checkouts that differ from the clean pinned source.

### Read-only fork candidates

Fork pull requests cannot publish into the upstream GHCR namespace. The dedicated CloudTAK and PostGIS workflows, and the Container workflow for the six TAK images, retain native builds, Copa patching and their existing security or upgrade checks. They export the resulting native image bytes as Actions artifacts after those checks. A receipt binds each archive to the repository, PR head, checked-out merge commit, producer workflow/run/attempt, native architecture, input label, config digests and layer contents. The receipt is an integrity record, not an attestation.

Consumers select the canonical producer for the same head and checkout. A scheduled producer's pending or failed build cannot be replaced by a published cache hit. Container's intentional immutable cache reuse records the exact native manifest and config digests in a separate decision artifact. A group outside the verified PR path filter may reuse its published immutable key after checking the requested native platform and inputs.

Each readiness poll shares one fresh metadata snapshot across the required platforms and uses authenticated conditional requests on later polls. Pending checks wait 15, 30, 60, then at most 120 seconds between polls; every required platform is examined before waiting. A producer that was never scheduled still fails after 90 seconds. Cached metadata requires GitHub's `304` confirmation of a previously validated ETag. HTTP failures remain terminal, including when older metadata exists. Error diagnostics contain fixed reason labels and bounded rate/request identifiers, excluding free-form server messages, credentials and signed URLs. See [GitHub's REST API polling guidance](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).

Package, native install/upgrade/airgap, Full Core, Benchmark, ATAK and Scan validate the selected artifacts before loading any image. Transport files are removed after verified loading. Each consuming job mirrors all eight application images into a uniquely named registry bound to `127.0.0.1`; its state binds both manifest and config digests. Package creation obtains a validated `--registry-override` through task variables, preserving canonical image names in the Zarf package and workload charts. Additional GHCR overrides, including overlapping source prefixes, are refused. Other overrides, such as a Docker Hub mirror, remain available.

Scan uses a separate current-side manifest with the verified loopback images pinned by manifest digest; it preserves the released-baseline lookup and maps digest image names explicitly for comparison. The canonical package definition is unchanged. Every consuming workflow removes only its own named registry on success, failure or cancellation. Main and release builds retain GHCR publishing, immutable indexes and their existing attestation policy.

To check a published tag:

```bash
docker buildx imagetools inspect ghcr.io/jonathanagustin/uds-takserver/takserver-api:5.7-RELEASE-14
```

A local `build.sh` run builds the host architecture. `PLATFORMS=linux/amd64,linux/arm64` builds both with Buildx, which emulates the other architecture under QEMU and takes much longer.

## Controlled runner comparison

The manual Test workflow accepts `runner_profile`: `temporary` (the default)
keeps the repository AMD override and Ubuntu 22.04 ARM runner; `ubuntu-24.04`
or `ubuntu-26.04` selects the matching native AMD and ARM labels. Pull requests
ignore this input. It does not change the normal runner defaults.

Use an exact reviewed main commit and the existing diagnostic selector for a
bounded comparison, then retain the run, attempt, recorded runner image, kernel
and selected-case results. A diagnostic success is not full acceptance. Newer
labels are candidates, not proof that the runner reliability issue is fixed.
Complete both-architecture Test, Full Core and Benchmark qualification before
changing defaults; this selector covers Test only.
