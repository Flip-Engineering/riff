import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"]);
let diagnostics = "", browser, page;
server.stderr.on("data", chunk => diagnostics += chunk);
const fixture = await new Promise((resolve, reject) => {
  let text = "";
  server.stdout.on("data", chunk => { text += chunk; if (text.includes("\n")) resolve(JSON.parse(text.split("\n")[0])); });
  server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});
const score = "X:1\nT:Kept motif\nM:4/4\nL:1/8\nQ:1/4=108\nK:Dm\nD2 F2 A2 G2|F2 E2 D4|\n";
const melody = score.replace("Kept motif", "Captured melody").replace("D2 F2", "E2 G2");
const firstId = "riff-score-v1:" + "a".repeat(64), melodyId = "riff-score-v1:" + "b".repeat(64);
const emptyId = "riff-score-v1:" + "c".repeat(64), freshId = "riff-score-v1:" + "d".repeat(64);
const unreadableId = "riff-score-v1:" + "e".repeat(64);
const initial = { title: "Exact composition", mode: "lyrics", lyrics: "[Verse]\nKeep our words.\n[Chorus]\nLet the voices rise.",
  style: "low drums and reeds", max_seconds: 12, steps: 8, cot: "full", score_source: firstId,
  abc: "", abc_draft: score, seed: "74", idea_engine: "phrases" };
const fields = recipe => ({ score_source: recipe.score_source, abc: recipe.abc, abc_draft: recipe.abc_draft, cot: recipe.cot });
let available = true, proposalText = score, capturedPlan = null, heldPlan = null, holdPlan = false, notifyHeld;
let capturedOnly = false;
const submissions = [], compositions = [], edits = [];
let performanceFixture = null;
const performanceJobId = "7".repeat(32);

