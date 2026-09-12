import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1365, height: 1050 }, reducedMotion: "reduce" });
const base = process.env.RIFF_URL;
const errors = [];
page.on("pageerror", error => errors.push(error.message));
mkdirSync("test-results", { recursive: true });
try {
  await page.goto(`${base}/?recording=${process.env.RIFF_TEST_TRACK}#studio`);
  await page.locator("#download-video").waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.querySelector("#download-video").disabled);
  const geometry = await page.evaluate(async () => {
    const canvas = document.createElement("canvas"); canvas.width = 440; canvas.height = 340;
    const context = canvas.getContext("2d");
    const seed = selected.recipe.seed;
    drawSeedArtwork(context, 440, 340, seed);
    const rest = context.getImageData(0, 0, 440, 340).data;
    const image = new Image();
    image.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(artSVG(seed));
    await image.decode(); context.setTransform(1, 0, 0, 1, 0, 0); context.drawImage(image, 0, 0, 440, 340);
    const original = context.getImageData(0, 0, 440, 340).data;
    let difference = 0;
    for (let i = 0; i < rest.length; i++) difference += Math.abs(rest[i] - original[i]);
    return difference / rest.length;
  });
  assert(geometry < 1, `Seed artwork changed: pixel MAE ${geometry}`);
  const response = await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 440; canvas.height = 340;
    const context = canvas.getContext("2d"), seed = selected.recipe.seed;
    const pixels = (seconds, motion) => { drawSeedArtwork(context, 440, 340, seed, seconds, motion); return canvas.toDataURL(); };
    const quiet = Array(8).fill(0), rest = pixels(0, quiet), later = pixels(45, quiet);
    const bands = [1, 2, 3, 4, 5, 6].map(band => { const value = [...quiet]; value[0] = .4; value[band] = .8; return pixels(2, value); });
    const seekAgain = pixels(2, [.4, .8, 0, 0, 0, 0, 0, 0]);
    const labels = [];
    const originalText = context.fillText.bind(context);
    context.fillText = (text, x, y) => { labels.push({ text, x, y, width: canvas.width, height: canvas.height }); originalText(text, x, y); };
    for (const [width, height] of [[640, 360], [360, 640], [440, 340]]) {
      canvas.width = width; canvas.height = height; drawSeedArtwork(context, width, height, seed);
    }
    return { silenceStill: rest === later, uniqueBands: new Set(bands).size, deterministic: bands[0] === seekAgain, labels };
  });
  assert(response.silenceStill && response.deterministic);
  assert.equal(response.uniqueBands, 6);
  assert.equal(response.labels.length, 3);
  for (const label of response.labels) {
    assert.equal(label.text, "riff.");
    assert(label.x < label.width * .12 && label.y > label.height * .85);
  }
  console.log("PASS Bass, voice, air, stereo and attacks shape distinct frames; silence stays still; deterministic seeking and corner branding work in every aspect ratio");
  await page.locator("[data-visual=sound]").click();
  await page.waitForFunction(() => soundMotion?.frames.length);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  await page.locator("#play").click();
  await page.waitForFunction(() => audio.currentTime > .3 && soundAnimation !== null);
  await page.locator("#play").click();
  await page.waitForFunction(() => soundAnimation === null);
  const frozen = await page.locator("#sound-field").evaluate(canvas => canvas.toDataURL());
  await page.waitForTimeout(120);
  assert.equal(await page.locator("#sound-field").evaluate(canvas => canvas.toDataURL()), frozen);
  await page.evaluate(() => { audio.currentTime = 2; });
  await page.waitForFunction(previous => document.querySelector("#sound-field").toDataURL() !== previous, frozen);
  console.log("PASS Original seed geometry and palette preserved; playback animates, pause freezes, seek redraws");

  await page.locator("#download-video").click();
  await page.locator("#video-options summary").click();
  await page.locator("#video-width").fill("320");
  await page.locator("#video-height").fill("248");
  await page.locator("#video-fps").fill("12");
  await page.emulateMedia({ reducedMotion: "reduce" });
  const recovered = new Set();
  const frameRoute = /\/api\/video-exports\/[a-f0-9]+\/frames$/;
  await page.route(frameRoute, async route => {
    const index = Number(route.request().headers()["x-riff-frame"]);
    if ([3, 5, 7].includes(index) && !recovered.has(index)) {
      recovered.add(index);
      if (index === 5) assert((await route.fetch()).ok(), "Deliver the frame before losing its response");
      if (index === 7) return route.fulfill({status:503,contentType:"application/json",body:JSON.stringify({error:"Temporary interruption"})});
      return route.abort("failed");
    }
    await route.continue();
  });
  const finished = page.waitForEvent("download", { timeout: 90000 });
  finished.catch(() => {});
  await page.locator("#render-video").click();
  await page.waitForFunction(() => videoExport?.id || !document.querySelector("#video-error").hidden);
  assert(await page.locator("#video-error").isHidden(), await page.locator("#video-error").textContent());
  const completedId = await page.evaluate(() => videoExport.id);
  const download = await finished;
  assert(download.suggestedFilename().endsWith(".mp4"));
  await download.saveAs("test-results/seed-visualization.mp4");
  await page.waitForFunction(() => videoExport === null);
  assert.equal(recovered.size, 3);
  const completed = await (await page.request.get(`${base}/api/video-exports/${completedId}`)).json();
  assert.equal(completed.status, "done"); assert.equal(completed.received, completed.frames);
  await page.unroute(frameRoute);
  console.log("PASS Interrupted frame uploads, lost acknowledgements and temporary server errors recover without duplicate frames");
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-of", "json", "test-results/seed-visualization.mp4"]));
  const video = probe.streams.find(stream => stream.codec_type === "video");
  const sound = probe.streams.find(stream => stream.codec_type === "audio");
  assert.equal(video.codec_name, "h264"); assert.equal(sound.codec_name, "aac");
  assert.equal(video.width, 320); assert.equal(video.height, 248);
  const expectedDuration = await page.evaluate(() => selected.audio.duration);
  assert(Math.abs(Number(sound.duration) - expectedDuration) < .04);
  assert(Math.abs(Number(video.duration) - expectedDuration) < 1 / 12);
  const expected = await page.evaluate(async () => {
    const motion = await visualizationFor(selected.id), canvas = document.createElement("canvas");
    canvas.width = 320; canvas.height = 248;
    drawSeedArtwork(canvas.getContext("2d"), 320, 248, selected.recipe.seed, 0, motionAt(motion, 0));
    return canvas.toDataURL().split(",")[1];
  });
  writeFileSync("test-results/seed-expected.png", Buffer.from(expected, "base64"));
  const raw = file => execFileSync("ffmpeg", ["-v", "error", "-i", file, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]);
  const a = raw("test-results/seed-expected.png"), b = raw("test-results/seed-visualization.mp4");
  assert.equal(a.length, b.length);
  const mae = a.reduce((total, value, i) => total + Math.abs(value - b[i]), 0) / a.length;
  assert(mae < 4, `MP4 differs from Live sound renderer: pixel MAE ${mae}`);
  console.log("PASS Real H.264/AAC MP4 contains complete recording; exported frame matches Live sound; reduced-motion preference does not disable requested video");
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert(await page.locator("#video-dialog").evaluate(dialog => dialog.scrollWidth <= dialog.clientWidth));
    await page.screenshot({ path: `test-results/video-export-${width}.png` });
  }
  await page.setViewportSize({ width: 1365, height: 1050 });
  await page.screenshot({ path: "test-results/video-export-desktop.png" });
  await page.locator("#render-video").click();
  await page.waitForFunction(() => videoExport?.id && document.querySelector("#video-progress").value > 0);
  const id = await page.evaluate(() => videoExport.id);
  await page.locator("#cancel-video").click();
  await page.waitForFunction(() => videoExport === null);
  assert.equal((await (await page.request.get(`${base}/api/video-exports/${id}`)).json()).status, "cancelled");
  let denied = 0;
  await page.route(frameRoute, route => { denied++; return route.fulfill({status:403,contentType:"application/json",body:JSON.stringify({error:"Access was denied."})}); });
  await page.locator("#render-video").click();
  await page.waitForFunction(() => videoExport === null && !document.querySelector("#video-error").hidden);
  assert.equal(denied, 1); assert.equal(await page.locator("#video-error").textContent(), "Access was denied.");
  await page.unroute(frameRoute);
  await page.locator("[data-close=video-dialog]").click();
  assert.equal(await page.locator("#download").getAttribute("href"), await page.evaluate(() => `/api/tracks/${selected.id}/audio?download=1`));
  assert.deepEqual(errors, []);
  console.log("PASS Video export cancellation, mobile layouts, preserved WAV download, and no browser errors");
} finally { await browser.close(); }
