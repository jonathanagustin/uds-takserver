// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Load the TAKX desktop in a real browser, the way a person would: open the
// bridge's start page, follow its redirect to noVNC, wait until noVNC has
// connected and drawn the remote screen, and save a screenshot.
//
//   node browser_probe.js URL SCREENSHOT.png
//
// URL is the bridge, for example http://127.0.0.1:16080/ behind exec_relay.py.
// Needs the 'playwright' module and its Chromium. Prints JSON facts; exit
// status 0 only when noVNC reports a connection and its canvas has the size
// of the remote desktop and is not blank.
const { chromium } = require('playwright');

(async () => {
  const [url, shot] = process.argv.slice(2);
  if (!url || !shot) {
    console.error('usage: node browser_probe.js URL SCREENSHOT.png');
    process.exit(2);
  }
  const facts = { url, status: 'failed' };
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    const sockets = [];
    page.on('websocket', (socket) => sockets.push(socket.url()));
    await page.goto(url, { waitUntil: 'load' });
    await page.waitForURL(/vnc\.html\?autoconnect=1/, { timeout: 20000 });
    facts.landed_on = page.url();
    await page.waitForFunction(
      () => document.documentElement.classList.contains('noVNC_connected'), null, { timeout: 60000 });
    await page.waitForFunction(
      () => { const c = document.querySelector('#noVNC_container canvas'); return c && c.width >= 1024; },
      null, { timeout: 30000 });
    await page.waitForTimeout(3000);
    facts.websockets = sockets;
    facts.canvas = await page.evaluate(() => {
      const canvas = document.querySelector('#noVNC_container canvas');
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      let min = 255; let max = 0;
      for (let i = 0; i < pixels.length; i += 4 * 7) {
        const luma = Math.round((299 * pixels[i] + 587 * pixels[i + 1] + 114 * pixels[i + 2]) / 1000);
        if (luma < min) min = luma;
        if (luma > max) max = luma;
      }
      return { width: canvas.width, height: canvas.height, luma_min: min, luma_max: max };
    });
    facts.title = await page.title();
    await page.screenshot({ path: shot });
    const ok = facts.canvas.width >= 1024 && facts.canvas.luma_max - facts.canvas.luma_min > 16
      && sockets.some((socket) => socket.endsWith('/websockify'));
    facts.status = ok ? 'ok' : 'failed';
  } catch (error) {
    facts.error = String(error);
    try { await (await browser.contexts()[0].pages())[0].screenshot({ path: shot }); } catch (ignored) { /* no page */ }
  } finally {
    await browser.close();
  }
  console.log(JSON.stringify(facts, null, 1));
  process.exit(facts.status === 'ok' ? 0 : 1);
})();
