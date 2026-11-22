// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

// Render nginx.conf into a writable /tmp directory so CloudTAK runs without
// root or a writable root filesystem. Replaces the sed/awk/grep pipeline in
// start.sh so nginx setup needs only Node.

import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

function toLines(text) {
    const normalized = text.replace(/\r\n/g, '\n');
    if (normalized === '') return [];
    const trimmed = normalized.endsWith('\n') ? normalized.slice(0, -1) : normalized;
    return trimmed === '' ? [] : trimmed.split('\n');
}

function rewriteBase(lines, nginxDir) {
    const out = [];
    for (const line of lines) {
        if (/^user /.test(line)) continue;
        if (/^pid /.test(line)) {
            out.push(`pid ${nginxDir}/nginx.pid;`);
            continue;
        }
        if (/^error_log /.test(line)) {
            out.push('error_log /dev/stderr warn;');
            continue;
        }
        const accessMatch = line.match(/^( *)access_log /);
        if (accessMatch) {
            const indent = accessMatch[1];
            out.push(
                `${indent}access_log /dev/stdout main;`,
                `${indent}client_body_temp_path ${nginxDir}/client_body;`,
                `${indent}proxy_temp_path ${nginxDir}/proxy;`,
                `${indent}fastcgi_temp_path ${nginxDir}/fastcgi;`,
                `${indent}uwsgi_temp_path ${nginxDir}/uwsgi;`,
                `${indent}scgi_temp_path ${nginxDir}/scgi;`,
            );
            continue;
        }
        out.push(line);
    }
    return out;
}

function injectBlocks(lines, { beforeMatch, beforeText, afterMatch, afterText }) {
    const beforeLines = beforeText ? toLines(beforeText) : [];
    const afterLines = afterText ? toLines(afterText) : [];
    const out = [];
    for (const line of lines) {
        if (beforeMatch && beforeLines.length > 0 && beforeMatch.test(line)) {
            out.push(...beforeLines);
        }
        out.push(line);
        if (afterMatch && afterLines.length > 0 && afterMatch.test(line)) {
            out.push(...afterLines);
        }
    }
    return out;
}

/**
 * Transform upstream nginx.conf output for unprivileged /tmp execution and
 * optional UDS SSO / basemap / IPv6 configuration.
 */
export function transformNginxConf(raw, options = {}) {
    const nginxDir = options.nginxDir ?? '/tmp/nginx';
    const hasIpv6 = options.hasIpv6 ?? true;

    let lines = rewriteBase(toLines(raw), nginxDir);

    if (options.oidcEnabled === true) {
        lines = injectBlocks(lines, {
            beforeMatch: /location ~ \^\/api/,
            beforeText: options.ssoConf ?? '',
            afterMatch: /server_tokens off;/,
            afterText: options.ssoHeadConf ?? '',
        });
        if (!lines.some((line) => line.includes('location ^~ /api/login/oidc'))) {
            throw new Error('rendered nginx.conf is missing SSO location ^~ /api/login/oidc');
        }
    }

    if (options.basemapEnabled === true) {
        lines = injectBlocks(lines, {
            beforeMatch: /location ~ \^\/api/,
            beforeText: options.basemapServerConf ?? '',
            afterMatch: /^ *scgi_temp_path /,
            afterText: options.basemapHttpConf ?? '',
        });
        if (!lines.some((line) => line.includes('location ^~ /uds-basemap/'))) {
            throw new Error('rendered nginx.conf is missing basemap location ^~ /uds-basemap/');
        }
    }

    if (hasIpv6 === false) {
        lines = lines.filter((line) => !/^\s*listen\s+\[::\]/.test(line));
    }

    return lines.length > 0 ? `${lines.join('\n')}\n` : '';
}

function main() {
    const nginxDir = process.argv[2] || '/tmp/nginx';
    fs.mkdirSync(nginxDir, { recursive: true });

    const raw = execFileSync(process.execPath, ['nginx.conf.js'], { encoding: 'utf8' });

    const oidcEnabled = (process.env.CLOUDTAK_Config_oidc_enabled || 'false') === 'true';
    const basemapEnabled = process.env.BASEMAP_ENABLED === 'true';

    let ssoConf = '';
    let ssoHeadConf = '';
    if (oidcEnabled) {
        ssoConf = fs.readFileSync('/home/etl/uds/nginx-sso.conf', 'utf8');
        ssoHeadConf = fs.readFileSync('/home/etl/uds/nginx-sso-head.conf', 'utf8');
    }

    let basemapServerConf = '';
    let basemapHttpConf = '';
    if (basemapEnabled) {
        const basemapDir = path.join(nginxDir, 'basemap');
        execFileSync(process.execPath, ['/home/etl/uds/basemap.mjs', 'nginx', basemapDir], { stdio: 'inherit' });
        basemapServerConf = fs.readFileSync(path.join(basemapDir, 'server.conf'), 'utf8');
        basemapHttpConf = fs.readFileSync(path.join(basemapDir, 'http.conf'), 'utf8');
    }

    const rendered = transformNginxConf(raw, {
        nginxDir,
        oidcEnabled,
        ssoConf,
        ssoHeadConf,
        basemapEnabled,
        basemapServerConf,
        basemapHttpConf,
        hasIpv6: fs.existsSync('/proc/net/if_inet6'),
    });

    fs.writeFileSync(path.join(nginxDir, 'nginx.conf'), rendered);
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
    main();
}
