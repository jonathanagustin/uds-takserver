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
//   view LAT LON ZOOM           move the map (keyboard-free: map.jumpTo)
//   place NAME LAT LON          draw a point through the CloudTAK UI at the
//                               given position and name it NAME
//   find NAME [SECONDS]         wait until a feature with callsign NAME is
//                               in CloudTAK's map (the client's own store);
//                               prints what CloudTAK holds for it
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
  return { keycloak_form: form, first_run: firstRun, url: page.url(), ...(await whoami(page)) };
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
  } else throw new Error(`unknown command ${command}`);
  console.log(JSON.stringify(result));
  // Detach only: the browser belongs to connect-cloudtak.sh.
  process.exit(0);
})().catch((error) => {
  console.error(String(error && error.stack ? error.stack : error).split("\n").slice(0, 6).join("\n"));
  process.exit(1);
});
