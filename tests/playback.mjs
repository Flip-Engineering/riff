import assert from "node:assert/strict";
import { launch } from "./browser-launch.mjs";

const browser = await launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
try {
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  let releaseAnalysis;
  const analysisGate = new Promise(resolve => releaseAnalysis = resolve);
  await page.route("**/visualization", async route => { await analysisGate; await route.continue(); });
  await page.goto(`${process.env.RIFF_URL}/?recording=${process.env.RIFF_TEST_TRACK}#studio`);
  await page.locator("#play:not([disabled])").waitFor();
  assert.equal(await page.locator("[data-visual=sound]").getAttribute("aria-pressed"), "true");
  assert.equal(await page.locator("[data-visual=art]").getAttribute("aria-pressed"), "false");
  assert(await page.locator("#sound-field").isVisible(), "Live sound is visible by default");
  await page.evaluate(() => {
    window.liveFrames = [];
    const draw = drawSeedArtwork;
    drawSeedArtwork = (...args) => {
      if (args[5]?.[0] > .01) liveFrames.push({time: args[4], clock: audio.currentTime, level: args[5][0], at: performance.now()});
      return draw(...args);
    };
  });
  await page.locator("#play").click();
  // The analysis route is still held, so live frames arriving here started without it.
  await page.waitForFunction(() => liveFrames.length >= 3 && audio.currentTime > .2);
  const motion = await page.evaluate(() => ({frames: liveFrames.slice(0, 3), pending: soundMotion === null, data: liveSound.read(audio.currentTime)}));
  assert(motion.pending, "The full-recording analysis is still deliberately blocked");
  assert(motion.frames.every(frame => Math.abs(frame.time - frame.clock) < .05), "Live frames use the current audio clock");
  assert(motion.frames[2].time > motion.frames[0].time);
  assert(motion.data.some(value => value > .01), "The actual playing audio drives the motion");
  releaseAnalysis();
  await page.waitForFunction(() => soundMotion?.frames.length);
  await page.unroute("**/visualization");
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
  console.log("PASS Live audio motion starts before delayed analysis and follows the playback clock");
  console.log("PASS Recording plays on one click; playback failures retain the actual error instead of a retry instruction");
} finally { await browser.close(); }
