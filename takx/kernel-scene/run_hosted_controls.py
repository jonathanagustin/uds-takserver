#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux GCC 14 runner for TAK Kernel 8.30.5 ASSIMPSceneSpi scene controls."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAKX_ROOT = HERE.parent
LOCK_PATH = HERE / "inputs.lock.json"
SCENE_PROBE_SRC = HERE / "KernelSceneProbe.cpp"
ASSIMP_PROBE_SRC = TAKX_ROOT / "native-assimp" / "AssimpSourceProbe.cpp"

SCHEMA = "uds-takx-kernel-scene-hosted-v1"
REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
KERNEL_SUBDIR = Path("takkernel/engine/src/main/cpp")

KERNEL_TUS = (
    "model/ASSIMPSceneSpi.cpp",
    "model/Scene.cpp",
    "model/SceneNode.cpp",
    "model/SceneBuilder.cpp",
    "model/SceneGraphBuilder.cpp",
    "model/Mesh.cpp",
    "model/MeshBuilder.cpp",
    "model/Material.cpp",
    "model/VertexDataLayout.cpp",
    "model/ResourceMapper.cpp",
    "math/Matrix2.cpp",
    "feature/Envelope2.cpp",
    "port/String.cpp",
    "port/StringBuilder.cpp",
    "port/Platform.cpp",
    "util/Memory.cpp",
    "util/MemBuffer2.cpp",
    "util/DataInput2.cpp",
    "util/DataOutput2.cpp",
    "util/IO.cpp",
    "util/IO2.cpp",
    "util/ZipFile.cpp",
    "util/ProtocolHandler.cpp",
    "util/ConfigOptions.cpp",
    "util/Logging.cpp",
    "util/Logging2.cpp",
    "util/ProcessingCallback.cpp",
    "thread/Mutex.cpp",
    "thread/Lock.cpp",
    "thread/RWMutex.cpp",
    "thread/impl/ThreadImpl_common.cpp",
    "thread/impl/ThreadImpl_libpthread.cpp",
    "util/MemBuffer.cpp",
    "thread/Monitor.cpp",
    "thread/Cond.cpp",
)

STATIC_LIB_NAMES = (
    "libassimp.a",
    "libIrrXML.a",
    "libgdal.a",
    "libminizip.a",
    "libkmlengine.a",
    "libkmldom.a",
    "libkmlbase.a",
    "libkmlconvenience.a",
    "libkmlregionator.a",
    "libkmlxsd.a",
    "libproj.a",
    "liburiparser.a",
    "libexpat.a",
    "libz.a",
)

ASSIMP_MARKER = "valid_cpp_c_geometry_material_transform_uv_normal_export_roundtrip=true"
SCENE_MARKER = "actual_kernel_assimp_scene_geometry_material_uv_bounds_bad_uri=true"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    if lock.get("revision") != REVISION:
        raise ValueError(f"unexpected kernel-scene revision: {lock.get('revision')}")
    if tuple(lock.get("kernel_tus", ())) != KERNEL_TUS:
        raise ValueError("KERNEL_TUS does not match inputs.lock.json")
    for src in (SCENE_PROBE_SRC, ASSIMP_PROBE_SRC):
        if not src.is_file():
            raise FileNotFoundError(f"missing probe source: {src}")
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": REVISION,
        "kernel_tu_count": len(KERNEL_TUS),
        "scene_probe_sha256": sha256_file(SCENE_PROBE_SRC),
        "assimp_probe_sha256": sha256_file(ASSIMP_PROBE_SRC),
        "lock_sha256": sha256_file(LOCK_PATH),
    }


def check_no_textrel(readelf_dynamic_text: str) -> None:
    if "TEXTREL" in readelf_dynamic_text:
        raise ValueError("unexpected TEXTREL in libkernel-scene-subset.so")


def run_cmd(argv: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    proc = subprocess.run(
        argv,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(argv)}\n"
            f"stdout:\n{proc.stdout[-4000:]}\nstderr:\n{proc.stderr[-4000:]}"
        )
    return proc.stdout + proc.stderr


