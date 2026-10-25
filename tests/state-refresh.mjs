import assert from "node:assert/strict";
import { launch } from "./browser-launch.mjs";

const browser = await launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
try {
  const page = await browser.newPage();
  await page.goto(`${process.env.RIFF_URL}/?recording=${process.env.RIFF_TEST_TRACK}#studio`);
  await page.locator("#play:not([disabled])").waitFor();
  await page.evaluate(async () => {
    clearTimeout(pollTimer);
    const original = api;
    const stale = await original("/api/state");
    const held = [];
    window.releaseState = async (index) => {
      const resolve = held[index];
      held[index] = null;
      if (index === 1) {
        // The post-save refresh must observe the committed preset.
        resolve(await original("/api/state"));
      } else {
        resolve(stale);
      }
    };
    window.heldStateCount = () => held.filter(Boolean).length;
    api = (...args) => {
      if (args[0].startsWith("/api/state") && held.length < 2) {
        return new Promise(resolve => held.push(resolve));
      }
      return original(...args);
    };
    window.oldRefresh = refresh();
  });
  await page.locator("#style").fill("A quiet ensemble with warm reeds.");
  await page.getByRole("button", { name: "Save this sound", exact: true }).click();
  await page.getByLabel("Sound name", { exact: true }).fill("Retained sound");
  await page.getByRole("button", { name: "Save sound", exact: true }).click();
  // The accepted sound must appear even while both state responses are held.
  await page.getByRole("button", { name: "Retained sound", exact: true }).waitFor({ timeout: 10000 });
  await page.waitForFunction(() => heldStateCount() === 2);
  assert(
    await page.evaluate(() => state.presets.some((preset) => preset.name === "Retained sound")),
    "The accepted sound must enter local state immediately",
  );
  // Release the post-save refresh first, then the older poll. The older
  // response must be discarded rather than removing the saved sound.
  await page.evaluate(() => releaseState(1));
  await page.waitForFunction(() => appliedRefreshRequest === refreshRequest);
  await page.evaluate(async () => { await releaseState(0); await oldRefresh; });
  assert.equal(await page.getByRole("button", { name: "Retained sound", exact: true }).count(), 1,
    "An older poll must not remove the sound returned by the post-save refresh");
  assert(
    await page.evaluate(() => state.presets.some((preset) => preset.name === "Retained sound")),
    "The stale response must not remove the saved sound from local state",
  );
  assert.equal(await page.locator("#style").inputValue(), "A quiet ensemble with warm reeds.");
  console.log("PASS Saved sound shows before state refresh, and a delayed pre-save response cannot remove it");
} finally { await browser.close(); }
