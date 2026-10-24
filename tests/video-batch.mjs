import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const base = process.env.RIFF_URL;
const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1365, height: 1050 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
const frameRoute = /\/api\/video-exports\/[a-f0-9]+\/frames$/;
const contentType = "application/x-riff-video-frames";
function frames(body) {
  const result = [];
  for (let offset = 0; offset < body.length;) {
    const size = body.readUInt32BE(offset); offset += 4;
    result.push(body.subarray(offset, offset + size)); offset += size;
  }
  return result;
}
function packet(values) {
  return Buffer.concat(values.flatMap(value => { const size = Buffer.alloc(4); size.writeUInt32BE(value.length); return [size, value]; }));
}
try {
  await page.goto(`${base}/?recording=${process.env.RIFF_TEST_TRACK}#studio`);
  await page.locator("#download-video:not([disabled])").waitFor();
  await page.locator("#download-video").click();
  await page.locator("#video-options summary").click();
  for (const [key, value] of Object.entries({ width: 320, height: 248, fps: 12 })) await page.locator(`#video-${key}`).fill(String(value));
  await page.locator("#video-selection").selectOption("passage");
  await page.locator("#video-start").fill("0"); await page.locator("#video-end").fill("2");
  let temporary = false, prefix = false, lost = false;
  let largestFrameBytes = 0;
  const requests = [];
  await page.route(frameRoute, async route => {
    const request = route.request(), index = Number(request.headers()["x-riff-frame"]);
    assert.equal(request.headers()["content-type"], contentType);
    const body = request.postDataBuffer(), values = frames(body);
    largestFrameBytes = Math.max(largestFrameBytes, ...values.map(value => value.length));
    requests.push({ index, count: values.length });
    if (!temporary) {
      temporary = true;
      return route.fulfill({ status: 503, json: { error: "Temporary upload failure" } });
    }
    if (!prefix) {
      prefix = true;
      const partial = packet(values.slice(0, 3));
      const accepted = await page.request.post(request.url(), {
        headers: { ...request.headers(), "content-length": String(partial.length) }, data: partial,
      });
      assert(accepted.ok()); assert.equal((await accepted.json()).received, 3);
      return route.abort("failed");
    }
    if (index === 16 && !lost) {
      lost = true;
      const response = await route.fetch(); assert(response.ok());
      assert.equal((await response.json()).received, 24);
      return route.abort("failed");
    }
    return route.continue();
  });
  const creation = page.waitForResponse(response => /\/api\/tracks\/[a-f0-9]+\/video-exports$/.test(response.url()) && response.status() === 201);
  const download = page.waitForEvent("download", { timeout: 90000 }); download.catch(() => {});
  await page.locator("#render-video").click();
  const job = await (await creation).json();
  mkdirSync("test-results", { recursive: true });
  const file = "test-results/batched-visualization.mp4";
  await (await download).saveAs(file);
  await page.waitForFunction(() => videoExport === null);
  assert(await page.locator("#video-error").isHidden(), await page.locator("#video-error").textContent());
  assert.deepEqual(requests, [{ index: 0, count: 16 }, { index: 0, count: 16 }, { index: 3, count: 13 }, { index: 16, count: 8 }]);
  assert(temporary && prefix && lost);
  const complete = await (await page.request.get(`${base}/api/video-exports/${job.id}`)).json();
  assert.equal(complete.status, "done"); assert.equal(complete.received, 24);
  const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-of", "json", file]));
  const visual = probe.streams.find(stream => stream.codec_type === "video");
  assert.equal(Number(visual.nb_frames), 24); assert.equal(visual.avg_frame_rate, "12/1");
  assert.equal(visual.width, 320); assert.equal(visual.height, 248);
  await page.unroute(frameRoute);
  console.log("PASS Batched export retries temporary errors, resumes an accepted prefix and recovers lost full acknowledgements without duplicate frames");

  let release;
  const held = new Promise(resolve => release = resolve);
  let firstBatch;
  const arrived = new Promise(resolve => firstBatch = resolve);
  await page.route(frameRoute, async route => {
    firstBatch(); await held; await route.abort().catch(() => {});
  });
  await page.locator("#render-video").click();
  await arrived;
  const cancelledId = await page.evaluate(() => videoExport.id);
  await page.locator("#cancel-video").click();
  await page.waitForFunction(() => videoExport === null);
  release();
  assert.equal((await (await page.request.get(`${base}/api/video-exports/${cancelledId}`)).json()).status, "cancelled");
  await page.unroute(frameRoute);
  const budget = largestFrameBytes * 2 + 8;
  await page.route(`${base}/api/capabilities`, async route => {
    const response = await route.fetch(), value = await response.json();
    value.video_frame_batch.max_bytes = budget;
    await route.fulfill({ response, json: value });
  });
  let admitted = 0, boundedBatches = 0;
  await page.route(frameRoute, async route => {
    const request = route.request(), body = request.postDataBuffer();
    if (request.headers()["content-type"] === contentType) {
      const values = frames(body);
      assert(body.length <= budget, "Batch respects the advertised byte budget");
      assert(values.length <= 16);
      admitted += values.length; boundedBatches++;
    } else admitted++;
    await route.continue();
  });
  const boundedDownload = page.waitForEvent("download", { timeout: 90000 }); boundedDownload.catch(() => {});
  await page.locator("#render-video").click();
  await boundedDownload;
  await page.waitForFunction(() => videoExport === null);
  assert.equal(admitted, 24); assert(boundedBatches > 2);
  await page.unroute(frameRoute);
  await page.unroute(`${base}/api/capabilities`);
  console.log("PASS Browser batches honor the byte budget without dropping the frame held for the next batch");
  await page.route(frameRoute, route => route.fulfill({ status: 400, json: { error: "Owned upload failure" } }));
  const failedCreation = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/video-exports"));
  await page.evaluate(() => document.querySelector("#video-form").requestSubmit());
  const failedJob = await (await failedCreation).json();
  await page.waitForFunction(() => videoExport === null);
  assert.match(await page.locator("#video-error").textContent(), /Owned upload failure/);
  assert.equal((await (await page.request.get(`${base}/api/video-exports/${failedJob.id}`)).json()).status, "cancelled");
  await page.unroute(frameRoute);
  console.log("PASS Fatal upload failure interrupts active draw/encode waits and cancels the owned server job");
  assert.deepEqual(errors, []);
  console.log("PASS Cancellation aborts a pending frame batch and cancels its owned server encoder");
} finally { await browser.close(); }
