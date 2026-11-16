# Checked feature-renderer JNI source profile

Version 11 stages eight exact C++ files and two interop headers from the frozen
Native07 Kernel composition. It adds checked callback boundaries, prevents
publication of a failed wrapper, rolls back failed datastore listener registration, and
preserves the first Java exception while clearing a disposed native pointer.
Actual Kernel version is 8.30.5; the composed parent is not pristine upstream.

The exact parent receipt is
`54630be999ac532f4724e0c62a5197e42f16f3244e1063022d6121e09cd8990e`.
All ten pre/postimages, patch bytes and seventeen owned diagnostic sources must match
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

## Reject disposed Java wrappers before inbound ownership access

Version 8 checks the real Java wrapper's native Pointer before attempting shared
ownership conversion. Disposal clears its raw/value fields while retaining the
Pointer type. Previously a disposed shared wrapper dereferenced a null shared
holder and crashed the JVM; a disposed unique wrapper returned success with an
empty native output. The guarded path returns invalid argument, keeps the caller's
previous output owner unchanged, checks JNI errors before subsequent operations,
and scopes the temporary Pointer reference. It retains the live raw address across
promotion and verifies the candidate before publication. Concurrent disposal is
still outside the sequential proof.

The unchanged Native13 baseline has four positive controls (live shared, live
unique, pending-exception identity and 1,024 conversions in one native call) and
two disposed-wrapper negatives. Core dumps are disabled. The first classification
attempt incorrectly expected the loop to warn; its unchanged successful log is
retained and the corrected review classifies it as a positive. No reproduced
local-reference leak is claimed. Adjudicated baseline receipt:
`131a14f4605087adbd9d7937b8a34fe392351b1273ad3c5ffc2a517f60dc07c9`.

The exact same compiled fixture class and probe pass all six cases on 159 fresh
JNI units with retained Native13 Core/providers/own Java, zero checked-JNI
diagnostics, and exclusive expected Core/JNI mappings. Candidate receipt:
`41bdd71178ff01f4d17b3a0f0db671623ff257b518e22367efd59ff64a84fc6c`;
control receipt:
`fac74b7daa8cfafcdb6c5efa4a7926019ab20378d868b7f41e51cbd91137b01d`.
Independent review verifies all source/object/input/fixture/log hashes and
preserved existing exports and required libraries; eight added exports are weak
template helpers.

Fresh Native14 compiles all 815 units (483 Core, 159 JNI, 173 Cesium), without
adopting candidate objects or libraries. Independent producer review checks all
object/source hashes and freshness, recipe snapshots, 3,861 bound tool files,
119 headers identical to Native07 and its own 2,701 Java classes identical to
Native13. The only changed source from Native13 is InterfaceMarshalContext.h.
Producer receipt:
`e09683c9e43e300e35845fb057b19f992d8595485d5da2435e5c9dab462db290`.

All ten scoped runtime cohorts pass, including the six new inbound cases, seven
outbound cases, prior feature/listener/registration/removal and elevation controls,
both surface call orders and the owned Xvfb/software-GL/private-font lifecycle.
All 71 runtime artifacts reproduce byte-for-byte. Final review rechecks 11,226
unique input hashes and display/auth cleanup. Pipeline receipt:
`6e156afd868e868f812c6334b245960a49c33fe1ceac0e4cba130683e1169288`.

| Native14 control cohort | Receipt SHA-256 |
| --- | --- |
| Inbound wrapper lifetime | `bd384aa12544120b220b24503e745160a4a816ee014a5184b2effb828748ccbb` |
| Outbound construction/cache/unique callbacks | `f6bc9dc05a404a5a646ebf9ca154cf829f3c21a80dc74be55834b57a96131369` |
| Feature constructor/disposal/cache/listeners | `6f9bce1557cd4f77aaf437315ded0761d23ea040e7a8b9488530f87bf315f065` |
| Visibility registration rollback | `d493f262844e2b04babce0b3fd6d8d4e11c4cadba8abccbd38cc2f6e01e7bd50` |
| Unknown/repeated listener removal | `189ed1f852dfa4286e4339524d404cfb5f9b3e910d7757290ccb3c360e3ef56f` |
| Surface and consumer matrix | `09d2d3435718f729804ce726abbd064820f75b2045f1e544d8f6fe489d9d0b44` |
| Listener/bounds and direct chunks | `827493761a09b4cc95fb69110a17cae24509e7221083fe4f9231a4e9e63653de` |
| Elevation failure cleanup | `b7d19edda881c897b6040dc94b9af579b8de0a9fbfcbf87aebc07d5a2d58a513` |
| Scalar return/publication | `b46b85de05758469dc2df8394def8fdb7fc432bd14842c90afe2ff3fde61dc88` |
| Scoped headed lifecycle | `13393f05351b572e26a982d32b35b051a493895d3f8a16c4c6a20a1e29d1d14c` |

