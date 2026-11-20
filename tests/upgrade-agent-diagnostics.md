# Agent failure evidence

`upgrade-agent-diagnostics.sh` runs only after failure: upgrade boundary checks
invoke it in `tests/upgrade.sh`, Test install rows invoke it immediately
after their runner-wedge check, and Full Core invokes it immediately after its
runner-wedge check with constant phase `full-core`. The workflow callers skip
capture when a runner network stall was detected and have a two-minute step
limit around the helper's own deadline. The shared Test upgrade steps do not
invoke the install-only caller, so existing upgrade boundary capture is not
duplicated. Capturing evidence cannot turn a failed validation into success.
It retains the 30-second process-group deadline and three-second command limits.
Each command also has a combined 1 MiB stdout/stderr ceiling. The bounded command
helper drains both pipes before releasing either complete stream; a timeout or
oversized response kills the owned process group and emits only a fixed omission.
Partial prefixes never reach discovery parsing, redaction or publication. Completed
nonzero exits retain their status and complete bounded error streams for redaction.
Whole-stream redaction has the same byte ceiling, including the final combined
sanitized log, and omits malformed UTF-8. These limits bound failure capture; they
do not diagnose the original failed request or change readiness/admission behavior.
CRI capture runs first so unavailable API calls cannot consume the entire budget.
It reads at most two k3d nodes and two agent container attempts per node, newest
first, including exited containers. Both API logs and CRI logs use source-side
80-line limits; each returned stream is sanitized before any local tail/output.
No cluster mutation, Docker inspection, environment dump or Secret read occurs.
Docker discovery, CRI listing and CRI log retrieval report their exit status.
Successful discovery with no eligible nodes, invalid CRI JSON/schema, no matching
agent attempts, and a successful empty log stream are distinct outcomes. CRI
selection reports only aggregate label-presence/match counts; it does not dump
container labels. Failed command output is sanitized as a whole before an
eight-line local tail. The final wrapper retains all individually bounded
streams, so its former 120-line tail cannot discard the first/newest CRI log.

Pod metadata includes image, explicitly configured command/args, restart count,
container IDs, termination reason, exit code and lifecycle timestamps. Live
startup/liveness/readiness timing integers and standard CPU/memory quantities
are allowlisted separately. Only the known header-free `/healthz` action on
8443 is shown; custom actions, exec commands, hosts, headers, URLs/query strings
and custom resource names are omitted. Missing timings stay missing rather
than being filled from chart defaults. An
unfamiliar invocation is omitted wholesale because positional arguments can be
credentials. Empty command/args mean the image defaults apply; they do not prove
an empty process invocation. Zarf v0.88.0's published Dockerfile declares
`/zarf internal agent --log-level=debug --log-format=console --no-color`.
CRI metadata adds attempt number, state and creation timestamp when the API is
unavailable. Authentication-bearing streams, including registry auth JSON, are
omitted as a whole before other redaction and local filtering.

CRI remains the preferred runtime path. If its wrapper/list/log call is
unavailable, invalid, unmatched or empty, a physical fallback requires a current
API Pod identity: namespace `zarf`, canonical agent Pod name, UID, discovered
k3d node, server container ID and integer restart count. It reads only
`/var/log/pods/<namespace>_<name>_<uid>/server/<attempt>.log` for that API-selected
attempt and at most two predecessors. Missing API identity or an undiscovered
node omits this fallback; no directory glob, Docker inspection, environment
read, Secret read or arbitrary file selection occurs.

Each physical read has the existing three-second command limit and a
65,537-byte ceiling. Over 65,536 bytes are omitted wholesale, incomplete
stdout is discarded, and symlink path components are refused. Complete streams
are sanitized before a local 80-line bound. Explicit exit status and captured
byte count distinguish a successful empty read from a missing/unreadable path
or failed/partial command. The same global 30-second process-group deadline
still applies and can truncate later evidence. On the observed hosted node,
`k3s crictl` returned `No help topic for 'crictl'`; this is not a claim that all
K3s 1.37 distributions removed the wrapper.

After all selected agents' primary logs, the helper may use any remaining
30-second capture budget for at most two agent-to-API snapshots. A validated
captured `zarf/agent-hook-*` Pod identity selects its existing `server` container;
`/zarf tools kubectl` reads only the `velero` Namespace UID using the container's
normal in-cluster identity and network path. Live Pod reads immediately before
and after the name-based exec must match the captured Pod UID, service account,
node and named server container ID, restart count and running-start timestamp.
Container ordering does not select the target. All identity reads and the exec
share one three-second snapshot deadline; kubectl requests retain their existing
two-second request limits. Private Pod JSON is limited to 64 KiB, and lookup
stdout to 128 bytes; neither an oversized nor partial response is published.
Global capture termination also stops the snapshot's owned command group and
emits only an unattributed interruption record.
The outer kubectl request includes exec establishment; elapsed time includes
identity observations, process startup and exec transport plus the inner lookup, not pure API GET
latency.
No token, kubeconfig or Secret content is collected or exported, and no diagnostic
Pod is created.
Pod responses stay private; environment and other nonidentity fields are not
exported. Only a stable before/after observation has `identityQualified: true`
and an attributed `agent` plus a valid Namespace UID when lookup succeeds.
A changed or unavailable identity leaves `identityQualified: false`, labels
the earlier metadata `selectedAgent`, and omits agent attribution and Namespace
UID even if the exec returned one. The bounded exec outcome remains separately
available without its output. Public incarnation fields are fixed container IDs,
restart counts and timestamps, alongside the selected Pod identity, elapsed
milliseconds, exit status and outcome.
Exec stderr and unsuccessful or malformed stdout are discarded wholesale.
Unavailable execution, timeout and combined exec/lookup failure are distinct;
a generic failure cannot establish whether exec or the nested API call failed.

