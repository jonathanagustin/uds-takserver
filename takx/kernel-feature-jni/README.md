# Checked feature-renderer callback source profile

Version 2 stages entry/post-callback JNI checks in six exact C++ files and
constructor-publication guards in the shared `InterfaceMarshalContext.h` from
the frozen full Native07 Kernel composition. It targets the observed renderer
warning stacks and the reproduced layer constructor-fault path.

The parent is the reviewed complete 815-unit composition, not a pristine upstream
checkout. Its receipt and all seven source preimages must match the closed lock.
Actual Kernel version remains 8.30.5. This profile prepares sources only; it does
not build a native library, start a JVM/display, download anything or grant SDK,
client, checked-callback or rendered-layer acceptance.

Default verification starts no process:

```sh
python3 -B takx/kernel-feature-jni/prepare.py
```

Explicit preparation requires the actual frozen parent and a fresh output root:

```sh
python3 -B takx/kernel-feature-jni/prepare.py --phase prepare \
  --source /path/to/context-fresh-native-07/layout \
  --parent-receipt /path/to/context-fresh-native-07/experiment-complete.json \
  --output /path/to/fresh-source-output
```

Preparation refuses unknown parent/source bytes, symlinks and overlap with the
source, repository or frozen parent root. It copies only the six closed sources,
applies the pinned patch with zero fuzz, verifies complete postimages, rechecks
parent/recipe/tool identity and writes a source receipt. Five offline guards are
wired into CI. The owned Java diagnostic depends on the actual context helpers
from #499, contour components from #589 and the qualified local display/font
runtime; it is locked for review rather than run by this preparer.

## Causal evidence and unresolved gates

Retained-object relinking reproduces the original Native07 JNI library byte for
byte before any replacement. Recompiling these six sources and relinking the
same retained objects removes the JNI warnings in the actual owned-display
contour replay. This is a six-source causal experiment, not a complete fresh
native build. Its native candidate receipt is
`65ac9951ac4b0ac502672c2dbea5bd7b70478667a21b094b6b0b4f3771da1255`;
the replay receipt is
`672c97661741942cc13ea71432455864ad7897f04bc5a0bb2c9f358cacfb3c23`.

The replay still fails the post-hide pixel control, despite zero JNI warning
lines. No rendering acceptance is inferred. Constructor/callback failure,
partial-object cleanup, exception identity and recovery remain unqualified and
need dedicated fault controls before broader adoption. A complete fresh native
producer and all required consumers must then pass. The earlier compilation
failure, original warning traces, unmodified full native parent, previous failed
rendering experiments and causal objects remain retained; none is relabeled as a
full SDK/client pass. All broad acceptance flags stay false.

Source review also identifies a concrete constructor-failure gate in the frozen
parent's `interop/InterfaceMarshalContext.h` (SHA-256
`c8c17f62f18bf771794b114f3826e22f612354121f6eff3340f90dc1992ed35c`).
Its shared-pointer marshal path assigns `new WrapperType(env, obj)` to the output
and immediately calls `NewWeakGlobalRef` without checking for a constructor's
pending Java exception (lines 153–156). The unique-pointer path also assigns the
new wrapper and returns success without a post-constructor exception check
(lines 225–231). The earlier version 1 six-source patch left that shared header unchanged.

This is a source-level hazard, not a reproduced fault result. The next control
must throw an identity-tagged Java exception from `Layer.getName` through the
real marshal path, check that the primary exception survives, require no
published partial peer or cache reference, and then prove fresh successful
marshal/recovery under strict JNI. Equivalent listener-registration and GL
subject failures need controls before expanding the patch. Positive rendering
warning removal does not qualify these failure paths.

The first owned constructor-fault replay now reproduces two strict-JNI
exception-pending warnings from the real `NativeLayer.wrap` entrypoint when
`Layer.getName` throws. Primary Java exception identity survives and a fresh
healthy wrapper completes afterward, but partial-peer/cache cleanup is not
qualified. The retained diagnostic receipt is
`8ed7e088ea4d2483719c42e0b589490447f3058a32d312f1d5c7694ed5b644bb`.
`tests/LayerConstructorFault.java` is owned and source-locked for this negative
control; default verification and source preparation do not execute it. The
six-source causal candidate remains unchanged and is not strict-fault accepted.

## Version 2 constructor publication candidate

The current preparer stages the six C++ changes plus an exact shared-header
change in `InterfaceMarshalContext.h`. Shared and unique marshal paths construct
a temporary peer, check for a pending constructor exception before publication,
and discard failed peers with the primary exception saved and restored around
cleanup. Shared cache insertion also waits for successful weak-reference
creation. Entry with an existing pending exception refuses without JNI work.

Because the header affects every wrapper instantiation, candidate03 recompiles
all 159 JNI translation units; it retains the Native07 core and other providers.
Its receipt is
`f9abca4a9d017efe1f9c5b5c938f070cb78642c857b0d5fb111f51992a2da226`.
The identical real `NativeLayer.wrap` constructor-fault fixture now records zero
strict-JNI diagnostics, preserved primary exception identity and fresh recovery.
Its receipt is `c943992d12938c96625daead33178909449d6f597bdf47e348efb3dfdf16a428`.

This qualifies only that narrow failure/recovery control. Partial-peer/cache
cleanup, listener-registration failures, GL subject failures, all other wrapper
instantiations, the actual renderer replay and a complete fresh native producer
still need qualification. No broader acceptance flag changes. The earlier
six-source warning-bearing constructor replay remains retained unchanged.

Renderer replay06 reached both visible colors and zero post-hide pixels, but
independent log review found an uncaught AWT lock-wait exception. Replay07
failed post-hide again (major 814/minor 268). Both have zero strict-JNI warnings,
but neither establishes clean renderer acceptance. The owned probe now records
uncaught thread failures in its shared failure channel. The source-only
preflight failure in replay05 and all runtime failures remain retained.
