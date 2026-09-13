import assert from "node:assert/strict";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

// Keep this trusted local fixture on an explicit graphics backend. Modern
// headless Chromium can otherwise silently exercise only the Canvas fallback.
const args = process.platform === "darwin" ? ["--use-angle=metal"]
  : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];
const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE, args });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
try {
  await page.addInitScript(() => {
    window.artworkGpu = { draws: 0, errors: [] };
    const original = WebGL2RenderingContext.prototype.drawElements;
    WebGL2RenderingContext.prototype.drawElements = function (...args) {
      const result = original.apply(this, args);
      window.artworkGpu.draws++;
      const error = this.getError();
      if (error) window.artworkGpu.errors.push(error);
      return result;
    };
  });
  await page.goto(process.env.RIFF_URL);
  await page.locator("#play:not([disabled])").waitFor();
  const result = await page.evaluate(() => {
    const canvas = document.createElement("canvas"), context = canvas.getContext("2d");
    canvas.width = 880; canvas.height = 680;
    const seed = "15961", motion = [.6, .7, .5, .2, .1, .4, .3, .2];
    const draw = (time, appearance) => {
      drawSeedArtwork(context, canvas.width, canvas.height, seed, time, motion, appearance);
      return canvas.toDataURL();
    };
    const before = artworkGpu.draws, first = draw(1), later = draw(7);
    const plain = draw(1, { ...RiffArtwork.defaults, color: 0, texture: 0 });
    const still = draw(1, { ...RiffArtwork.defaults, motion: 0 });
    const sought = draw(1);
    canvas.width = 1920; canvas.height = 1080; draw(7);
    const gl = document.createElement("canvas").getContext("webgl2");
    const info = gl?.getExtension("WEBGL_debug_renderer_info");
    return { draws: artworkGpu.draws - before, errors: artworkGpu.errors,
      moves: first !== later, material: plain !== first, motionControl: still !== first,
      deterministic: sought === first, renderer: info && gl.getParameter(info.UNMASKED_RENDERER_WEBGL) };
  });
  assert.equal(result.draws, 6, "Each frame uses the actual shader renderer");
  assert.deepEqual(result.errors, []);
  for (const key of ["moves", "material", "motionControl", "deterministic", "renderer"]) assert(result[key], key);
  await page.locator("[data-visual=sound]").click();
  await page.waitForFunction(() => soundMotion?.frames.length);
  await page.evaluate(() => { audio.currentTime = 3; synchronizeSoundField(); });
  mkdirSync("test-results", { recursive: true });
  await page.locator("#sound-field").screenshot({ path: "test-results/artwork-webgl.png" });
  assert.deepEqual(errors, []);
  console.log(`PASS Actual WebGL rendering, material controls, motion, resizing and seeking: ${result.renderer}`);
} finally { await browser.close(); }
