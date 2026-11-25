#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX SDK + CommonCommo coexistence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
TAKX_ROOT = HERE.parent
KERNEL_NATIVE_DIR = TAKX_ROOT / "kernel-native"
SDK_PACKAGE_DIR = TAKX_ROOT / "sdk-package"
COMMO_NATIVE_DIR = TAKX_ROOT / "native-commoncommo"

SCHEMA = "uds-takx-sdk-coexist-hosted-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
LOAD_ORDERS = ("sdk-first", "common-first")

STRICT_JNI_AND_STARTUP_MARKERS = (
    "WARNING in native method",
    "WARNING: JNI",
    "FATAL ERROR",
    "JNI DETECTED ERROR",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
    "EngineLibrary: Failed to load",
)

FORBIDDEN_HOST_PREFIXES = (
    "libGLdispatch.so",
    "libGLESv2.so",
    "liblas.so",
    "liblas_c.so",
    "libgdal.so",
    "libgdalalljni.so",
    "libassimp.so",
    "libsqlite3.so",
    "libsqlcipher.so",
    "libssl.so",
    "libcrypto.so",
    "libtakengine.so",
    "libtakenginejni.so",
    "libcommoncommojni.so",
)

BUILD_SCRIPT = HERE / "build.py"
RESOURCES_LOCK = SDK_PACKAGE_DIR / "runtime-resources.lock.json"

COEXIST_JAVA_FILES = (
    KERNEL_NATIVE_DIR / "KernelRuntimeSmoke.java",
    KERNEL_NATIVE_DIR / "NativeDatabaseCandidate.java",
    KERNEL_NATIVE_DIR / "KernelThrowingLoggerCandidate.java",
    SDK_PACKAGE_DIR / "StockLoaderSmoke.java",
    SDK_PACKAGE_DIR / "RuntimeResourcesSmoke.java",
    SDK_PACKAGE_DIR / "DefaultIoReadonlySmoke.java",
    SDK_PACKAGE_DIR / "GdalReducedProfileSmoke.java",
    TAKX_ROOT / "CommonCommoSmoke.java",
    TAKX_ROOT / "CommonCommoSignalSmoke.java",
    TAKX_ROOT / "CommonCommoRoutingSmoke.java",
    COMMO_NATIVE_DIR / "CommonCommoThrowingLogger.java",
    HERE / "JoinedSdkCommo.java",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    """Validate frozen sdk-coexist runner, lockfile, and Java coexistence control sources."""
    for path in (BUILD_SCRIPT, RESOURCES_LOCK, *COEXIST_JAVA_FILES):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing or empty required sdk-coexist input: {path}")

    resource_lock = json.loads(RESOURCES_LOCK.read_text(encoding="utf-8"))
    if resource_lock.get("official_revision") != EXPECTED_REVISION:
        raise ValueError(
            f"unexpected runtime-resources.lock.json revision: {resource_lock.get('official_revision')}"
        )
    files = resource_lock.get("files", [])
    if not files:
        raise ValueError("runtime-resources.lock.json contains no files")

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": EXPECTED_REVISION,
        "load_orders": list(LOAD_ORDERS),
        "locked_runtime_resources": len(files),
        "runtime_resources_lock_sha256": _sha256_file(RESOURCES_LOCK),
        "build_py_sha256": _sha256_file(BUILD_SCRIPT),
        "source_hashes": {path.name: _sha256_file(path) for path in COEXIST_JAVA_FILES},
    }


