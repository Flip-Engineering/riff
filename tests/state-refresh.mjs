import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.RIFF_URL}/?recording=${process.env.RIFF_TEST_TRACK}#studio`);
  await page.locator("#play:not([disabled])").waitFor();
  await page.evaluate(async () => {
    clearTimeout(pollTimer);
    const original = api;
    const stale = await original("/api/state");
    let hold = true;
    api = (...args) => {
      if (args[0] === "/api/state" && hold) {
        hold = false;
        return new Promise(resolve => { window.releaseOldState = () => resolve(stale); });
      }
      return original(...args);
    };
    window.oldRefresh = refresh();
  });
  await page.locator("#style").fill("A quiet ensemble with warm reeds.");
  await page.getByRole("button", { name: "Save this sound", exact: true }).click();
  await page.getByLabel("Sound name", { exact: true }).fill("Retained sound");
  await page.getByRole("button", { name: "Save sound", exact: true }).click();
  await page.getByRole("button", { name: "Retained sound", exact: true }).waitFor();
  await page.evaluate(async () => { releaseOldState(); await oldRefresh; });
  assert.equal(await page.getByRole("button", { name: "Retained sound", exact: true }).count(), 1,
    "An older poll must not remove the sound returned by the post-save refresh");
  assert.equal(await page.locator("#style").inputValue(), "A quiet ensemble with warm reeds.");
  console.log("PASS A delayed pre-save state response cannot overwrite the saved sound or draft");
} finally { await browser.close(); }
