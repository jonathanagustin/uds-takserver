// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// The deployed TAKX desktop package, seen from a real browser (Playwright's
// Chromium): the sign-in gate on the tenant gateway and the desktop behind it.
//
//   node deploy_browser.cjs gate USERFILE OUTDIR allowed|denied [STATEFILE]
//   node deploy_browser.cjs drive STATEFILE OUTDIR STEPS.json
//
// gate    Open TAKX_DESKTOP_URL with an empty profile. The gateway must send
//         the browser to Keycloak. A WebSocket to /websockify opened before
//         the sign-in must fail. Then sign in as the user of USERFILE
//         ({"username","password"}, mode 600, see deploy_devusers.py).
//           allowed  the browser must land on noVNC, noVNC must report a
//                    connection over wss://<host>/websockify, and its canvas
//                    must hold a non-blank remote screen. The page is saved
//                    as OUTDIR/desktop-through-browser.png and, with
//                    STATEFILE, the session cookies are stored (mode 600).
//           denied   the browser must not get the noVNC page, and neither a
//                    request for /vnc.html nor a WebSocket to /websockify
//                    made with that signed-in session may succeed.
// drive   Open the desktop with the stored session (the sign-in page is never
//         shown), wait for noVNC and run STEPS.json: a list of
//         {"do": "wait"|"move"|"click"|"drag"|"wheel"|"key"|"shot"|"mark"|"reload", ...}
//         with remote-desktop coordinates. "mark" also notes how many
//         WebSocket frames have arrived so far, and "reload" loads the start
//         page again and notes where the browser ends up. Mouse and keyboard go through
//         noVNC and the gateway like a person's. With TAKX_BROWSER_HEADED=1
//         the browser is a kiosk window on $DISPLAY, to be recorded there.
//
// Nothing secret is printed or drawn: the user name and password are read
// from the file and typed into the Keycloak form, no screenshot is taken
// while that form is filled, and URLs are logged without their query. Facts
// go to OUTDIR/<command>.json and to standard output.
//
// The browser gets no proxy (--no-proxy-server): it reaches the cluster
// gateways through /etc/hosts and nothing else. Certificate checks stay on.
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

const base = (process.env.TAKX_DESKTOP_URL || 'https://takx.uds.dev/').replace(/\/+$/, '');
const host = new URL(base).host;
const bare = (url) => { try { const u = new URL(url); return `${u.origin}${u.pathname}`; } catch (e) { return String(url).split('?')[0]; } };
const now = () => new Date().toISOString();

async function launch(headed) {
  const args = ['--no-proxy-server'];
  if (headed) args.push('--kiosk', '--window-position=0,0', '--window-size=1920,1080', '--disable-infobars');
  return chromium.launch({ headless: !headed, args });
}

// A WebSocket to the bridge path from the page; resolves to what happened.
function tryWebSocket(page) {
  return page.evaluate((url) => new Promise((resolve) => {
    const result = { url, opened: false, bytes: 0 };
    let socket;
    try { socket = new WebSocket(url, ['binary']); } catch (error) { resolve({ ...result, error: String(error) }); return; }
    const done = (how) => { result.outcome = how; try { socket.close(); } catch (e) { /* closed */ } resolve(result); };
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => { result.opened = true; };
    socket.onmessage = (event) => { result.bytes += event.data.byteLength || event.data.length || 0; };
    socket.onerror = () => done('error');
    socket.onclose = (event) => { result.close_code = event.code; done(result.opened ? 'closed after open' : 'closed before open'); };
    setTimeout(() => done(result.opened ? 'open' : 'timeout'), 8000);
  }), `wss://${host}/websockify`);
}

function watch(page, facts) {
  facts.navigations = [];
  facts.websockets = [];
  page.on('response', (response) => {
    const request = response.request();
    if (request.isNavigationRequest() && request.frame() === page.mainFrame()) {
      facts.navigations.push({ status: response.status(), url: bare(response.url()) });
    }
  });
  page.on('websocket', (socket) => {
    const record = { url: bare(socket.url()), frames_received: 0, opened_utc: now() };
    facts.websockets.push(record);
    socket.on('framereceived', () => { record.frames_received += 1; });
    socket.on('close', () => { record.closed_utc = now(); });
  });
}

