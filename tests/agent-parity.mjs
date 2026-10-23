import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chromium } from "playwright";

// The Studio and agents queue takes through one server operation. This test drives the Studio's own
// controls against a real server and checks the two places they could drift apart: what a submit does
// when a response is lost, and the recipe the Studio builds for a variation or performance refinement
// compared with the draft the agent operations build for the same take.
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"], {
  env: { ...process.env, RIFF_PERFORMANCE_FIXTURE: "1" },
});
let diagnostics = ""; server.stderr.on("data", data => diagnostics += data);
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", data => { output += data; if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
  server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});
const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
const call = (path, body) => page.evaluate(({ path, body }) => api(path, body ? "POST" : "GET", body), { path, body });
const operation = (name, body) => call(`/api/v1/operations/${name}`, body);
// Playwright does not wait for a promise returned by waitForFunction's predicate, so poll from here.
const until = async (check, what) => {
  for (let attempt = 0; attempt < 300; attempt++) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 100)); }
  throw new Error(`Timed out waiting for ${what}`);
};
// What a take would be queued as. Without a retained seed the engine chooses one at random, so it is left out.
const effective = async (recipe, keepSeed = false) => {
  const checked = await operation("recipe.validate", { recipe });
  assert(checked.valid, checked.error);
  const { seed, ...rest } = checked.effective_recipe;
  return keepSeed ? { seed, ...rest } : rest;
};
try {
  await page.goto(fixture.url);
  await page.locator("#refine-performance").waitFor();

  // A response lost after the server queued the take must not queue it twice.
  const title = "A take whose answer was lost";
  await page.locator("#title").fill(title);
  let lost = 0;
  await page.route("**/api/generations", async route => {
    if (!lost++) { await route.fetch(); await route.abort("failed"); } else await route.continue();
  });
  await page.evaluate(() => document.querySelector("#generation-form").requestSubmit());
  await page.locator("#form-error").waitFor();
  const answered = page.waitForResponse(response => response.url().endsWith("/api/generations"));
  await page.evaluate(() => document.querySelector("#generation-form").requestSubmit());
  const retry = await (await answered).json();
  assert.equal(retry.replayed, true);
  assert.match(retry.operation_id, /^[a-f0-9]{32}$/);
  await page.unroute("**/api/generations");
  const queued = await operation("study.create", { recipe: { title: "Checking the count" } });
  const listing = await call("/api/v1/operations");
  assert.equal(listing.operations.filter(item => item.title === title).length, 1);
  assert.equal((await call(`/api/v1/operations/${retry.operation_id}`)).request.title, title);
  assert.equal(queued.status, "queued");
  console.log("PASS A submit whose response was lost returns the take already queued, with its operation identity");

  // Refining a saved performance in the Studio and through the agent operation build the same take.
  await page.reload();
  await page.locator("#refine-performance").waitFor();
  await page.locator("#refine-performance").click();
  const refined = await page.evaluate(() => formRecipe());
  const replay = await operation("take.replay", {
    source: fixture.track_id, replay: "performance", retain: ["performance"], enqueue: false,
    changes: { title: refined.title } });
  assert(replay.validation.valid, replay.validation.error);
  assert.deepEqual(await effective(refined), await effective(replay.draft));
  assert.equal(replay.parent.id, fixture.track_id);
  assert.equal(refined.parent_track_id, replay.draft.parent_track_id);
  assert.equal(refined.performance_source, replay.draft.performance_source);

  // An ordinary variation keeps the inputs and the lineage link, and chooses a new seed.
  await page.reload();
  await page.locator("#variation").waitFor();
  await page.locator("#variation").click();
  const varied = await page.evaluate(() => formRecipe());
  const developed = await operation("take.develop", { source: fixture.track_id, retain: [], enqueue: false,
    changes: { title: varied.title } });
  assert(developed.validation.valid, developed.validation.error);
  assert.deepEqual(await effective(varied), await effective(developed.draft));
  assert.equal(developed.derivation.retained.seed, false);
  assert.equal(varied.parent_track_id, fixture.track_id);
  assert(!varied.performance_source);
  console.log("PASS The Studio's refine-performance and variation drafts equal the agent operations' drafts for the same take");

  // Rendering a performance made earlier (Render performance in the history) builds the same take as the agent's replay.
  const made = await call("/api/generations", { title: "Phrasing for later", lyrics: "[Verse]\nLamps along the wall.",
    style: "slow folk, warm room", seed: "77", max_seconds: 12, render_mode: "performance" });
  await until(async () => (await call(`/api/jobs/${made.id}`)).status === "performed", "the performance to be saved");
  await page.reload();
  await page.locator('.main-nav [data-view="library"]').click();
  await page.locator(`[data-finish-performance="${made.id}"]`).click();
  await page.waitForFunction(id => formRecipe().performance_source === id, made.id);
  const rendered = await page.evaluate(() => formRecipe());
  const rerun = await operation("take.replay", { source: made.id, replay: "performance", enqueue: false,
    changes: { title: rendered.title } });
  assert(rerun.validation.valid, rerun.validation.error);
  assert.deepEqual(await effective(rendered, true), await effective(rerun.draft, true));
  assert.equal(rendered.performance_source, made.id);
  assert.equal(rerun.draft.performance_source, made.id);
  assert.equal(rerun.validation.effective_recipe.seed, "77");
  console.log("PASS The Studio's Render performance draft equals take.replay for the same saved performance");
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
  server.kill("SIGTERM");
}