try {
  browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
  page = await browser.newPage({ viewport: { width: 1365, height: 1000 }, reducedMotion: "reduce" });
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(recipe => {
    if (!localStorage.getItem("riff.draft")) localStorage.setItem("riff.draft", JSON.stringify(recipe));
  }, initial);
  const plan = (id, title, artifact, abc, cot, tokenCount = 37, displayError = "") => ({ id, title, finished: 1789300800,
    recipe: { cot, symbolic_plan: { artifact_id: artifact, abc, token_count: tokenCount, truncated: false,
      ...(displayError ? { display_error: displayError } : {}) } } });
  await page.route("**/api/state", async route => {
    const response = await route.fetch(), next = await response.json();
    next.engine = { ...next.engine, capabilities: { ...next.engine?.capabilities, exact_score_replay: available } };
    next.tracks[0].recipe = { ...next.tracks[0].recipe, ...initial,
      ...(capturedOnly ? { score_source: "" } : {}),
      symbolic_plan: { artifact_id: firstId, abc: score, token_count: 37, truncated: false }, ...performanceFixture };
    if (performanceFixture) next.jobs.push({ id: performanceJobId, title: "Captured performance", status: "performed",
      created: 1789300800, finished: 1789300801, performance_available: true, recipe: performanceFixture });
    next.plans = [plan("1".repeat(32), "Captured melody", melodyId, melody, "melody"),
      plan("2".repeat(32), "Blank captured score", emptyId, "", "full", 0),
      plan("3".repeat(32), "Legacy written score", "", score, "full"),
      plan("6".repeat(32), "Preserved unavailable notation", unreadableId, "", "full", 37,
        "The notation view is unavailable. The saved score can still be reused.")];
    if (capturedPlan) next.plans.unshift(capturedPlan);
    await route.fulfill({ response, json: next });
  });
  await page.route("**/api/tracks/*", async route => {
    if (route.request().method() !== "GET") return route.continue();
    const response = await route.fetch(), track = await response.json();
    track.recipe = { ...track.recipe, ...initial,
      ...(capturedOnly ? { score_source: "" } : {}),
      symbolic_plan: { artifact_id: firstId, abc: score, token_count: 37, truncated: false }, ...performanceFixture };
    await route.fulfill({ response, json: track });
  });
  await page.route(`**/api/jobs/${performanceJobId}`, route => route.fulfill({ status: 200,
    json: { id: performanceJobId, status: "performed", recipe: { ...initial, ...performanceFixture } } }));
  await page.route("**/api/generations", async route => {
    const recipe = route.request().postDataJSON(); submissions.push(recipe);
    // Exercise the UI contract without invoking a model or requiring an owned
    // artifact in this synthetic browser fixture. Backend ownership has its own tests.
    const normalized = { ...recipe }; delete normalized.abc_draft;
    await route.fulfill({ status: 201, json: { id: "4".repeat(32), recipe: normalized } });
  });
  await page.route("**/api/composition/revise", async route => {
    edits.push(route.request().postDataJSON());
    await route.fulfill({ status: 200, json: { abc: proposalText, summary: "A score suggestion" } });
  });
  await page.route("**/api/plans", async route => {
    const recipe = route.request().postDataJSON(); compositions.push(recipe);
    const finish = async () => {
      capturedPlan = plan("5".repeat(32), "Fresh composition", freshId, melody, recipe.cot);
      await route.fulfill({ status: 201, json: { id: capturedPlan.id, recipe } });
    };
    if (holdPlan) { heldPlan = finish; notifyHeld?.(); } else await finish();
  });
  const current = () => page.evaluate(() => formRecipe());
  const assertAttached = async (id, text, cot) => assert.deepEqual(fields(await current()),
    { score_source: id, abc: "", abc_draft: text, cot });
  const open = async () => {
    if (await page.locator("#score-dialog").isVisible()) return;
    if (!await page.locator("#score-open").isVisible()) await page.locator(".creative-controls > summary").click();
    await page.locator("#score-open").click();
  };
  const close = () => page.getByLabel("Close composition", { exact: true }).click();
  const history = async title => {
    if (!await page.locator("#score-history").isVisible()) await page.locator(".score-history > summary").click();
    await page.locator("#score-history button").filter({ hasText: title }).click();
  };
  const submit = async button => {
    const received = page.waitForResponse(response => response.url().endsWith("/api/generations") && response.request().method() === "POST");
    await page.locator(button).click();
    assert.equal((await received).status(), 201);
    return submissions.at(-1);
  };

  await page.goto(fixture.url);
  await page.locator("#play:not([disabled])").waitFor();
  await assertAttached(firstId, score, "full");
  await open();
  assert.equal(await page.locator("#score-source").inputValue(), score);
  assert(await page.locator("#score-use").isHidden());
  assert.equal(await page.locator("#score-status").innerText(), "Saved score attached");
  await page.locator("#apply-score-header").click();
  await assertAttached(firstId, score, "full");
  await page.locator("#transpose-up").click();
  assert.equal((await current()).score_source, ""); assert.notEqual((await current()).abc, score);
  await page.locator("#score-undo").click(); await assertAttached(firstId, score, "full");
  await page.getByText("Edit ABC notation", { exact: true }).click();
  await page.locator("#score-source").fill(score + "% A written change\n");
  assert.equal((await current()).score_source, "");
  await page.locator("#score-undo").click(); await assertAttached(firstId, score, "full");
  await page.locator("#import-abc").setInputFiles({ name: "same.abc", mimeType: "text/plain", buffer: Buffer.from(score) });
  await assertAttached(firstId, score, "full");
  await page.locator("#import-abc").setInputFiles({ name: "changed.abc", mimeType: "text/plain", buffer: Buffer.from(melody) });
  await page.waitForFunction(() => formRecipe().score_source === "");
  await page.locator("#score-undo").click(); await assertAttached(firstId, score, "full");
  console.log("PASS Exact score attachment survives unchanged text; transpose, typing and changed import detach, and Undo restores all four score fields");

  await page.getByText("Describe a musical change", { exact: true }).click();
  for (const text of [score, melody]) {
    proposalText = text;
    await page.locator("#score-change").fill("Consider this motif");
    await page.locator("#revise-score").click();
    await page.locator("#score-proposal").waitFor({ state: "visible" });
    assert.equal(edits.at(-1).score_source, firstId);
    assert.equal(edits.at(-1).abc, ""); assert.equal(edits.at(-1).abc_draft, score);
    await assertAttached(firstId, score, "full");
    await page.locator("#apply-score-proposal").click();
    if (text === score) await assertAttached(firstId, score, "full");
    else {
      assert.equal((await current()).score_source, "");
      await page.locator("#score-undo").click(); await assertAttached(firstId, score, "full");
    }
  }
  await page.locator("#score-mode").selectOption("melody"); await assertAttached(firstId, score, "melody");
  await page.locator("#score-undo").click(); await assertAttached(firstId, score, "full");
  await close();
  await page.locator("#abc").fill(score + "% An edit from the studio\n");
  assert.equal((await current()).score_source, "");
  await open(); await page.locator("#score-undo").click(); await assertAttached(firstId, score, "full");
  await close();
  await page.reload(); await page.locator("#play:not([disabled])").waitFor();
  await assertAttached(firstId, score, "full");
  const generated = await submit("#generate");
  assert.equal(generated.score_source, firstId); assert.equal(generated.abc, "");
  await assertAttached(firstId, score, "full");
  await page.locator("#variation").click(); await assertAttached(firstId, score, "full");
  if (!await page.locator(".study-controls > summary").isVisible()) await page.locator(".creative-controls > summary").click();
  if (!await page.locator("#audition").isVisible()) await page.locator(".study-controls > summary").click();
  const beforeStudy = await current(), study = await submit("#audition");
  assert.equal(study.score_source, firstId); assert.equal(study.abc, "");
  assert.deepEqual(await current(), beforeStudy);
  console.log("PASS AI revision sends the attachment and readable draft separately; no-op proposals preserve it, and reload, generation, variation and study retain it");

  // A real YuE2 generation stores its newly captured score in symbolic_plan
  // while score_source is empty. The ordinary variation action must hydrate
  // that captured artifact before the user queues the child take.
  capturedOnly = true;
  await page.reload(); await page.locator("#play:not([disabled])").waitFor();
  await page.locator("#variation").click();
  await assertAttached(firstId, score, "full");
  capturedOnly = false;
  console.log("PASS Ordinary variation hydrates a captured symbolic-plan artifact when score_source was empty");

  await open();
  await history("Captured melody");
  assert.equal(await page.locator("#score-mode").inputValue(), "melody");
  await page.locator("#score-mode").selectOption("full");
  await page.getByRole("button", { name: "Use this score", exact: true }).click();
  await assertAttached(melodyId, melody, "full");
  await page.locator("#score-undo").click();
  await page.locator("#score-mode").selectOption("melody");
  await page.getByRole("button", { name: "Use this score", exact: true }).click();
  await assertAttached(melodyId, melody, "melody");
  await history("Blank captured score");
  await page.getByRole("button", { name: "Use this score", exact: true }).click();
  await assertAttached(emptyId, "", "full");
  assert.match(await page.locator("#score-empty").innerText(), /blank score/i);
  await history("Preserved unavailable notation");
  assert.match(await page.locator("#score-empty").innerText(), /notation is unavailable/i);
  assert.doesNotMatch(await page.locator("#score-empty").innerText(), /blank score/i);
  assert(await page.locator("#score-use").isEnabled());
  await page.locator("#score-use").click();
  await assertAttached(unreadableId, "", "full");
  assert.match(await page.locator("#score-status").innerText(), /notation unavailable/i);
  assert.doesNotMatch(await page.locator("#score-empty").innerText(), /blank score/i);
  console.log("PASS A score with unavailable notation remains reusable and is never described as blank");
  available = false; await page.evaluate(() => refresh());
  await history("Captured melody");
  assert(await page.locator("#score-use").isDisabled());
  assert.equal((await current()).abc, melody);
  await history("Legacy written score");
  assert(await page.locator("#score-use").isHidden());
  assert.equal((await current()).abc, score);
  available = true; await page.evaluate(() => refresh());
  await history("Captured melody"); await page.locator("#score-use").click();
  await assertAttached(melodyId, melody, "melody");
  await close();
  await page.locator("#planning").selectOption("off");
  assert.deepEqual(fields(await current()), { score_source: "", abc: "", abc_draft: melody, cot: "off" });
  await open(); await page.locator("#score-undo").click(); await assertAttached(melodyId, melody, "melody");
  console.log("PASS Captured melody/full modes and valid empty scores are usable; older engines and legacy scores keep editable notation; Direct detaches and Undo restores");

  await page.locator("#compose-score").click();
  await page.waitForFunction(() => document.querySelector("#score-status").textContent === "Score ready");
  assert.equal(compositions.at(-1).score_source, ""); assert.equal(compositions.at(-1).abc, "");
  assert.equal((await current()).score_source, "");
  await page.locator("#score-use").click(); await assertAttached(freshId, melody, "melody");
  holdPlan = true; heldPlan = null;
  const held = new Promise(resolve => notifyHeld = resolve);
  await page.locator("#compose-score").click();
  await held;
  if (!await page.locator("#score-source").isVisible()) await page.getByText("Edit ABC notation", { exact: true }).click();
  const editedWhileWaiting = melody + "% Keep this change while composing\n";
  await page.locator("#score-source").fill(editedWhileWaiting);
  assert(heldPlan); await heldPlan();
  await page.waitForFunction(() => document.querySelector("#score-status").textContent === "Score ready");
  assert.equal((await current()).abc, editedWhileWaiting);
  assert.equal((await current()).score_source, "");
  console.log("PASS Fresh composition clears the attachment intentionally and an in-flight composition cannot overwrite subsequent score edits");

  mkdirSync("test-results", { recursive: true });
  await history("Captured melody"); await page.locator("#score-use").click();
  await page.screenshot({ path: "test-results/score-attachment-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert(await page.locator("#score-dialog").evaluate(node => node.scrollWidth <= innerWidth));
  await page.screenshot({ path: "test-results/score-attachment-mobile.png" });
  assert(!await page.locator("#score-dialog").innerText().then(text => text.includes("riff-score-v1:") || text.includes("151643")));
  assert.deepEqual(errors, []);
  console.log("PASS Attachment controls fit narrow screens and expose no artifact paths or token arrays");

  await close(); await page.setViewportSize({ width: 1365, height: 1000 });
  for (const shape of [
    { name: "newly generated score", source: "", artifact: firstId, text: score, cot: "full", tokens: 37 },
    { name: "explicit saved score", source: melodyId, artifact: firstId, text: melody, cot: "melody", tokens: 37 },
    { name: "empty captured score", source: "", artifact: emptyId, text: "", cot: "full", tokens: 0 },
    { name: "legacy readable score", source: "", artifact: "", text: score, cot: "full", tokens: 37 },
  ]) {
    const expectedSource = shape.source || shape.artifact;
    performanceFixture = { score_source: shape.source, abc: "", abc_draft: undefined, cot: shape.cot,
      symbolic_plan: { artifact_id: shape.artifact, abc: shape.text, token_count: shape.tokens, truncated: false },
      performance: { frames: 300, seconds: 12, truncated: false } };
    await page.reload(); await page.locator("#refine-performance").waitFor();
    for (const action of ["refine", "finish"]) {
      if (action === "refine") await page.locator("#refine-performance").click();
      else {
        await page.evaluate(() => showView("library"));
        await page.locator(`[data-finish-performance="${performanceJobId}"]`).click();
        await page.waitForFunction(id => formRecipe().performance_source === id, performanceJobId);
      }
      const expectedPerformance = action === "refine" ? fixture.track_id : performanceJobId;
      const outgoing = await submit("#generate");
      assert.equal(outgoing.score_source, expectedSource, `${action}: ${shape.name}`);
      assert.equal(outgoing.abc, expectedSource ? "" : shape.text, `${action}: ${shape.name}`);
      assert.equal(outgoing.abc_draft, shape.text);
      assert.equal(outgoing.cot, shape.cot); assert.equal(outgoing.max_seconds, 12);
      assert.equal(outgoing.performance_source, expectedPerformance);
    }
  }
  assert.deepEqual(errors, []);
  console.log("PASS Refine performance and Finish sound submit captured or explicit score IDs with separate notation, including empty scores; legacy notation and mode/duration are preserved");
} finally {
  await page?.unrouteAll({ behavior: "wait" });
  await browser?.close();
  const exited = once(server, "exit"); server.kill("SIGTERM"); await exited;
}
