# Checked feature-renderer JNI source profile

Version 4 stages seven exact C++ files and two interop headers from the frozen
Native07 Kernel composition. It adds checked callback boundaries, prevents
publication of a failed wrapper, rolls back failed listener registration, and
preserves the first Java exception while clearing a disposed native pointer.
Actual Kernel version is 8.30.5; the composed parent is not pristine upstream.

The exact parent receipt is
`54630be999ac532f4724e0c62a5197e42f16f3244e1063022d6121e09cd8990e`.
All nine pre/postimages, patch bytes and seven owned diagnostic sources must match
the closed lock. Source preparation grants no runtime or SDK acceptance.

## Use

Default verification starts no process:

```sh
python3 -B takx/kernel-feature-jni/prepare.py
python3 -B -m unittest discover -s takx/kernel-feature-jni -p test_prepare.py
```

Explicit preparation requires the frozen parent layout and receipt, and a fresh
output outside the source, repository and parent-receipt root:

```sh
python3 -B takx/kernel-feature-jni/prepare.py --phase prepare \
  --source /qualified/native07/layout \
  --parent-receipt /qualified/native07/experiment-complete.json \
  --output /fresh/feature-jni-source
```

The preparer verifies exact inputs, invokes `patch --fuzz=0`, rechecks every
postimage and input, and writes a source receipt. It does not compile or run the
owned diagnostics. All native-builder, callback, rendering, SDK and client
acceptance flags remain false.

## Changes

The six renderer/feature C++ boundaries check entry and post-callback exception
state. `InterfaceMarshalContext.h` holds a temporary peer until construction and
weak-reference creation succeed, preserving the primary exception during failed
peer cleanup. The direct `NativeFeatureDataStore4.wrap` entrypoint also refuses
publication after constructor failure.

The datastore records its listener before registration, allowing rollback when
registration has a side effect and then throws. Cleanup checks callback results
and retains the primary failure over a secondary removal failure. `Pointer.h`
saves a pending exception around the deleter and pointer-field clearing, so
ordinary disposal and disposal entered with a pending primary remain JNI-clean.

Version 4 also compares wrapper class identity with JNI `IsSameObject`, using a
scoped class reference and preserving a lookup exception before cache work.
Visibility-listener registration checks the Java callback result. These changes
cover the ordinary cached/listener path; asynchronous cleanup and throwing
visibility registration remain unqualified.

## Scoped evidence

The two-boundary causal candidate freshly compiles all 159 JNI units against
retained Native09 Core and its own Java classes. Review verifies exactly two
changes relative to Native09, fresh object hashes, identical export names/types
and identical required libraries. Its receipt is
`beb8c5efad5bd283c957fd99e33d95208702a7d815c7fd87ffadff89118d0ca1`.
The owned sequential cache/visibility control passes 16 assertions with zero
JNI warnings. The same test artifacts against original Native09 JNI produce
three warnings; both have identical test-class bytes. Positive receipt:
`506b6c2fddfe4740a71f490564b4583361f781d4df47a1bf4724603df0285934`.
Three constructor/disposal regression consumers also remain JNI-clean, with
primary identity and fresh recovery preserved. Their receipt is
`64a7d70f4ed47e12ff1379c05a35d91df1b1de23b48fdafea052e2cd381d911a`.
Instrumented and uninstrumented animator replays pass their scoped CPU/pixel
controls with zero JNI warnings. Their receipts are
`48909b40946d92025ecb8693d9d635db34d2910769d8f80810e4d4dcad752dc1`
and `9e457e963b84cbb9a84507f4aaff42393a4e68189d7a465ca8e66bc24164b683`.
These are causal consumers with retained Core, not complete fresh version-4
producer or full renderer/SDK/client acceptance. Earlier failures remain retained;
the pixel result alone does not establish their cause.

Native09 freshly compiled all 815 units (483 core, 159 JNI, 173 Cesium) with all
nine version-3 files. Independent review verifies object freshness, all source
hashes, recipe snapshots and 119 byte-identical JNI headers. Its receipt is
`942142207e50c6336c949fea35292a9d3ebf8ea633a7474f12e295557ca1360f`.
The earlier seven-file Native08 producer remains retained separately.

The controlled consumers use Native09's own 2701 Java classes and newly built
Core/JNI libraries. They pass with zero strict-JNI warnings, preserved primary
exception identity and fresh recovery:

- Real `NativeLayer.wrap` with a throwing `getName`.
- Real `NativeFeatureDataStore4.wrap` with side-effecting registration failure,
  secondary removal failure, ordinary disposal failure and disposal entered
  with a pending primary. Removal and pointer clearing are checked.
- Production GLLayer2 marshal with subject failure, nested layer-name failure
  and pending-exception entry. Failed output peers remain unpublished.

