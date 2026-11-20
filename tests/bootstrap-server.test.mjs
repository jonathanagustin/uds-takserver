// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createHmac } from 'node:crypto';
import { execFileSync, spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import tls from 'node:tls';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { serverReconciler } from '../.github/container-and-chart/cloudtak/bootstrap-server.mjs';

const urls = { url: 'ssl://new-stream:8089', api: 'https://new-api:8443', webtak: 'https://new-web:8447' };
const credentials = { cert: 'synthetic-certificate', key: 'synthetic-private-key' };

function fixture(initial = {}) {
    const record = { status: 'configured', auth: true, ...urls, name: 'Operator name', connection: false,
        connection_status: 'live', updated: '0', ...initial };
    const writes = [];
    const options = {
        urls, username: 'cloudtak-admin', password: 'synthetic-password', token: () => 'synthetic-token',
        local: async (route, { method = 'GET', headers, body } = {}) => {
            assert.equal(route, '/api/server');
            assert.equal(headers.Authorization, 'Bearer synthetic-token');
            if (method === 'PATCH') {
                writes.push(structuredClone(body));
                Object.assign(record, body, { status: 'configured', auth: true, updated: String(writes.length) });
            }
            return { status: 200, text: JSON.stringify(record) };
        },
    };
    return { record, writes, options, reconcile: serverReconciler(options) };
}

test('first setup includes the bootstrap identity', async () => {
    const f = fixture({ status: 'unconfigured', auth: false });
    await f.reconcile(credentials);
    assert.deepEqual(f.writes, [{ ...urls, auth: credentials, name: 'TAK Server',
        username: 'cloudtak-admin', password: 'synthetic-password' }]);
});

test('configured records are authenticated, updated once, and otherwise left stable', async () => {
    const f = fixture({ url: 'ssl://obsolete-stream:8089' });
    await f.reconcile(credentials);
    await f.reconcile(credentials);
    assert.deepEqual(f.writes, [{ ...urls, auth: credentials }]);
    assert.equal(f.record.name, 'Operator name');
    assert.equal(f.record.connection, false);
    assert.equal(f.record.url, urls.url);
});

test('replacement credentials with unchanged endpoints are applied once', async () => {
    const f = fixture();
    await f.reconcile(credentials);
    const next = { cert: 'replacement-certificate', key: 'replacement-key' };
    await f.reconcile(next);
    await f.reconcile(next);
    assert.equal(f.writes.length, 2);
    assert.deepEqual(f.writes[1].auth, next);
});

test('a restarted process replays redacted credentials once', async () => {
    const f = fixture();
    await f.reconcile(credentials);
    const restarted = serverReconciler(f.options);
    await restarted(credentials);
    await restarted(credentials);
    assert.equal(f.writes.length, 2);
    assert.deepEqual(f.writes[0], f.writes[1]);
});

test('an externally updated record is reconciled without overwriting operator fields', async () => {
    const f = fixture();
    await f.reconcile(credentials);
    f.record.updated = 'external-change';
    f.record.name = 'New operator name';
    await f.reconcile(credentials);
    await f.reconcile(credentials);
    assert.equal(f.writes.length, 2);
    assert.equal(f.record.name, 'New operator name');
    assert.equal(f.record.connection, false);
});

test('failed or malformed reads never become successful configuration', async () => {
    for (const response of [
        { status: 401, text: 'synthetic-private-key' },
        { status: 403, text: 'synthetic-private-key' },
        { status: 500, text: 'synthetic-private-key' },
        { status: 200, text: 'invalid-json' },
        { status: 200, text: '{"status":"unexpected"}' },
        { status: 200, text: 'null' },
    ]) {
        const f = fixture();
        let writes = 0;
        const reconcile = serverReconciler({ ...f.options, local: async (_path, opts) => {
            if (opts.method === 'PATCH') writes++;
            return response;
        } });
        await assert.rejects(reconcile(credentials), (error) => {
            assert.doesNotMatch(error.message, /synthetic-private-key/);
            return true;
        });
        assert.equal(writes, 0);
    }
});

test('failed or unconfirmed writes are retried instead of being remembered', async () => {
    for (const response of [
        { status: 403, text: 'synthetic-private-key' },
        { status: 200, text: 'invalid-json' },
        { status: 200, text: '{"status":"configured","auth":false}' },
        { status: 200, text: '{"status":"unconfigured"}' },
        { status: 200, text: JSON.stringify({ ...urls, status: 'configured', auth: true }) },
        { status: 200, text: JSON.stringify({ ...urls, url: 'ssl://wrong:8089', status: 'configured', auth: true, updated: '1' }) },
    ]) {
        const f = fixture();
        let fail = true;
        const reconcile = serverReconciler({ ...f.options, local: async (route, opts) => {
            if (opts.method === 'PATCH' && fail) return response;
            return f.options.local(route, opts);
        } });
        await assert.rejects(reconcile(credentials));
        fail = false;
        await reconcile(credentials);
        await reconcile(credentials);
        assert.equal(f.writes.length, 1);
    }
});

test('the real bootstrap process reconciles persisted settings and rotated mounted credentials over TLS', async (t) => {
    const directory = mkdtempSync(path.join(os.tmpdir(), 'uds-bootstrap-'));
    t.after(() => rmSync(directory, { recursive: true, force: true }));
    execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
        '-keyout', path.join(directory, 'admin.key'), '-out', path.join(directory, 'admin.pem')], { stdio: 'ignore' });
    const cert = readFileSync(path.join(directory, 'admin.pem'));
    const key = readFileSync(path.join(directory, 'admin.key'));
    execFileSync('openssl', ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', '/CN=replacement', '-keyout', path.join(directory, 'replacement.key'),
        '-out', path.join(directory, 'replacement.pem')], { stdio: 'ignore' });
    const replacementCert = readFileSync(path.join(directory, 'replacement.pem'));
    const replacementKey = readFileSync(path.join(directory, 'replacement.key'));
    writeFileSync(path.join(directory, 'ca.pem'), cert);
    const acceptedClients = [];
    const peer = tls.createServer({ cert, key, ca: [cert, replacementCert],
        requestCert: true, rejectUnauthorized: true }, (socket) => {
        acceptedClients.push(socket.getPeerCertificate().subject.CN);
        socket.end('verified client');
    });
    await new Promise(resolve => peer.listen(0, '127.0.0.1', resolve));
    t.after(() => new Promise(resolve => peer.close(resolve)));
    const stream = `ssl://127.0.0.1:${peer.address().port}`;
    const record = { id: 42, status: 'configured', auth: true, url: 'ssl://obsolete:8089',
        api: 'https://obsolete:8443', webtak: 'https://obsolete:8447', name: 'Retained display name',
        connection: true, updated: '0', connection_status: 'dead' };
    let patches = 0;
    let connections = 0;
    const adoptedCertificates = [];
    const signing = 'synthetic-signing-secret';
    const server = http.createServer(async (req, res) => {
        const chunks = [];
        for await (const chunk of req) chunks.push(chunk);
        res.setHeader('Content-Type', 'application/json');
        if (req.url === '/api/server') {
            const [head, body, signature] = (req.headers.authorization?.replace(/^Bearer /, '') || '').split('.');
            const expected = createHmac('sha256', signing).update(`${head}.${body}`).digest('base64url');
            if (signature !== expected) {
                res.writeHead(401).end('{}');
                return;
            }
            if (req.method === 'PATCH') {
                const patch = JSON.parse(Buffer.concat(chunks));
                patches++;
                assert.equal(patch.url, stream);
                assert.equal(patch.name, undefined);
                await new Promise((resolve, reject) => {
                    const socket = tls.connect({ host: '127.0.0.1', port: peer.address().port,
                        ca: cert, servername: 'localhost', ...patch.auth });
                    let reply = '';
                    socket.setEncoding('utf8');
                    socket.on('data', chunk => { reply += chunk; });
                    socket.on('end', () => {
                        if (reply !== 'verified client') reject(new Error('TLS peer did not verify the client'));
                        else { connections++; resolve(); }
                    });
                    socket.on('error', reject);
                });
                adoptedCertificates.push(patch.auth.cert);
                Object.assign(record, patch, { auth: true, updated: String(patches), connection_status: 'live' });
            }
            res.end(JSON.stringify(record));
        } else res.end('{}');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    t.after(() => { server.closeAllConnections(); return new Promise(resolve => server.close(resolve)); });
    const origin = `http://127.0.0.1:${server.address().port}`;
    const script = fileURLToPath(new URL('../.github/container-and-chart/cloudtak/bootstrap.mjs', import.meta.url));
    const child = spawn(process.execPath, [script], { env: { ...process.env, CLOUDTAK_LOCAL_URL: origin,
        TAK_API_URL: origin, TAK_WEB_URL: origin, TAK_STREAM_URL: stream, TAK_ADMIN_DIR: directory,
        TAK_ADMIN_KEY_PASSPHRASE: 'unused-for-unencrypted-fixture', CLOUDTAK_USERNAME: 'cloudtak-admin',
        CLOUDTAK_PASSWORD: 'synthetic-password', SigningSecret: signing,
        BASEMAP_ENABLED: '', BOOTSTRAP_INTERVAL_SECONDS: '0.02' }, stdio: ['ignore', 'pipe', 'pipe'] });
    const closed = new Promise(resolve => child.once('close', resolve));
    t.after(async () => { child.kill(); await closed; });
    let output = '';
    let errors = '';
    child.stdout.on('data', chunk => { output += chunk; });
    child.stderr.on('data', chunk => { errors += chunk; });
    const waitUntil = (ready, label, timeoutMs = 5000) => new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
            clearInterval(poll); reject(new Error(`bootstrap did not ${label}: ${errors}`));
        }, timeoutMs);
        const poll = setInterval(() => {
            if (ready()) { clearTimeout(timeout); clearInterval(poll); resolve(); }
        }, 10);
    });
    await waitUntil(() => output.includes('unchanged, connection live'), 'settle');
    assert.equal(patches, 1);

    // A read that crosses a projected Secret update can see a mismatched pair.
    // Leave that state visible until the process rejects it, then complete it.
    writeFileSync(path.join(directory, 'admin.pem'), replacementCert);
    await waitUntil(() => errors.includes('certificate and private key do not match'), 'reject the mismatched pair');
    assert.equal(patches, 1);
    writeFileSync(path.join(directory, 'admin.key'), replacementKey);
    const stableCycles = () => (output.match(/unchanged, connection live/g) || []).length;
    const previousCycles = stableCycles();
    await waitUntil(() => patches === 2 && stableCycles() > previousCycles, 'adopt the rotated pair', 20000);
    child.kill();
    await closed;
    assert.equal(patches, 2);
    assert.equal(connections, 2);
    assert.deepEqual(acceptedClients, ['localhost', 'replacement']);
    assert.deepEqual(adoptedCertificates, [cert.toString(), replacementCert.toString()]);
    assert.equal(record.id, 42);
    assert.equal(record.name, 'Retained display name');
    assert.equal(record.url, stream);
    assert.equal(errors, 'not ok - TAK admin certificate and private key do not match\n');
    assert.doesNotMatch(output, /PRIVATE KEY|synthetic-password/);
});
