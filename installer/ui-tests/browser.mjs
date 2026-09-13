import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../priv/static/", import.meta.url));
const evidence = fileURLToPath(new URL(".artifacts/", import.meta.url));
await mkdir(evidence, { recursive: true });
const token = "test-installer-token-with-no-production-authority";
const files = [
  { id: "music", label: "YuE2 music model", state: "pending", downloaded_bytes: 0, total_bytes: 2_140_000_000 },
  { id: "decoder", label: "Sound decoder", state: "pending", downloaded_bytes: 0, total_bytes: 780_000_000 },
  { id: "writer", label: "Lyric writer", state: "pending", downloaded_bytes: 0, total_bytes: 430_000_000 },
];
const total = files.reduce((sum, file) => sum + file.total_bytes, 0);
let snapshot = { state: "idle", stage: "checking", app: { available: true, version: "test" }, progress: { downloaded_bytes: 0, total_bytes: total, completed_files: 0, total_files: files.length }, assets: structuredClone(files), can_retry: true };
let offline = false, forbidden = false, holdRead = false, releaseRead = null, heldReadReady = null, closeAfterOpen = false;
const requests = [], failures = [];

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://fixture.invalid");
  if (url.pathname.startsWith("/api/")) {
    requests.push({ path: url.pathname, method: req.method, token: req.headers["x-riff-installer-token"], origin: req.headers.origin });
    const send = (code, data) => { res.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" }); res.end(JSON.stringify(data)); };
    if (forbidden || req.headers["x-riff-installer-token"] !== token) return send(403, { error: "Unauthorized" });
    if (req.method === "POST" && req.headers.origin !== `http://127.0.0.1:${server.address().port}`) return send(403, { error: "Origin mismatch" });
    if (offline) return send(503, { error: "Temporarily unavailable" });
    if (url.pathname === "/api/status") {
      const response = structuredClone(snapshot);
      if (holdRead) {
        holdRead = false;
        await new Promise(resolve => { releaseRead = resolve; heldReadReady?.(); });
      }
      return send(200, response);
    }
    if (["/api/install", "/api/retry"].includes(url.pathname)) {
      snapshot = { ...snapshot, state: "installing", stage: "checking", error: null, message: null };
      return send(200, snapshot);
    }
    if (url.pathname === "/api/cancel") {
      snapshot = { ...snapshot, state: "cancelling", message: null };
      return send(200, snapshot);
    }
    if (url.pathname === "/api/open" && snapshot.state === "ready") return send(200, { ...snapshot, opened: true, closing: closeAfterOpen });
    return send(409, { error: "Not available yet" });
  }
  const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
  if (!["index.html", "installer.js", "installer.css", "flip-face.svg"].includes(name)) { res.writeHead(404); res.end(); return; }
  let body = await readFile(path.join(root, name));
  if (name === "index.html") body = body.toString().replace("RIFF_INSTALLER_TOKEN", token);
  const type = name.endsWith(".js") ? "text/javascript" : name.endsWith(".css") ? "text/css" : name.endsWith(".svg") ? "image/svg+xml" : "text/html";
  res.writeHead(200, { "Content-Type": type, "Cache-Control": "no-store" }); res.end(body);
});
await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
const url = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const context = await browser.newContext({ viewport: { width: 1060, height: 790 }, hasTouch: true, reducedMotion: "reduce" });
const page = await context.newPage();
page.on("pageerror", error => failures.push(error.message));
async function refresh() { await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange"))); }
async function state(value) { await page.waitForFunction(value => document.body.dataset.state === value, value); }
async function update(values) { snapshot = { ...snapshot, ...values }; await refresh(); await state(snapshot.state); }
async function screenshot(name) { await page.screenshot({ path: path.join(evidence, `${name}.png`), fullPage: true }); }
const checks = [];
function pass(name) { checks.push(name); console.log(`PASS ${name}`); }

try {
  await page.goto(url);
  await page.locator("#primary-action:not([disabled])").waitFor();
  assert.equal(await page.locator("#heading").textContent(), "Install Riff");
  assert.match(await page.locator("#download-summary").textContent(), /3\.4 GB/);
  assert.equal(await page.locator(".brand-r img").getAttribute("src"), "/flip-face.svg");
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement.id), "primary-action");
  const focus = await page.locator("#primary-action").evaluate(element => getComputedStyle(element).outlineStyle);
  assert.equal(focus, "solid");
  await page.keyboard.press("Tab");
  assert.equal(await page.evaluate(() => document.activeElement.id), "details-label");
  await page.keyboard.press("Enter");
  assert.equal(await page.locator("#download-details").getAttribute("open"), "");
  assert.equal(await page.locator("#asset-list li").count(), 3);
  await screenshot("desktop-idle");
  pass("Keyboard-first install and optional model details; established Riff logo");

  // Simulate an older read finishing after Install. It must not undo the new
  // state or briefly offer a second Install action.
  const readStarted = new Promise(resolve => { heldReadReady = resolve; });
  holdRead = true; await refresh(); await readStarted;
  await page.locator("#primary-action").click(); await state("installing");
  releaseRead(); releaseRead = null;
  await page.waitForTimeout(150);
  assert.equal(await page.locator("body").getAttribute("data-state"), "installing");
  assert.equal(requests.filter(req => req.path === "/api/install").length, 1);
  pass("Late status responses cannot overwrite a newly started setup");

  await update({ stage: "downloading", progress: { ...snapshot.progress, downloaded_bytes: 1_290_000_000 }, assets: [{ ...files[0], state: "downloading", downloaded_bytes: 1_290_000_000 }, files[1], files[2]], current_file: "music" });
  await page.waitForFunction(() => document.querySelector("#download-progress").getAttribute("aria-valuenow") === "38");
  assert.match(await page.locator("#progress-detail").textContent(), /1\.3 GB of 3\.4 GB/);
  assert.equal(await page.locator("#primary-action").isVisible(), false);
  assert.equal(await page.locator("#cancel-action").isEnabled(), true);
  await screenshot("desktop-downloading");
  await page.locator("#cancel-action").click(); await state("cancelling");
  assert.equal(await page.locator("#cancel-action").isDisabled(), true);
  await update({ state: "cancelled" });
  assert.match(await page.locator("#description").textContent(), /Verified downloads are kept/);
  assert.equal(await page.locator("#download-progress").getAttribute("aria-valuenow"), "38");
  await page.locator("#primary-action").click(); await state("installing");
  assert.equal(requests.filter(req => req.path === "/api/retry").length, 1);
  pass("Aggregate download progress, stop acknowledgement, preserved progress and continue");

  const malicious = '<img src=x onerror="window.injected=true"> Download interrupted. Check your connection and try again.';
  await update({ state: "error", error: malicious });
  assert.equal(await page.locator("#setup-message").textContent(), malicious);
  assert.equal(await page.locator("#setup-message img").count(), 0);
  assert.equal(await page.evaluate(() => window.injected), undefined);
  await page.setViewportSize({ width: 320, height: 760 });
  await screenshot("mobile-error");
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.locator("#primary-action").tap(); await state("installing");
  assert.equal(requests.filter(req => req.path === "/api/retry").length, 2);
  pass("Actionable error recovery, escaped server text and mobile touch layout");

  await update({ state: "installing", stage: "verifying", error: null, progress: { ...snapshot.progress, downloaded_bytes: total } });
  await page.waitForFunction(() => document.querySelector("#download-progress").getAttribute("aria-valuenow") === "100");
  assert.equal(await page.getByRole("button", { name: "Open Riff", exact: true }).count(), 0);
  await update({ state: "prepared", stage: "app_required", app: { available: false }, assets: files.map(file => ({ ...file, state: "verified", downloaded_bytes: file.total_bytes })) });
  assert.equal(await page.locator("#heading").textContent(), "Music models are ready");
  assert.equal(await page.getByRole("button", { name: "Open Riff", exact: true }).count(), 0);
  assert.match(await page.locator("#description").textContent(), /application package is needed/);
  await screenshot("mobile-models-prepared");
  await update({ state: "idle", app: { available: false } });
  assert.equal(await page.locator("#primary-action").textContent(), "Download music models");
  assert.match(await page.locator("#description").textContent(), /application is also needed/);
  pass("100% download and models-only completion never claim the app is installed");

  await update({ state: "ready", stage: "complete", app: { available: true }, app_url: "https://untrusted.example/never-navigate" });
  await page.locator("#primary-action").click();
  await page.waitForFunction(() => document.querySelector("#setup-message").textContent === "Riff is open.");
  assert.equal(requests.filter(req => req.path === "/api/open").length, 1);
  assert.equal(page.url(), `${url}/`, "Only the verified server opener runs; no navigation to a supplied URL");
  await screenshot("mobile-ready");
  pass("Open Riff requires ready state and invokes the authenticated application opener");

  offline = true; await refresh();
  await page.waitForFunction(() => !document.querySelector("#connection-message").hidden);
  assert.equal(await page.locator("#primary-action").isDisabled(), true);
  assert.match(await page.locator("#connection-message").textContent(), /Reconnecting/);
  offline = false; await refresh();
  await page.locator("#primary-action:not([disabled])").waitFor();
  forbidden = true; await refresh();
  await page.waitForFunction(() => document.querySelector("#connection-message").textContent.includes("expired"));
  assert.equal(await page.locator("#primary-action").isDisabled(), true);
  forbidden = false; await refresh();
  await page.locator("#primary-action:not([disabled])").waitFor();
  assert(requests.every(req => req.token === token));
  assert(requests.filter(req => req.method === "POST").every(req => req.origin === url));
  pass("Connection recovery and expired launch tokens disable unsafe repeated actions");

  await update({ state: "installing", progress: { total_bytes: 0, downloaded_bytes: 0 } });
  assert.equal(await page.locator("#download-progress").getAttribute("aria-valuenow"), null);
  const animation = await page.locator("#progress-fill").evaluate(element => getComputedStyle(element).animationName);
  assert.equal(animation, "none");
  assert.deepEqual(failures, []);
  pass("Unknown totals stay indeterminate; reduced motion is respected; no browser errors");
  closeAfterOpen = true;
  await update({ state: "ready", stage: "complete" });
  await page.locator("#primary-action").click();
  await page.waitForFunction(() => document.querySelector("#heading").textContent === "Riff is open");
  const completedRequests = requests.length;
  offline = true;
  await refresh();
  await page.waitForTimeout(4250);
  assert.equal(requests.length, completedRequests, "A finished installer must stop polling its exited VM");
  assert.equal(await page.locator("#primary-action").isDisabled(), true);
  assert.equal(await page.locator("#connection-message").isVisible(), false);
  assert.equal(await page.locator("#description").textContent(), "You can close this setup window.");
  await screenshot("mobile-open-finished");
  pass("Opening the app can close the installer VM without reconnect errors or repeated actions");
  await writeFile(path.join(evidence, "fixture-validation.json"), JSON.stringify({ checked_at: new Date().toISOString(), checks, requests: requests.map(({ token: _, ...request }) => request), browser_errors: failures }, null, 2));
} finally {
  releaseRead?.();
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