def audit_coexist_output(
    order: str,
    profile: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    library_path: Path,
    native_manifest: dict[str, str],
    commo_jni_path: Path,
    commo_jni_sha256: str,
) -> dict:
    """Enforce zero-exit, strict -Xcheck:jni cleanliness, order markers, and exact ELF maps."""
    if order not in LOAD_ORDERS:
        raise ValueError(f"unsupported load order: {order}")
    if profile not in ("baseline", "reduced-kml"):
        raise ValueError(f"unsupported SDK profile: {profile}")
    if exit_code != 0:
        raise ValueError(f"coexistence order {order} exited with {exit_code}")

    combined = f"{stdout}\n{stderr}"
    diags = [
        line
        for line in combined.splitlines()
        if any(marker in line for marker in STRICT_JNI_AND_STARTUP_MARKERS)
    ]
    if diags:
        raise ValueError(f"strict -Xcheck:jni or startup diagnostic in {order}: {diags[:3]}")

    required_markers = [
        "PASS_JOINED_FIRST_GROUP_" + ("SDK" if order == "sdk-first" else "COMMONCOMMO"),
        "PASS_JOINED_COMMON_CONSTRUCTOR_PKCS12_SIGNAL_ROUTING_LOGGER",
        "PASS_JOINED_KERNEL_CONTROLS_AFTER_COMMON",
        f"PASS_JOINED_STOCK_SDK_COMMONCOMMO {order}",
        "PASS_STOCK_ENGINE_RESOURCE_LOADER_SCOPED_COMPONENT",
    ]
    if profile == "reduced-kml":
        required_markers.append("PASS_REAL_REDUCED_PROFILE_GDAL_SKIP_INIT_REGISTRY_NO_SUPPRESSION")

    missing = [marker for marker in required_markers if marker not in stdout]
    if missing:
        raise ValueError(f"missing required coexistence markers in {order}: {missing}")

    expected_resolved = library_path.resolve()
    expected_packaged = {
        str(expected_resolved / name): sha for name, sha in native_manifest.items()
    }
    expected_commo = str(commo_jni_path.resolve())
    allowed_exact = {**expected_packaged, expected_commo: commo_jni_sha256}

    mapped_elfs = {
        path_str: sha
        for sha, path_str in re.findall(
            r"^JOINED_ELF ([0-9a-f]{64}) ([^\r\n]+)$", stdout, re.MULTILINE
        )
    }
    if not mapped_elfs:
        raise ValueError(f"missing JOINED_ELF map records in {order}")

    for pkg_path, pkg_sha in expected_packaged.items():
        if mapped_elfs.get(pkg_path) != pkg_sha:
            raise ValueError(f"packaged SDK provider absent or mismatched in {order}: {pkg_path}")

    if mapped_elfs.get(expected_commo) != commo_jni_sha256:
        raise ValueError(
            f"CommonCommo JNI provider absent or mismatched in {order}: {expected_commo}"
        )

    for mapped_path in mapped_elfs:
        if mapped_path not in allowed_exact and Path(mapped_path).name.startswith(
            FORBIDDEN_HOST_PREFIXES
        ):
            raise ValueError(
                f"forbidden shared/host provider substitution in {order}: {mapped_path}"
            )

    return {
        "qualified": True,
        "order": order,
        "profile": profile,
        "exit_code": 0,
        "strict_jni_warnings": 0,
        "required_markers_count": len(required_markers),
        "packaged_sdk_provider_count": len(expected_packaged),
        "commoncommo_jni_verified": True,
        "mapped_elf_count": len(mapped_elfs),
    }


def _clean_env(java_home: Path, tmp_dir: Path) -> dict[str, str]:
    removed = {
        "CLASSPATH",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "BASH_ENV",
        "ENV",
        "PYTHONPATH",
        "_JAVA_OPTIONS",
        "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "JDK_JAVAC_OPTIONS",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in removed
        and not k.startswith(
            ("LD_", "DYLD_", "JAVA_", "JDK_", "CCACHE_", "GDAL_", "CPL_", "PROJ_", "OPENSSL_")
        )
    }
    env.update(
        JAVA_HOME=str(java_home),
        TMPDIR=str(tmp_dir),
        OPENSSL_CONF="/dev/null",
        OPENSSL_MODULES="/nonexistent-owned-openssl-providers",
        LC_ALL="C.UTF-8",
        PYTHONNOUSERSITE="1",
    )
    return env


