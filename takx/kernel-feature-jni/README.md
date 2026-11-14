# Checked feature-renderer callback source profile

This opt-in source profile adds entry and post-callback JNI checks in six exact
files from the frozen full Native07 public Kernel composition. It targets the
native warning stacks observed when a real feature datastore/layer renders
project contours. It changes no generic JNI primitive helper, production header,
Java class overlay, JNI entry point or private SDK version alias.

The parent is the reviewed complete 815-unit composition, not a pristine upstream
checkout. Its receipt and all six source preimages must match the closed lock.
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
