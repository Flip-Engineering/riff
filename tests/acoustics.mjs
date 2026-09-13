import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

// Actual served studio, with tiny acoustic API responses at the browser edge.
// Real Store/Elixir/native fixture execution is test_acoustic_integration.py.
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-B", "-u", "tests/review_browser_server.py"]);
let diagnostics = "", browser;
server.stderr.on("data", value => diagnostics += value);
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", value => { output += value; if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
  server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});
const source = "riff-acoustic-v1:" + "a".repeat(64), savedJob = "b".repeat(32), failedJob = "c".repeat(32);
const upstream = ["lyrics", "style", "abc", "score_source", "mode", "cot", "max_seconds", "steps", "solver", "cfg_scale", "temperature", "seed", "refinement", "performance_source"];
let ready = true, compatible = true, sourceFailure = false, submitFailure = false, hold = null, arrived;
let changedBuild = false, count = 0, requests = [], jobs = [];
try {
  const track = await (await fetch(`${fixture.url}/api/tracks/${fixture.track_id}`)).json();
  const original = { mode: "lyrics", solver: "midpoint", cfg_scale: 1, temperature: 1, refinement: {},
    ...track.recipe, max_seconds: 12, seed: "0", acoustic_source: "", decoder: {},
    score_source: "", performance_source: "", render_mode: "sound" };
  const metadata = { id: source, artifact_id: source, title: "The kept sound", source_job_id: savedJob,
    duration: 12, decode_core_frames: 1024, decode_halo_frames: 16, vae_storage: 0,
    sample_rate: 48000, downsampling_ratio: 1920, compatible: true, inputs: original,
    decoder_controls: { type: "object", properties: { core_frames: { type: "integer", minimum: 1 },
      halo_frames: { type: "integer", minimum: 0 }, storage: { type: "integer", enum: [0, 1, 2, 3, 4, 5, 6] } } } };
  const proposal = { id: "d".repeat(32), track_id: fixture.track_id, created: 1789315200, status: "done",
    model: "fixture/producer", summary: "Keep the performance; give the audio another finish.", notes: "A preserved take.",
    keep_lyrics: true, source_seed: "0", source_recipe: original, revision: {},
    generation: { ...original, title: "Another finish", render_mode: "music", acoustic_source: source,
      decoder: { core_frames: 512, halo_frames: 0, storage: 0 } } };
  const initial = { ...original, title: "My unfinished draft", style: "a different musical direction", seed: "74", render_mode: "music" };
  jobs = [
    { id: savedJob, title: metadata.title, status: "synthesized", created: 1789315200, acoustic_source: source, acoustic_seconds: 12, performance_available: true },
    { id: failedJob, title: "Audio interrupted", status: "failed", error: "The audio could not finish.", created: 1789315201, acoustic_source: source, acoustic_seconds: 12, performance_available: true },
  ];
  browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
  console.log(`Browser ${browser.version()}`);
  const page = await browser.newPage({ viewport: { width: 1280, height: 1000 }, reducedMotion: "reduce" });
  const errors = []; page.on("pageerror", error => { errors.push(error.message); console.error("Browser error:", error.message); });
  await page.addInitScript(value => { if (!localStorage.getItem("riff.draft")) localStorage.setItem("riff.draft", JSON.stringify(value)); }, initial);
  await page.route("**/api/state", async route => {
    const response = await route.fetch(), next = await response.json();
    next.engine = { ...next.engine, ready, capabilities: { ...next.engine.capabilities, acoustic_checkpoint: true, acoustic_decode: true } };
    next.jobs = jobs; next.reviews = [proposal]; next.review_settings = { ...next.review_settings, enabled: true };
    next.tracks[0].acoustic_source = source;
    if (changedBuild) next.studio.build = "f".repeat(64);
    await route.fulfill({ response, json: next });
  });
  await page.route(`**/api/tracks/${fixture.track_id}`, async route => {
    const response = await route.fetch(), value = await response.json();
    value.recipe = { ...original, acoustic: metadata, performance: { frames: 300, sha256: "e".repeat(64) } };
    await route.fulfill({ response, json: value });
  });
  await page.route("**/api/acoustics/*", async route => {
    assert.equal(decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-1)), source);
    await route.fulfill({ status: sourceFailure ? 404 : 200, json: sourceFailure ? { error: "This saved sound is unavailable." } : { ...metadata, compatible } });
  });
  await page.route(`**/api/tracks/${fixture.track_id}/reviews`, route => route.fulfill({ json: { reviews: [proposal] } }));
  await page.route("**/api/generations", async route => {
    const payload = route.request().postDataJSON(); requests.push(payload);
    if (hold) { arrived?.(); await hold; }
    if (submitFailure) { submitFailure = false; await route.fulfill({ status: 400, json: { error: "Please try finishing this audio again." } }); return; }
    if (payload.acoustic_source) {
      assert.equal(payload.acoustic_source, source);
      for (const key of upstream) if (key in payload) assert.deepEqual(payload[key], original[key], key);
      for (const [key, value] of Object.entries(payload.decoder || {})) {
        assert(Number.isInteger(value)); assert(value >= (key === "core_frames" ? 1 : 0));
        if (key === "storage") assert(value <= 6);
      }
    }
    const id = (++count).toString(16).padStart(32, "0");
    const recipe = payload.acoustic_source ? { ...original, ...payload, render_mode: "music", acoustic: metadata } : payload;
    jobs.push({ id, title: recipe.title, created: 1789315201 + count, status: recipe.render_mode === "sound" ? "synthesized" : "queued",
      ...(recipe.render_mode === "sound" ? { acoustic_source: source, acoustic_seconds: 12, performance_available: true } : {}) });
    await route.fulfill({ status: 201, json: { id, status: "queued", recipe } });
  });
  const current = () => page.evaluate(() => formRecipe());
  const refresh = () => page.evaluate(() => refresh());
  const attached = () => page.waitForFunction(() => RiffAcoustics.ready() && formRecipe().acoustic_source);
  await page.goto(fixture.url);
  await page.waitForFunction(() => state.jobs.length >= 2 && selected);
  const before = await current();
  await page.evaluate(() => showView("library"));
  const finish = page.locator(`[data-finish-audio="${savedJob}"]`);
  assert.equal(await page.locator(`[data-finish-performance="${savedJob}"]`).count(), 0);
  assert.equal(await page.locator(`[data-finish-audio="${failedJob}"]`).count(), 1);
  await finish.focus(); await refresh(); await refresh();
  assert(await finish.evaluate(button => button === document.activeElement));
  await finish.click();
  await page.waitForFunction(() => state.jobs.length === 3);
  assert.deepEqual(requests.at(-1), { acoustic_source: source, decoder: {} });
  assert.deepEqual(await current(), before);
  submitFailure = true; await page.locator(`[data-finish-audio="${failedJob}"]`).click();
  await page.getByText("Please try finishing this audio again.", { exact: true }).waitFor();
  assert.deepEqual(await current(), before);
  console.log("PASS Saved and interrupted synthesis gets direct Finish audio, focus survives polling, queue errors preserve the draft");

  await page.evaluate(() => showView("studio"));
  await page.locator("#track-details").click();
  await page.waitForFunction(() => !document.querySelector("#finish-audio").disabled);
  await page.locator("#detail-audio-refinements > summary").click();
  assert.equal(await page.locator("#detail-audio-core").getAttribute("placeholder"), "1024");
  await page.locator("#detail-audio-core").fill("512");
  await page.locator("#detail-audio-halo").fill("0");
  await page.locator("#detail-audio-storage").selectOption("0");
  await page.locator("#finish-audio").click();
  await page.waitForFunction(() => state.jobs.length === 4);
  assert.deepEqual(requests.at(-1).decoder, { core_frames: 512, halo_frames: 0, storage: 0 });
  assert.deepEqual(await current(), before);
  const number = requests.length;
  await page.locator("#detail-audio-core").fill("0"); await page.locator("#finish-audio").click();
  assert.equal(requests.length, number);
  await page.locator('[data-decoder-reset="detail-audio"]').click();
  assert.equal(await page.locator("#detail-audio-core").inputValue(), "");
  mkdirSync("test-results", { recursive: true });
  await page.screenshot({ path: "test-results/acoustic-details-desktop.png" });
  await page.locator('[data-close="track-dialog"]').click();
  compatible = false;
  await page.locator("#track-details").click();
  await page.getByText("This saved sound needs its original audio decoder.", { exact: true }).waitFor();
  assert(await page.locator("#finish-audio").isDisabled());
  await page.locator('[data-close="track-dialog"]').click(); compatible = true;
  console.log("PASS Track details retain exact synthesis with optional native-zero controls, original defaults and compatibility errors");

  await page.locator("#producer-panel > summary").click();
  const generate = page.locator(`[data-generate-review="${proposal.id}"]`);
  await generate.waitFor(); assert.equal(await generate.innerText(), "Finish audio");
  await generate.click();
  await page.waitForFunction(() => state.jobs.length === 5);
  assert.deepEqual(await current(), before);
  assert.equal(requests.at(-1).seed, "0");
  assert.equal(requests.at(-1).review_id, proposal.id);
  await page.locator(`[data-use-review="${proposal.id}"]`).click(); await attached();
  assert.equal((await current()).seed, "0");
  assert.deepEqual((await current()).decoder, proposal.generation.decoder);
  await page.locator("#acoustic-context").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/acoustic-draft-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#acoustic-context").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/acoustic-draft-mobile.png" });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.locator("#title").fill("My chosen finish");
  assert.equal((await current()).acoustic_source, source);
  ready = false; await refresh();
  assert(await page.locator("#generate").isEnabled());
  await page.locator("#generate").click();
  await page.waitForFunction(() => state.jobs.length === 6); await attached();
  assert.equal(requests.at(-1).title, "My chosen finish");
  assert.equal(requests.at(-1).acoustic_source, source);
  console.log("PASS Producer decode recommendations preserve all musical inputs and seed; attached Finish audio works without main-model readiness");

  await page.locator(".creative-controls > summary").click();
  await page.locator("#seed").fill("7");
  assert.equal((await current()).acoustic_source, "");
  assert.deepEqual((await current()).decoder, {});
  assert.equal((await current()).seed, "7");
  assert(await page.locator("#acoustic-context").isHidden());
  await page.locator("#revision-undo").click(); await attached();
  assert.equal((await current()).seed, "0");
  assert.equal((await current()).title, "My chosen finish");
  if (!await page.locator("#audio-core").isVisible()) await page.locator("#audio-refinements > summary").click();
  await page.locator("#audio-core").fill("384");
  await page.locator("#audio-halo").fill("0");
  const attachedDraft = await current();
  await page.reload(); await attached();
  assert.deepEqual(await current(), attachedDraft);
  changedBuild = true; await refresh();
  await page.locator("#refresh-studio").click();
  await page.waitForLoadState("domcontentloaded"); await attached();
  assert.deepEqual(await current(), attachedDraft);
  assert.equal(await page.locator("#audio-halo").inputValue(), "0");
  assert(await page.evaluate(() => audio.paused));
  ready = true; await refresh();
  await page.locator("#fresh-sound").click();
  assert.equal((await current()).acoustic_source, "");
  await page.locator("#revision-undo").click(); await attached();
  await page.locator("#style").fill("A fresh ensemble");
  assert.equal((await current()).acoustic_source, "");
  assert.equal((await current()).style, "A fresh ensemble");
  console.log("PASS Musical edits deliberately detach with Undo; title/decoder edits, reload and explicit update refresh preserve the attachment and draft");

  if (!await page.locator("#render-mode").isVisible()) await page.locator(".creative-controls > summary").click();
  await page.locator("#render-mode").selectOption("sound");
  assert(await page.locator("#solver-control").isVisible());
  await page.locator("#generate").click();
  await page.waitForFunction(() => state.jobs.some(job => job.id !== "b".repeat(32) && job.status === "synthesized"));
  assert.equal(requests.at(-1).render_mode, "sound");
  assert.equal(requests.at(-1).acoustic_source, "");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#track-details").click();
  await page.waitForFunction(() => !document.querySelector("#finish-audio").disabled);
  await page.locator("#finish-audio").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/acoustic-details-mobile-collapsed.png" });
  await page.locator("#detail-audio-refinements > summary").click();
  await page.screenshot({ path: "test-results/acoustic-details-mobile.png" });
  await page.locator("#finish-audio").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/acoustic-details-mobile-lower.png" });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.locator('[data-close="track-dialog"]').click();
  console.log("PASS Saving synthesis is an ordinary output choice with synthesis controls; the detail UI fits mobile without changing the live artwork");

  const retained = await current();
  let release;
  hold = new Promise(resolve => release = resolve);
  const arrival = new Promise(resolve => arrived = resolve);
  await page.evaluate(() => showView("library"));
  const requestCount = requests.length;
  await page.locator(`[data-finish-audio="${savedJob}"]`).click();
  await arrival;
  jobs[0].title = "The kept sound, renamed";
  await refresh();
  assert(await page.locator(`[data-finish-audio="${savedJob}"]`).isDisabled());
  assert(await page.locator(`[data-finish-audio="${failedJob}"]`).isDisabled());
  assert(await page.locator("#refresh-studio").isDisabled());
  assert.equal(await page.locator("#studio-update-hint").innerText(), "Your audio is being queued.");
  await page.locator(`[data-finish-audio="${failedJob}"]`).evaluate(button => button.click());
  assert.equal(requests.length, requestCount + 1);
  release(); hold = null;
  await page.waitForFunction(() => !RiffAcoustics.refreshWait());
  assert.deepEqual(await current(), retained);
  console.log("PASS A pending finish owns its buttons across history redraws, blocks refresh and duplicate submissions, and leaves the creative draft intact");

  sourceFailure = true;
  await page.evaluate(() => showView("studio"));
  await page.locator("#track-details").click();
  await page.locator("#detail-acoustic-status").filter({ hasText: "This saved sound is unavailable." }).waitFor();
  assert(await page.locator("#finish-audio").isDisabled());
  await page.locator('[data-close="track-dialog"]').click();
  await page.evaluate(recipe => openRecipe(recipe), proposal.generation);
  await page.locator("#acoustic-status").filter({ hasText: "This saved sound is unavailable." }).waitFor();
  assert(await page.locator("#generate").isDisabled());
  await page.locator("#fresh-sound").click();
  assert.equal((await current()).acoustic_source, "");
  assert.equal((await current()).style, original.style);
  console.log("PASS Unavailable saved sound stays visible and cannot be queued; its musical inputs remain available for a new performance");
  sourceFailure = false;
  await page.evaluate(recipe => openRecipe(recipe), { ...proposal.generation, style: "An intentionally different ensemble" });
  await page.waitForFunction(() => !RiffAcoustics.attached());
  assert.equal((await current()).acoustic_source, "");
  assert.equal((await current()).style, "An intentionally different ensemble");
  assert.equal((await current()).seed, "0");
  assert(!await page.locator("#toast").innerText().then(text => text.includes("Undo brings it back")));
  console.log("PASS A changed imported recipe keeps the actual musical edit and detaches an incompatible saved-sound reference");
  assert.deepEqual(errors, []);
} finally {
  await browser?.close();
  const stopped = once(server, "exit"); server.kill("SIGTERM"); await stopped;
}
