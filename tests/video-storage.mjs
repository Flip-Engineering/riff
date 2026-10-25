// Provoke Chromium's real Blob backing-store error without exhausting host RAM.
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, chmodSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { chromium } from "playwright";

if (process.platform === "win32" || process.getuid?.() === 0) {
  console.log("SKIP Blob storage permission fault requires an unprivileged POSIX user");
  process.exit(0);
}
const root = mkdtempSync(join(tmpdir(), "riff-video-storage-"));
const profile = join(root, "profile");
const context = await chromium.launchPersistentContext(profile, {
  headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE,
  args: ["--blob-transport-by-file-trigger=1"],
});
const page = await context.newPage();
const base = process.env.RIFF_URL;
const blocked = [];
const errors = [];
page.on("pageerror", error => errors.push(error.message));
try {
  await page.goto(`${base}/?recording=${process.env.RIFF_TEST_TRACK}#studio`);
  await page.locator("#download-video:not([disabled])").waitFor();
  // Initialize file transport and prove that it is writable before the fault.
  assert.equal(await page.evaluate(async () => (await new Blob([new Uint8Array(1024 * 1024)]).arrayBuffer()).byteLength), 1024 * 1024);
  for (const name of readdirSync(join(profile, "Default", "blob_storage"))) {
    const path = join(profile, "Default", "blob_storage", name);
    chmodSync(path, 0o500); blocked.push(path);
  }
  assert(blocked.length, "The browser must really use disk-backed Blob storage");
  const storageRoot = join(profile, "Default", "blob_storage");
  chmodSync(storageRoot, 0o500); blocked.push(storageRoot);
  await page.evaluate(() => {
    const canvas = document.createElement("canvas"); canvas.width = 1024; canvas.height = 1024;
    const image = canvas.getContext("2d").createImageData(1024, 1024);
    for (let offset = 0; offset < image.data.length; offset += 65536) crypto.getRandomValues(image.data.subarray(offset, offset + 65536));
    window.storageFaultImage = image;
  });
  await page.locator("#download-video").click();
  await page.locator("#video-options summary").click();
  for (const [key, value] of Object.entries({ width: 1024, height: 1024, fps: 12 })) await page.locator(`#video-${key}`).fill(String(value));
  await page.locator("#video-selection").selectOption("passage");
  await page.locator("#video-end").fill("0.25");
  await page.evaluate(() => { preferredVideoTransport = async () => "png"; drawSeedArtwork = context => context.putImageData(storageFaultImage, 0, 0); });
  const creation = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/video-exports"));
  await page.evaluate(() => document.querySelector("#video-form").requestSubmit());
  const job = await (await creation).json();
  await page.waitForFunction(() => videoExport === null);
  assert.match(await page.locator("#video-error").textContent(), /video frame 1 .*NotReadableError/,
    JSON.stringify(await (await page.request.get(`${base}/api/video-exports/${job.id}`)).json()));
  console.log(`PASS Real Chromium backing-store failure: ${await page.locator("#video-error").textContent()}`);
  assert(await page.locator("#save-video").isHidden());
  const cancelled = await (await page.request.get(`${base}/api/video-exports/${job.id}`)).json();
  assert.equal(cancelled.status, "cancelled");
  assert.equal(cancelled.received, 0);

  for (const path of blocked.splice(0)) chmodSync(path, 0o700);
  const download = page.waitForEvent("download");
  await page.evaluate(() => document.querySelector("#video-form").requestSubmit());
  await download;
  await page.waitForFunction(() => videoExport === null);
  assert(await page.locator("#video-error").isHidden());
  assert.deepEqual(errors, []);
  console.log("PASS PNG storage failure cancels its server job; restoring storage lets a fresh export finish");
} finally {
  for (const path of blocked) chmodSync(path, 0o700);
  await context.close();
  rmSync(root, { recursive: true, force: true });
}