This context-level guard does not qualify every direct Pointer helper, allocation
failure or concurrent mutation of pointer fields. Full renderer, portable builder,
SDK and client gates remain open. Previous Native13 evidence below remains bound
to its version-7 inputs.

## Outbound wrapper construction and live cache entries

Version 7 checks native-to-Java construction before publishing the result or a
weak cache entry. It allocates the Java object separately and keeps a strong local
reference while invoking its real constructor and, on failure, clearing its native
Pointer. Cleanup runs after releasing the context mutex and preserves the original
Java exception over a secondary cleanup exception. Cache lookups verify that the
wrapper still has the expected nonzero native pointer. Successful uniquely owned
wrappers also enter the weak cache, allowing real visibility callbacks and
const-reference lookup to recover their Java peer.

An owned class loader fails one dependency while the unchanged real SDK
NativePeerManager initializes. The Native12 baseline passes the shared-wrapper
positive case and rejects six cases covering shared/unique initialization failure,
secondary cleanup failure, recycled native addresses, disposed const-reference
lookup and missing uniquely owned visibility callbacks. Baseline receipt:
`facd68f01d665295aa3372e44a63a814eac57250bc2fc4356ddf06ff57e788bb`.
The candidate compiles all 159 JNI units against retained Native12 Core/providers
and its own Java classes. The exact same three fixture classes and native probe
pass all seven cases with zero checked-JNI diagnostics and exclusive expected
Core/JNI mappings. Candidate receipt:
`5c99e0293eb382db496855c2876e7312de3e4a57cfed986c277c3e29a4e971e7`;
control receipt:
`ba060a1a84fea58024a1b12d63bd6423b84daaea631770781c3c0db382733950`.
Independent review rechecks all input, source, object, fixture and log digests.
Every JNI entry point and required-library inventory is preserved. Eleven weak
context template exports disappear and 27 template/helper exports appear; the
entire C++ export inventory is therefore not identical.

Fresh Native13 then compiles the complete 815-unit composition (483 Core, 159
JNI, 173 Cesium), with no adopted candidate objects or libraries. Producer review
verifies all object/source hashes and freshness, 3,861 bound tool files and recipe
snapshots. Its own 2,701 Java classes match Native12 byte-for-byte, and all 119
JNI headers match Native07. The sole source change from Native12 is
InterfaceMarshalContext.h. Producer receipt:
`713519a0e5fe7ee1e9c7877ef5ba969d07e8e4555397acde5b7cc3405f6b7800`.

All nine scoped runtime cohorts pass, including the seven new outbound controls,
prior constructor/disposal/cache/listener and registration/removal cases, elevation
fault/scalar/bulk/cursor cases, both surface call orders and the owned
Xvfb/software-GL/private-font headed lifecycle. Independent recompilation
reproduces all 69 runtime class/probe artifacts byte-for-byte. Final review
rechecks 11,218 unique input hashes and confirms display/auth cleanup. Pipeline
receipt:
`57fd751940417e7483e570930c7622673fa1eb83b6a5fa784bcc8b16a9cf7c43`.

| Native13 control cohort | Receipt SHA-256 |
| --- | --- |
| Outbound construction/cache/unique callbacks | `26581d965e4941ccd3c4fb0f93c655cfc945dc66684c32066e84fa575b9017d0` |
| Feature constructors/disposal/cache/listeners | `ae611f5b655d362af00f03e9b3d14b4c42f91aef3bb477e0e2a221293e86430e` |
| Visibility registration rollback | `5b8635eba9030a813c5e170b0c6f6994a01fe368a3928ba6a123e87a97db60b7` |
| Unknown/repeated listener removal | `edb5e1b297407e3a3f5ab9e809f8b08659be0c6c5d250bff6225719e2429e12b` |
| Surface and consumer matrix | `28ced5cacec3f78bde5574c3b1468d88409da6291b7e2ad0daf4e1b53b1e0b02` |
| Listener/bounds and direct chunks | `f5a25f1ef6520f550dd6fc2de222c9ad6d506404d8487217d89c2ac9647b220f` |
| Elevation failure cleanup | `74990e73d00f0b1b2f3d6a2e63320896a126324f964fc48ebf2a66dd9beff845` |
| Scalar return/publication | `27e8ff11adc8046f492c69b55663d951d6eba22d4e22fc2ab606d935033ba99d` |
| Scoped headed lifecycle | `4125f623572233f4e464c9cd7644a0d8403fd9a0d261d08e16819298bdb5c83f` |

