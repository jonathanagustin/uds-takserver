# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only

# Multi-stage builder for the prebuilt TAKX libglvnd 1.7.0 OCI cache:
# - Runs on the prebuilt takx-gl-runner:bookworm-amd64 image (Python 3.12 + GCC + Ninja + Mesa llvmpipe).
# - Verifies exact SHA-256 hashes of libglvnd-1.7.0 and meson-1.7.2 wheel from takx/native-gles/inputs.lock.json.
# - Builds genuine libEGL.so.1, libGLESv2.so.2, libGLdispatch.so.0 + headers from official NVIDIA source.
# - Qualifies real surfaceless EGL + OpenGL ES 3.x pixel readback on Mesa llvmpipe through the built libglvnd.
# - Exports only /glvnd into a minimal scratch OCI artifact.

FROM ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64 AS builder

SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

ARG GLVND_URL="https://codeload.github.com/NVIDIA/libglvnd/tar.gz/refs/tags/v1.7.0"
ARG GLVND_SHA256="073e7292788d4d3eeb45ea6c7bdcce9bfdb3b3eef8d7dbd47f2f30dce046ef98"
ARG MESON_URL="https://files.pythonhosted.org/packages/e5/2b/46bda4ef5a7ae4135dbfe27fc0368c44e5a349a897a54fdf2cedb8dcb66e/meson-1.7.2-py3-none-any.whl"
ARG MESON_SHA256="82c6818dc81743c96de3a458f06175776ebfde4081195ea31ea6971838f25e38"

ENV LANG=C \
    LC_ALL=C \
    TZ=UTC \
    SOURCE_DATE_EPOCH=1704067200

COPY surfaceless_es3.c /build/surfaceless_es3.c
WORKDIR /build

RUN python3 - <<'PY'
import hashlib, json, os, shutil, subprocess, tarfile, urllib.request, zipfile
from pathlib import Path

def fetch_verified(url: str, dest: Path, expected_sha: str) -> None:
    urllib.request.urlretrieve(url, dest)
    got = hashlib.sha256(dest.read_bytes()).hexdigest()
    if got != expected_sha:
        raise SystemExit(f"SHA-256 mismatch for {dest.name}: {got} != {expected_sha}")

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

glvnd_tgz = Path("/build/libglvnd-1.7.0.tar.gz")
meson_whl = Path("/build/meson-1.7.2-py3-none-any.whl")
fetch_verified(os.environ.get("GLVND_URL", "https://codeload.github.com/NVIDIA/libglvnd/tar.gz/refs/tags/v1.7.0"), glvnd_tgz, "073e7292788d4d3eeb45ea6c7bdcce9bfdb3b3eef8d7dbd47f2f30dce046ef98")
fetch_verified(os.environ.get("MESON_URL", "https://files.pythonhosted.org/packages/e5/2b/46bda4ef5a7ae4135dbfe27fc0368c44e5a349a897a54fdf2cedb8dcb66e/meson-1.7.2-py3-none-any.whl"), meson_whl, "82c6818dc81743c96de3a458f06175776ebfde4081195ea31ea6971838f25e38")

with tarfile.open(glvnd_tgz, "r:gz") as tf:
    tf.extractall("/build")

meson_site = Path("/build/meson-site")
with zipfile.ZipFile(meson_whl) as zf:
    zf.extractall(meson_site)

menv = os.environ.copy()
menv["PYTHONPATH"] = str(meson_site)

subprocess.run([
    "python3", "-m", "mesonbuild.mesonmain", "setup", "/build/out", "/build/libglvnd-1.7.0",
    "--prefix=/glvnd", "--libdir=lib", "--buildtype=release",
    "-Dx11=disabled", "-Dglx=disabled", "-Degl=true",
    "-Dgles1=false", "-Dgles2=true", "-Dhgl=false", "-Dheaders=true",
    "--wrap-mode=nodownload",
], env=menv, check=True)
subprocess.run(["ninja", "-C", "/build/out", "-j2"], env=menv, check=True)
subprocess.run(["python3", "-m", "mesonbuild.mesonmain", "install", "-C", "/build/out", "--no-rebuild"], env=menv, check=True)

shutil.copyfile("/build/libglvnd-1.7.0/README.md", "/glvnd/LICENSE-NOTICE.md")

# Qualify genuine surfaceless EGL + OpenGL ES 3.x dispatch to Mesa llvmpipe.
mesa_egl = Path("/usr/lib/x86_64-linux-gnu/libEGL_mesa.so.0")
if not mesa_egl.is_file():
    raise SystemExit(f"missing Mesa EGL ICD: {mesa_egl}")
vendor_json = Path("/build/mesa-vendor.json")
vendor_json.write_text(json.dumps({"file_format_version": "1.0.0", "ICD": {"library_path": str(mesa_egl)}}) + "\n")

probe = Path("/build/surfaceless_es3")
subprocess.run([
    "gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
    "-I/glvnd/include", "/build/surfaceless_es3.c",
    "/glvnd/lib/libEGL.so.1", "/glvnd/lib/libGLESv2.so.2", "-ldl",
    "-Wl,-rpath,/glvnd/lib", "-o", str(probe),
], check=True)

run_env = os.environ.copy()
run_env["LD_LIBRARY_PATH"] = "/glvnd/lib"
run_env["__EGL_VENDOR_LIBRARY_FILENAMES"] = str(vendor_json)
run_env["LIBGL_DRIVERS_PATH"] = "/usr/lib/x86_64-linux-gnu/dri"
run_env["LIBGL_ALWAYS_SOFTWARE"] = "1"
run_env["GALLIUM_DRIVER"] = "llvmpipe"
run_env["EGL_PLATFORM"] = "surfaceless"
out = subprocess.check_output([str(probe)], env=run_env, text=True)
print(out)
assert "real_surfaceless_es3_clear_readback=true" in out
assert "/glvnd/lib/libEGL.so.1" in out
assert "/glvnd/lib/libGLESv2.so.2" in out
assert "/glvnd/lib/libGLdispatch.so.0" in out

receipt = {
    "schema": "uds-takx-gles-cache-v1",
    "component": "libglvnd",
    "version": "1.7.0",
    "arch": "x86_64",
    "source_archive_sha256": "073e7292788d4d3eeb45ea6c7bdcce9bfdb3b3eef8d7dbd47f2f30dce046ef98",
    "meson_wheel_sha256": "82c6818dc81743c96de3a458f06175776ebfde4081195ea31ea6971838f25e38",
    "libraries": {
        "libEGL.so.1": sha256_file(Path("/glvnd/lib/libEGL.so.1").resolve()),
        "libGLESv2.so.2": sha256_file(Path("/glvnd/lib/libGLESv2.so.2").resolve()),
        "libGLdispatch.so.0": sha256_file(Path("/glvnd/lib/libGLdispatch.so.0").resolve()),
    },
    "controls": {
        "real_surfaceless_es3_clear_readback": True,
        "built_glvnd_dispatch_loaded": True,
    },
    "scope": {
        "software_mesa_llvmpipe": True,
        "hardware_gpu_accepted": False,
        "sdk_accepted": False,
        "security_accepted": False,
    },
}
Path("/glvnd/gles-receipt.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
PY

FROM scratch
COPY --from=builder /glvnd /glvnd
