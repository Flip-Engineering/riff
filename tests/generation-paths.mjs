import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
const score = "X:1\nM:4/4\nL:1/8\nK:Dm\nD2 F2 A2 G2|";
const lyrics = "[Verse]\nThe windows open to the rain.\nWe meet the morning once again.\n\n[Chorus]\nA little room to move.\nA little light comes through.";
async function submit(button) {
  const response = page.waitForResponse(r => r.url().endsWith("/api/generations") && r.request().method() === "POST");
  await page.locator(button).click();
  const result = await response, body = await result.json();
  assert.equal(result.status(), 201, JSON.stringify(body));
  return body.recipe;
}
try {
  await page.goto(process.env.RIFF_URL);
  await page.locator("#play:not([disabled])").waitFor();
  await page.evaluate(({ score, lyrics }) => {
    fillRecipe({ title: "Direct path", mode: "lyrics", lyrics, abc: score, cot: "full", max_seconds: 32,
      steps: 8, solver: "ab2", refinement: { semantic_min_tokens: 600 } }); saveDraft();
  }, { score, lyrics });
  await page.locator(".creative-controls > summary").click();
  await page.locator("#planning").selectOption("off");
  assert.equal(await page.evaluate(() => formRecipe().abc), "");
  await page.reload();
  await page.locator("#play:not([disabled])").waitFor();
  assert.equal(await page.evaluate(() => formRecipe().abc), "");
  assert.equal(await page.locator("#abc").inputValue(), score);
  const direct = await submit("#generate");
  assert.equal(direct.cot, "off"); assert.equal(direct.abc, ""); assert.equal(direct.solver, "ab2");
  assert.equal(await page.locator("#abc").inputValue(), score);
  await page.locator(".creative-controls > summary").click();
  await page.locator("#planning").selectOption("full");
  assert.equal(await page.evaluate(() => formRecipe().abc), score);
  await page.locator("#render-mode").selectOption("plan");
  await page.locator("#planning").selectOption("off");
  assert.equal(await page.locator("#render-mode").inputValue(), "music");
  console.log("PASS Direct generation excludes the dormant score, preserves it across reload/submission, and exits score-only output");

  await page.locator(".study-controls > summary").click();
  await page.locator("#audition-length").fill("2");
  await page.locator("#audition-steps").fill("3");
  const before = await page.evaluate(() => formRecipe());
  const whole = await submit("#audition");
  assert.equal(whole.max_seconds, 2); assert.equal(whole.steps, 3); assert.equal(whole.refinement.semantic_min_tokens, 50);
  assert.equal(whole.render_mode, "music"); assert.equal(whole.performance_source, "");
  assert.equal(whole.cot, "off"); assert.equal(whole.abc, ""); assert.equal(whole.lyrics, lyrics);
  assert.deepEqual(await page.evaluate(() => formRecipe()), before);
  await page.locator("#planning").selectOption("full");
  await page.locator("#lyrics").evaluate(input => { const start = input.value.indexOf("[Chorus]"); input.focus(); input.setSelectionRange(start, input.value.length); input.dispatchEvent(new Event("select")); });
  const excerpt = lyrics.slice(lyrics.indexOf("[Chorus]"));
  assert.equal(await page.locator("#audition-excerpt").innerText(), excerpt);
  const selected = await submit("#audition");
  assert.equal(selected.lyrics, excerpt); assert.equal(selected.abc, ""); assert.equal(selected.cot, "full");
  assert.equal(await page.locator("#abc").inputValue(), score); assert.equal(await page.locator("#lyrics").inputValue(), lyrics);
  await page.locator("#lyrics").fill("Completely new words for the next song.");
  assert.equal(await page.locator("#audition-selection").innerText(), "Whole draft");
  const fresh = await submit("#audition");
  assert.equal(fresh.lyrics, "Completely new words for the next song."); assert.equal(fresh.abc, score);
  await page.route("**/api/generations", route => route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: "Choose a valid duration." }) }));
  await page.locator("#audition").click();
  await page.locator("#study-error").waitFor();
  assert.equal(await page.locator("#study-error").innerText(), "Choose a valid duration.");
  assert(!(await page.locator("#audition").isDisabled()));
  console.log("PASS Studies use their own duration/quality, preserve the full draft, audition the visible selection and report errors beside the action");
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