async function novncFacts(page) {
  await page.waitForFunction(
    () => document.documentElement.classList.contains('noVNC_connected'), null, { timeout: 60000 });
  await page.waitForFunction(
    () => { const c = document.querySelector('#noVNC_container canvas'); return c && c.width >= 1024; },
    null, { timeout: 30000 });
  await page.waitForTimeout(3000);
  return page.evaluate(() => {
    const canvas = document.querySelector('#noVNC_container canvas');
    const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let min = 255; let max = 0;
    for (let i = 0; i < pixels.length; i += 4 * 7) {
      const luma = Math.round((299 * pixels[i] + 587 * pixels[i + 1] + 114 * pixels[i + 2]) / 1000);
      if (luma < min) min = luma;
      if (luma > max) max = luma;
    }
    const box = canvas.getBoundingClientRect();
    return { width: canvas.width, height: canvas.height, luma_min: min, luma_max: max,
      shown: { x: box.x, y: box.y, width: box.width, height: box.height } };
  });
}

async function gate(userFile, out, expect, stateFile) {
  const user = JSON.parse(fs.readFileSync(userFile, 'utf8'));
  const facts = { command: 'gate', url: `${base}/`, user: path.basename(userFile, '.json'), expect, status: 'failed', started_utc: now() };
  const browser = await launch(false);
  let filled = false;
  let page;
  try {
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
    page = await context.newPage();
    watch(page, facts);

    // 1. No session: the gateway must hand the browser to Keycloak.
    await page.goto(`${base}/`, { waitUntil: 'load' });
    await page.getByLabel('Username or email').waitFor({ timeout: 30000 });
    facts.unauthenticated = {
      landed_on: bare(page.url()),
      title: await page.title(),
      redirect_chain: facts.navigations.slice(),
      bridge_page_seen: await page.evaluate(() => Boolean(document.querySelector('#noVNC_container'))),
    };
    await page.screenshot({ path: path.join(out, 'signin-page-before-typing.png') });
    // 2. The bridge path itself, without a session. In a second empty
    //    profile: Authservice keeps one pending sign-in per session cookie,
    //    and a second request with the cookie of the first replaces its
    //    state, after which the sign-in ends in "your session has expired".
    const probeContext = await browser.newContext();
    const probe = await probeContext.newPage();
    const probeFacts = {};
    watch(probe, probeFacts);
    await probe.goto(`${base}/`, { waitUntil: 'load' });
    facts.unauthenticated.websocket = await tryWebSocket(probe);
    facts.unauthenticated.websockets_seen_by_browser = probeFacts.websockets;
    await probeContext.close();

    // 3. Sign in. No screenshot from here until the form is gone.
    const before = facts.navigations.length;
    await page.getByLabel('Username or email').fill(user.username);
    await page.getByLabel('Password', { exact: true }).fill(user.password);
    filled = true;
    await page.getByRole('button', { name: 'Sign In' }).click();
    facts.signed_in_utc = now();

    if (expect === 'allowed') {
      await page.waitForURL(/vnc\.html\?autoconnect=1/, { timeout: 60000 });
      filled = false;
      facts.landed_on = bare(page.url());
      facts.canvas = await novncFacts(page);
      facts.title = await page.title();
      facts.after_signin = facts.navigations.slice(before);
      await page.screenshot({ path: path.join(out, 'desktop-through-browser.png') });
      const live = facts.websockets.filter((socket) => socket.url === `wss://${host}/websockify` && socket.frames_received > 0);
      facts.cookies = (await context.cookies()).map((cookie) => ({
        name: cookie.name, domain: cookie.domain, httpOnly: cookie.httpOnly, secure: cookie.secure, sameSite: cookie.sameSite }));
      if (stateFile) {
        fs.writeFileSync(stateFile, '', { mode: 0o600 });
        await context.storageState({ path: stateFile });
        fs.chmodSync(stateFile, 0o600);
      }
      const ok = facts.unauthenticated.landed_on.startsWith('https://sso.') && !facts.unauthenticated.websocket.opened
        && live.length > 0 && facts.canvas.width >= 1024 && facts.canvas.luma_max - facts.canvas.luma_min > 16;
      facts.status = ok ? 'ok' : 'failed';
    } else {
      // Wherever the browser ends up, it must not be the desktop.
      await page.waitForLoadState('networkidle', { timeout: 60000 });
      await page.waitForTimeout(3000);
      const onForm = await page.getByLabel('Password', { exact: true }).count();
      filled = onForm > 0;
      facts.landed_on = bare(page.url());
      facts.title = await page.title();
      facts.after_signin = facts.navigations.slice(before);
      facts.page_text = (await page.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ').slice(0, 400);
      facts.bridge_page_seen = await page.evaluate(() => Boolean(document.querySelector('#noVNC_container')));
      if (!filled) await page.screenshot({ path: path.join(out, 'refused-through-browser.png') });
      // The same signed-in browser asks for the bridge directly.
      const direct = await context.newPage();
      const directFacts = { navigations: [], websockets: [] };
      watch(direct, directFacts);
      const response = await direct.goto(`${base}/vnc.html?autoconnect=1&resize=scale&path=websockify`, { waitUntil: 'load' }).catch((error) => ({ error: String(error) }));
      await direct.waitForTimeout(4000);
      facts.direct_request = {
        final_status: response && response.status ? response.status() : null,
        error: response && response.error ? response.error : undefined,
        landed_on: bare(direct.url()),
        chain: directFacts.navigations,
        bridge_page_seen: await direct.evaluate(() => Boolean(document.querySelector('#noVNC_container'))),
        page_text: (await direct.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ').slice(0, 300),
        websockets_seen_by_browser: directFacts.websockets.length,
      };
      const directFilled = await direct.getByLabel('Password', { exact: true }).count();
      if (!directFilled) await direct.screenshot({ path: path.join(out, 'refused-direct-request.png') });
      facts.direct_request.websocket = await tryWebSocket(direct);
      const ok = !facts.bridge_page_seen && !facts.direct_request.bridge_page_seen
        && !facts.direct_request.websocket.opened && facts.websockets.every((socket) => socket.frames_received === 0)
        && directFacts.websockets.every((socket) => socket.frames_received === 0);
      facts.status = ok ? 'ok' : 'failed';
    }
  } catch (error) {
    facts.error = String(error).split('\n')[0];
    if (page) {
      facts.failed_on = bare(page.url());
      // Never a picture of a filled sign-in form.
      if (!filled) { try { await page.screenshot({ path: path.join(out, 'failure.png') }); } catch (e) { /* no page */ } }
      try { facts.failed_text = (await page.evaluate(() => document.body.innerText)).replace(/\s+/g, ' ').slice(0, 300); } catch (e) { /* no page */ }
    }
  } finally {
    await browser.close();
  }
  facts.finished_utc = now();
  return facts;
}

async function drive(stateFile, out, stepsFile) {
  const steps = JSON.parse(fs.readFileSync(stepsFile, 'utf8'));
  const headed = process.env.TAKX_BROWSER_HEADED === '1';
  const facts = { command: 'drive', url: `${base}/`, headed, status: 'failed', started_utc: now(), marks: [], shots: [] };
  const browser = await launch(headed);
  let page;
  try {
    const context = await browser.newContext({
      viewport: headed ? null : { width: 1920, height: 1080 }, storageState: stateFile });
    page = await context.newPage();
    watch(page, facts);
    await page.goto(`${base}/`, { waitUntil: 'load' });
    await page.waitForURL(/vnc\.html\?autoconnect=1/, { timeout: 60000 });
    facts.landed_on = bare(page.url());
    facts.signin_page_shown = facts.navigations.some((entry) => entry.url.includes('/login-actions/') || entry.url.endsWith('/openid-connect/auth') && entry.status === 200);
    facts.canvas = await novncFacts(page);
    facts.viewport = await page.evaluate(() => ({ width: window.innerWidth, height: window.innerHeight }));
    facts.desktop_visible_utc = now();
    // Remote-desktop coordinates to page coordinates (noVNC scales the canvas).
    const at = (x, y) => {
      const s = facts.canvas.shown;
      return [s.x + (x * s.width) / facts.canvas.width, s.y + (y * s.height) / facts.canvas.height];
    };
    for (const step of steps) {
      if (step.do === 'wait') {
        await page.waitForTimeout(step.ms);
      } else if (step.do === 'mark') {
        facts.marks.push({ utc: now(), text: step.text,
          frames_received: facts.websockets.map((socket) => socket.frames_received),
          sockets_closed: facts.websockets.filter((socket) => socket.closed_utc).length });
      } else if (step.do === 'reload') {
        // A new page load in the same profile: does the gateway still know the session?
        const before = facts.navigations.length;
        await page.goto(`${base}/`, { waitUntil: 'load' });
        await page.waitForTimeout(step.ms || 6000);
        facts.marks.push({ utc: now(), text: 'reload', landed_on: bare(page.url()), chain: facts.navigations.slice(before),
          bridge_page_seen: await page.evaluate(() => Boolean(document.querySelector('#noVNC_container'))) });
      } else if (step.do === 'move') {
        await page.mouse.move(...at(step.x, step.y), { steps: step.steps || 10 });
      } else if (step.do === 'click') {
        await page.mouse.move(...at(step.x, step.y), { steps: 8 });
        await page.waitForTimeout(250);
        await page.mouse.click(...at(step.x, step.y), { button: step.button || 'left', delay: 80 });
      } else if (step.do === 'drag') {
        await page.mouse.move(...at(step.x, step.y), { steps: 8 });
        await page.mouse.down({ button: step.button || 'left' });
        await page.mouse.move(...at(step.to_x, step.to_y), { steps: step.steps || 40 });
        await page.waitForTimeout(150);
        await page.mouse.up({ button: step.button || 'left' });
      } else if (step.do === 'wheel') {
        await page.mouse.move(...at(step.x, step.y), { steps: 4 });
        for (let i = 0; i < (step.times || 1); i += 1) {
          await page.mouse.wheel(0, step.delta);
          await page.waitForTimeout(step.pause || 200);
        }
      } else if (step.do === 'key') {
        await page.keyboard.press(step.key);
      } else if (step.do === 'shot') {
        const file = path.join(out, `${step.name}.png`);
        await page.screenshot({ path: file });
        facts.shots.push({ utc: now(), file });
      } else {
        throw new Error(`unknown step ${JSON.stringify(step)}`);
      }
    }
    facts.status = facts.websockets.some((socket) => socket.frames_received > 0) ? 'ok' : 'failed';
  } catch (error) {
    facts.error = String(error).split('\n')[0];
    if (page) { try { await page.screenshot({ path: path.join(out, 'drive-failure.png') }); } catch (e) { /* no page */ } }
  } finally {
    await browser.close();
  }
  facts.finished_utc = now();
  return facts;
}

(async () => {
  const [command, ...rest] = process.argv.slice(2);
  let facts;
  let name;
  if (command === 'gate' && rest.length >= 3 && ['allowed', 'denied'].includes(rest[2])) {
    fs.mkdirSync(rest[1], { recursive: true });
    facts = await gate(rest[0], rest[1], rest[2], rest[3]);
    name = `gate-${facts.user}`;
  } else if (command === 'drive' && rest.length === 3) {
    fs.mkdirSync(rest[1], { recursive: true });
    facts = await drive(rest[0], rest[1], rest[2]);
    name = `drive-${path.basename(rest[2], '.json')}`;
  } else {
    console.error('usage: node deploy_browser.cjs gate USERFILE OUTDIR allowed|denied [STATEFILE]\n       node deploy_browser.cjs drive STATEFILE OUTDIR STEPS.json');
    process.exit(2);
  }
  const text = JSON.stringify(facts, null, 1);
  fs.writeFileSync(path.join(rest[1], `${name}.json`), `${text}\n`);
  console.log(text);
  process.exit(facts.status === 'ok' ? 0 : 1);
})();
