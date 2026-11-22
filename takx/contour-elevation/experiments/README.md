# Owned software contour drawing controls

`ContourDrawProbe.java` is a source-locked diagnostic, excluded from default
compilation/execution. It uses the actual context admission helpers from PR #499,
complete Native10 Kernel 8.30.5 Core/JNI and its own Java classes (PR #595), JOGL,
an owned Xvfb display and the separately qualified private font03 runtime. It is
not a portable native builder or stock-JDK/hardware/full SDK/client acceptance.

The probe samples the real public elevation registry, clips project contours,
builds native styles/features in an owned persistent FeatureLayer3, and registers
the actual batch renderer SPI with GLMapView. No fixture draws the colored lines.
An owned FPS animator performs drawing and GL-current teardown; visibility
changes run on the AWT event thread. Immutable volatile frame snapshots prevent
stale or partially published pixel counts. Uncaught worker failures are captured
and printed. Readback checks the actual read framebuffer binding.

The contour owner binds renderer visibility to real surface-cache invalidation.
The probe requires an empty hidden baseline, both green major and magenta minor
pixels when visible, and zero target-color pixels after hiding, repeated for
three cycles. It closes the visibility binding, removes the layer and stops,
releases and disposes the view on its current GL context before owner disposal.
The 20-pixel color thresholds, 30/120-frame budgets, stall cap and strict-JNI gate
remain unchanged.

## Current scoped evidence

The version-2 CPU/retirement suite passes 615 assertions with zero JNI warnings.
The binding also passes single- and three-cycle animator/software replays with
complete Native10 Core/JNI and its own Java classes. Receipts are listed in the
parent README. This qualifies only the owned software/private-font experiment;
all broad rendering, SDK, client and public TAKX contour API flags remain false.

A barrier-only hide replay still retains pixels, while the matched replay with
`SurfaceRendererControl.markDirty()` clears them. A two-phase diagnostic records
zero pixels after each framebuffer clear, then colored pixels after rendering
on 121 hidden-layer frames. Read/draw bindings and write masks are correct. The
owned binding invalidates after all visibility listeners complete; it does not
replace a Kernel class or claim a global SDK root cause.

## Retained failures

Original manual-display replays 01/02 retain pixels after hiding and report
unchecked JNI calls. Replay03's bounded native write stacks identify observed
prefixes in ManagedLayer, ManagedGLLayerSpi2, ManagedGLLayer2,
ManagedFeatureDataStore2, feature Interop and ManagedFeatureDefinition; missing
frames are not inferred. That traced run also fails visible colors. Earlier
sources, logs, libraries, synthetic databases and screenshots remain frozen.
The historical published probe remains available at commit `5a7c620`.

Corrected animator and checked-JNI experiments retain their earlier partial
passes, AWT lock failures and failed hide results. Complete Native10 replay17
still fails post-hide pixels without the binding, despite zero JNI warnings.
An isolated one-bit `volatile` visibility-field hypothesis also fails and was
not adopted. None of these failures is overwritten or relabeled as acceptance.
