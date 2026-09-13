import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

// Own server, database, credential double, provider double, and browser.
// No remote requests, actual credentials, or music model are used in this test.
const server = spawn(process.env.RIFF_PYTHON || "python3", [
  "-u",
  "tests/review_browser_server.py",
]);
let diagnostics = "";
server.stderr.on("data", (chunk) => {
  diagnostics += chunk;
});
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", (chunk) => {
    output += chunk;
    if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0]));
  });
  server.once("exit", (code) =>
    reject(new Error(`Fixture exited ${code}: ${diagnostics}`)),
  );
});
let executablePath = process.env.RIFF_BROWSER_EXECUTABLE;
if (!executablePath && !existsSync(chromium.executablePath())) {
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  executablePath = readdirSync(cache)
    .filter((name) => name.startsWith("chromium_headless_shell-"))
    .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }))
    .map((name) =>
      join(
        cache,
        name,
        "chrome-headless-shell-mac-arm64/chrome-headless-shell",
      ),
    )
    .find((path) => existsSync(path));
}
const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({
  viewport: { width: 1365, height: 1050 },
  reducedMotion: "reduce",
});
const errors = [],
  checks = [];
page.on("pageerror", (error) => errors.push(error.message));
const check = (name) => {
  checks.push(name);
  console.log(`PASS ${name}`);
};
const settings = page.locator("#review-settings-dialog");
async function openSettings() {
  await page
    .getByRole("button", { name: "Review settings", exact: true })
    .click();
  await settings.waitFor({ state: "visible" });
}
async function saveSettings() {
  await page
    .getByRole("button", { name: "Save review settings", exact: true })
    .click();
  await settings.waitFor({ state: "hidden" });
}
async function doneReviews(number) {
  await page.waitForFunction(
    (count) => document.querySelectorAll("[data-use-review]").length === count,
    number,
  );
}
async function shot(name) {
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    `${name}: page overflow`,
  );
  assert(
    await page.evaluate(() =>
      [...document.querySelectorAll("dialog[open]")].every(
        (d) => d.scrollWidth <= d.clientWidth,
      ),
    ),
    `${name}: dialog overflow`,
  );
  await page.screenshot({ path: `test-results/${name}.png`, fullPage: false });
}
mkdirSync("test-results", { recursive: true });
try {
  await page.goto(`${fixture.url}/?recording=${fixture.track_id}#studio`);
  await page.locator("#producer-panel").waitFor({ state: "visible" });
  await page.locator("#producer-panel > summary").click();
  assert(await page.locator("#request-review").isDisabled());
  await openSettings();
  await page.locator("#reviews-enabled").check();
  await page.locator("#review-api-key").fill("browser-fixture-secret");
  await page
    .getByRole("button", { name: "Browse current audio models" })
    .click();
  await page.waitForFunction(
    () =>
      document.querySelector("#review-models option")?.value ===
      "fixture/audio-model",
  );
  await page.locator("#review-model").fill("fixture/custom-audio");
  await saveSettings();
  await page.waitForFunction(
    () => !document.querySelector("#request-review").disabled,
  );
  assert(
    !(await page.evaluate(() => JSON.stringify(localStorage))).includes(
      "browser-fixture-secret",
    ),
  );
  check(
    "Enable reviews, browse audio models, choose a custom model; no key in browser storage",
  );

  await openSettings();
  assert.equal(await page.locator("#review-api-key").inputValue(), "");
  assert(
    (await page.locator("#review-key-status").textContent()).includes(
      "Your key is saved",
    ),
  );
  await shot("review-settings-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await shot("review-settings-mobile");
  await page.setViewportSize({ width: 320, height: 740 });
  await shot("review-settings-narrow");
  await page.locator("#review-api-key").fill("replacement-fixture-secret");
  await saveSettings();
  await page.setViewportSize({ width: 1365, height: 1050 });
  check(
    "Replace a saved key; password stays blank on reopen; responsive settings",
  );

  await page.locator("#title").fill("My unfinished draft");
  await page.locator("#review-focus").fill("Bring the crowd forward.");
  await page.locator("#request-review").click();
  await doneReviews(1);
  assert(
    !(await page.locator("#producer-notes").textContent()).includes(
      "replacement-fixture-secret",
    ),
  );
  await page.locator(".review-listening-notes > summary").click();
  await page
    .getByRole("button", { name: "Play from 0:00", exact: true })
    .click();
  await page.waitForFunction(
    () => document.querySelector("audio").currentTime > 0,
  );
  check("Saved musical notes, playable timestamps, credential redaction");

  await page.getByRole("button", { name: "Edit in studio", exact: true }).click();
  assert.equal(await page.locator("#title").inputValue(), "Another take");
  assert.equal(await page.locator("#custom-steps").inputValue(), "37");
  assert.equal(await page.locator("#duration").inputValue(), "26");
  assert.equal(await page.locator("#seed").inputValue(), "15961");
  assert.equal(await page.locator("#guidance").inputValue(), "1.6");
  assert.equal(await page.locator("#planning").inputValue(), "full");
  assert((await page.locator("#abc").inputValue()).includes("K:Dm"));
  assert.equal(await page.locator("#control-semantic_top_p").inputValue(), "0.82");
  assert(
    (await page.locator("#lyrics").inputValue()).includes("A little light"),
  );
  assert.equal(
    await page.locator("#style").inputValue(),
    "Congas and a whispered chorus",
  );
  await page.locator(".creative-controls > summary").click();
  await page.locator("#temperature").fill("0.92321");
  await page.locator("#guidance").fill("1.61234");
  await page.locator("#duration").fill("26.04");
  await page.locator("#seed").fill("7");
  assert.equal(await page.evaluate(() => formRecipe().seed), "7", "The artist can change a retained seed");
  assert(await page.locator("#generation-form").evaluate(form => form.checkValidity()),
    "Continuous model settings must remain valid after editing a recommendation");
  await page.locator(".creative-controls > summary").click();
  await page
    .getByRole("button", { name: "Undo revision", exact: true })
    .click();
  assert.equal(
    await page.locator("#title").inputValue(),
    "My unfinished draft",
  );
  check(
    "Complete proposed take carries score, seed, duration, sampling controls and kept lyrics; undo restores the draft",
  );

  await page.locator("#review-keep-lyrics").uncheck();
  await page.locator("#request-review").click();
  await doneReviews(2);
  await page.locator("[data-use-review]").first().click();
  assert.equal(await page.locator("#lyrics").inputValue(), "Fresh words");
  // Finish auditioning the timestamp before expecting a new take to enter the
  // player. Riff intentionally preserves an actively playing recording.
  if (await page.getByRole("button", { name: "Pause recording", exact: true }).count())
    await page.getByRole("button", { name: "Pause recording", exact: true }).click();
  const pending = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/generations") && r.request().method() === "POST",
  );
  await page.locator("#title").fill("A draft to keep while generating");
  await page.locator("[data-review-score]").first().locator("summary").click();
  await page.locator(".review-score-preview svg").first().waitFor();
  await page.locator("[data-generate-review]").first().click();
  const generation = await (await pending).json();
  assert.equal(generation.recipe.steps,37);
  assert.equal(generation.recipe.seed,"15961");
  assert.equal(generation.recipe.cfg_scale,1.6);
  assert.equal(generation.recipe.refinement.semantic_top_p,.82);
  assert(generation.recipe.abc.includes("K:Dm"));
  assert.equal(await page.locator("#title").inputValue(),"A draft to keep while generating");
  assert.equal(generation.recipe.parent_track_id, fixture.track_id);
  assert(generation.recipe.review_id);
  await page.waitForFunction(id => state.tracks.some(track => track.id === id), generation.id);
  assert.equal(await page.evaluate(() => selected.id), fixture.track_id);
  await page.goto(`${fixture.url}/?recording=${generation.id}#studio`);
  await page.waitForFunction(
    (id) => document.querySelector("#review-parent")?.href.includes(id),
    fixture.track_id,
  );
  check(
    "Rendered score and one-click generation preserve the draft and current review, with ancestry on the new take",
  );

  await page.goto(`${fixture.url}/?recording=${fixture.track_id}#studio`);
  await page.locator("#producer-panel").waitFor({ state: "visible" });
  await page.locator("#producer-panel > summary").click();
  await doneReviews(2);
  await page.locator("#producer-panel").scrollIntoViewIfNeeded();
  await shot("review-notes-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#producer-panel").scrollIntoViewIfNeeded();
  await shot("review-notes-mobile");
  await page.setViewportSize({ width: 320, height: 740 });
  await page.locator("#producer-panel").scrollIntoViewIfNeeded();
  await shot("review-notes-narrow");
  await page.setViewportSize({ width: 1365, height: 1050 });
  check(
    "Review history and focus survive reload; producer pane fits 390 and 320px screens",
  );

  await page.locator('[name="creation-mode"][value="free"]').check();
  await page.locator("#title").fill("A wordless starting point");
  const freePending = page.waitForResponse(
    (r) =>
      r.url().endsWith("/api/generations") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: /Generate take/ }).click();
  const free = await (await freePending).json();
  assert.equal(free.recipe.lyrics, "");
  await page.waitForFunction(id => state.tracks.some(track => track.id === id), free.id);
  await page.goto(`${fixture.url}/?recording=${free.id}#studio`);
  await page.waitForFunction(
    (id) =>
      document.querySelector("#download")?.getAttribute("href")?.includes(id),
    free.id,
  );
  if (!await page.locator("#producer-panel").evaluate(panel => panel.open))
    await page.locator("#producer-panel > summary").click();
  assert(!(await page.locator("#review-keep-lyrics").isVisible()));
  await page.locator("#request-review").click();
  await doneReviews(1);
  await page.locator("[data-use-review]").click();
  assert(
    await page.locator('[name="creation-mode"][value="lyrics"]').isChecked(),
  );
  assert.equal(await page.locator("#lyrics").inputValue(), "Fresh words");
  check(
    "A review can introduce lyrics to a Free play take when the artist allows it",
  );

  await page.goto(`${fixture.url}/?recording=${fixture.track_id}#studio`);
  await page.locator("#producer-panel").waitFor({ state: "visible" });
  await page.locator("#producer-panel > summary").click();
  await doneReviews(2);
  await page.locator("#review-focus").fill("wait");
  await page.locator("#request-review").click();
  await page.getByText("Listening…", { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Cancel review", exact: true })
    .click();
  await page.getByText("Cancelled", { exact: true }).waitFor();
  check("Cancel an in-flight review from the player");

  await openSettings();
  await page
    .getByRole("button", { name: "Remove saved key", exact: true })
    .click();
  await page.waitForFunction(
    () => !document.querySelector("#reviews-enabled").checked,
  );
  assert(
    (await page.locator("#review-key-status").textContent()).includes(
      "reconnect",
    ),
  );
  await page
    .getByRole("button", { name: "Close review settings", exact: true })
    .click();
  assert(await page.locator("#request-review").isDisabled());
  await openSettings();
  await page.locator("#review-api-key").fill("unsaved-fixture-secret");
  await page.keyboard.press("Escape");
  await settings.waitFor({ state: "hidden" });
  assert.equal(await page.locator("#review-api-key").inputValue(), "");
  assert(
    !(await page.evaluate(() => JSON.stringify(localStorage))).includes(
      "fixture-secret",
    ),
  );
  check(
    "Remove key and disconnect; closing settings clears an unsaved password",
  );
  assert.deepEqual(errors, []);
  writeFileSync(
    "test-results/reviews.json",
    JSON.stringify({ fixture: true, checks, errors }, null, 2) + "\n",
  );
} finally {
  await browser.close();
  server.kill("SIGTERM");
  await once(server, "exit");
}
