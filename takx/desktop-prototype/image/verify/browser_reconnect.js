// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// What a person sees when the bridge ends a WebSocket at its session limit:
// open the desktop in a real browser, stay on the page, and record every
// WebSocket the page opens and every state noVNC reports.
//
//   node browser_reconnect.js URL SCREENSHOT.png SECONDS
//
// URL is the bridge behind exec_relay.py, which adds the token to every
// request as Authservice does. The bridge has to run with a limit well below
// SECONDS (TAKX_AUTH_WS_MAX_SECONDS). Exit status 0 only when the page opened
// at least three WebSockets, each earlier one was closed, and noVNC is
// connected at the end: the start page asks noVNC to reconnect by itself.
// Needs the 'playwright' module and its Chromium. Prints JSON facts.
const { chromium } = require('playwright');

(async () => {
  const [url, shot, seconds] = process.argv.slice(2);
  if (!url || !shot || !Number(seconds)) {
    console.error('usage: node browser_reconnect.js URL SCREENSHOT.png SECONDS');
    process.exit(2);
  }
  const facts = { url, status: 'failed', websockets: [] };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    const started = Date.now();
    const elapsed = () => Math.round((Date.now() - started) / 100) / 10;
    page.on('websocket', (socket) => {
      const entry = { opened_at_s: elapsed() };
      facts.websockets.push(entry);
      socket.on('close', () => { entry.closed_at_s = elapsed(); });
    });
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForFunction(
      () => document.documentElement.classList.contains('noVNC_connected'), null, { timeout: 60000 });
    facts.first_connected_at_s = elapsed();
    facts.states = [];
    const until = Date.now() + Number(seconds) * 1000;
    while (Date.now() < until) {
      const classes = await page.evaluate(() => document.documentElement.className);
      const found = classes.match(/noVNC_(connected|connecting|disconnected|reconnecting|disconnecting)/);
      const state = found ? found[1] : classes;
      if (!facts.states.length || facts.states[facts.states.length - 1].state !== state) {
        facts.states.push({ at_s: elapsed(), state });
      }
      await page.waitForTimeout(200);
    }
    facts.connected_at_the_end = await page.evaluate(
      () => document.documentElement.classList.contains('noVNC_connected'));
    facts.canvas = await page.evaluate(() => {
      const canvas = document.querySelector('#noVNC_container canvas');
      return canvas ? { width: canvas.width, height: canvas.height } : null;
    });
    await page.screenshot({ path: shot });
    const closed = facts.websockets.slice(0, -1).every((socket) => socket.closed_at_s !== undefined);
    facts.status = facts.websockets.length >= 3 && closed && facts.connected_at_the_end ? 'ok' : 'failed';
  } catch (error) {
    facts.error = String(error);
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(facts, null, 1));
  process.exit(facts.status === 'ok' ? 0 : 1);
})();