Every consumer verifies that only Native09 Core/JNI images are mapped. Twelve
runtime classes/probe ELFs were independently reproduced byte for byte from the
recorded compiler commands. The controlled-consumer receipt is
`8d4ece7b77b3312c52f914bd71f6e8e9221374615b924d050e67568212a3b531`.
This does not qualify every wrapper, allocator or concurrent cleanup path.

A further consumer matrix passes 166 elevation checks, 62 feature bindings and
typed options/envelope controls, 22 managed callback failure operations with
listener/child cleanup, and both surface construction orders with 100 concurrent
calls. Each uses Native09's own Java/Core/JNI, checks exclusive Core/JNI mapping,
and reports zero strict-JNI warnings. Its receipt is
`378832301e0a9c0f6dc74faf51e1303bc32532700ab539cda9776b4ebcba3e93`.
Sixteen test classes/probe libraries reproduce byte for byte from the recorded
compiler commands. These scoped controls do not qualify all elevation paths or
listener concurrency.

A separate Native09 elevation matrix passes 112 listener/bounds checks and 231
chunk checks with zero strict-JNI warnings and exclusive Native09 Core/JNI
mapping. Its receipt is
`be4bd428d5685a0d98d07ab6c40e788e57c40123e3751770dfed6c661f68656b`.
Nine test classes/probe libraries reproduce byte for byte. These controls do
not establish general asynchronous listener safety.

The cursor/bulk elevation fault matrix also passes 13 cases, including secondary
cleanup failures, with preserved primary exceptions, zero strict-JNI warnings
and exclusive Native09 Core/JNI mapping. Its receipt is
`de067384f9edbc631e84e4e9f1fd10842b7ba7aeefbda7b124359f0824af56f6`.

Six scalar elevation fault cases also pass with zero strict-JNI warnings and
exclusive Native09 Core/JNI mapping. Their receipt is
`c245309f3fc30d1cb2637c5dc6368fd9895cb052b12b231945aa22d6befcbe4d`.
The cursor/bulk and scalar test classes reproduce byte for byte.

The scoped headed lifecycle consumer also passes with Native09's own Java and
Core/JNI: three context recreation cycles with two live views, fresh identities,
retained text/shaders, old-worker retirement and three terminal renderer
collections. It reports zero strict-JNI warnings; review verifies exclusive
Native09 Core/JNI mapping, eight byte-reproducible test artifacts and owned
display/auth cleanup. Its receipt is
`c2ae9ad7544198be7aaf242c1f38bf8f20817390a79ad2778e5d143ec7909c0e`.
This uses isolated Xvfb/software GL and private font03; it does not establish
stock-JDK, hardware, full renderer or general concurrent cleanup acceptance.

Two separate animator-owned software pixel controls pass drawing, post-hide
checks and cleanup without JNI or uncaught runtime failures. These experiments
use the intermediate nine-file JNI candidate with retained Native07 core,
canonical Java and private font03; they are not Native09 renderer acceptance.
Their receipts are
`d18fb064aa03cc221899ca9a2552461ab735c9819b4d371a8db1d5e7d9aae4a6`
and `8a94bb25b5beef34a4e42424d1a2476563209ba9987e8ce9749aedfa2aa7daa9`.
The animator harness is still separate from the owned historical draw probe.

The first complete Native09 animator draw attempt stopped at an owned database
path collision (replay12); its failed evidence remains retained. With separate
CPU/draw paths (replay13), 597 CPU checks and visible contour pixels pass, but
post-hide pixels remain at 814 major and 268 minor. All bound input hashes and
exclusive Native09 Core/JNI mapping verify, with zero strict-JNI warnings.
This is a failed pixel acceptance result, not an established SDK root cause.
Its receipt is
`4e19f0e80c92bcbd4b98c727b38ab72c84c87aec618c533931d3d80ccbf4978a`.

An instrumented observer replay14 reports Java/native visibility false, three
listener events on each side and zero hidden pixels, but is rejected for three
strict-JNI warnings in native layer listener registration and repeated wrapping.
The additional observer changes lifetime/timing; it does not establish why the
uninstrumented replay13 kept pixels. Both failures remain retained.

## Remaining gates

Allocation/class-initialization failures, complete cache/peer cleanup,
asynchronous listener concurrency and retained-listener use after disposal need
qualification. The profile is not integrated into the portable native builder.

Actual contour rendering remains unaccepted. Replay06 reached visible/hidden
pixel markers but logged an uncaught AWT lock-wait exception; replay07 retained
post-hide pixels. Both were JNI-clean. The owned display probe now records
uncaught worker exceptions in its failure channel. The experiments use owned
Xvfb/software Mesa and the qualified private font03 runtime, and do not establish
stock-JDK, hardware, complete renderer/lifecycle or full SDK/client acceptance.
