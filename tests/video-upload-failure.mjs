import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.RIFF_URL;
const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const frameRoute = /\/api\/video-exports\/[a-f0-9]+\/frames$/;
try {
  for (const transport of ["png", "h264"]) {
    const page = await browser.newPage();
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(`${base}/?recording=${process.env.RIFF_TEST_TRACK}#studio`);
    await page.locator("#download-video:not([disabled])").waitFor();
    await page.evaluate(transport => {
      preferredVideoTransport = async () => transport;
      window.uploadWorkers = [];
      const NativeWorker = Worker;
      window.Worker = class extends NativeWorker {
        constructor(...args) {
          super(...args);
          const record = { stopped: false }; uploadWorkers.push(record);
          const stop = this.terminate.bind(this);
          this.terminate = () => { record.stopped = true; stop(); };
        }
      };
      window.uploadCodecs = [];
      if (window.VideoEncoder) {
        const NativeEncoder = VideoEncoder;
        window.VideoEncoder = class extends NativeEncoder {
          constructor(...args) { super(...args); uploadCodecs.push(this); }
        };
      }
    }, transport);
    await page.locator("#download-video").click();
    await page.locator("#video-options summary").click();
    for (const [key, value] of Object.entries({ width: 320, height: 248, fps: 12 })) await page.locator(`#video-${key}`).fill(String(value));
    await page.locator("#video-selection").selectOption("passage");
    await page.locator("#video-end").fill("4");
    async function start() {
      const creation = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/video-exports"));
      await page.evaluate(() => document.querySelector("#video-form").requestSubmit());
      const job = await (await creation).json();
      assert.equal(job.transport, transport, "The requested real encoder must run");
      return job;
    }
    async function stopped(job, status) {
      await page.waitForFunction(() => videoExport === null);
      const saved = await (await page.request.get(`${base}/api/video-exports/${job.id}`)).json();
      assert.equal(saved.status, status);
      assert(await page.evaluate(() => uploadWorkers.every(worker => worker.stopped) && uploadCodecs.every(codec => codec.state === "closed")));
      return saved;
    }
    let temporary = true;
    const requests = [];
    await page.route(frameRoute, async route => {
      requests.push(Number(route.request().headers()["x-riff-frame"]));
      if (temporary) { temporary = false; return route.fulfill({ status: 503, json: { error: "Temporary upload fault" } }); }
      await route.continue();
    });
    const retriedDownload = page.waitForEvent("download");
    const retried = await start();
    await retriedDownload;
    assert.equal((await stopped(retried, "done")).received, retried.frames);
    assert.deepEqual(requests.slice(0, 2), [0, 0]);
    await page.unroute(frameRoute);

    await page.route(frameRoute, route => route.fulfill({ status: 400, json: { error: "Owned fatal upload fault" } }));
    const failed = await start();
    await stopped(failed, "cancelled");
    assert.match(await page.locator("#video-error").textContent(), /Owned fatal upload fault/);
    await page.unroute(frameRoute);
    const freshDownload = page.waitForEvent("download");
    const fresh = await start();
    await freshDownload;
    assert.equal((await stopped(fresh, "done")).received, fresh.frames);
    assert(await page.locator("#video-error").isHidden());

    await page.route(frameRoute, route => route.fulfill({ status: 503, json: { error: "Hold in retry" } }));
    const cancelled = await start();
    await page.waitForFunction(() => document.querySelector("#video-status").textContent === "Reconnecting…");
    await page.locator("#cancel-video").focus();
    await page.keyboard.press("Enter");
    await stopped(cancelled, "cancelled");
    await page.unroute(frameRoute);
    assert.deepEqual(errors, []);
    console.log(`PASS ${transport}: temporary upload retry, fatal failure then fresh retry, and keyboard cancellation during retry close every worker/codec`);
    await page.close();
  }
} finally { await browser.close(); }
