import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"], {
  env: { ...process.env, RIFF_QUEUE_FIXTURE: "1", RIFF_MULTI_TRACK_FIXTURE: "1" },
});
let diagnostics = "";
server.stderr.on("data", data => diagnostics += data);
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", data => { output += data; if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
  server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});
const browser = await chromium.launch({ headless: process.env.RIFF_BROWSER_HEADED !== "1", executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, reducedMotion: "no-preference" });
const errors = [];
page.on("pageerror", error => errors.push(error.message));
const call = (route, body) => page.evaluate(async ({ route, body }) => {
  const response = await fetch(route, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json", "X-Riff-Request": "1" }, body: body ? JSON.stringify(body) : undefined });
  const result = await response.json(); if (!response.ok) throw new Error(result.error); return result;
}, { route, body });
mkdirSync("test-results", { recursive: true });
try {
  await page.goto(fixture.url);
  await page.locator("#play:not([disabled])").waitFor();
  await page.locator('[name="creation-mode"][value="lyrics"]').check();
  await page.locator("#lyrics").fill("Keep this composition exactly as I write it.");
  const draft = await page.evaluate(() => formRecipe());
  await page.locator("#take-comparison > summary").click();
  const a = await page.locator("#compare-a").inputValue(), b = await page.locator("#compare-b").inputValue();
  assert(a && b && a !== b);
  await page.locator("#play").click();
  await page.evaluate(() => { audio.currentTime = 3; });
  await page.locator('[data-compare="b"]').click();
  await page.waitForFunction(id => selected?.id === id && !audio.paused && audio.currentTime >= 3 && audio.currentTime < 4.5, b);
  await page.locator("#play").click();
  const paused = await page.evaluate(() => audio.currentTime);
  await page.locator('[data-compare="a"]').click();
  await page.waitForFunction(id => selected?.id === id, a);
  assert(await page.evaluate(() => audio.paused));
  assert(Math.abs(await page.evaluate(() => audio.currentTime) - paused) < .1);
  await page.locator("#compare-start").fill("1");
  await page.locator("#compare-end").fill("2.5");
  await page.locator("#compare-loop").click();
  await page.evaluate(() => { audio.currentTime = 2.35; });
  await page.locator("#play").click();
  await page.waitForFunction(() => audio.currentTime >= 1 && audio.currentTime < 2);
  await page.locator("#play").click();
  assert.deepEqual(await page.evaluate(() => formRecipe()), draft);
  await page.locator(".comparison-changes > summary").click();
  await page.locator("#compare-changes").getByText("Length", { exact: true }).waitFor();
  console.log("PASS A/B preserves the listening position and play state; passage looping and differences leave the draft unchanged");

  await page.locator("#open-sound-view").click();
  await page.locator("#sound-view-dialog[open]").waitFor();
  assert(await page.locator("#sound-view-canvas").evaluate(canvas => canvas.width > 1000 && canvas.height > 400));
  await page.screenshot({ path: "test-results/sound-view-desktop.png" });
  await page.locator("#sound-view-seek").fill("4");
  await page.waitForFunction(() => Math.abs(audio.currentTime - 4) < .1);
  await page.keyboard.press("Escape");
  assert(await page.locator("#sound-view-dialog").isHidden());
  assert.equal(await page.evaluate(() => document.activeElement.id), "open-sound-view");
  console.log("PASS Immersive sound view shares the audio clock, seeks, closes with Escape, and restores keyboard focus");

  const base = { title: "Queue take", style: "test", lyrics: "Original words", mode: "lyrics", seed: "9", max_seconds: 12, steps: 8, cot: "off" };
  const active = await call("/api/generations", { ...base, title: "Active fixture", style: "queue fixture hold" });
  await page.waitForFunction(id => state.live?.id === id, active.id);
  const pending = [];
  for (const title of ["First queued", "Second queued", "Third queued"]) pending.push(await call("/api/generations", { ...base, title }));
  await page.locator(`[data-queued-job="${pending[2].id}"]`).waitFor();
  const up = page.locator(`[data-job="${pending[2].id}"][data-queue-move="up"]`);
  await up.focus(); await page.keyboard.press("Enter");
  await page.waitForFunction(id => document.querySelectorAll("[data-queued-job]")[1]?.dataset.queuedJob === id, pending[2].id);
  assert.equal(await page.evaluate(() => document.activeElement.dataset.job), pending[2].id);
  await page.keyboard.press("Enter");
  await page.waitForFunction(id => document.querySelector("[data-queued-job]")?.dataset.queuedJob === id, pending[2].id);
  assert.equal(await up.getAttribute("aria-disabled"), "true");
  assert.equal((await call(`/api/jobs/${active.id}`)).status, "running");
  await page.screenshot({ path: "test-results/studio-comparison-queue.png", fullPage: true });
  await call(`/api/jobs/${active.id}/cancel`, {});
  await page.waitForFunction(ids => ids.every(id => state.jobs.some(job => job.id === id && job.status === "done")), pending.map(job => job.id));
  assert.equal(await page.locator("#compare-a").inputValue(), a);
  assert.deepEqual(await page.evaluate(() => formRecipe()), draft);
  console.log("PASS Keyboard queue ordering retains focus and leaves the active take, comparison and draft intact");

  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: `test-results/studio-flow-${width}.png`, fullPage: true });
    await page.locator("#open-sound-view").click();
    assert(await page.evaluate(() => document.querySelector("#sound-view-dialog").scrollWidth <= innerWidth));
    await page.screenshot({ path: `test-results/sound-view-${width}.png` });
    await page.locator("#close-sound-view").click();
  }
  assert.deepEqual(errors, []);
  console.log("PASS Comparison and immersive playback fit narrow screens without browser errors");
} finally {
  await browser.close(); const stopped = once(server, "exit"); server.kill("SIGTERM"); await stopped;
}
