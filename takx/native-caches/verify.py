#!/usr/bin/env python3
# Copyright 2026 uds-takserver authors
# SPDX-License-Identifier: GPL-3.0-only
"""Unified offline guard and restored-prefix verifier for TAKX Layer-0 native OCI caches."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCHEMA = "uds-takx-native-cache-v1"
REQUIRED_CONTROLS = (
    "static_pic_control",
    "whole_archive_no_undefined",
    "no_textrel",
)

COMPONENTS = {
    "zlib": {
        "version": "1.3.1",
        "archive_sha256": "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23",
        "license_sha256": "845efc77857d485d91fb3e0b884aaa929368c717ae8186b66fe1ed2495753243",
        "control_file": "zlib_control.c",
        "control_marker": "zlib_static_pic_control=true",
        "required_artifacts": ("lib/libz.a", "include/zlib.h", "LICENSE"),
    },
    "expat": {
        "version": "2.10.0",
        "archive_sha256": "c624ba2998fc65df1163a49641ff3f9ab73138b11641d92c86bee0fb01b1bc7f",
        "license_sha256": "31b15de82aa19a845156169a17a5488bf597e561b2c318d159ed583139b25e87",
        "control_file": "expat_control.c",
        "control_marker": "expat_static_pic_control=true",
        "required_artifacts": ("lib/libexpat.a", "include/expat.h", "COPYING"),
    },
    "quictls": {
        "version": "3.0.14+quic1",
        "archive_sha256": "75b0d9425c914913840d376388d8d8e6105ec8a03a9c33ab56fc3ac0384003a2",
        "license_sha256": "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a",
        "control_file": "quictls_control.c",
        "control_marker": "quictls_static_pic_control=true",
        "required_artifacts": ("lib/libssl.a", "lib/libcrypto.a", "LICENSE.txt"),
    },
    "openssl": {
        "version": "3.5.9",
        "archive_sha256": "603f5602e2eef00d77fbd429d34dcd5822bb301757a1bc9cdb24c670f1eb859a",
        "license_sha256": "7d5450cb2d142651b8afa315b5f238efc805dad827d91ba367d8516bc9d49e7a",
        "control_file": "openssl_control.c",
        "control_marker": "openssl_static_pic_control=true",
        "required_artifacts": ("lib/libssl.a", "lib/libcrypto.a", "LICENSE.txt"),
    },
    "curl": {
        "version": "8.23.0",
        "archive_sha256": "8dadd7d2536a314f982ad05a651b713f426bf7d02cc2c76cffb9796f2218e6c5",
        "license_sha256": "82f2f4427d6545ee5aaac4f0b80428da6cc8ba41c2cf5da3a03680ec327b9681",
        "control_file": "curl_control.c",
        "control_marker": "curl_static_pic_control=true",
        "required_artifacts": ("lib/libcurl.a", "include/curl/curl.h", "COPYING"),
    },
    "sqlcipher": {
        "version": "4.5.7",
        "archive_sha256": "b670845f28da0a3c717e991e9f18a334e08f6bc977190bbce6be864ca229f96d",
        "license_sha256": "09e4af560ce2e3c9c2aa6b564e35947b03db7d1ae345f22a32793ed46542cc14",
        "control_file": "sqlcipher_control.c",
        "control_marker": "sqlcipher_static_pic_control=true",
        "required_artifacts": ("lib/libsqlite3.a", "include/sqlite3.h", "LICENSE.md"),
    },
    "laszip": {
        "version": "3.4.3",
        "archive_sha256": "66336908e17bb92739803c80fe627461860cacb017dcff55e476e131981e7b6d",
        "license_sha256": "dbc8eab1421212bf7b392ea00619f6b8286df50b702d21f6e7382805828a1cef",
        "control_file": "laszip_control.c",
        "control_marker": "laszip_static_pic_control=true",
        "required_artifacts": ("lib/liblaszip.a", "include/laszip/laszip_api.h", "COPYING"),
    },
    "assimp": {
        "version": "4.0.1",
        "archive_sha256": "459eadafbb84251a93f94c70f2fd8922c169911ff89fe526f1529bac6f699ae0",
        "license_sha256": "a26ccc3dbf2f58ea99c100945a8a126fa0f9f4d7fd2b49aa8bdb8e09355864d8",
        "control_file": "assimp_control.c",
        "control_marker": "assimp_static_pic_control=true",
        "required_artifacts": ("lib/libassimp.a", "lib/libIrrXML.a", "LICENSE"),
    },
    "nghttp2": {
        "version": "1.62.1",
        "archive_sha256": "3966ec82fda7fc380506d372a260d8d9b6e946be4deaef1fecc1a74b4809ae3d",
        "license_sha256": "6b94f3abc1aabd0c72a7c7d92a77f79dda7c8a0cb3df839a97890b4116a2de2a",
        "control_file": "nghttp2_control.c",
        "control_marker": "nghttp2_static_pic_control=true",
        "required_artifacts": ("lib/libnghttp2.a", "include/nghttp2/nghttp2.h", "COPYING"),
    },
    "libmicrohttpd": {
        "version": "1.0.1",
        "archive_sha256": "a89e09fc9b4de34dde19f4fcb4faaa1ce10299b9908db1132bbfa1de47882b94",
        "license_sha256": "7399547209438c93f9b90297954698773d4846cea44cde5ca982c84c45952a3b",
        "control_file": "libmicrohttpd_control.c",
        "control_marker": "libmicrohttpd_static_pic_control=true",
        "required_artifacts": ("lib/libmicrohttpd.a", "include/microhttpd.h", "COPYING"),
    },
    "ngtcp2": {
        "version": "1.6.0",
        "archive_sha256": "3e40cfb92ab710295cb7c07721db28d16c6f251f61420a462c448cf39a59cc29",
        "license_sha256": "542dd526d2ea23565f0571cc68df6f05ed11248a90ff0f32de19d8ba589db513",
        "control_file": "ngtcp2_control.c",
        "control_marker": "ngtcp2_static_pic_control=true",
        "required_artifacts": (
            "lib/libngtcp2.a",
            "lib/libngtcp2_crypto_quictls.a",
            "include/ngtcp2/ngtcp2.h",
            "COPYING",
        ),
    },
    "protobuf": {
        "version": "3.21.0",
        "archive_sha256": "28e74a8a241a91c0a6aec997d0d6efda9bc0baa0ed7acfaf392555b88bca8b7c",
        "license_sha256": "6e5e117324afd944dcf67f36cf329843bc1a92229a8cd9bb573d7a83130fea7d",
        "control_file": "protobuf_control.cpp",
        "control_marker": "protobuf_static_pic_control=true",
        "required_artifacts": (
            "lib/libprotobuf-lite.a",
            "bin/protoc",
            "include/google/protobuf/stubs/common.h",
            "LICENSE",
        ),
    },
    "uriparser": {
        "version": "0.9.8",
        "archive_sha256": "d6289387eaf2495e38ed80d709ad673fe04d63fad707badfee96f3d2dabc7c35",
        "license_sha256": "287f09e6546a9610f949f89e8fb937cacfeabd7bfaa8c8a0c18312193bf04ad3",
        "control_file": "uriparser_control.c",
        "control_marker": "uriparser_static_pic_control=true",
        "required_artifacts": ("lib/liburiparser.a", "include/uriparser/Uri.h", "COPYING"),
    },
    "fmt": {
        "version": "10.2.1",
        "archive_sha256": "1250e4cc58bf06ee631567523f48848dc4596133e163f02615c97f78bab6c811",
        "license_sha256": "07580f2a3b35709ce703d523f447b242f6dfec7582a8c0df102c7fa2849375f8",
        "control_file": "fmt_control.cpp",
        "control_marker": "fmt_static_pic_control=true",
        "required_artifacts": ("lib/libfmt.a", "include/fmt/core.h", "LICENSE"),
    },
    "zstd": {
        "version": "1.5.6",
        "archive_sha256": "30f35f71c1203369dc979ecde0400ffea93c27391bfd2ac5a9715d2173d92ff7",
        "license_sha256": "7055266497633c9025b777c78eb7235af13922117480ed5c674677adc381c9d8",
        "control_file": "zstd_control.c",
        "control_marker": "zstd_static_pic_control=true",
        "required_artifacts": ("lib/libzstd.a", "include/zstd.h", "LICENSE"),
    },
    "meshoptimizer": {
        "version": "0.20",
        "archive_sha256": "cf1077a83958bed3d8da28a841ca53a6a42d871e49023edce64e37002a0f5a48",
        "license_sha256": "af0399f6fe106205872fd58c25f5a33fdefd34754b6d96d3f6b4307eb17d05ed",
        "control_file": "meshoptimizer_control.cpp",
        "control_marker": "meshoptimizer_static_pic_control=true",
        "required_artifacts": ("lib/libmeshoptimizer.a", "include/meshoptimizer.h", "LICENSE.md"),
    },
    "asyncplusplus": {
        "version": "1.1",
        "archive_sha256": "abca8bcdd423a0bccd677fe5e21d368a0e2310d466e208e147a0f033eab95c1e",
        "license_sha256": "cfd9b9cfc85de17b05900d2870413ff80e7427bc9c156cd5d189b1ce9158b9ef",
        "control_file": "asyncplusplus_control.cpp",
        "control_marker": "asyncplusplus_static_pic_control=true",
        "required_artifacts": ("lib/libasync++.a", "include/async++.h", "LICENSE"),
    },
    "tinyxml2": {
        "version": "10.0.0",
        "archive_sha256": "3bdf15128ba16686e69bce256cc468e76c7b94ff2c7f391cc5ec09e40bff3839",
        "license_sha256": "9332252e9b9e46db8285d4a3f0bf25f139bf1dca6781b956d57f2302efca6432",
        "control_file": "tinyxml2_control.cpp",
        "control_marker": "tinyxml2_static_pic_control=true",
        "required_artifacts": ("lib/libtinyxml2.a", "include/tinyxml2.h", "LICENSE.txt"),
    },
    "libjpeg-turbo": {
        "version": "3.0.3",
        "archive_sha256": "a649205a90e39a548863a3614a9576a3fb4465f8e8e66d54999f127957c25b21",
        "license_sha256": "e548c96c0c09397c94b5c5531836c02ff2c403f1f2b130cad8e3ec9439b5a7cc",
        "control_file": "libjpeg_turbo_control.c",
        "control_marker": "libjpeg_turbo_static_pic_control=true",
        "required_artifacts": ("lib/libjpeg.a", "include/jpeglib.h", "LICENSE.md"),
    },
    "libwebp": {
        "version": "1.4.0",
        "archive_sha256": "12af50c45530f0a292d39a88d952637e43fb2d4ab1883c44ae729840f7273381",
        "license_sha256": "5aec868f669e384a22372a4e8a1a6cd7d44c64cd451f960ca69cc170d1e13acf",
        "control_file": "libwebp_control.c",
        "control_marker": "libwebp_static_pic_control=true",
        "required_artifacts": (
            "lib/libwebp.a",
            "lib/libsharpyuv.a",
            "include/webp/encode.h",
            "COPYING",
        ),
    },
    "spdlog": {
        "version": "1.14.1",
        "archive_sha256": "1586508029a7d0670dfcb2d97575dcdc242d3868a259742b69f100801ab4e16b",
        "license_sha256": "4ccecab18d1ff0b61174fe3d6c430541625d3ddb865b0d5887db296f883c76e7",
        "control_file": "spdlog_control.cpp",
        "control_marker": "spdlog_static_pic_control=true",
        "required_artifacts": ("lib/libspdlog.a", "include/spdlog/spdlog.h", "LICENSE"),
    },
    "draco": {
        "version": "1.5.7",
        "archive_sha256": "bf6b105b79223eab2b86795363dfe5e5356050006a96521477973aba8f036fe1",
        "license_sha256": "d3709b0fb4b8a94bbb1d02b8a2e484f258b0d9c5c5a01f940391f3fe662cd1a4",
        "control_file": "draco_control.cpp",
        "control_marker": "draco_static_pic_control=true",
        "required_artifacts": ("lib/libdraco.a", "include/draco/compression/decode.h", "LICENSE"),
    },
    "zlib-ng": {
        "version": "2.1.5",
        "archive_sha256": "3f6576971397b379d4205ae5451ff5a68edf6c103b2f03c4188ed7075fbb5f04",
        "license_sha256": "d3c80be055d94d798eaa786116e84fa0b010bc11420b5d2060d978ea77845436",
        "control_file": "zlib_ng_control.c",
        "control_marker": "zlib_ng_static_pic_control=true",
        "required_artifacts": ("lib/libz-ng.a", "include/zlib-ng.h", "LICENSE.md"),
    },
    "modp-base64": {
        "version": "2020-09-26",
        "archive_sha256": "8c26f89672c6a5343ba8ed58200207e67530bc5a318130442069204068b1585c",
        "license_sha256": "1fecc108ae7078aab437014074370f3a8a58bcb231596e5c4e59f6cc4083b10d",
        "control_file": "modp_base64_control.c",
        "control_marker": "modp_base64_static_pic_control=true",
        "required_artifacts": ("lib/libmodp_b64.a", "include/modp_b64.h", "COPYING"),
    },
    "abseil": {
        "version": "20240116.2",
        "archive_sha256": "733726b8c3a6d39a4120d7e45ea8b41a434cdacde401cba500f14236c49b39dc",
        "license_sha256": "c79a7fea0e3cac04cd43f20e7b648e5a0ff8fa5344e644b0ee09ca1162b62747",
        "control_file": "abseil_control.cpp",
        "control_marker": "abseil_static_pic_control=true",
        "required_artifacts": (
            "lib/libabsl_strings.a",
            "lib/libabsl_base.a",
            "include/absl/strings/str_cat.h",
            "LICENSE",
        ),
    },
    "libiconv": {
        "version": "1.15",
        "archive_sha256": "ccf536620a45458d26ba83887a983b96827001e92a13847b45e4925cc8913178",
        "license_sha256": "56bdea73b6145ef6ac5259b3da390b981d840c24cb03b8e1cbc678de7ecfa18d",
        "control_file": "libiconv_control.c",
        "control_marker": "libiconv_static_pic_control=true",
        "required_artifacts": ("lib/libiconv.a", "include/iconv.h", "COPYING.LIB"),
    },
    "sqlite3": {
        "version": "3.46.0",
        "archive_sha256": "6f8e6a7b335273748816f9b3b62bbdc372a889de8782d7f048c653a447417a7d",
        "license_sha256": "409765acd2efdd885e9251f1aee9214969f9a82cb92bc7bcbb35fe5e71cea1ef",
        "control_file": "sqlite3_control.c",
        "control_marker": "sqlite3_static_pic_control=true",
        "required_artifacts": ("lib/libsqlite3.a", "include/sqlite3.h", "README.txt"),
    },
    "ktx": {
        "version": "4.3.1",
        "archive_sha256": "98f051c91e723f85f3a5136a75dfa0ee186d72cc93223fd0daa1b3c8077ccb69",
        "license_sha256": "0f92b4ce771585dfebc963ede074a30b4431b9e327911db44097d83ccf372128",
        "control_file": "ktx_control.c",
        "control_marker": "ktx_static_pic_control=true",
        "required_artifacts": ("lib/libktx.a", "include/ktx.h", "LICENSE.md"),
    },
    "s2geometry": {
        "version": "0.11.1",
        "archive_sha256": "bdbeb8ebdb88fa934257caf81bb44b55711617a3ab4fdec2c3cfd6cc31b61734",
        "license_sha256": "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        "control_file": "s2geometry_control.cpp",
        "control_marker": "s2geometry_static_pic_control=true",
        "required_artifacts": ("lib/libs2.a", "include/s2/s2cell_id.h", "LICENSE"),
    },
    "libxml2": {
        "version": "2.13.3",
        "archive_sha256": "0805d7c180cf09caad71666c7a458a74f041561a532902454da5047d83948138",
        "license_sha256": "c99aae1afe013e50b8b3701e089222b351258043c3025b64053a233fd25b4be7",
        "control_file": "libxml2_control.c",
        "control_marker": "libxml2_static_pic_control=true",
        "required_artifacts": (
            "lib/libxml2.a",
            "include/libxml2/libxml/parser.h",
            "Copyright",
        ),
    },
    "proj": {
        "version": "4.9.1",
        "archive_sha256": "fca0388f3f8bc5a1a803d2f6ff30017532367992b30cf144f2d39be88f36c319",
        "license_sha256": "2dcac4af6990093ef48b896bfb81452d46a31b08d46cce34b27e83f93b2e37f6",
        "control_file": "proj_control.c",
        "control_marker": "proj_static_pic_control=true",
        "required_artifacts": ("lib/libproj.a", "include/proj_api.h", "COPYING"),
    },
    "geos": {
        "version": "3.4.3",
        "archive_sha256": "cfbf68079117c1c2b76411636444ff41d73c31093c4cab9dcc9a8c1bbe7e3897",
        "license_sha256": "dc626520dcd53a22f727af3ee42c770e56c97a64fe3adb063799d8ab032fe551",
        "control_file": "geos_control.c",
        "control_marker": "geos_static_pic_control=true",
        "required_artifacts": ("lib/libgeos.a", "lib/libgeos_c.a", "include/geos_c.h", "COPYING"),
    },
    "mdbtools": {
        "version": "1.0.0",
        "archive_sha256": "3446e1d71abdeb98d41e252777e67e1909b186496fda59f98f67032f7fbcd955",
        "license_sha256": "b7993225104d90ddd8024fd838faf300bea5e83d91203eab98e29512acebd69c",
        "control_file": "mdbtools_control.c",
        "control_marker": "mdbtools_static_pic_control=true",
        "required_artifacts": ("lib/libmdb.a", "include/mdbtools.h", "COPYING.LIB"),
    },
    "libkml": {
        "version": "1.3.0",
        "archive_sha256": "39692c0184933424e35ef69e5101f1ff0d664f2b50b972887cfc15fda032d1cb",
        "license_sha256": "e7952c5e1745f629c235c7aacd066fc36034d37571a964b9142d7fb95b5523eb",
        "control_file": "libkml_control.cpp",
        "control_marker": "libkml_static_pic_control=true",
        "required_artifacts": (
            "lib/libkmlbase.a",
            "lib/libkmldom.a",
            "lib/libkmlengine.a",
            "lib/libkmlconvenience.a",
            "lib/libkmlregionator.a",
            "lib/libkmlxsd.a",
            "lib/libminizip.a",
            "include/kml/dom.h",
            "COPYING",
        ),
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_component_offline(name: str) -> dict:
    if name not in COMPONENTS:
        raise ValueError(f"unknown component: {name}")
    spec = COMPONENTS[name]
    comp_dir = ROOT / name
    dockerfile = (comp_dir / "Dockerfile").read_text(encoding="utf-8")
    control = (comp_dir / spec["control_file"]).read_text(encoding="utf-8")
    if spec["archive_sha256"] not in dockerfile:
        raise ValueError(f"{name}/Dockerfile missing locked archive SHA-256")
    if spec["license_sha256"] not in dockerfile:
        raise ValueError(f"{name}/Dockerfile missing locked license SHA-256")
    if spec["control_marker"] not in control:
        raise ValueError(f"{name}/{spec['control_file']} missing control marker")
    return {
        "component": name,
        "version": spec["version"],
        "dockerfile_sha256": sha256_file(comp_dir / "Dockerfile"),
        "control_source_sha256": sha256_file(comp_dir / spec["control_file"]),
    }


def verify_all_offline() -> dict:
    return {
        "schema": SCHEMA,
        "phase": "verify",
        "components": {name: verify_component_offline(name) for name in sorted(COMPONENTS)},
    }


def validate_receipt(name: str, receipt: dict, prefix: Path | None = None) -> None:
    if name not in COMPONENTS:
        raise ValueError(f"unknown component: {name}")
    spec = COMPONENTS[name]
    if receipt.get("schema") != SCHEMA:
        raise ValueError("unexpected native cache receipt schema")
    if receipt.get("component") != name or receipt.get("version") != spec["version"]:
        raise ValueError(f"unexpected component/version for {name}")
    if receipt.get("arch") not in ("x86_64", "amd64"):
        raise ValueError("native cache requires x86_64")
    if receipt.get("source_archive_sha256") != spec["archive_sha256"]:
        raise ValueError("source_archive_sha256 mismatch")
    if receipt.get("license_sha256") != spec["license_sha256"]:
        raise ValueError("license_sha256 mismatch")
    controls = receipt.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("missing controls object")
    for ctrl in REQUIRED_CONTROLS:
        if controls.get(ctrl) is not True:
            raise ValueError(f"required control did not pass: {ctrl}")
    scope = receipt.get("scope")
    if not isinstance(scope, dict):
        raise ValueError("missing scope object")
    if scope.get("static_pic_host_abi") is not True:
        raise ValueError("scope.static_pic_host_abi must be true")
    for key in ("sdk_accepted", "security_accepted"):
        if scope.get(key) is not False:
            raise ValueError(f"scope.{key} must remain false")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("missing artifacts object")
    for rel in spec["required_artifacts"]:
        if len(str(artifacts.get(rel, ""))) != 64:
            raise ValueError(f"missing or invalid hash for {rel}")
        if prefix is not None:
            path = prefix / rel
            if not path.is_file():
                raise FileNotFoundError(f"missing restored artifact: {rel}")
            actual = sha256_file(path)
            if actual != artifacts[rel]:
                raise ValueError(f"hash mismatch for {rel}: {actual} != {artifacts[rel]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("verify", "check-prefix"), default="verify")
    parser.add_argument("--component", choices=sorted(COMPONENTS))
    parser.add_argument("--prefix", type=Path)
    args = parser.parse_args(argv)
    if args.phase == "verify":
        out = verify_component_offline(args.component) if args.component else verify_all_offline()
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    if not args.component or not args.prefix:
        parser.error("--component and --prefix are required for --phase check-prefix")
    receipt = json.loads((args.prefix / "cache-receipt.json").read_text(encoding="utf-8"))
    validate_receipt(args.component, receipt, prefix=args.prefix)
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
