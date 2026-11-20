# Observable project contour styling

This is a working Java style model for the contour UI contract visible in the
pinned official TAKX 5.8 source. `ContourFunction` carries elevation in meters
and the major/minor flag. `ContourStyle` stores and publishes real color,
stroke/label-size and label-text callbacks, unit selection, major/minor
visibility, outlines and label visibility. Changes notify registered listeners.
There are no placeholder generation, feature-store or layer implementations.

The artifact is named `contour-style-project.jar`. It implements these two
project classes in `gov.tak.api.contour`; it does not identify itself as a
private Kernel SDK version. Complete client compilation and native styling are
still open. This recipe does not change an SDK or the client compiler classpath.

## Declared behavior

Default values follow the `ContourListPanel` style construction: opaque white
major and off-white minor lines, widths 2/1, both line classes enabled, size-20
labels enabled in meters. Outlines default to false, a project choice. Callback
objects retain their identity and receive the caller's elevation and major flag.
Custom callbacks are caller-owned: their return values, exceptions and thread
safety are preserved. The model does not silently evaluate or replace them.

The default text mapper converts meters to the actual public Kernel `Span`
units, rounds to an integer with `Locale.ROOT` formatting and appends the
authentic unit abbreviation. Kilometer, meter, mile, yard, foot and nautical
mile conversions are implemented. Non-finite elevations are refused. This is
an explicit project formatting policy; the caller does not reveal a private
SDK's rounding or label-placement behavior. A custom mapper can replace it.
Constructor widths must be finite in `(0, 64]`, and label sizes in `(0, 256]`.

Each successful setter commits its state, then synchronously dispatches one
property notification to an insertion-order listener snapshot outside the
model monitor. Reassigning an existing value still notifies. Registration is
identity-based, duplicate registrations are ignored, and each model permits
256 listeners. Removal affects subsequent snapshots. All snapshot listeners
are attempted; the first thrown unchecked exception/error is rethrown after
dispatch and later failures are suppressed on it. State remains committed.
Property numbers are project-assigned; only the symbolic names observed in the
caller are a compatibility input.

Individual reads, changes and copies publish their state under the monitor.
Concurrent setters may dispatch concurrently; this is not an EDT dispatcher
or an atomic multi-setter transaction. Listener code must protect its own
state. `copy()` snapshots callbacks and values and starts with an independent
listener registry. A callback shared by two copies remains shared.

## Pinned caller proof