Pointer allocation failure, C++ allocation failure, arbitrary constructor reentry
and concurrent disposal/cache lookup remain outside these sequential controls.
Complete cache/peer lifetime, portable builder, hardware/stock-JDK rendering,
full renderer, SDK and client acceptance remain open. Existing Native12 evidence
below remains bound to its version-6 inputs.

## Null listener removal and fresh version-6 Native12

The real NativeLayer Java wrapper can pass a null Pointer when removing an
unregistered listener, removing a listener twice, or removing it after failed
registration. Native11 aborts under checked JNI in all three cases while reading
a field from that null object. The owned negative experiment disables core dumps
and retains its failed logs. Baseline receipt:
`ebc452d41ab21aba3256d5ef386632e45fb2b9e8b3e9411b6e2c7598d7aaab5f`.

Version 6 returns immediately for a null listener Pointer before any JNI field
read. The same fixture classes pass all three cases (26 checks) on a candidate
with 159 fresh JNI units and retained Native11 Core/providers/own Java, with zero
strict JNI diagnostics and exclusive expected mappings. Candidate receipt:
`267e1bfbe384274e37e066013e2935ca95ea7e3aee7ff0bb255328e163ec1d88`;
control receipt:
`2221ddb1920dd4a432ce84c06e7d0b29570cb0aff4081bbfa72ada5897763bc0`.
Independent review verifies every source/object/input/class/log digest and
identical exported-symbol/required-library inventories. The only changed source
versus Native11 is jnativelayer.cpp.

Fresh Native12 then compiles all 815 units (483 Core, 159 JNI, 173 Cesium) from
the complete version-6 source composition and passes producer CPU/database/logger
controls. Its own 2,701 Java classes match Native11 byte-for-byte; all 119 JNI
headers match the frozen parent. Independent review verifies object freshness,
source/recipe snapshots and 3,861 bound tool files. Producer receipt:
`7be80650bf6afcafcd1cdf9d6657b0460de606e2051fff34f8d9cd34ddb5b85b`.

Both the new removal cases and the prior registration-failure cases pass on
Native12's own Java/Core/JNI. All existing scoped CPU/callback/cache/elevation/
surface and headed lifecycle controls also pass with zero strict JNI diagnostics.
Independent recompilation reproduces all 64 runtime class/probe artifacts byte
for byte; final review rechecks 11,207 unique input hashes. The headed control
uses owned Xvfb/software GL/private font03 and verifies display/auth cleanup.

| Native12 control cohort | Receipt SHA-256 |
| --- | --- |
| Constructor/disposal/cache/listener | `47873ba2616c5d91342226d77d3c13342337b50dfab883bfeb3ba15ec0afdadc` |
| Registration failure/recovery, 3 cases/27 checks | `a009a0b636acaea525eac3200f284e12cd768f7d3bcc68855504c7683d5daa6c` |
| Unknown/repeated removal, 3 cases/26 checks | `6c6ecf1002fa99a7b800c874731f165123716b9c26f14df6ca5aeb19006b2f84` |
| Elevation/feature/callback/surface matrix | `d89a0da3e271c9174996d3a9f26b4a03efbcc5a391018d28b3ef143abddffdf6` |
| Listener/bounds and direct chunk matrix | `642b748dfc0e3b9fde42ca74222e4737b1d62a203fb542c5f095b41d414ce765` |
| Bulk fault matrix | `ad195f7cd9a0ee0c1ebcb24cbf57eaec7edbd28042659c1a11e474d3be1afdd9` |
| Scalar fault matrix | `456b263a173ec71da80ed4fbef6f0868afa2d5a39eb30d32809bc6cd965587ef` |
| Scoped headed lifecycle | `e955072eeac7680964d1b6a841d47eb045246512b721b970ba5b7b45f2ce832c` |

Qualification pipeline receipt:
`ce0cfa36537d5465ed1a5518ceff33bb5718489e90e4d1614a9b419d819f0b3e`.
This establishes the listed sequential null/removal and failure/recovery behavior.
Allocation/class initialization, pending-entry cleanup beyond the listed cases,
uncooperative Java removal, asynchronous retained listeners, complete cache/peer
cleanup, portable builder, contour pixels, stock-JDK/hardware rendering and full
SDK/client acceptance remain open. All broad acceptance flags stay false.
Native11 and earlier experiments retain their original source/recipe scope.

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

