# Public vehicle CoT metadata library

This project profile implements vehicle-model CoT metadata through the real
public Kernel `ICotDetailHandler2`, `CotEvent` and `CotDetail` APIs. Its generic
Interop callback matches the pinned TAKX 5.8 caller. It does not create vehicle
meshes, implement contours, or identify public Kernel 8.30.5 as private 9.12.6.

The locked public Android handler establishes the `model` wire fields: vehicle
type, category, name and outline. The pinned 5.8 caller additionally expects
stroke/fill color callbacks. This project policy exports both colors and gives
fill precedence for a model's single color. Signed decimal, unsigned decimal and
32-bit hexadecimal ARGB values are accepted; malformed values fail before the
handler mutates callback-owned state. Colors may precede a valid vehicle model
in an incoming event. Unrelated model types are ignored. Callback exceptions
propagate without replacement. Asset availability belongs to the caller's
`setVehicleInfo` callback, which can refuse an unavailable model.

`inputs.lock.json` records both source revisions and their source hashes. The
new handler is an explicit project implementation, not a recovered private
implementation. Its controls use actual public CoT classes and an owned test
model. Thirty-four checks cover wire round trips, color order/precedence,
invalid/missing fields, callback refusal/error preservation and absence of SDK
native libraries. No full Client or mesh runtime acceptance follows.

Default verification and seven offline guards need no Java or downloads:

```sh
python3 build.py
python3 -B -m unittest discover -s . -p 'test_*.py' -v
```

The opt-in controls consume the independently reviewed SHA-256 of a complete
`public-context-client-java` receipt (1,239 sources / 2,702 classes), all of its real JARs,
and an explicit JDK. They verify all class/dependency hashes before and after
execution and preserve failures in a fresh, disjoint output directory:

```sh
python3 build.py --phase controls --java-proof /path/to/full-java-proof \
  --java-proof-sha256 REVIEWED_RECEIPT_SHA256 --java-home /path/to/jdk17 \
  --output /path/to/new/vehicle-controls
```

The profile downloads and installs nothing. The complete 5.8 Platform/Client,
contour generation/layers, real vehicle assets/meshes, peer interoperability and
portable SDK assembly remain separate qualification gates.

The composition recipe is staged in #499 as `takx/kernel-render-context/client_java.py`. Its source revision and reviewed native/Java input receipts remain explicit prerequisites; this metadata profile does not build them or silently select a legacy checkpoint.


## Build and use the library

`build` creates `vehicle-metadata.jar`, containing the handler and its `Interop`
interface, source, license and provenance. SDK dependencies and test classes are
excluded. Both API classes are loaded from that JAR for the real CoT controls;
loose build classes are excluded from their runtime classpath.

```sh
python3 build.py --phase build --java-proof /path/to/full-java-proof \
  --java-proof-sha256 REVIEWED_RECEIPT_SHA256 --java-home /path/to/jdk17 \
  --output /path/to/new/vehicle-library
```

Add the resulting JAR to the client classpath alongside the authenticated public
Kernel CoT API dependencies. The output receipt records the artifact and class
hashes and marks artifact acceptance only after the packaged controls and all
input rechecks pass. Default verification still compiles or downloads nothing.

Two actual JDK17 builds produced the same JAR bytes. Each passed all 34 real CoT
controls with no strict JNI diagnostics and verified both API class origins.
A further replay freshly compiled the complete 1,815-source 5.7 Platform and
three genuine 5.8 vehicle adapter sources against the packaged API. That replay
reused pinned source templates and dependency JARs; earlier replay setup failures
remain retained. `QUALIFICATION.json` records the reviewed artifact, source and
receipt hashes. This qualifies the metadata library; vehicle assets, meshes and
complete 5.8 Platform/Client execution retain their separate acceptance gates.
