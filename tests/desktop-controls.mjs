import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
let stopped = false, cancellation, needsRepair = false, retried = false;
function desktop(system) {
  return { ...system, desktop: true, managed: true, prerequisites: [],
    pending: needsRepair ? { version: "0.0.2" } : null,
    task: { action: "update", status: needsRepair ? "failed" : stopped ? "cancelled" : "running", message: needsRepair ? "Prepare this update again" : stopped ? "Update preparation stopped" : "Preparing the update" } };
}
try {
  for (const path of ["state", "system"]) await page.route(`**/api/${path}`, async route => {
    const response = await route.fetch(), data = await response.json();
    if (path === "state") data.maintenance = desktop(data.maintenance);
    await route.fulfill({ response, json: path === "state" ? data : desktop(data) });
  });
  await page.route("**/api/system/cancel", async route => {
    cancellation = route.request(); stopped = true;
    await route.fulfill({ json: { status: "stopping" } });
  });
  await page.route("**/api/system/update", async route => {
    assert.equal(route.request().method(), "POST");
    retried = true; needsRepair = false; stopped = false;
    await route.fulfill({ status: 202, json: { status: "running" } });
  });
  await page.goto(process.env.RIFF_URL);
  await page.locator("#play:not([disabled])").waitFor();
  await page.locator("#title").fill("Keep this draft");
  const before = await page.evaluate(() => formRecipe());
  await page.getByLabel("Studio settings", { exact: true }).click();
  assert(await page.locator("#setup-engine").isHidden(), "Desktop setup does not offer a compiler action");
  assert(!/builds|compiler|curl/.test(await page.locator("#setup-description").textContent()));
  await page.locator("#engine-threads").fill("7");
  await page.getByRole("button", { name: "Stop preparation", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#system-task").textContent === "Update preparation stopped");
  assert.equal(cancellation.method(), "POST");
  assert(await page.locator("#cancel-update").isHidden());
  assert.equal(await page.locator("#engine-threads").inputValue(), "7", "Cancelling an update preserves unsaved settings edits");
  needsRepair = true;
  await page.evaluate(() => refresh());
  await page.getByRole("button", { name: "Prepare again", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#system-task").textContent === "Preparing the update");
  assert(retried, "A failed prepared update has a graphical recovery action");
  assert.deepEqual(await page.evaluate(() => formRecipe()), before);
  assert.deepEqual(errors, []);
  console.log("PASS Desktop update cancellation is visible, preserves the draft, and removes compiler setup from the installed flow");
} finally { await browser.close(); }
