# Checked feature-renderer JNI source profile

Version 5 stages eight exact C++ files and two interop headers from the frozen
Native07 Kernel composition. It adds checked callback boundaries, prevents
publication of a failed wrapper, rolls back failed datastore listener registration, and
preserves the first Java exception while clearing a disposed native pointer.
Actual Kernel version is 8.30.5; the composed parent is not pristine upstream.

The exact parent receipt is
`54630be999ac532f4724e0c62a5197e42f16f3244e1063022d6121e09cd8990e`.
All ten pre/postimages, patch bytes and eight owned diagnostic sources must match
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
Visibility-listener registration checks the Java callback result. Those version-4 changes cover the ordinary cached/listener path.
Version 5 additionally checks the native visibility-registration entrypoint after
Java registration. On a Java failure it attempts listener removal with the pending
exception cleared, then restores the exact original throwable even if removal
throws. It returns without publishing a native Pointer. A Java removal callback
that throws before removing its own listener is still outside the proven scope;
this is a cleanup attempt, not a guarantee about arbitrary Java implementations.

## Visibility registration failure controls

The identical owned fixture rejects Native10 in three isolated JVMs: registration
throws before adding a listener, throws after adding it, or throws after adding
and then encounters a secondary removal error. Each baseline fails because no
rollback removal is attempted. Baseline receipt:
`5da6bb7e0445c224e0feaf73cb8d039f69e87ff56a8f1b403a8093be518cc009`.

A candidate with 159 freshly compiled JNI units and retained Native10 Core,
providers and producer-own Java passes all three cases (27 checks), with zero
strict JNI diagnostics and exclusive expected Core/JNI mappings. The tests check
original exception identity, attempted removal, no retained listener or published
peer in these cases, fresh registration/callback/removal, and pointer retirement.
The fixture class bytes match the rejected baseline exactly. Candidate receipt:
`e9a80714499cc3a98c27c78f238afe1f4cd7f47f92a1622afbbcfe05961b70ed`;
controls receipt:
`08684f081d8142917095b84d7f4eb624173a9e25bbf8fe5b2a673551b47116fd`.
Independent review verifies all input/source/object/class/log hashes, exactly one
changed source relative to Native10 (`jnativelayer.cpp`), and identical exported
symbol and required-library inventories. This candidate is not a fresh complete
native graph; earlier Native10 proofs continue to describe version 4 only.

## Scoped evidence

The preceding version-4 Native10 producer freshly compiles all 815 units (483 core,
159 JNI, 173 Cesium). Independent review verifies every object's freshness and
hash, all source and recipe snapshots, 48 graph recipe files, and 119 unchanged
JNI headers. Its own 2701 Java classes are byte-identical to Native09. No causal
objects or libraries were adopted.

Native10 consumers pass constructor/disposal faults and 16 cache/listener
assertions; 166 elevation checks; 62 feature bindings and typed options/envelope
controls; 22 managed callback fault operations; both surface construction orders
and 100 concurrent calls; 112 elevation listener/bounds and 231 chunk checks;
13 cursor/bulk fault cases (97 assertions); and six scalar fault cases (34
assertions). Every consumer checks exclusive Native10 Core/JNI mapping and has
zero strict-JNI warnings.

The scoped headed lifecycle consumer also passes three context recreation
cycles with two live views, old-worker retirement and three terminal renderer
collections. Its isolated Xvfb/software GL/private font03 scope remains explicit.
Across these consumers and lifecycle controls, 58 test classes/probe libraries
reproduce byte for byte from recorded compiler commands.

| Current receipt | SHA-256 |
| --- | --- |
| Fresh Native10 producer | `986054e2e8c412b57ccc1d2d7bfb64302db55fccc7939dae768ab1b59812d7c4` |
| Constructor/disposal/cache | `f0734b72f7d4b128a05f83b4cae31312600e5420b8522a5abb3ad9c6ede28b8a` |
| Elevation/feature/callback/surface | `5f1a28a799a4cc08a6a2d9cd1c88693856b1847eace1565aa67bb1e249c20249` |
| Elevation listener/chunk | `f3d15c1cf57a4c9e5fa39f0d9b72b669f03a2e9f6866ca80e3a548b2914cca12` |
| Cursor/bulk faults | `6f8c9ed8ebeff1007ce1866c363def82afc493d71b9af82a258d0a5cbdc8d9f7` |
| Scalar faults | `1c2cd2563782b749a352189b5ecdd28b24de0212c27d94229327ca1a6a15cb42` |
| Scoped headed lifecycle | `178bd6ba84352156ddaaaa715c790c00ef2894041e937398be1c7439b8345c9a` |

Native10 contour replay17 passes 597 CPU controls and draws visible pixels, but
fails the post-hide check with 416 major/231 minor pixels remaining. Bound input
hashes and exclusive Native10 Core/JNI mapping verify; JNI warnings remain zero.
Its failed receipt is
`4d7fd2622fa1c2efa47f5c2e0965892bf303bf5f02e3503001c9d8e0d05f2e11`.
This remains a failed pixel result, not an established SDK root cause.
A two-phase GL-state diagnostic (replay19) records zero colored pixels after
clearing every frame, expected read/draw bindings, then colored pixels after
rendering on 121 hidden-layer frames. It remains rejected; it narrows that run
beyond a stale framebuffer read without establishing the SDK cause.

Earlier scoped receipts remain below to preserve the causal and failed evidence.

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