## Fresh version-5 Native11 qualification

Native11 freshly compiles all 815 units (483 Core, 159 JNI, 173 Cesium) with the
complete version-5 composition and passes the generic CPU/database/logger JNI
controls. Independent review verifies object freshness and hashes, every source
and recipe snapshot, 3,861 bound tool files, 119 unchanged JNI headers and its own
2,701 Java classes. The Java classes match Native10 byte-for-byte; the sole native
source change relative to Native10 is `jnativelayer.cpp`. No causal objects or
libraries are adopted. Producer receipt:
`831ef28172da6a5d3b00bcc6723bc9f9148f1da625c438c7d6399c433f9dddb7`.

The same three registration-failure cases pass against Native11's own Core/JNI
and Java: 27 checks, zero strict JNI diagnostics, expected exclusive mappings,
primary exception identity, cleanup attempts and fresh registration recovery.
Existing constructor/disposal, cache/listener, feature lookup, managed callback,
elevation/cursor/bulk/scalar and both surface-order controls also pass. The scoped
headed lifecycle passes three recreation cycles, two live views, worker retirement
and terminal renderer collections on owned Xvfb/software GL/private font03.
Independent compilation reproduces all 61 runtime class/probe artifacts byte for
byte and verifies removal of the owned display and auth file.

| Control cohort | Receipt SHA-256 |
| --- | --- |
| Constructor/disposal/cache/listener | `e18d71c98185af03bc3556dc9de3c036366c5cbb21a09114c358c6e239c853ed` |
| Registration failure/recovery, 3 cases/27 checks | `614d64e34924cad1512e31dae6763613e8bd0d9c7e11435923c54f2c26de9aad` |
| Elevation/feature/callback/surface matrix | `da66707c1d5291aaba2d5818ebc99a819f3ed3f921bc77338530813472cf249a` |
| Listener/bounds and direct chunk matrix | `c69f2011a5ca78681bf59cd1370e8a7ea78fc74063c8f577e53705ea529d501a` |
| Bulk fault matrix | `76924b119a304d75540c37fc93e127f656367cc40443a69a722a70045dfe95ed` |
| Scalar fault matrix | `99777743190e6eadcd2e8b1030f95de09d6ddae5b65fd2008752b37dda76af85` |
| Scoped headed lifecycle | `1f183a5476bcd96dc4393eb25b50d7b112a3e50e7a0480f3365e68262be11b6a` |

The retained constructor/cache harness prints a historical Native10 mapping label;
its wrapper checks the passed library paths. Independent review explicitly verifies
those arguments and mappings bind Native11 before accepting that log marker.
An initial registration replay stopped before runtime on a harness baseline-path
error; its incomplete evidence remains retained separately. The corrected replay
and all subsequent artifact reviews complete successfully. Pipeline receipt:
`0a493d9f41eb275b6f035cc5c5562dbf005ec449330cb1aaf41ae1ec158dcae3`.

These are scoped local results. Allocation/class initialization, uncooperative
Java removal before its own cleanup, asynchronous retained listeners, complete
cache/peer cleanup, portable builder, contour pixels, hardware/stock-JDK rendering
and full SDK/client acceptance remain open. All broad acceptance flags stay false.
Earlier Native10 proofs and pixel failures retain their original source scope.

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

## Direct pointer ownership checks

Version 11 checks direct raw/shared pointer access, promotion and shared-holder
inspection before touching Java fields or native ownership. Null objects,
disposed holders and pending Java exceptions are rejected without replacing an
existing output owner. Failed allocation during unique-to-shared promotion
leaves Java ownership intact so a later retry succeeds. A valid empty shared
holder remains supported; a disposed holder has no holder address.

An independently recompiled before/after probe reproduces fourteen baseline
failures and passes all eighteen candidate cases with zero strict-JNI diagnostics.
The receipt SHA256 is
`aa485e5e42cfd089836fdfb4f1f8fe4f7b9adc867ff370577babba64094bbfb7`.
A separate real `NewPointer(empty shared_ptr)` probe checks get, shared inspection
and promotion against both headers. It preserves the upstream result and output
ownership. These direct-header controls use unchanged Native14 provider libraries;
they do not replace a complete candidate native rebuild or concurrency testing.

The rejected version-9 candidate refused valid empty shared holders. Version 10
restored those holders but returned the wrong status for disposed pointers.
Both failed candidates and partial native rebuilds are retained; neither is
accepted.

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