Official TAKX revision:
[`af54a3e1fe8796b6e109e2e5925dbef524ea7e31`](https://github.com/TAK-Product-Center/TAKX/tree/af54a3e1fe8796b6e109e2e5925dbef524ea7e31).
Public ATAK/Kernel revision:
[`9f6893dd657feacc35ec5de03dad721c2e44170e`](https://github.com/TAK-Product-Center/atak-civ/tree/9f6893dd657feacc35ec5de03dad721c2e44170e).
Exact bytes and license files are bound by `inputs.lock.json`.

The explicit recipe compiles the genuine `Span.java` and `NonNull.java` from
that Kernel source, and uses the genuine Maven Jakarta annotation API 2.1.1
artifact. It compiles the project library into an inventory-checked JAR before
compiling controls; no loose library classes appear on the control classpath.
Both control programs verify their API class origins against that JAR.

The caller control compiles the complete, unchanged official `ContourUnits.java`.
It also extracts the exact unchanged `GradientColorFunction` record from
`ContourListPanel.java` into an owned control wrapper. The slice and full parent
file each have a byte pin. This exercises the actual HSV/alpha gradient through
the model, endpoints, clamping, major/minor callback arguments, flat-gradient
fallback, flat-color replacement, notification, and the actual UI's unit
selection/round-trip logic. It is a caller-slice compilation, not compilation
of the full panel or the complete contour service. Generated source retains the
official source identity in the receipt and the output includes both upstream
license files. The library JAR contains only project classes/sources, project
license and exact provenance; Kernel, Jakarta and upstream caller classes are
excluded.

Default verification starts no process and needs no external source or tool
installation:

```sh
python3 takx/contour-style/build.py
python3 -m unittest discover -s takx/contour-style -p 'test_*.py'
```

An explicit local replay requires the pinned source projections, annotation
JAR, exact Temurin macOS ARM64 JDK and a fresh output outside all inputs:

```sh
python3 takx/contour-style/build.py --phase controls \
  --java-home /private/tmp/uds-takx-jdk17/jdk-17.0.20+8/Contents/Home \
  --takx /private/tmp/uds-takx-58-source \
  --kernel /private/tmp/uds-takx-contour-inputs/kernel \
  --annotations /private/tmp/uds-takx-contour-inputs/jakarta.annotation-api-2.1.1.jar \
  --output /private/tmp/uds-contour-style-proof
```

The recipe never downloads dependencies. It checks source/tool bytes before
importing the executor or creating output, rejects symlink/overlapping inputs,
rechecks consumed bytes between commands, and bounds each process to 30 seconds,
16 MiB total output files and 1 MiB logs. Environment-injected Java options and
classpaths are removed. Four offline guards cover default non-execution,
changed sources/acceptance claims, invalid dependency identity and output guards.

The local macOS ARM64 replay passes 311 model assertions and 23 pinned-caller
assertions. Both commands use `-Xcheck:jni`, but these are pure Java controls:
no project native providers are supplied or exercised. Empty JNI diagnostic
output provides no qualification of native JNI behavior. Existing Linux
AMD64 native contour receipts retain their original historical scope and are
not requalified by this style work. All broad acceptance flags remain false.

A supplementary native Linux ARM64 Podman control recompiles the same caller
sources against the same JAR, then passes the same 311/23 assertions. All five
caller class files reproduce byte-for-byte from the macOS compilation. It uses
the official `eclipse-temurin` Linux ARM64 child image
`sha256:5917f2e8d265897305225e8708dc15ce30eba4ac0c88a9855b878e730e7b3f3e`
(Temurin 17.0.21+9), with a read-only filesystem and source/artifact mounts,
network disabled and bounded disposable temporary files. This additional JVM
portability control is separate from the Mac-pinned packaging recipe and does
not qualify the native Linux AMD64 producer.

Retained local artifact hashes:

- macOS recipe receipt: `6814eefe92a267418909cce204db8b896988d0ff2ceda3a7ed0267f2ca0d17c4`
- Linux ARM64 supplementary receipt: `8e79935f9695c1129c689590539b3bd59e4819d86ceb0067c504bf69e849fb95`
- project style JAR: `27b6fa636b0149267525d419b73bf65ed42a9b76bf1734d9e7ab208d023b13e4`

The supplementary receipt records the exact container command, source/JAR
identities, JVM binary/module hashes in its bounded log, and the five independent
class comparisons. These are local integrity records, not publisher attestation.

## Remaining adapter contracts

The following evidence is from the exact TAKX revision above. Local component
bytes are pinned separately in the same lock; the inspected baseline is the
cancellation fix on top of PR #589, not an inferred future integration.

| Caller contract | Required implementation beyond this model |
| --- | --- |
| `ContourListPanel.java:78-84` constructs `ContourLineFactory.Generation(new ElevationSource.QueryParameters(), 50, Span.METER, 2)`. `GenerationDialog.java:139-143,298` replaces and restores the full query object. `ContourLinesService.java:240-256` replaces native spatial geometry and enables strict spatial filtering. | A factory must snapshot and honor the supported public query fields, preserve meter/unit and major-interval semantics, define bounded sampling resolution/coverage/no-data policy, and genuinely clip the selected geometry. Current `ElevationContours.generate` only accepts one exact source type/flags and an explicitly sized non-wrapping grid. Passing through only `types`, or inventing a fixed grid without a declared policy, would lose caller behavior. |
| `ContourListPanel.java:119` installs `InMemoryFeatureDataStore::new`. `ContourLinesService.java:219-220` passes produced lines into the layer factory. | Honor the supplied datastore factory and its real feature-set/update/ownership contracts. Current `ContourLayer` requires an owned POSIX file and hardcodes `FeatureSetDatabase2`; that is not the requested in-memory backend. Establish the actual interface/provider lifecycle before implementing `ContourLayerParameters` or `DefaultContourLayerFactory`. |
| `ContourListPanel.java:235-266,323-400` reads and changes callback colors/widths, labels, units, major/minor visibility and outlines. Its listener at `:530-539` excludes four label properties from preview refresh. `ContourMapRenderer.java:86-92` reads live visibility/color callbacks. | This model implements the state and notifications. Native features still need genuine update operations for color/width, per-class filtering, outlines and label styles, with callback failure and disposal behavior. Current `ContourFeatures.create` emits immutable initial stroke styles. Model tests do not qualify a native style binder or label renderer. |
| `ContourLinesService.java:160-184` builds asynchronously and copies layer parameters; `GenerationDialog.java:229-270` uses `CompletableFuture.runAsync` with shared generation/style inputs. `ContourPanel.java:79` also renders a preview asynchronously. | Define generation snapshots, style binding ownership, cancellation/publication ordering and dispatch onto the real native/renderer owner. The existing `ContourLayer` has a single-owner-thread contract; making this Java model observable does not make native layer operations thread-safe. |
| `ContourLinesService.java:91-96` calls `removeLayer(group.getLayer(), false)`. `RaptorLayerManager.java:412-415` uses that flag for a confirmation prompt; `:428-440` marks/removes/fires a notification. `TakLayerManager.java:172-205` removes layer factories/maps. | Trace the actual contour-layer removal listener through map detachment, queued/in-flight renderer retirement and datastore disposal. The inspected removal methods do not establish where a new contour owner would close. The `false` argument is not evidence of a disposal policy. |

Nine other imported contour types remain unimplemented here:
`ContourLayerFactory`, `ContourLayerParameters`, `ContourLine`,
`ContourLineFactory`, `ContourLineLayer`, `ContourProgressListener`,
`DefaultContourLayerFactory`, `DefaultContourLineFactory` and
`InvalidContourException`. Their nested/member contracts must be implemented
with real generation/providers/ownership before claiming a contour API adapter.
Full TAKX 5.8 Platform/Client compilation, actual native drawing/update/lifetime
controls, desktop startup and server interoperability remain open in #589/#477.
