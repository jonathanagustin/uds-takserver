// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Desktop prototype, connected run: drive CloudTAK in a real Chrome as the
// browser peer of the TAKX client. The browser is started by
// connect-cloudtak.sh (headed, on its own X display, DevTools port on
// loopback); every call of this script attaches to it, does one thing and
// detaches, so the browser session survives between calls.
//
//   signin USER PASSWORD_FILE   Keycloak sign-in for CloudTAK (first run:
//                               callsign = USER). Never run while a
//                               recording of the browser display is in
//                               progress: the Keycloak form is on screen.
//                               The password is read from the file and is
//                               not printed.
//   whoami                      CloudTAK's own answers for this session:
//                               GET /api/login, /api/marti/group, /api/server
//   shot FILE                   screenshot of the page
//   view LAT LON ZOOM           move the map (the map's own URL fragment)
//   place NAME LAT LON          a user's steps: Geometry Editing > Coordinate
//                               Input, type the name and the position,
//                               create the point, open it (radial menu >
//                               details), Share > Broadcast To All Users
//   find NAME [SECONDS]         a user's steps: the map's search box until
//                               its MAP FEATURES list has NAME, select it
//                               (the map flies to the feature), open its
//                               details panel; prints what the panel says
//   remove NAME                 find it, then radial menu > delete (used to
//                               clear the markers of an earlier take)
//
// The page is driven through the DevTools protocol, so the X pointer of the
// browser display does not move in a recording; typing and every reaction
// of the page are real. Radial menu offsets belong to CloudTAK 13.112.3;
// this is not a general UI driver.
//
// Output is one JSON object on stdout. Environment: CLOUDTAK_URL,
// CLOUDTAK_CDP (default http://127.0.0.1:9222).
const fs = require("fs");
const { chromium } = require("playwright");

const url = process.env.CLOUDTAK_URL || "https://map.uds.dev/";
const cdp = process.env.CLOUDTAK_CDP || "http://127.0.0.1:9222";

async function api(page, path) {
  return await page.evaluate(async (p) => {
    const token = localStorage.getItem("CapacitorStorage.token");
    const res = await fetch(p, { headers: { Authorization: `Bearer ${token}` } });
    const text = await res.text();
    let body = text;
    try {
      body = JSON.parse(text);
    } catch {
      // Not JSON: keep the text.
    }
    return { code: res.status, body };
  }, path);
}

async function whoami(page) {
  const me = await api(page, "/api/login");
  const groups = await api(page, "/api/marti/group");
  const server = await api(page, "/api/server");
  return {
    login: { code: me.code, email: me.body.email, access: me.body.access },
    groups: { code: groups.code, names: (groups.body?.data || []).map((g) => `${g.name}:${g.direction}`) },
    server: { code: server.code, connection_status: server.body.connection_status, url: server.body.url, version: server.body.version },
  };
}

async function signin(page, user, secretFile) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  // Either Keycloak's form (no session yet) or the map (session still valid).
  const username = page.getByLabel("Username or email");
  const canvas = page.locator("canvas.maplibregl-canvas");
  await username.or(canvas).first().waitFor({ state: "visible", timeout: 60000 });
  const form = await username.isVisible();
  if (form) {
    await username.fill(user);
    await page.getByLabel("Password").fill(fs.readFileSync(secretFile, "utf8").trim());
    await page.getByRole("button", { name: "Sign In" }).click();
  }
  await page.locator("canvas.maplibregl-canvas").waitFor({ state: "visible", timeout: 120000 });
  const firstRun = await page.getByText("Welcome to CloudTAK").waitFor({ state: "visible", timeout: 15000 }).then(() => true).catch(() => false);
  if (firstRun) {
    await page.getByPlaceholder("Enter your callsign").fill(user);
    await page.getByRole("button", { name: "Get Started" }).click();
  }
  // A new browser profile is asked for location and notification permission.
  await page.getByText("Skip for now").click({ timeout: 5000 }).catch(() => {});
  return { keycloak_form: form, first_run: firstRun, url: page.url(), ...(await whoami(page)) };
}

const pause = (page, ms) => page.waitForTimeout(ms);

