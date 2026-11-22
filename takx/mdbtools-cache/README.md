<!-- Copyright 2026 uds-takserver authors -->
<!-- SPDX-License-Identifier: GPL-3.0-only -->

# Prebuilt TAKX MDBTools 1.0.0 Static PIC OCI Cache

Builds and publishes the ATAK-pinned **MDBTools 1.0.0** static PIC library (`libmdb.a` + headers + `COPYING` + `COPYING.LIB` + `mdbtools-receipt.json`) as a minimal `scratch` OCI artifact:

```text
ghcr.io/jonathanagustin/uds-takserver/takx-native-mdbtools:1.0.0-amd64
```

## Provenance & Controls

- Uses the digest-pinned GCC 14.2.0 compiler image shared with `takx/geos-cache/` and `takx/proj-cache/`.
- Verifies exact SHA-256 hashes of `mdbtools.tar.gz`, `mdbtools-pgsc.patch`, `COPYING`, and `COPYING.LIB` matching `takx/native-mdbtools/inputs.lock.json`.
- Compiles static PIC `libmdb.a` (`--disable-glib --disable-sql --disable-man --disable-nls --enable-iconv`), runs `mdb_control.c` (verifying `mdb_ole_read_next`, `mdb_set_boolean_fmt_numbers`, and missing-file rejection), verifies whole-archive `--no-undefined` / zero `TEXTREL`, and re-qualifies the extracted prefix inside `ghcr.io/jonathanagustin/uds-takserver/takx-gl-runner:bookworm-amd64`.
- No third-party test database is fetched or redistributed.
- Published tags carry Sigstore build provenance attestations (`actions/attest-build-provenance`).
- Scope remains explicitly bounded (`static_pic_host_abi: true`, `sdk_accepted: false`, `security_accepted: false`).
