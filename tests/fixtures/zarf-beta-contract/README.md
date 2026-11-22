# Zarf beta value migration contract

This fixture supports #712. It compares the ten root package variable defaults
with their proposed beta package-value paths. The runner checks the production
variable names and defaults to prevent this copy drifting silently.

Run from the repository root:

```sh
uv run --no-project --with pyyaml==6.0.3 python -B tests/zarf_beta_contract.py --cli uds --uds
uv run --no-project --with pyyaml==6.0.3 python -B tests/zarf_beta_contract.py --cli /path/to/zarf
```

Validated with UDS CLI 0.42.0 (embedded Zarf 1.0.1).
Each invocation renders defaults and all ten overrides for both schemas, verifies
`chartOverrides` root overlay propagation across `import.local`, checks that an
`optional: true` component receives mapped values and retains `optional: true`
in the built package definition, confirms `values.schema` rejects an invalid
`backup.filesystem.owner` value, and creates both packages in temporary
directories. The chart is local; no cluster, registry, image, or production
package build is required. The existing `package-values` CI job runs this

The chart deliberately quotes every ConfigMap data value. Beta `--set-values`
infers boolean types for `true` and `false`; legacy variables are strings. This
proves equivalence at that explicit string consumer boundary. Production chart
conditionals, template files, image references, real shared charts, and live
Next-bundle deployment/upgrade acceptance still remain separate gates for #712.
