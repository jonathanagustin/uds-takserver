// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { transformNginxConf } from '../.github/container-and-chart/cloudtak/render-nginx-conf.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CLOUDTAK_DIR = path.resolve(HERE, '..', '.github', 'container-and-chart', 'cloudtak');
const SSO_CONF = fs.readFileSync(path.join(CLOUDTAK_DIR, 'nginx-sso.conf'), 'utf8');
const SSO_HEAD_CONF = fs.readFileSync(path.join(CLOUDTAK_DIR, 'nginx-sso-head.conf'), 'utf8');

const SAMPLE_NGINX_CONF = [
    'user nginx;',
    'worker_processes auto;',
    'pid /run/nginx.pid;',
    'error_log /var/log/nginx/error.log notice;',
    '',
    'http {',
    '    access_log /var/log/nginx/access.log main;',
    '    server_tokens off;',
    '',
    '    server {',
    '        listen 5000;',
    '        listen [::]:5000;',
    '        location ~ ^/api {',
    '            proxy_pass http://127.0.0.1:4999;',
    '        }',
    '    }',
    '}',
    '',
].join('\n');

test('base transform drops user and moves pid, logs, and temp paths under nginxDir', () => {
    const out = transformNginxConf(SAMPLE_NGINX_CONF, { nginxDir: '/tmp/nginx' });
    assert.ok(!/^user /m.test(out));
    assert.ok(out.includes('pid /tmp/nginx/nginx.pid;\n'));
    assert.ok(out.includes('error_log /dev/stderr warn;\n'));
    assert.ok(out.includes(
        '    access_log /dev/stdout main;\n'
        + '    client_body_temp_path /tmp/nginx/client_body;\n'
        + '    proxy_temp_path /tmp/nginx/proxy;\n'
        + '    fastcgi_temp_path /tmp/nginx/fastcgi;\n'
        + '    uwsgi_temp_path /tmp/nginx/uwsgi;\n'
        + '    scgi_temp_path /tmp/nginx/scgi;\n'
    ));
    assert.ok(out.includes('        listen [::]:5000;\n'));
});

test('oidcEnabled injects SSO location before ^/api and sub_filter after server_tokens off', () => {
    const out = transformNginxConf(SAMPLE_NGINX_CONF, {
        oidcEnabled: true,
        ssoConf: SSO_CONF,
        ssoHeadConf: SSO_HEAD_CONF,
    });
    const ssoIdx = out.indexOf('location ^~ /api/login/oidc');
    const apiIdx = out.indexOf('location ~ ^/api');
    assert.ok(ssoIdx !== -1 && apiIdx !== -1 && ssoIdx < apiIdx);

    const tokensIdx = out.indexOf('    server_tokens off;\n');
    const headIdx = out.indexOf("sub_filter '</head>'");
    assert.ok(tokensIdx !== -1 && headIdx > tokensIdx);

    assert.throws(
        () => transformNginxConf('http {\n    access_log /var/log/nginx/access.log main;\n}\n', {
            oidcEnabled: true,
            ssoConf: SSO_CONF,
            ssoHeadConf: SSO_HEAD_CONF,
        }),
        /missing SSO location/,
    );
});

test('basemapEnabled injects basemap server block before ^/api and http block after scgi_temp_path', () => {
    const basemapServer = [
        '        location ^~ /uds-basemap/ {',
        '            proxy_pass https://tile.example/;',
        '        }',
        '',
    ].join('\n');
    const basemapHttp = '    proxy_cache_path /tmp/nginx/basemap/cache keys_zone=uds_basemap:10m;\n';

    const out = transformNginxConf(SAMPLE_NGINX_CONF, {
        basemapEnabled: true,
        basemapServerConf: basemapServer,
        basemapHttpConf: basemapHttp,
    });
    const mapIdx = out.indexOf('location ^~ /uds-basemap/');
    const apiIdx = out.indexOf('location ~ ^/api');
    assert.ok(mapIdx !== -1 && apiIdx !== -1 && mapIdx < apiIdx);

    const scgiIdx = out.indexOf('scgi_temp_path /tmp/nginx/scgi;\n');
    const cacheIdx = out.indexOf('proxy_cache_path /tmp/nginx/basemap/cache');
    assert.ok(scgiIdx !== -1 && cacheIdx > scgiIdx);

    assert.throws(
        () => transformNginxConf('http {\n    access_log /var/log/nginx/access.log main;\n}\n', {
            basemapEnabled: true,
            basemapServerConf: basemapServer,
            basemapHttpConf: basemapHttp,
        }),
        /missing basemap location/,
    );
});

test('hasIpv6 false strips IPv6 listen directives while preserving IPv4 listen directives', () => {
    const withoutV6 = transformNginxConf(SAMPLE_NGINX_CONF, { hasIpv6: false });
    assert.ok(!withoutV6.includes('listen [::]:5000'));
    assert.ok(withoutV6.includes('        listen 5000;\n'));

    const withV6 = transformNginxConf(SAMPLE_NGINX_CONF, { hasIpv6: true });
    assert.ok(withV6.includes('        listen [::]:5000;\n'));
    assert.ok(withV6.includes('        listen 5000;\n'));
});

test('combined SSO, basemap, and IPv6 disabled preserves both injections and valid structure', () => {
    const basemapServer = '        location ^~ /uds-basemap/ {\n            proxy_pass https://tile.example/;\n        }\n';
    const basemapHttp = '    proxy_cache_path /tmp/nginx/basemap/cache keys_zone=uds_basemap:10m;\n';
    const out = transformNginxConf(SAMPLE_NGINX_CONF, {
        oidcEnabled: true,
        ssoConf: SSO_CONF,
        ssoHeadConf: SSO_HEAD_CONF,
        basemapEnabled: true,
        basemapServerConf: basemapServer,
        basemapHttpConf: basemapHttp,
        hasIpv6: false,
    });

    assert.ok(!/^user /m.test(out));
    assert.ok(!out.includes('listen [::]:5000'));
    assert.ok(out.includes('location ^~ /api/login/oidc'));
    assert.ok(out.includes('location ^~ /uds-basemap/'));
    assert.ok(out.indexOf('location ^~ /api/login/oidc') < out.indexOf('location ^~ /uds-basemap/'));
    assert.ok(out.indexOf('location ^~ /uds-basemap/') < out.indexOf('location ~ ^/api'));
    assert.ok(out.includes('proxy_cache_path /tmp/nginx/basemap/cache'));
    assert.ok(out.includes("sub_filter '</head>'"));
});
