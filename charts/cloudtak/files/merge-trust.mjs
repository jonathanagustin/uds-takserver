// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

// Node accepts one extra CA file. Keep the TAK CA and Core's optional bundle
// together, on a volume mounted read-only by the Node containers.
import fs from "node:fs";

const [takCa, bundle, output] = process.argv.slice(2);
if (!takCa || !output) {
  console.error("usage: merge-trust <tak-ca> <bundle> <output>");
  process.exit(1);
}
const takBytes = fs.readFileSync(takCa);
if (takBytes.length === 0) {
  console.error(`empty TAK CA: ${takCa}`);
  process.exit(1);
}
const parts = [takBytes, Buffer.from("\n")];
if (bundle && fs.existsSync(bundle) && fs.statSync(bundle).isFile()) {
  parts.push(fs.readFileSync(bundle), Buffer.from("\n"));
}
fs.writeFileSync(output, Buffer.concat(parts), { mode: 0o444 });
fs.chmodSync(output, 0o444);
