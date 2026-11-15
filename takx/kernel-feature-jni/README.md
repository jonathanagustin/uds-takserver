# Checked feature-renderer JNI source profile

Version 3 stages seven exact C++ files and two interop headers from the frozen
Native07 Kernel composition. It adds checked callback boundaries, prevents
publication of a failed wrapper, rolls back failed listener registration, and
preserves the first Java exception while clearing a disposed native pointer.
Actual Kernel version is 8.30.5; the composed parent is not pristine upstream.

The exact parent receipt is
`54630be999ac532f4724e0c62a5197e42f16f3244e1063022d6121e09cd8990e`.
All nine pre/postimages, patch bytes and six owned diagnostic sources must match
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

## Scoped evidence

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

Two separate animator-owned software pixel controls pass drawing, post-hide
checks and cleanup without JNI or uncaught runtime failures. These experiments
use the intermediate nine-file JNI candidate with retained Native07 core,
canonical Java and private font03; they are not Native09 renderer acceptance.
Their receipts are
`d18fb064aa03cc221899ca9a2552461ab735c9819b4d371a8db1d5e7d9aae4a6`
and `8a94bb25b5beef34a4e42424d1a2476563209ba9987e8ce9749aedfa2aa7daa9`.
The animator harness is still separate from the owned historical draw probe.

## Remaining gates

The remaining typed/elevation/surface/lifecycle consumers need qualification
against Native09. Allocation/class-initialization failures, complete cache/peer cleanup,
asynchronous listener concurrency and retained-listener use after disposal need
qualification. The profile is not integrated into the portable native builder.

Actual contour rendering remains unaccepted. Replay06 reached visible/hidden
pixel markers but logged an uncaught AWT lock-wait exception; replay07 retained
post-hide pixels. Both were JNI-clean. The owned display probe now records
uncaught worker exceptions in its failure channel. The experiments use owned
Xvfb/software Mesa and the qualified private font03 runtime, and do not establish
stock-JDK, hardware, complete renderer/lifecycle or full SDK/client acceptance.
