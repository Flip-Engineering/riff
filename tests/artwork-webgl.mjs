import assert from "node:assert/strict";
import { mkdirSync, readFileSync } from "node:fs";
import { launch } from "./browser-launch.mjs";

// Keep this trusted local fixture on an explicit graphics backend. Modern
// headless Chromium can otherwise silently exercise only the Canvas fallback.
const args = process.platform === "darwin" ? ["--use-angle=metal"]
  : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"];
const browser = await launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE, args });
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
  for (const failure of ["unavailable", "exception"]) {
    const recovery = await browser.newPage();
    recovery.on("console", m => console.log("PAGE:", m.text()));
    await recovery.evaluate(failure => {
      window.graphicsProbe = { blocked: true, attempts: 0, contexts: [], draws: 0, clock: 0 };
      const now = performance.now.bind(performance);
      performance.now = () => now() + graphicsProbe.clock;
      const getContext = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (type, ...args) {
        if (type === "webgl2") {
          graphicsProbe.attempts++;
          if (graphicsProbe.blocked) {
            if (failure === "exception") throw new Error("Temporary context allocation failure");
            return null;
          }
        }
        const context = getContext.call(this, type, ...args);
        if (type === "webgl2" && context) graphicsProbe.contexts.push(context);
        return context;
      };
      const drawElements = WebGL2RenderingContext.prototype.drawElements;
      WebGL2RenderingContext.prototype.drawElements = function (...args) {
        graphicsProbe.draws++;
        return drawElements.apply(this, args);
      };
    }, failure);
    await recovery.addScriptTag({ content: readFileSync("web/artwork.js", "utf8") });
    const recovered = await recovery.evaluate(() => {
      const canvas = document.createElement("canvas"); canvas.width = 880; canvas.height = 680;
      window.drawRecoveryFrame = () => {
        drawSeedArtwork(canvas.getContext("2d"), 880, 680, "15961", 1, [.6, .7, .5, .2, .1, .4, .3, .2]);
        return canvas.toDataURL();
      };
      // Pin the virtual clock while graphics are unavailable so host load
      // between evaluation rounds cannot expire the 5 s probe throttle.
      graphicsProbe.clock -= performance.now();
      drawRecoveryFrame();
      graphicsProbe.clock -= performance.now();
      graphicsProbe.blocked = false;
      for (let i = 0; i < 5; i++) {
        graphicsProbe.clock -= performance.now();
        drawRecoveryFrame();
      }
      const throttledAttempts = graphicsProbe.attempts;
      // Expire the throttle: the retry probe runs and restores the renderer.
      graphicsProbe.clock += 5001;
      graphicsProbe.clock += 5001;
      window.recoveryPixels = drawRecoveryFrame();
      return { throttledAttempts, attempts: graphicsProbe.attempts, draws: graphicsProbe.draws };
    });
    const probeLog = await recovery.evaluate(() => window.__probeLog || []).catch(error => [["probe-log-read-failed", String(error)]]);
    console.log("PROBELOG " + JSON.stringify(probeLog));
    assert.equal(recovered.throttledAttempts, 1, "Unavailable graphics are not probed on every frame");
    assert.equal(recovered.attempts, 2, "Renderer retries a temporary initialization failure");
    assert.equal(recovered.draws, 1, "GPU rendering resumes in the same page without reloading");
    if (failure === "unavailable") {
      for (let cycle = 0; cycle < 2; cycle++) {
        await recovery.evaluate(() => {
          if (!graphicsProbe.lossExtension) {
            const gl = graphicsProbe.contexts[0];
            graphicsProbe.lossExtension = gl.getExtension("WEBGL_lose_context");
            graphicsProbe.losses = 0; graphicsProbe.restorations = 0;
            gl.canvas.addEventListener("webglcontextlost", () => graphicsProbe.losses++);
            gl.canvas.addEventListener("webglcontextrestored", () => graphicsProbe.restorations++);
          }
          graphicsProbe.lossExtension.loseContext();
        });
        await recovery.waitForFunction(count => graphicsProbe.losses === count, cycle + 1);
        const fallback = await recovery.evaluate(() => {
          const before = graphicsProbe.draws;
          drawRecoveryFrame();
          return graphicsProbe.draws === before;
        });
        assert(fallback, "Lost contexts use the Canvas fallback");
        await recovery.evaluate(() => graphicsProbe.lossExtension.restoreContext());
        await recovery.waitForFunction(count => graphicsProbe.restorations === count, cycle + 1);
        const restored = await recovery.evaluate(() => ({ pixels: drawRecoveryFrame() === recoveryPixels,
          attempts: graphicsProbe.attempts, contexts: graphicsProbe.contexts.length,
          uniqueContexts: new Set(graphicsProbe.contexts).size }));
        assert(restored.pixels, "Restored shaders reproduce the same artwork");
        assert.equal(restored.attempts, 3 + cycle, "Restoration reuses the original canvas and context");
        assert.equal(restored.contexts, 2 + cycle);
        assert.equal(restored.uniqueContexts, 1, "Restoration must not allocate replacement contexts");
      }
    }
    await recovery.close();
  }
  console.log("PASS Temporary GPU initialization failures retry without reload; repeated context restoration preserves pixels and reuses its canvas");
} finally { await browser.close(); }
