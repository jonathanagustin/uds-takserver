# Core Restore schema dependency

This reviewable patch adds `Restore.spec.resourcePolicy` to Core's vendored CRD
and a strict server-side dry-run to Core's Velero validation task. It is based on
upstream Core commit `cff1255458757284305d2755314e1738e3e281ed`; `source.json`
records the exact patch and before/after file hashes. The generated reference
schema follows the upstream Velero resource-policy API used by the TAK test.

Apply `change.patch` only to that exact Core source revision after checking its
SHA-256 against `source.json`. Run `git apply --check` before applying it, then use
Core's normal build, test and signed-commit contribution process. The included
validation task uses server-side dry-run and does not persist a Restore or delete
application namespaces. A successful patch application proves source
compatibility, not installed CRD support or successful backup/restore.

The upstream fork could not be created through the available GitHub connection
(HTTP 403). These files make the prepared dependency reviewable without changing
a deployed Core. This TAK PR remains a draft until the Core change is reviewed,
the installed API accepts the exact Restore and full data/client acceptance passes.

`change.patch` is a proposed change to Defense Unicorns' AGPL-3.0-or-later Core
source. The upstream license is preserved verbatim in `LICENSE.md`; this notice
applies to this dependency artifact, not the rest of the TAK repository.
