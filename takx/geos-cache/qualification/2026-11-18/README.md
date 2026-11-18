# Native hosted local GEOS qualification

The exact producer and two-prefix consumer records from [run 44357847600,
attempt 1](https://github.com/jonathanagustin/uds-takserver/actions/runs/44357847600)
are retained here without rewriting their bytes. The source head is
`df1eb30b32aa6311e3523726c2e75b30a3075f33`; `hosted-trial.json` records the
actual synthetic merge checkout/tree and record hashes.

The fresh native AMD64 build used the pinned compiler image and existing
source/patch/configuration/tool/ABI gates. Both generated headers selected
`HAVE_LONG_INT_64`, and the executed integer probe confirmed eight-byte
arithmetic. Genuine C geometry/error and strict whole-archive PIC controls
passed for the producer and two unrelated restored prefixes, with matching
header, archive and initialized-provider identities. The consumer had neither
the original producer/source tree nor host GEOS available.

Independent review downloaded artifact `14451988100`, matched both record hashes
to the hosted binding, checked the workflow run/job/attempt and synthetic merge
parents/tree, compared all consumed recipes with the reviewed source, verified
the canonical OCI/capsule and exact five-file payload, and restored it again
without executing the payload. `verified.json` records the archive/capsule
hashes, local subject and input key. This independent restore is an integrity
control, not another native execution.

The earlier [failed hosted trial](https://github.com/jonathanagustin/uds-takserver/actions/runs/44357031894)
and prior stopped/emulated trials remain failures or limited evidence. The
hosted failure occurred before compilation when capability-free Docker root
could not traverse the runner-owned private mount; the qualified source runs
Docker as the invoking UID/GID without granting capabilities or widening the
directory permissions.

Acceptance is limited to fresh native **local** production and relocation.
Registry roundtrip, publisher authentication, immutable-key promotion, SDK/TAKX
acceptance and security suitability remain unqualified. Their acceptance flags
remain false; issue #482 stays open. These records do not authenticate a future
registry publisher or authorize an automatic SDK cache consumer.
