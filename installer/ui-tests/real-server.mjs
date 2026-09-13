import assert from "node:assert/strict";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "playwright";

// Use only an isolated installer configured with purpose-made fixture assets.
// Matching the complete asset list makes accidental production downloads fail
// before an installation mutation can be sent.
const url = process.env.RIFF_INSTALLER_URL;
const expectedIds = process.env.RIFF_INSTALLER_EXPECT_ASSET_IDS?.split(",").filter(Boolean).sort();
assert(url && expectedIds?.length, "Provide RIFF_INSTALLER_URL and RIFF_INSTALLER_EXPECT_ASSET_IDS for the isolated test server");
assert(["127.0.0.1", "localhost"].includes(new URL(url).hostname), "The acceptance server must be loopback-only");
const evidence = fileURLToPath(new URL(".artifacts/", import.meta.url));
const staticRoot = fileURLToPath(new URL("../priv/static/", import.meta.url));
await mkdir(evidence, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1060, height: 790 } });
const errors = [];
page.on("pageerror", error => errors.push(error.message));
await page.addInitScript(() => {
  window.installerCspViolations = [];
  document.addEventListener("securitypolicyviolation", event => window.installerCspViolations.push({ directive: event.violatedDirective, blocked: event.blockedURI }));
});

try {
  const html = await page.goto(url);
  assert.match(html.headers()["content-security-policy"], /frame-ancestors 'none'/);
  await page.locator("#primary-action:not([disabled])").waitFor();
  const token = await page.locator('meta[name="riff-installer-token"]').getAttribute("content");
  assert(token && token !== "RIFF_INSTALLER_TOKEN");
  const api = async (endpoint, method = "GET") => page.request.fetch(new URL(`/api/${endpoint}`, url).href, {
    method,
    headers: { "X-Riff-Installer-Token": token, ...(method === "POST" ? { Origin: new URL(url).origin, "Content-Type": "application/json" } : {}) },
    ...(method === "POST" ? { data: {} } : {}),
  });
  const before = await (await api("status")).json();
  assert.deepEqual(before.assets.map(asset => asset.id).sort(), expectedIds);
  assert.equal(before.app.available, false, "This acceptance fixture verifies models-only truthfulness");
  assert.equal((await page.request.get(new URL("/api/status", url).href)).status(), 403);
  for (const name of ["installer.js", "installer.css", "flip-face.svg"]) {
    const response = await page.request.get(new URL(`/${name}`, url).href);
    assert.equal(response.status(), 200);
    assert.deepEqual(await response.body(), await readFile(path.join(staticRoot, name)), `${name} is served from this implementation`);
  }
  const initialOperation = before.state === "idle" ? "install" : "retry";
  const requested = page.waitForResponse(response => response.url().endsWith(`/api/${initialOperation}`) && response.request().method() === "POST");
  await page.locator("#primary-action").click();
  assert.equal((await requested).status(), 200);
  await page.waitForFunction(() => ["prepared", "error"].includes(document.body.dataset.state) && !document.querySelector("#primary-action").disabled);
  const installed = await (await api("status")).json();
  assert.equal(installed.state, "prepared", installed.error || installed.message);
  assert(installed.assets.every(asset => asset.state === "verified" && asset.downloaded_bytes === asset.total_bytes));
  assert.equal(installed.progress.completed_files, installed.progress.total_files);
  assert.equal(await page.getByRole("button", { name: "Open Riff", exact: true }).count(), 0);
  assert.equal((await api("open", "POST")).status(), 409);
  assert.equal(await page.locator("#download-progress").getAttribute("aria-valuenow"), "100");
  assert(await page.locator("#download-progress").evaluate(element => {
    const fill = element.querySelector("#progress-fill");
    return Math.abs(fill.getBoundingClientRect().width - element.getBoundingClientRect().width) < 1;
  }), "Verified completion draws a full progress bar immediately");
  await page.locator("#download-details").evaluate(element => { element.open = true; });
  await page.screenshot({ path: path.join(evidence, "elixir-prepared-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 320, height: 760 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: path.join(evidence, "elixir-prepared-mobile.png"), fullPage: true });

  const retried = page.waitForResponse(response => response.url().endsWith("/api/retry") && response.request().method() === "POST");
  await page.locator("#primary-action").click();
  assert.equal((await retried).status(), 200);
  await page.waitForFunction(() => ["prepared", "error"].includes(document.body.dataset.state) && !document.querySelector("#primary-action").disabled);
  const rechecked = await (await api("status")).json();
  assert.equal(rechecked.state, "prepared", rechecked.error || rechecked.message);
  assert(rechecked.assets.every(asset => asset.state === "verified"));
  assert.deepEqual(await page.evaluate(() => window.installerCspViolations), []);
  assert.deepEqual(errors, []);
  await writeFile(path.join(evidence, "elixir-validation.json"), JSON.stringify({
    checked_at: new Date().toISOString(),
    initial_state: before.state,
    initial_operation: initialOperation,
    asset_ids: expectedIds,
    total_bytes: installed.progress.total_bytes,
    verified_files: installed.progress.completed_files,
    state: installed.state,
    retry_state: rechecked.state,
    authenticated_status: true,
    source_bytes_verified: true,
    open_unavailable_until_app_ready: true,
    csp_violations: [],
    browser_errors: errors,
  }, null, 2));
  console.log(`PASS Actual Elixir installer: ${installed.progress.completed_files} fixture files verified through setup, retained-file retry, authenticated routes, app readiness guard, desktop/mobile layout and CSP`);
} finally { await browser.close(); }