def qualify(
    sdk_package_dir: Path,
    commoncommo_dir: Path,
    java_home: Path,
    output_dir: Path,
) -> dict:
    """Compile coexistence controls and run both sdk-first and common-first JVMs under -Xcheck:jni."""
    verified = verify_offline()
    sdk_package_dir = sdk_package_dir.resolve()
    commoncommo_dir = commoncommo_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")

    manifest_path = sdk_package_dir / "version-abi-manifest.json"
    sdk_jar = sdk_package_dir / "kernel-8.30.5-linux-amd64-public-cpu.jar"
    third_party_dir = sdk_package_dir / "third-party"
    commo_jar = commoncommo_dir / "jcommoncommo.jar"
    commo_lib_dir = commoncommo_dir / "lib"
    commo_jni = commo_lib_dir / "libcommoncommojni.so"

    for required_file in (manifest_path, sdk_jar, commo_jar, commo_jni):
        if not required_file.is_file() or required_file.is_symlink():
            raise FileNotFoundError(f"missing regular coexistence input: {required_file}")
    if not third_party_dir.is_dir():
        raise FileNotFoundError(f"missing third-party directory: {third_party_dir}")

    dep_jars = sorted(third_party_dir.glob("*.jar"))
    if not dep_jars:
        raise FileNotFoundError(f"no dependency JARs found under {third_party_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native_manifest = manifest["native_resources"]
    if len(native_manifest) != 9:
        raise ValueError(f"expected 9 native resources in SDK manifest, found {len(native_manifest)}")

    profile_raw = manifest.get("profile", "reduced-kml")
    profile = "reduced-kml" if "reduced" in str(profile_raw) else "baseline"

    java_bin = java_home / "bin/java"
    javac_bin = java_home / "bin/javac"
    for tool in (java_bin, javac_bin):
        if not tool.is_file():
            raise FileNotFoundError(f"missing JDK tool: {tool}")

    output_dir.mkdir(parents=True, exist_ok=True)
    classes_dir = output_dir / "classes"
    classes_dir.mkdir(parents=True, exist_ok=True)

    native_manifest_file = output_dir / "native-resources.sha256"
    native_manifest_file.write_text(
        "".join(f"{sha} {name}\n" for name, sha in native_manifest.items()),
        encoding="utf-8",
    )

    resource_lock = json.loads(RESOURCES_LOCK.read_text(encoding="utf-8"))
    runtime_resources_file = output_dir / "runtime-resources.sha256"
    runtime_resources_file.write_text(
        "".join(
            f"{item.get('lfs_payload', item)['sha256']} {item['kind']} {item['destination']}\n"
            for item in resource_lock["files"]
        ),
        encoding="utf-8",
    )

    compile_cp = os.pathsep.join(str(p) for p in [sdk_jar, *dep_jars, commo_jar])
    compile_env = _clean_env(java_home, output_dir)
    subprocess.run(
        [
            str(javac_bin),
            "-J-Xmx256m",
            "-J-XX:ActiveProcessorCount=2",
            "--release",
            "17",
            "-proc:none",
            "-implicit:none",
            "-sourcepath",
            "",
            "-cp",
            compile_cp,
            "-d",
            str(classes_dir),
            *(str(p) for p in COEXIST_JAVA_FILES),
        ],
        env=compile_env,
        check=True,
        timeout=120,
    )

    jar_md5 = _md5_file(sdk_jar)
    commo_jni_sha256 = _sha256_file(commo_jni)
    runtime_cp = os.pathsep.join(str(p) for p in [classes_dir, sdk_jar, *dep_jars, commo_jar])

    order_results: dict[str, dict] = {}
    for order in LOAD_ORDERS:
        app_dir = output_dir / order
        tmp_dir = app_dir / "tmp"
        home_dir = app_dir / "home"
        data_dir = app_dir / "data"
        for sub in (tmp_dir, home_dir, data_dir):
            sub.mkdir(parents=True, exist_ok=True)

        library_path = tmp_dir / "takkernel/engine" / jar_md5 / "lib/linux64"
        run_env = _clean_env(java_home, tmp_dir)

        proc = subprocess.run(
            [
                str(java_bin),
                "-Xcheck:jni",
                "-Xmx256m",
                "-XX:MaxMetaspaceSize=128m",
                "-XX:ActiveProcessorCount=2",
                "-Djava.awt.headless=true",
                f"-XX:ErrorFile={app_dir / 'hs_err_%p.log'}",
                f"-Djava.io.tmpdir={tmp_dir}",
                f"-Duser.home={home_dir}",
                f"-DTAK_APP_DATA_PATH={data_dir}",
                f"-Djava.library.path={library_path}{os.pathsep}{commo_lib_dir}",
                "-cp",
                runtime_cp,
                "JoinedSdkCommo",
                order,
                str(sdk_jar),
                str(commo_jar),
                str(native_manifest_file),
                str(runtime_resources_file),
                profile,
            ],
            env=run_env,
            cwd=app_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"JoinedSdkCommo {order} failed ({proc.returncode}):\n"
                f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            )
        order_results[order] = audit_coexist_output(
            order=order,
            profile=profile,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            library_path=library_path,
            native_manifest=native_manifest,
            commo_jni_path=commo_jni,
            commo_jni_sha256=commo_jni_sha256,
        )

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "revision": EXPECTED_REVISION,
        "sdk_profile": profile,
        "sdk_jar": {
            "name": sdk_jar.name,
            "sha256": _sha256_file(sdk_jar),
            "md5": jar_md5,
        },
        "commoncommo": {
            "jar_sha256": _sha256_file(commo_jar),
            "jni_so_sha256": commo_jni_sha256,
        },
        "verified": verified,
        "orders": order_results,
        "controls": {
            "sdk_first_co_load_passed": order_results["sdk-first"]["qualified"],
            "common_first_co_load_passed": order_results["common-first"]["qualified"],
            "strict_jni_warnings": 0,
        },
        "scope": {
            "hosted_linux_x86_64_sdk_commoncommo_coexist": True,
            "sdk_accepted": False,
            "security_accepted": False,
            "full_client_accepted": False,
            "renderer_accepted": False,
            "takserver_interoperability": False,
            "tls_peer_under_co_load": False,
            "quic_peer": False,
        },
    }
    (output_dir / "hosted-sdk-coexist-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), default="verify")
    parser.add_argument("--sdk-package-dir", type=Path)
    parser.add_argument("--commoncommo-dir", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        if any(
            v is not None
            for v in (args.sdk_package_dir, args.commoncommo_dir, args.java_home, args.output)
        ):
            parser.error("--phase verify does not accept qualify paths")
        print(json.dumps(verify_offline(), indent=2))
        return 0

    if not all((args.sdk_package_dir, args.commoncommo_dir, args.java_home, args.output)):
        parser.error(
            "--phase qualify requires --sdk-package-dir, --commoncommo-dir, --java-home, and --output"
        )
    receipt = qualify(
        sdk_package_dir=args.sdk_package_dir,
        commoncommo_dir=args.commoncommo_dir,
        java_home=args.java_home,
        output_dir=args.output,
    )
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