async function centre(page) {
  const box = await page.locator("canvas.maplibregl-canvas").boundingBox();
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

// The feature under the centre of the map: radial menu, then one of its
// items. Offsets from the centre: details (-21, +67), delete (-21, -66).
async function radial(page, dx, dy) {
  const c = await centre(page);
  await page.mouse.click(c.x, c.y);
  await pause(page, 1500);
  await page.mouse.click(c.x + dx, c.y + dy);
  await pause(page, 1800);
}

async function panel(page) {
  const uid = (/\/cot\/([^#?]+)/.exec(page.url()) || [])[1] || null;
  const text = uid ? await page.evaluate(() => {
    const share = document.querySelector('[title="Share"]');
    let node = share;
    for (let i = 0; node && i < 6; i += 1) node = node.parentElement;
    return node ? node.innerText : "";
  }) : "";
  const lines = text.split("\n").map((line) => line.trim()).filter(Boolean);
  const after = (label) => { const i = lines.indexOf(label); return i >= 0 ? lines[i + 1] : null; };
  return { uid, name: lines[0] || null, type: after("TYPE"), location: after("LOCATION"), creator: after("CREATOR") };
}

async function view(page, lat, lon, zoom) {
  await page.evaluate((h) => { location.hash = h; }, `#${zoom}/${lat}/${lon}`);
  await pause(page, 2500);
  return { url: page.url() };
}

async function place(page, name, lat, lon) {
  await page.locator('[title="Geometry Editing"], [aria-label="Geometry Editing"]').first().click();
  await pause(page, 900);
  await page.getByText("Coordinate Input").click();
  await pause(page, 1200);
  const field = page.getByPlaceholder("Name");
  await field.click();
  await field.pressSequentially(name, { delay: 45 });
  await pause(page, 500);
  await page.locator('label:has-text("Coordinates") + div .cursor-pointer').first().click();
  await pause(page, 600);
  const position = page.locator('label:has-text("Coordinates") + div input[type=text]');
  await position.fill("");
  await position.pressSequentially(`${lat}, ${lon}`, { delay: 45 });
  await pause(page, 700);
  await position.press("Enter"); // creates the point; the map flies to it
  await pause(page, 4000);
  await radial(page, -21, 67);
  const created = await panel(page);
  if (created.name !== name) throw new Error(`the open feature is '${created.name}', not '${name}'`);
  await page.locator('[title="Share"]').first().click();
  await pause(page, 1500);
  await page.getByRole("button", { name: "Broadcast To All Users" }).click();
  const at = new Date().toISOString();
  await pause(page, 1500);
  return { placed: created, broadcast_clicked_utc: at };
}

async function find(page, name, seconds) {
  const deadline = Date.now() + seconds * 1000;
  for (;;) {
    await page.locator('[title="Search Button"]').first().click();
    await pause(page, 700);
    await page.keyboard.type(name, { delay: 35 });
    await pause(page, 2200);
    const listed = await page.evaluate((n) => {
      const text = document.body.innerText;
      const at = text.indexOf("MAP FEATURES");
      return at >= 0 && text.slice(at).split("\n").map((line) => line.trim()).includes(n);
    }, name);
    if (listed) break;
    await page.keyboard.press("Escape");
    await pause(page, 400);
    if (Date.now() > deadline) return { found: false, name };
  }
  const at = new Date().toISOString();
  await page.keyboard.press("Enter"); // the map flies to the feature
  await pause(page, 4000);
  const url = page.url();
  await radial(page, -21, 67);
  return { found: true, name, listed_utc: at, map_url: url, panel: await panel(page) };
}

async function remove(page, name) {
  const found = await find(page, name, 0);
  if (!found.found) return { removed: false, name };
  await page.locator('[title="Close Menu"], [aria-label="Close Menu"]').first().click().catch(() => {});
  await pause(page, 800);
  await radial(page, -21, -66);
  await page.getByRole("button", { name: /^(Delete|Yes|OK|Confirm)$/ }).first().click({ timeout: 2500 }).catch(() => {});
  await pause(page, 1000);
  return { removed: true, name, was: found.panel };
}

(async () => {
  const [command, ...args] = process.argv.slice(2);
  const browser = await chromium.connectOverCDP(cdp);
  const context = browser.contexts()[0];
  const page = context.pages()[0] || (await context.newPage());
  let result;
  if (command === "signin") result = await signin(page, args[0], args[1]);
  else if (command === "whoami") result = await whoami(page);
  else if (command === "shot") {
    await page.screenshot({ path: args[0] });
    result = { shot: args[0], url: page.url() };
  } else if (command === "view") result = await view(page, args[0], args[1], args[2]);
  else if (command === "place") result = await place(page, args[0], args[1], args[2]);
  else if (command === "find") result = await find(page, args[0], Number(args[1] || 30));
  else if (command === "remove") result = await remove(page, args[0]);
  else throw new Error(`unknown command ${command}`);
  console.log(JSON.stringify(result));
  // Detach only: the browser belongs to connect-cloudtak.sh.
  process.exit(0);
})().catch((error) => {
  console.error(String(error && error.stack ? error.stack : error).split("\n").slice(0, 6).join("\n"));
  process.exit(1);
});
