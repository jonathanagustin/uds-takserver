# Zarf beta value migration contract

This fixture supports #712. It compares the ten root package variable defaults
with their proposed beta package-value paths. The runner checks the production
variable names and defaults to prevent this copy drifting silently.

Run from the repository root:

```sh
uv run --no-project --with pyyaml==6.0.3 python -B tests/zarf_beta_contract.py --cli uds --uds
uv run --no-project --with pyyaml==6.0.3 python -B tests/zarf_beta_contract.py --cli /path/to/zarf
```

Validated with UDS CLI 0.41.0 (embedded Zarf 0.89.0) and Zarf 1.0.1.
Each invocation renders defaults and all ten overrides for both schemas, then
creates both packages in temporary directories. The beta package imports a
`ZarfComponentConfig` through `import.local`, exercising component-relative chart
paths and package-value propagation across the import boundary. The chart is local; no cluster,
registry, image, or production package build is required. The existing package
values CI job runs this contract with the repository's pinned UDS CLI.

The chart deliberately quotes every ConfigMap data value. Beta `--set-values`
infers boolean types for `true` and `false`; legacy variables are strings. This
proves equivalence at that explicit string consumer boundary. Production chart
conditionals, template files, image references, real shared charts, optional
components, ATAK build templates, and bundle overrides still need their own
migration and acceptance checks. This fixture does not deploy the real package
or complete #712. Production package schemas and defaults remain unchanged.
