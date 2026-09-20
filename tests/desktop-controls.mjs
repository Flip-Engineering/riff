import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
let stopped = false, cancellation, preferencesRequest, needsRepair = false, retried = false;
let taskOverride = null, sourceMode = false, rejectSettings = true;
function desktop(system) {
  return { ...system, desktop: !sourceMode, managed: !sourceMode, prerequisites: [],
    pending: needsRepair ? { version: "0.0.2" } : null,
    task: taskOverride || { action: "update", status: needsRepair ? "failed" : stopped ? "cancelled" : "running", message: needsRepair ? "Prepare this update again" : stopped ? "Update preparation stopped" : "Preparing the update" } };
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
  await page.route("**/api/system/engine", async route => {
    await route.fulfill({ status: rejectSettings ? 400 : 200,
      json: rejectSettings ? { error: "Choose another music engine" } : { saved: true } });
  });
  await page.route("**/api/system/preferences", async route => {
    preferencesRequest = route.request().postDataJSON();
    await route.fulfill({ status: 400, json: { error: "Update preferences could not be saved" } });
  });
  await page.goto(process.env.RIFF_URL);
  await page.locator("#play:not([disabled])").waitFor();
  await page.locator("#title").fill("Keep this draft");
  const before = await page.evaluate(() => formRecipe());
  await page.getByLabel("Studio settings", { exact: true }).click();
  assert(await page.locator("#setup-engine").isHidden(), "Desktop setup does not offer a compiler action");
  assert(!/builds|compiler|curl/.test(await page.locator("#setup-description").textContent()));
  assert.equal(await page.locator("#update-task-slot #system-feedback").count(), 1);
  assert.equal(await page.locator("#system-progress").getAttribute("aria-label"), "Update progress");
  assert(await page.locator("#check-update").isDisabled());
  assert(await page.locator("#setup-description").isHidden());
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
  sourceMode = true;
  taskOverride = { action: "setup", status: "running", message: "Downloading music models" };
  await page.evaluate(() => refresh());
  assert.equal(await page.locator("#engine-task-slot #system-feedback").count(), 1);
  assert.equal(await page.locator("#system-progress").getAttribute("aria-label"), "Setup progress");
  assert.equal(await page.locator("#system-log").innerText(), "Setup log");
  assert(await page.locator("#setup-description").isVisible());
  sourceMode = false;
  taskOverride = { action: "check", status: "done", message: "Riff is up to date" };
  await page.evaluate(() => refresh());
  assert.equal(await page.locator("#update-task-slot #system-feedback").count(), 1);
  assert(await page.locator("#system-progress").isHidden());
  assert(await page.locator("#system-log").isHidden(), "An update check has no setup log");
  assert(await page.locator("#check-update").isEnabled());
  await page.getByRole("button", { name: "Use engine settings", exact: true }).click();
  await page.locator("#system-error:not([hidden])").waitFor();
  assert.equal(await page.locator("#system-error").innerText(), "Choose another music engine");
  assert.equal(await page.locator("#system-error").getAttribute("role"), "alert");
  await page.evaluate(() => refresh());
  assert(await page.locator("#system-error").isVisible(), "Polling must preserve the request error inside the dialog");
  rejectSettings = false;
  await page.getByRole("button", { name: "Use engine settings", exact: true }).click();
  assert(await page.locator("#system-error").isHidden(), "A retry clears its prior request error");
  const checkedBefore = await page.locator("#automatic-checks").isChecked();
  const downloadsBefore = await page.locator("#automatic-downloads").isChecked();
  // A rejected save can roll back before Playwright's setChecked postcondition.
  // Check the submitted intent and the eventual rollback, not a transient state.
  await page.locator("#automatic-checks").click();
  await page.waitForFunction(() => document.querySelector("#system-error").textContent === "Update preferences could not be saved");
  assert.deepEqual(preferencesRequest, { automatic_checks: !checkedBefore, automatic_downloads: downloadsBefore });
  assert(await page.locator("#system-error").isVisible());
  assert.equal(await page.locator("#automatic-checks").isChecked(), checkedBefore);
  assert.equal(await page.locator("#automatic-downloads").isChecked(), downloadsBefore);
  assert(await page.locator("#automatic-checks").isEnabled());
  assert(await page.locator("#automatic-downloads").isEnabled());
  assert.deepEqual(await page.evaluate(() => formRecipe()), before);
  assert.deepEqual(errors, []);
  console.log("PASS Desktop update recovery, operation-specific status, dialog errors and draft preservation");
} finally { await browser.close(); }
