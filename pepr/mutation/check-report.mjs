// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const path = process.argv[2] ?? new URL("./reports/mutation.json", import.meta.url);
const report = JSON.parse(readFileSync(path, "utf8"));
const expected = [
  "capabilities/oidc-render.ts",
  "scripts/webhook-scope.ts",
];
const files = Object.entries(report.files ?? {});
assert.deepEqual(
  files.map(([name]) => name).sort(),
  expected,
  "Mutation report must contain exactly the configured source targets",
);
for (const [name, entry] of files) {
  assert.ok(
    entry.mutants.some(mutant => ["Killed", "Survived", "Timeout"].includes(mutant.status)),
    `No executable mutations were tested for ${name}; a dry run is not a mutation result`,
  );
}