These are explicitly **postfailure snapshots**, not latency measurements or
causal evidence for the earlier admission request. They cannot recover an
AdmissionReview UID that official Zarf 0.88 never logged. Agent Pod metadata
and exec are not atomic: stable API observations bracket the call but cannot
guarantee instantaneous container identity while API status is stale. A detected
Pod replacement, restart or expired exec route makes attribution unavailable.
The unchanged global capture deadline may omit
snapshots entirely; probes never precede or displace primary evidence. No
admission bypass, retry, probe relaxation, audit configuration or global
timeout change is introduced.

Source contracts:

- [Zarf v0.88.0 deployment](https://github.com/zarf-dev/zarf/blob/v0.88.0/packages/zarf-agent/chart/templates/deployment.yaml): `server` container and `agent-hook` pod label.
- [Zarf v0.88.0 Dockerfile](https://github.com/zarf-dev/zarf/blob/v0.88.0/Dockerfile): image process defaults.
- [k3d default labels](https://github.com/k3d-io/k3d/blob/main/pkg/types/defaults.go): `app=k3d`; [node roles](https://github.com/k3d-io/k3d/blob/main/pkg/types/types.go): server and agent.
- [k3s multicall dispatch](https://github.com/k3s-io/k3s/blob/main/cmd/k3s/main.go): `k3s crictl` locates its configured CRI endpoint via `findCriConfig`.
- [containerd CRI container listing](https://github.com/containerd/containerd/blob/main/internal/cri/server/container_list.go): exposes CRI metadata, labels, creation time and state.
- [Kubernetes v1.37.1 log paths](https://github.com/kubernetes/kubernetes/blob/v1.37.1/pkg/kubelet/kuberuntime/helpers.go): `BuildPodLogsDirectory` and `buildContainerLogsPath` bind namespace/name/UID/container/restart count.
- [CRI tools logs](https://github.com/kubernetes-sigs/cri-tools/blob/master/cmd/crictl/logs.go): bounded runtime log retrieval, including terminated container IDs.

Fixtures: `python3 -m unittest discover -s tests -p test_upgrade_agent_diagnostics.py`.
These establish capture behavior and redaction; they do not reproduce the agent
failure or establish its cause. The outer deadline may truncate evidence when
multiple commands are slow. No unchanged CI retry is implied.

A manually dispatched Test workflow can select focused evidence:

| `diagnostic_case` | Selected runtime work |
| --- | --- |
| `airgap-amd64` | Existing AMD airgap install row only; receipts omitted |
| `upgrade-amd64` | Regular upgrade, AMD only |
| `pg15-amd64` | Retained PostgreSQL 15 upgrade, AMD only |
| `pg15-both` | Retained PostgreSQL 15 upgrades, AMD and ARM |

AMD uses the existing `TEST_RUNNER` repository variable; ARM uses the ordinary
native ARM runner. Upgrade selections skip installs and the unselected upgrade family.
`airgap-amd64` selects only the existing AMD airgap install row and skips both
upgrade families; its success is diagnostic-only and emits no install receipt. `pg15-both` runs the two additional main-branch PG15 paths without
repeating the three install and two regular-upgrade paths. It does not reuse
or certify any earlier result: retain the separate PR evidence for those paths.

These modes cannot be combined with `trust_bundle`, are ignored for PR/push
events, and report only diagnostic success. Default `none` preserves all ordinary
matrix entries and PG15 trigger rules. Dispatch is deliberate; no automatic
retry, scheduling or result reuse is added.

The airgap firewall uses built-in DROP for Docker embedded DNS and its terminal
deny rule, preserving the internal-network and loopback allow ordering. This
reduces dependency on an optional REJECT extension; it is not a diagnosis of
the prior hidden-stderr failure. Firewall command failures still raise and
restore node egress. Their stderr is sanitized as a whole, authentication-bearing
streams are omitted, and only a bounded single-line detail is printed. The
Docker packet fixtures assert DROP counters but remain opt-in; fixture edits
and offline rule-order tests do not establish live airgap acceptance.

Ordinary and focused CI now inherit `fit-runner.sh`'s upstream two-replica
agent baseline. The explicit manual `airgap-amd64` case additionally requires
two current replicas through the bounded live health helper after successful
application checks and egress restoration. One unhealthy replica, stale
ownership, duplicate endpoint or failed direct health prevents diagnostic
success. [Run 42800551477](https://github.com/jonathanagustin/uds-takserver/actions/runs/42800551477)
validated one native AMD offline profile and both current replicas. It is not
a startup/liveness root-cause finding or full cross-profile acceptance; the
intermittent exit-2 cause remains unknown. Probes and limits are unchanged.

The post-block host transport check uses the same validated public-IP
`AIRGAP_PROBE_URL` as the node checks (default `http://1.1.1.1/cdn-cgi/trace`).
It ignores curl configuration and proxies, makes one bounded request, and
accepts any HTTP status, including 500, as reachability evidence. Transport,
TLS and non-HTTP failures still stop deployment; credentials/query/fragment
URLs are rejected without logging their values. No retry or TLS verification
bypass is used. This removes the unrelated GitHub Zen dependency; the prior
host TLS EOF cause remains unknown.

Snapshot fixtures: `python3 -B -m unittest tests.test_upgrade_agent_api_snapshot`.