def qualify(atak_source: Path, deps_prefix: Path, output: Path) -> dict:
    verified = verify_offline()
    kernel_root = atak_source / KERNEL_SUBDIR
    if not kernel_root.is_dir():
        raise FileNotFoundError(f"missing ATAK Kernel C++ root: {kernel_root}")

    output.mkdir(parents=True, exist_ok=True)
    static_libs = {name: deps_prefix / "lib" / name for name in STATIC_LIB_NAMES}
    for name, path in static_libs.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing prebuilt static dependency {name}: {path}")

    # 1. Build a shared libassimp_probe.so from prebuilt static PIC Assimp + IrrXML + zlib
    #    and run AssimpSourceProbe.cpp to verify Assimp round-trip and emit roundtrip.assbin.
    assimp_so = output / "libassimp_probe.so"
    run_cmd(
        [
            "g++",
            "-shared",
            "-Wl,--whole-archive",
            str(static_libs["libassimp.a"]),
            str(static_libs["libIrrXML.a"]),
            "-Wl,--no-whole-archive",
            str(static_libs["libz.a"]),
            "-Wl,--no-undefined",
            "-lm",
            "-o",
            str(assimp_so),
        ],
        timeout=120,
    )
    assimp_probe = output / "assimp-probe"
    run_cmd(
        [
            "g++",
            "-std=c++17",
            "-O1",
            f"-I{deps_prefix / 'include'}",
            str(ASSIMP_PROBE_SRC),
            str(assimp_so),
            f"-Wl,--disable-new-dtags,-rpath,{output}",
            "-ldl",
            "-o",
            str(assimp_probe),
        ],
        timeout=90,
    )
    valid_dir = output / "valid"
    valid_dir.mkdir(parents=True, exist_ok=True)
    assimp_out = run_cmd([str(assimp_probe), str(valid_dir)], timeout=30)
    if ASSIMP_MARKER not in assimp_out:
        raise RuntimeError(f"AssimpSourceProbe failed:\n{assimp_out}")
    fixture = valid_dir / "roundtrip.assbin"
    if not fixture.is_file():
        raise FileNotFoundError(f"AssimpSourceProbe did not emit {fixture}")

    # 2. Compile all 35 whole TAK Kernel 8.30.5 C++ scene translation units.
    obj_dir = output / "kernel-objects"
    obj_dir.mkdir(parents=True, exist_ok=True)
    include_flags = [
        f"-I{kernel_root}",
        f"-I{deps_prefix / 'include'}",
    ]
    objects: list[Path] = []
    source_digests: dict[str, str] = {}
    for tu in KERNEL_TUS:
        src = kernel_root / tu
        if not src.is_file():
            raise FileNotFoundError(f"missing Kernel TU: {src}")
        source_digests[tu] = sha256_file(src)
        obj = obj_dir / (tu.replace("/", "_") + ".o")
        run_cmd(
            [
                "g++",
                "-std=c++17",
                "-O1",
                "-fPIC",
                *include_flags,
                "-c",
                str(src),
                "-o",
                str(obj),
            ],
            timeout=120,
        )
        objects.append(obj)

    # 3. Link libkernel-scene-subset.so with strict --no-undefined and verify zero TEXTREL.
    scene_so = output / "libkernel-scene-subset.so"
    run_cmd(
        [
            "g++",
            "-shared",
            "-Wl,--no-undefined",
            *[str(o) for o in objects],
            str(assimp_so),
            "-Wl,--start-group",
            *[str(static_libs[name]) for name in STATIC_LIB_NAMES if name not in ("libassimp.a", "libIrrXML.a")],
            "-Wl,--end-group",
            f"-Wl,--disable-new-dtags,-rpath,{output}",
            "-pthread",
            "-ldl",
            "-lm",
            "-o",
            str(scene_so),
        ],
        timeout=180,
    )
    check_no_textrel(run_cmd(["readelf", "-d", str(scene_so)]))

    # 4. Compile and run KernelSceneProbe.cpp against roundtrip.assbin and a missing URI.
    scene_probe = output / "kernel-scene-probe"
    run_cmd(
        [
            "g++",
            "-std=c++17",
            "-O1",
            *include_flags,
            str(SCENE_PROBE_SRC),
            str(scene_so),
            f"-Wl,--disable-new-dtags,-rpath,{output}",
            "-o",
            str(scene_probe),
        ],
        timeout=90,
    )
    scene_out = run_cmd([str(scene_probe), str(fixture), str(output / "missing.assbin")], timeout=30)
    if SCENE_MARKER not in scene_out:
        raise RuntimeError(f"KernelSceneProbe failed:\n{scene_out}")

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "revision": REVISION,
        "verify": verified,
        "kernel_sources": source_digests,
        "artifacts": {
            "roundtrip.assbin": sha256_file(fixture),
            "libassimp_probe.so": sha256_file(assimp_so),
            "libkernel-scene-subset.so": sha256_file(scene_so),
            "kernel-scene-probe": sha256_file(scene_probe),
        },
        "controls": {
            "assimp_roundtrip_passed": True,
            "kernel_35_tus_compiled": True,
            "strict_no_undefined_link": True,
            "no_textrel": True,
            "kernel_scene_probe_passed": True,
        },
        "scope": {
            "linux_gcc14_static_pic_host_abi": True,
            "sdk_accepted": False,
            "security_accepted": False,
            "client_accepted": False,
        },
    }
    receipt_path = output / "hosted-kernel-scene-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--atak-source", type=Path)
    parser.add_argument("--deps-prefix", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2, sort_keys=True))
        return 0
    if not args.atak_source or not args.deps_prefix or not args.output:
        parser.error("--atak-source, --deps-prefix, and --output are required for --phase qualify")
    receipt = qualify(args.atak_source, args.deps_prefix, args.output)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
