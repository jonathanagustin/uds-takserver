#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Hosted Linux x86_64 CI qualification runner for TAKX Kernel 8.30.5 format regressions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
KERNEL_NATIVE_DIR = HERE.parent / "kernel-native"
SCHEMA = "uds-takx-kernel-regressions-hosted-v1"
EXPECTED_REVISION = "9f6893dd657feacc35ec5de03dad721c2e44170e"
CATEGORIES = ("dted", "mbtiles", "geopackage", "kml", "elevation", "obj")
CLASS_PATTERN = re.compile(
    r"^(KernelFormatRegressions|KernelRegressionSmoke|KernelRuntimeSmoke|"
    r"NativeDatabaseCandidate|KernelThrowingLoggerCandidate)(?:\$[A-Za-z0-9_]+)?\.class$"
)
STRICT_JNI_AND_STARTUP_MARKERS = (
    "WARNING in native method",
    "WARNING: JNI",
    "FATAL ERROR",
    "JNI DETECTED ERROR",
    "UnsatisfiedLinkError",
    "NoClassDefFoundError",
    "EngineLibrary: Failed to load",
)
REQUIRED_REGRESSION_FOOTER = "PASS_REAL_KERNEL_FORMAT_REGRESSIONS_NO_RENDERER_OR_FULL_CLIENT_CLAIM"
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
)

UPSTREAM_LOCK = HERE / "upstream.lock.json"
BUILD_SCRIPT = HERE / "build.py"
REGRESSION_JAVA_FILES = (
    HERE / "java/KernelFormatRegressions.java",
    HERE / "java/KernelRegressionSmoke.java",
    KERNEL_NATIVE_DIR / "KernelRuntimeSmoke.java",
    KERNEL_NATIVE_DIR / "NativeDatabaseCandidate.java",
    KERNEL_NATIVE_DIR / "KernelThrowingLoggerCandidate.java",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_offline() -> dict:
    """Validate frozen kernel-regressions lockfile, runner, and Java regression sources."""
    for path in (UPSTREAM_LOCK, BUILD_SCRIPT, *REGRESSION_JAVA_FILES):
        if not path.is_file() or path.stat().st_size == 0:
            raise FileNotFoundError(f"missing or empty required kernel-regressions input: {path}")

    lock = json.loads(UPSTREAM_LOCK.read_text(encoding="utf-8"))
    if lock.get("schema") != 1:
        raise ValueError(f"unexpected upstream.lock.json schema: {lock.get('schema')}")
    if lock.get("revision") != EXPECTED_REVISION:
        raise ValueError(f"unexpected upstream.lock.json revision: {lock.get('revision')}")
    tests = lock.get("tests", [])
    if len(tests) != 6:
        raise ValueError(f"expected 6 upstream regression entries, found {len(tests)}")

    return {
        "schema": SCHEMA,
        "phase": "verify",
        "revision": EXPECTED_REVISION,
        "categories": list(CATEGORIES),
        "upstream_tests_count": len(tests),
        "upstream_lock_sha256": _sha256_file(UPSTREAM_LOCK),
        "build_py_sha256": _sha256_file(BUILD_SCRIPT),
        "source_hashes": {
            path.name: _sha256_file(path) for path in REGRESSION_JAVA_FILES
        },
    }


def audit_category_output(
    category: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    library_path: Path,
    native_manifest: dict[str, str],
) -> dict:
    """Enforce zero-exit, strict -Xcheck:jni cleanliness, category assertion marker, and ELF maps."""
    if exit_code != 0:
        raise ValueError(f"regression category {category} exited with {exit_code}")

    combined = f"{stdout}\n{stderr}"
    diags = [
        line
        for line in combined.splitlines()
        if any(marker in line for marker in STRICT_JNI_AND_STARTUP_MARKERS)
    ]
    if diags:
        raise ValueError(f"strict -Xcheck:jni or startup diagnostic in {category}: {diags[:3]}")

    cat_match = re.search(
        rf"^PASS_REAL_FORMAT_CATEGORY name={re.escape(category)} assertions=(\d+)\b",
        stdout,
        re.MULTILINE,
    )
    if not cat_match or int(cat_match.group(1)) < 1:
        raise ValueError(f"missing PASS_REAL_FORMAT_CATEGORY marker for {category}")
    if REQUIRED_REGRESSION_FOOTER not in stdout:
        raise ValueError(f"missing regression footer marker in {category}")

    expected_resolved = library_path.resolve()
    expected_packaged = {
        str(expected_resolved / name): sha for name, sha in native_manifest.items()
    }
    mapped_elfs = {
        path_str: sha
        for sha, path_str in re.findall(r"^ELF ([0-9a-f]{64}) ([^\r\n]+)$", stdout, re.MULTILINE)
    }
    if not mapped_elfs:
        raise ValueError(f"missing ELF map records in {category}")

    for pkg_path, pkg_sha in expected_packaged.items():
        if mapped_elfs.get(pkg_path) != pkg_sha:
            raise ValueError(f"packaged provider absent or mismatched in {category}: {pkg_path}")

    for mapped_path in mapped_elfs:
        if mapped_path not in expected_packaged and Path(mapped_path).name.startswith(
            FORBIDDEN_HOST_PREFIXES
        ):
            raise ValueError(f"unpackaged or host provider substitution in {category}: {mapped_path}")

    return {
        "qualified": True,
        "exit_code": 0,
        "assertions": int(cat_match.group(1)),
        "strict_jni_warnings": 0,
        "packaged_provider_count": len(expected_packaged),
        "mapped_elf_count": len(mapped_elfs),
    }


def _clean_env(java_home: Path, tmp_dir: Path) -> dict[str, str]:
    removed = {
        "CLASSPATH",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "_JAVA_OPTIONS",
        "JAVA_TOOL_OPTIONS",
        "JDK_JAVA_OPTIONS",
        "JDK_JAVAC_OPTIONS",
    }
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in removed
        and not k.startswith(("LD_", "JAVA_", "JDK_", "CCACHE_", "GDAL_", "CPL_", "PROJ_"))
    }
    env.update(
        JAVA_HOME=str(java_home),
        TMPDIR=str(tmp_dir),
        LC_ALL="C.UTF-8",
        PYTHONNOUSERSITE="1",
    )
    return env


