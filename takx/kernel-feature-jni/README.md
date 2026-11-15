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

Native08 freshly compiled all 815 units (483 core, 159 JNI, 173 Cesium), retained
119 byte-identical JNI headers, and passed its generic CPU checks. Its receipt is
`4dd4b843249c921de25491de16f4f90befc3ffeaae99ff9a6aebbdb884ac09e0`.
That producer covers the earlier seven-file version 2, which still fails the
later listener/disposal controls. It is not adopted as a producer for version 3.

The nine-file candidate freshly recompiles all 159 JNI units while retaining the
Native07 core/providers. Its receipt is
`372d0cbe743230e91ec5d8bf1702e0106e665c820e5d3bcf94be0b039c3254bc`.
The following owned controls pass with zero strict-JNI warnings, preserved
exception identity and fresh recovery:

- Real `NativeLayer.wrap` with a throwing `getName`, receipt
  `06a50057d6924d05fc89a0b619c03c7eb04113570efd9caca18a19e72cb076a8`.
- Real `NativeFeatureDataStore4.wrap`, side-effecting registration failure,
  secondary removal failure, ordinary disposal failure, and disposal with a
  pending primary. Pointer clearing and removal are checked; only the candidate
  JNI library is mapped. Receipt:
  `bc1f1240d1f1f805a715bd981e3db0fdd9d493263ed8a52aa63edc036b16bc5a`.
- Production GLLayer2 marshal with subject failure, nested layer-name failure and
  pending-exception entry. Failed output peers remain unpublished. Receipt:
  `91f70f78760aeae64bac2c286acfe24c27589df949c9ab2b9c2617e611c8713d`.

Earlier warning-bearing and failed controls remain retained. The GL subject
control is a CPU constructor test and does not establish rendered behavior.

## Remaining gates

A complete fresh producer for all nine version-3 files and required consumers
must pass. Allocation/class-initialization failures, complete cache/peer cleanup,
asynchronous listener concurrency and retained-listener use after disposal need
qualification. The profile is not integrated into the portable native builder.

Actual contour rendering remains unaccepted. Replay06 reached visible/hidden
pixel markers but logged an uncaught AWT lock-wait exception; replay07 retained
post-hide pixels. Both were JNI-clean. The owned display probe now records
uncaught worker exceptions in its failure channel. The experiments use owned
Xvfb/software Mesa and the qualified private font03 runtime, and do not establish
stock-JDK, hardware, complete renderer/lifecycle or full SDK/client acceptance.
