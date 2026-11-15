# Additional AMD64 startup pair — November 15, 2026

One additional AMD64 application-start pair passed in
[run 43766367603, attempt 1](https://github.com/jonathanagustin/uds-takserver/actions/runs/43766367603),
source `254c30c4142dae05ee3e18b54dc7c991b924fda8`.
The AMD64 job completed the native-architecture, clean-daemon, Core restore,
disposable-cluster, package acquisition, cold deployment and warm restart steps.
Its artifact ZIP SHA-256 matches GitHub metadata; all six original JSON files and
four exact collector/workflow source snapshots are retained here. The source
pair validator replay agrees with the original accepted pair, and `check.py`
independently recomputes phase timestamps and verifies the source/run bindings.

The cold and warm Pod-creation-to-Ready spans were 170 and 130 seconds. The
[earlier AMD64 pair](../startup-pairs-20261114/README.md) recorded 140 and 100
seconds. These are two pair-level observations from different executions, not
an estimate of a startup distribution or evidence of a performance change. The
four application/boundary rows below belong to one additional pair and are not
four independent trials. All displayed durations are seconds.

| Architecture | Boundary | Application | Paired pull (s) | Wait deps | Trust | OIDC | Container→JVM | JVM→log | Log→Ready |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| amd64 | cold | takserver-api | 1.000 | 42.000 | 45.000 | 1.000 | 0.469 | 71.329 | 8.201 |
| amd64 | cold | takserver-messaging | 1.000 | 42.000 | 45.000 | 1.000 | 0.626 | 58.601 | 8.773 |
| amd64 | warm | takserver-api | 0.000 | 4.000 | 45.000 | 0.000 | 1.048 | 74.172 | 1.779 |
| amd64 | warm | takserver-messaging | 0.000 | 4.000 | 45.000 | 0.000 | 1.053 | 61.050 | 5.896 |

| Architecture | Cold Pod span (s) | Warm Pod span (s) |
| --- | --- | --- |
| amd64 | 170.000 | 130.000 |


The restored Core namespace UID matches the earlier AMD64 execution: checkpoint
restoration can clone that identity, so it cannot count distinct physical
clusters. The new run/cohort and application Pod/JVM identities differ from the
retained prior pair. The successful reviewed bootstrap and deployment steps are
retained in `job.json`; repeated run/cohort, Pod or JVM identities are rejected by
the checker. The existing within-pair platform/configuration/identity guards stay
intact. Across-install configuration fingerprints include resource identities and
are not proof of byte-identical settings or secret equivalence.

ARM64 failed the Core-restore step before the cold boundary. Its observed step
metadata is retained in `arm-status.json`; no ARM64 phase or accepted pair is
reported from this run. This does not replace the earlier successful ARM64 pair
or explain the restore failure. Post-failure upload/cleanup status is separate
from application qualification.

“Cold” describes absence of the measured application at the declared boundary,
not proven absence of cached images or data. Node image inventories may be
truncated. No CDS/probe/JVM setting changed, class-loading duration, observer
overhead, cache benefit, ISA ranking or tuning benefit is established. The trust
init durations do not establish a causal contribution from any particular sleep
or network operation. The separately proposed TLS-fetch deadline was not in this
experiment source.

## Recheck

```sh
python3 -B bench/results/startup-repeat-20261115/check.py --table
python3 -B -m unittest tests.test_startup_evidence
```

`sources.json` retains artifact/job identity and every original file hash. The
package field is the package-key hash captured by the reviewed acquisition step;
it is not an independently recomputed package archive digest. Replaying a local
checkout without the same image-tag pinning and UDS CLI does not establish that
key. Original records and earlier qualification evidence remain unchanged.