def qualify(
    sdk_package_dir: Path,
    java_home: Path,
    output_dir: Path,
    gdal_data_jar: Path | None = None,
) -> dict:
    """Compile regression controls and run all 6 format categories under JDK 17 -Xcheck:jni."""
    verified = verify_offline()
    sdk_package_dir = sdk_package_dir.resolve()
    java_home = java_home.resolve()
    output_dir = output_dir.resolve()

    if output_dir.exists():
        raise ValueError(f"new output directory required: {output_dir}")

    manifest_path = sdk_package_dir / "version-abi-manifest.json"
    sdk_jar = sdk_package_dir / "kernel-8.30.5-linux-amd64-public-cpu.jar"
    third_party_dir = sdk_package_dir / "third-party"
    for required_file in (manifest_path, sdk_jar):
        if not required_file.is_file() or required_file.is_symlink():
            raise FileNotFoundError(f"missing regular SDK package input: {required_file}")
    if not third_party_dir.is_dir():
        raise FileNotFoundError(f"missing third-party directory: {third_party_dir}")

    dep_jars = sorted(third_party_dir.glob("*.jar"))
    if not dep_jars:
        raise FileNotFoundError(f"no dependency JARs found under {third_party_dir}")

    gdal_data_meta: dict | None = None
    extra_jars: list[Path] = []
    if gdal_data_jar is not None:
        if gdal_data_jar.is_symlink():
            raise ValueError(f"symlink gdal-data JAR refused: {gdal_data_jar}")
        resolved_gdal_data = gdal_data_jar.resolve()
        if not resolved_gdal_data.is_file() or resolved_gdal_data.suffix != ".jar":
            raise FileNotFoundError(f"regular .jar file required for --gdal-data-jar: {resolved_gdal_data}")
        extra_jars.append(resolved_gdal_data)
        gdal_data_meta = {
            "name": resolved_gdal_data.name,
            "sha256": _sha256_file(resolved_gdal_data),
            "bytes": resolved_gdal_data.stat().st_size,
        }

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    native_manifest = manifest["native_resources"]
    if len(native_manifest) != 9:
        raise ValueError(f"expected 9 native resources in SDK manifest, found {len(native_manifest)}")

    java_bin = java_home / "bin/java"
    javac_bin = java_home / "bin/javac"
    for tool in (java_bin, javac_bin):
        if not tool.is_file():
            raise FileNotFoundError(f"missing JDK tool: {tool}")

    output_dir.mkdir(parents=True, exist_ok=True)
    classes_dir = output_dir / "classes"
    tmp_dir = output_dir / "tmp"
    home_dir = output_dir / "home"
    data_dir = output_dir / "data"
    fixtures_dir = output_dir / "fixtures"
    for sub in (classes_dir, tmp_dir, home_dir, data_dir, fixtures_dir):
        sub.mkdir(parents=True, exist_ok=True)

    env = _clean_env(java_home, tmp_dir)
    sdk_cp = os.pathsep.join(str(p) for p in [sdk_jar, *dep_jars, *extra_jars])
    subprocess.run(
        [
            str(javac_bin),
            "-J-Xmx256m",
            "--release",
            "17",
            "-proc:none",
            "-implicit:none",
            "-sourcepath",
            "",
            "-cp",
            sdk_cp,
            "-d",
            str(classes_dir),
            *(str(p) for p in REGRESSION_JAVA_FILES),
        ],
        env=env,
        check=True,
        timeout=120,
    )
    for cls_path in classes_dir.rglob("*.class"):
        rel = cls_path.relative_to(classes_dir).as_posix()
        if not CLASS_PATTERN.fullmatch(rel):
            raise ValueError(f"unexpected non-control compiled class: {rel}")

    jar_md5 = hashlib.md5(sdk_jar.read_bytes(), usedforsecurity=False).hexdigest()
    library_path = tmp_dir / "takkernel/engine" / jar_md5 / "lib/linux64"
    run_cp = f"{classes_dir}{os.pathsep}{sdk_cp}"

    category_outcomes: dict[str, dict] = {}
    for category in CATEGORIES:
        proc = subprocess.run(
            [
                str(java_bin),
                "-Xcheck:jni",
                "-Xmx256m",
                "-XX:ActiveProcessorCount=2",
                "-Djava.awt.headless=true",
                f"-Djava.library.path={library_path}",
                f"-Djava.io.tmpdir={tmp_dir}",
                f"-Duser.home={home_dir}",
                f"-DTAK_APP_DATA_PATH={data_dir}",
                "-cp",
                run_cp,
                "KernelRegressionSmoke",
                str(sdk_jar),
                str(fixtures_dir / category),
                category,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        try:
            category_outcomes[category] = audit_category_output(
                category,
                proc.stdout,
                proc.stderr,
                proc.returncode,
                library_path,
                native_manifest,
            )
        except ValueError as exc:
            raise RuntimeError(
                f"{exc}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
            ) from exc

    receipt = {
        "schema": SCHEMA,
        "phase": "qualify",
        "verified_inputs": verified,
        "sdk_artifact": {
            "name": sdk_jar.name,
            "sha256": _sha256_file(sdk_jar),
            "md5": jar_md5,
            "bytes": sdk_jar.stat().st_size,
            "third_party_jar_count": len(dep_jars),
        },
        "gdal_data_jar": gdal_data_meta,
        "qualified_format_categories": list(category_outcomes),
        "category_outcomes": category_outcomes,
        "scope": {
            "xcheck_jni_clean": True,
            "all_six_format_categories_qualified": True,
            "kml_elevation_sampling_qualified": False,
            "renderer_acceptance": False,
            "full_client_acceptance": False,
            "security_accepted": False,
        },
    }
    receipt_path = output_dir / "hosted-kernel-regressions-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, indent=2))
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "qualify"), required=True)
    parser.add_argument("--sdk-package-dir", type=Path)
    parser.add_argument("--gdal-data-jar", type=Path)
    parser.add_argument("--java-home", type=Path, default=Path("/usr/lib/jvm/java-17-openjdk-amd64"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.phase == "verify":
        print(json.dumps(verify_offline(), indent=2))
        return 0

    missing = [
        flag
        for flag, val in (
            ("--sdk-package-dir", args.sdk_package_dir),
            ("--output", args.output),
        )
        if val is None
    ]
    if missing:
        parser.error(f"--phase qualify requires {' '.join(missing)}")

    qualify(
        sdk_package_dir=args.sdk_package_dir,
        java_home=args.java_home,
        output_dir=args.output,
        gdal_data_jar=args.gdal_data_jar,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
