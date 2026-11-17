// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

import { crawlCommand } from './crawl.safe.mjs';

await crawlCommand().parseAsync(process.argv);
