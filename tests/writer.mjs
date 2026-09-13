import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
const score = 'X:1\nM:7/8\nL:1/8\nK:Dm\n"Dm"D2 F A2 G2|';
let incoming, proposed, release;
try {
  await page.goto(process.env.RIFF_URL);
  await page.locator("#play:not([disabled])").waitFor();
  await page.evaluate(score => {
    fillRecipe({ title: "Opening idea", lyrics: "The tide has room for everyone.", mode: "lyrics", style: "Plucked chamber music",
      cot: "full", abc: score, max_seconds: 18, steps: 11, solver: "ab2", seed: "8151", cfg_scale: 1.4, temperature: .85,
      refinement: { abc_top_k: 41, semantic_min_tokens: 100, semantic_top_p: .83 },
      idea_engine: "openrouter", brief: "Develop this into a short call and response", parent_track_id: selected.id }); saveDraft();
  }, score);
  const before = await page.evaluate(() => formRecipe());
  assert(await page.locator("#cloud-writer-hint").isVisible(), "Restoring a cloud draft also restores its sharing explanation");
  await page.route("**/api/inspiration", async route => {
    incoming = route.request().postDataJSON();
    proposed = { ...incoming, title: "A room in the tide", lyrics: "The tide has room.\nThe shore replies.", lyrics_source: "ai",
      style: "Close solo caller, plucked oud and low woodwinds", cot: "full", abc: score.replace('"Dm"', '"Gm"'),
      steps: 23, cfg_scale: 1.7, temperature: .72, refinement: { abc_temperature: .44, semantic_top_p: .79 },
      writer_model: "fixture/composer", writer_summary: "An asymmetrical answering phrase." };
    if (release) await new Promise(resolve => { release = resolve; });
    const { writer_model, writer_summary, lyrics_source, ...generation } = proposed;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ generation, writer_model, writer_summary, lyrics_source, theme_name: writer_summary }) });
  });
  async function write() {
    const response = page.waitForResponse(r => r.url().endsWith("/api/inspiration"));
    await page.locator("#surprise").click(); await response;
    await page.waitForFunction(() => !document.querySelector("#surprise").disabled);
  }
  await write();
  for (const key of ["abc", "cot", "steps", "solver", "max_seconds", "seed", "cfg_scale", "temperature", "refinement", "parent_track_id"])
    assert.deepEqual(incoming[key], before[key], `Full input ${key} reaches the writer`);
  assert.equal(incoming.abc_draft, undefined); assert.equal(incoming.lyrics_draft, undefined);
  const after = await page.evaluate(() => formRecipe());
  for (const key of ["title", "lyrics", "lyrics_source", "style", "abc", "steps", "cfg_scale", "temperature", "refinement", "writer_model", "writer_summary"])
    assert.deepEqual(after[key], proposed[key], `Written ${key} becomes an editable control`);
  assert.equal(after.parent_track_id, before.parent_track_id);
  const queued = page.waitForResponse(r => r.url().endsWith("/api/generations") && r.request().method() === "POST");
  await page.locator("#generate").click(); const accepted = await queued;
  assert.equal(accepted.status(), 201); const job = await accepted.json();
  for (const key of ["lyrics", "style", "abc", "steps", "solver", "cfg_scale", "temperature", "refinement", "writer_model"])
    assert.deepEqual(job.recipe[key], after[key], `${key} survives submission`);
  await page.locator("#undo-idea").click();
  assert.deepEqual(await page.evaluate(() => formRecipe()), before);
  console.log("PASS Full writer inputs, score, refinement and provenance reach the editable studio and queue; Undo restores the entire draft");

  release = true;
  await page.locator("#idea-engine").selectOption("ai");
  let writingStage = "Waiting for memory";
  await page.route("**/api/state", async route => {
    const response = await route.fetch(), next = await response.json();
    next.writing = { id: "writer:browser-fixture", job_id: null, stage: writingStage };
    await route.fulfill({ response, json: next });
  });
  const pending = page.waitForResponse(r => r.url().endsWith("/api/inspiration"));
  await page.locator("#surprise").click();
  await page.waitForFunction(() => document.querySelector("#surprise").disabled);
  await page.waitForFunction(() => document.querySelector("#writer-status").textContent === "Waiting for memory");
  assert(await page.locator("#stop-writing").isVisible(), "Waiting writing remains cancellable");
  writingStage = "Writing a new song idea";
  await page.waitForFunction(() => document.querySelector("#writer-status").textContent === "Writing a new song idea");
  await page.locator("#lyrics").fill("My edits take precedence.");
  while (typeof release !== "function") await new Promise(resolve => setTimeout(resolve, 10));
  release(); release = null; await pending;
  await page.waitForFunction(() => !document.querySelector("#surprise").disabled);
  assert.equal(await page.locator("#lyrics").inputValue(), "My edits take precedence.");
  assert.equal(await page.locator("#abc").inputValue(), before.abc);
  assert.equal(await page.locator("#writer-status").textContent(), "Ready for an idea");
  await page.unroute("**/api/state");
  console.log("PASS Local writing shows reservation wait and active writing independently of music progress");
  console.log("PASS A delayed full-recipe proposal cannot overwrite typing in the current draft");
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
