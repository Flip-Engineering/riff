import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${process.env.RIFF_URL}/?recording=${process.env.RIFF_TEST_TRACK}#studio`);
  await page.locator("#play:not([disabled])").waitFor();
  await page.locator("#play").click();
  await page.waitForFunction(() => audio.currentTime > .2);
  await page.locator("#play").click();
  const result = await page.evaluate(async () => {
    const original = audio.play;
    let called = false;
    audio.play = () => {
      called = true;
      return Promise.reject(new DOMException("Playback fixture: source unavailable.", "NotSupportedError"));
    };
    try {
      const pending = togglePlay();
      const synchronous = called;
      await pending;
      return { synchronous, message: document.querySelector("#toast").textContent };
    } finally { audio.play = original; }
  });
  assert.equal(result.synchronous, true, "Play must reach the media element without an unrelated async preparation step");
  assert.equal(result.message, "Playback fixture: source unavailable.");
  assert.deepEqual(errors, []);
  console.log("PASS Recording plays on one click; playback failures retain the actual error instead of a retry instruction");
} finally { await browser.close(); }
