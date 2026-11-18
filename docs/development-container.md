# Development container

Run shell, Python and local Helm rendering checks in Linux on macOS, Linux or Windows
through WSL2. The host launcher needs a POSIX shell and a running Docker or
Podman engine. Python, Bash, GNU utilities, Helm and ShellCheck run in
the container, so their host versions do not determine these checks.

```sh
./dev/run
CONTAINER_RUNTIME=podman ./dev/run
./dev/run python3 -B -m unittest tests.test_dev_package_key
./dev/run bash tests/test_runtime_paths.sh
./dev/run shell
```

The launcher builds `dev/Dockerfile`, using only the small `dev/` directory as
the build context. Subsequent runs use the container engine's build cache.
The official Python base is pinned to a multiarchitecture index for native
Linux amd64 and arm64. Python dependencies have explicit versions in
`dev/requirements.txt`; Debian utilities come from the base distribution's
current repositories when the image is built.
Helm **4.3.1** and ShellCheck **0.11.0** match `mise.toml`. The build downloads
their official Linux amd64 or arm64 release archives and verifies both pinned
SHA256 checksums before extracting or installing either binary. Other image
architectures are refused. The version and checksum pins live in `dev/install-tools`.

With no command, `check` first verifies both tool versions, runs the repository's
strict ShellCheck options on the development scripts, and renders both local
charts with Helm. It then runs the launcher, tool-installation, profile, package-key, image-producer,
receipt/collector/reuse, agent diagnostic/snapshot, kernel diagnostic and demo
failure-diagnostic unit tests, plus the Velero trial and local chart rendering
controls. Package-render decision/wiring guards and runtime-path shell controls
also run. The package-wide Zarf renderer still requires its UDS CLI, upstream
chart and built Pepr inputs; installing Helm alone does not supply those.
This is a focused offline check set. The complete CI suite, browser tests,
native SDK builds and live cluster acceptance retain their own dependencies
and gates. Arguments after `dev/run` select another command inside the same
environment; they are passed directly without shell evaluation.

The checkout is mounted at `/workspace` with write access for deliberate build
outputs. The container runs with the host user's numeric UID and GID, and an
ephemeral home. Linked Git worktrees are supported by mounting only that
repository's Git metadata read-only. A separate temporary Git repository used
by a fixture still has independent metadata. Git commits and branch changes
should be made on the host.

`CONTAINER_RUNTIME` selects one executable or absolute path. On macOS the name
`podman` also resolves its standard installation when it is absent from `PATH`;
an explicit executable path is always honored exactly. Otherwise Docker
is preferred when installed, followed by Podman (including its standard macOS
installation path). A stopped selected engine produces an error; the launcher
does not silently switch daemons or start a VM. `DEV_TOOLCHAIN_IMAGE` changes
the local image tag. No images are pushed by the launcher.

Podman on Linux uses `keep-id` when the selected engine reports rootless user
namespaces. Enforcing SELinux hosts may need an explicit local bind-mount label
policy; the launcher does not relabel the checkout or Git metadata automatically.

No container-engine socket, kubeconfig, host home or SSH agent is mounted
automatically, and host environment variables are not forwarded. The checkout
and its Git metadata remain visible to the selected command. Building the TAK application images
already uses containerized Gradle, Java and Node stages; driving that build or
deploying a k3d cluster additionally needs deliberate engine/network access.
This development image does not supply that access, the UDS/k3d CLIs,
desktop clients or KVM. Keep the existing Docker-based cluster workflow for
those tasks. Podman support for these local developer checks does not establish
Podman acceptance for the full deployment workflow.
