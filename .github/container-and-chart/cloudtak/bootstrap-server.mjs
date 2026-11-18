// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

import { createHash } from 'node:crypto';

// Upstream redacts the saved private credentials. Replay them once when this
// process starts, then only when the mounted material or server record changes.
// Its PATCH refreshes the admin connection, so avoid repeating an unchanged
// write on every polling cycle.
export function serverReconciler({ local, token, urls, username, password }) {
    let applied;

    function response(res, method) {
        if (res.status !== 200) throw new Error(`${method} /api/server returned ${res.status}`);
        let body;
        try {
            body = JSON.parse(res.text);
        } catch {
            throw new Error(`${method} /api/server returned invalid JSON`);
        }
        if (!body || !['configured', 'unconfigured'].includes(body.status)) {
            throw new Error(`${method} /api/server returned an invalid server record`);
        }
        return body;
    }

    return async ({ cert, key }) => {
        const headers = { Authorization: `Bearer ${token()}` };
        const current = response(await local('/api/server', { headers }), 'GET');
        const fingerprint = createHash('sha256').update(cert).update('\0').update(key).digest('hex');
        const matches = Object.entries(urls).every(([name, value]) => current[name] === value);
        if (current.status === 'configured' && current.auth === true && matches &&
            applied?.fingerprint === fingerprint && applied.updated === current.updated) {
            return `unchanged, connection ${current.connection_status}`;
        }

        // Name, connection enablement, and other operator settings belong to
        // CloudTAK once configured. Username/password are for first setup only.
        const body = { ...urls, auth: { cert, key } };
        if (current.status === 'unconfigured') {
            Object.assign(body, { name: 'TAK Server', username, password });
        }
        const next = response(await local('/api/server', { method: 'PATCH', headers, body }), 'PATCH');
        if (next.status !== 'configured' || next.auth !== true || typeof next.updated !== 'string' ||
            !Object.entries(urls).every(([name, value]) => next[name] === value)) {
            throw new Error('PATCH /api/server did not confirm the managed connection settings');
        }
        applied = { fingerprint, updated: next.updated };
        return `configured, connection ${next.connection_status}`;
    };
}
