# Historical GEOS source profile

This optional Linux amd64 component experiment builds official GEOS 3.4.3 with its original ATAK portability patch. It does not build the Kernel SDK or TAKX client.

The lock pins the official archive, LGPL 2.1 license text, three patch pre/postimages and tool versions. Supply Linux amd64 GCC/G++ **14.2.0**, GNU Make **4.4.1** and GNU binutils **2.44** (`ar`, `ranlib`, `readelf`). The recipe uses checked-in configure, static PIC libraries and C++11, with no host GEOS fallback. It checks real C geometry intersection, distance, buffer validity/area and malformed-input error handling, plus a whole-archive PIC link. This does not establish security suitability for this historical release, distribution obligations, C++/target ABI or full upstream test-suite acceptance.

The supported historical C compatibility mode is explicitly `-O2 -fPIC -std=gnu89`; C++ remains `-O2 -fPIC -std=c++11`. This keeps the locked configure script's implicit-int/undeclared-function probes usable under GCC 14, whose default-mode diagnostics can otherwise produce false-negative features. No configure script or additional upstream source is patched, and no cached feature answers are supplied. Inherited Autoconf/PostgreSQL feature answers and configure/preprocessor overrides are removed. The actual geometry consumer still compiles separately with `-std=c11`.

Before make, the selected C++ compiler preprocesses `build/include/config.h` and `build/include/geos/platform.h` independently. Their active `HAVE_INT64_T_64`, `HAVE_LONG_INT_64` and `HAVE_LONG_LONG_INT_64` choices must agree, at least one must be defined as `1`, and an active `INT64_IS_REALLY32` fallback is refused. An unavailable `int64_t` probe alone is permitted when an agreeing long/long-long selection works. The checked-in `GeosInt64.cpp` control then compiles and runs against the generated platform header: it requires eight-bit bytes, an eight-byte signed integer with 63 value bits, and real positive/negative arithmetic above the 32-bit range. Text in an inactive conditional branch cannot satisfy this check.

Run only with the reviewed tools and verified cache (network remains off by default):

```sh
python3 takx/native-geos/build.py --output /private/new-geos-output --cache /private/source-cache
```

`--fetch-dependencies` explicitly allows only the locked official archive and patch downloads. Output must be new and outside the repository. One make job and a 600-second total command budget bound the experiment; failures stop and retain bounded local diagnostics, without a success receipt. Keep output private. No automatic source compatibility edits or retries occur. The output/cache size check is a between-command check, not a filesystem quota.

Cheap offline checks:

```sh
python3 -B -m unittest discover -s takx/native-geos -p test_build.py -v
```

An actual completed proof can be checked with `TAKX_NATIVE_GEOS_PROOF=/private/new-geos-output` using the same command. The receipt binds recipe/helper bytes, exact patched source inventory, installed files, tools, output artifacts and runtime closure. Its `integer_configuration` also binds both generated headers, `config.log`, `config.status`, the top-level generated Makefile, compiler-selected feature maps/preprocessor output identities and the copied integer probe source, executable and successful output. After the geometry/error, PIC and runtime controls, the recipe refuses changed configuration/probe files before executing the integer control again and requires identical qualification results before writing `complete.json`. Local `int64-before-*` and `int64-after-*` logs retain both checks; the existing command log records their exact arguments. The offline tests execute synthetic header/width/signedness/staleness controls when a Linux C++ compiler is available. Those fixture controls do not qualify a GEOS build or its toolchain. Full SDK, Spatialite, renderer, JNI, other target architectures and live TAKX remain separate gates.

The first local attempt built and installed both real libraries, then stopped at a
probe assertion that incorrectly omitted the official runtime SVN suffix. The
corrected probe requires exactly `3.4.3-CAPI-1.8.3 r4285`, as defined by the locked
source. The original stopped evidence is retained separately; any successful
receipt must come from a new complete entrypoint run, not continued old outputs.
That fresh corrected run passed, including independent source/output verification
and replay of the geometry controls. It is historical evidence for the earlier
recipe, which did not qualify generated 64-bit features. Its recipe/proof hashes
remain unchanged; it is not upgraded by this source change. This revised recipe
adds the integer control to its recipe identity and requires a completely new
entrypoint run with the exact tools above. A fresh proof of the revised profile
has not yet been completed; source/fixture checks do not meet that gate. The
separate cache-profile observation used AMD64 emulation on ARM and different
Make/binutils versions, so it cannot supply that proof. No geometry corruption
or invalidation of the earlier geometry/PIC results has been demonstrated.
