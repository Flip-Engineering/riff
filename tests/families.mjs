import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { launch } from "./browser-launch.mjs";

// Study families through the Studio's own controls, against a real server: start a family from a study,
// add another, see what they share and how each differs, select one and develop it with edited controls.
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"], { env: { ...process.env, RIFF_QUEUE_FIXTURE: "1" } });
let diagnostics = ""; server.stderr.on("data", data => diagnostics += data);
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", data => { output += data; if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
  server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});
const browser = await launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
const call = (path, body) => page.evaluate(({ path, body }) => api(path, body ? "POST" : "GET", body), { path, body });
const operation = (name, body) => call(`/api/v1/operations/${name}`, body);
// Playwright does not wait for a promise returned by waitForFunction's predicate, so poll from here.
const until = async (check, what) => {
  for (let attempt = 0; attempt < 300; attempt++) { if (await check()) return; await new Promise(resolve => setTimeout(resolve, 100)); }
  throw new Error(`Timed out waiting for ${what}`);
};
const overflow = () => page.evaluate(() => { const dialog = document.querySelector("#families-dialog"); return dialog.scrollWidth - dialog.clientWidth; });
try {
  await page.goto(fixture.url);
  await page.locator("#title").fill("Night Orchard");
  await page.locator(".creative-controls > summary").click();
  await page.locator(".study-controls > summary").click();
  await page.locator("#audition-length").fill("5");

  // Start a family from the first study.
  await page.locator("#study-family").selectOption("__new");
  await page.locator("#audition").click();
  assert.match(await page.locator("#study-error").innerText().catch(() => ""), /^$|Name the new family/);
  await page.locator("#study-family-name").fill("Orchard studies");
  await page.locator("#seed").fill("11");
  await page.locator("#audition").click();
  await page.waitForFunction(() => [...document.querySelector("#study-family").options].some(option => /Orchard studies/.test(option.textContent)));
  const family = (await call("/api/v1/families")).families[0];
  assert.deepEqual([family.name, family.takes], ["Orchard studies", 1]);
  assert.equal(await page.locator("#study-family").inputValue(), family.family_id);

  // Add a second study with another seed and a different length.
  await page.locator("#seed").fill("22");
  await page.locator("#audition-length").fill("7");
  await page.locator("#audition").click();
  await until(async () => (await call(`/api/v1/families/${family.family_id}`)).takes.length === 2, "the second study");
  await until(async () => (await call(`/api/v1/families/${family.family_id}`)).takes.every(take => take.status === "done"), "both studies to finish");

  // The board shows what the studies share and each one's own differences.
  await page.locator("#families-open").click();
  await page.locator("#family-board .family-take").nth(1).waitFor();
  assert.equal(await page.locator("#family-title").innerText(), "Orchard studies");
  assert.match(await page.locator(".family-shared").innerText(), /Direction/);
  assert.match(await page.locator(".family-shared").innerText(), /Planning/);
  assert.doesNotMatch(await page.locator(".family-shared").innerText(), /Seed/);
  const chips = await page.locator(".family-take .family-diff").allInnerTexts();
  assert.match(chips[0], /Seed\s+11/); assert.match(chips[0], /Length\s+5 seconds/);
  assert.match(chips[1], /Seed\s+22/); assert.match(chips[1], /Length\s+7 seconds/);
  assert.equal(await page.locator(".family-take .family-role").first().textContent(), "Study");
  console.log("PASS A family started from a study collects later studies and shows shared inputs and each take's differences");

  // Select the second take with a reason.
  const second = page.locator(".family-take").nth(1);
  await second.locator("input[name=rationale]").fill("Warmer opening");
  await second.getByRole("button", { name: "Select this take" }).click();
  await page.locator(".family-take[data-selected] .family-selected").waitFor();
  assert.match(await page.locator(".family-take[data-selected] h4").innerText(), /Night Orchard/);
  assert.match(await page.locator(".family-history").textContent(), /Warmer opening/);
  const chosen = await call(`/api/v1/families/${family.family_id}`);
  assert.equal(chosen.selection.current.rationale, "Warmer opening");
  assert.equal(chosen.takes[1].selected, true);

  // Develop it with edited controls: the seed is kept, the length and steps change.
  await second.getByRole("button", { name: "Develop this study" }).click();
  const form = second.locator("form[data-develop]");
  assert.equal(await form.locator("[name=duration]").inputValue(), "7");
  assert(await form.locator("[name=retain][value=seed]").isChecked());
  assert(await form.locator("[name=retain][value=score]").isDisabled());
  assert(await form.locator("[name=retain][value=performance]").isDisabled());
  await form.locator("[name=duration]").fill("20");
  await form.locator("[name=steps]").fill("12");
  await form.locator("[name=title]").fill("Night Orchard, developed");
  assert(!(await form.locator("[name=style]").isVisible()), "Style and words stay behind Edit inputs until asked for");
  await form.locator("summary", { hasText: "Edit inputs" }).click();
  await form.locator("[name=style]").fill("brushed drums, warm bass");
  await form.locator("[name=lyrics]").fill("Words drafted while another take is running");
  await form.locator("[name=retain][value=seed]").uncheck();
  await page.locator(".family-take").first().locator("[name=rationale]").fill("Unsent first-take rationale");
  await page.locator("#family-name-input").fill("Unsent family rename");
  await form.locator("[name=lyrics]").evaluate(input => { input.focus(); input.setSelectionRange(7, 14); });
  const assertDraft = async (focus = true) => {
    assert.equal(await form.locator("[name=duration]").inputValue(), "20");
    assert.equal(await form.locator("[name=steps]").inputValue(), "12");
    assert.equal(await form.locator("[name=title]").inputValue(), "Night Orchard, developed");
    assert.equal(await form.locator("[name=style]").inputValue(), "brushed drums, warm bass");
    assert.equal(await form.locator("[name=lyrics]").inputValue(), "Words drafted while another take is running");
    assert(!(await form.locator("[name=retain][value=seed]").isChecked()));
    assert.equal(await page.locator(".family-take").first().locator("[name=rationale]").inputValue(), "Unsent first-take rationale");
    assert.equal(await page.locator("#family-name-input").inputValue(), "Unsent family rename");
    assert(await form.locator(".family-inputs").evaluate(details => details.open));
    if (focus) {
      assert.equal(await page.evaluate(() => document.activeElement.name), "lyrics");
      assert.deepEqual(await form.locator("[name=lyrics]").evaluate(input => [input.selectionStart, input.selectionEnd]), [7, 14]);
    }
  };

  const developRoute = "**/api/v1/operations/take.develop";
  const gate = () => {
    let open;
    const promise = new Promise(resolve => { open = resolve; });
    const timer = setTimeout(open, 5000);
    return { promise, open: () => { clearTimeout(timer); open(); } };
  };
  const failureGate = gate();
  let failureStarted;
  const failureRequest = new Promise(resolve => { failureStarted = resolve; });
  await page.route(developRoute, async route => {
    failureStarted();
    await failureGate.promise;
    await route.fulfill({ status: 400, json: { error: "Fixture develop failure" } });
  });
  await form.getByRole("button", { name: "Develop", exact: true }).click();
  await failureRequest;
  const detachedForm = await form.elementHandle();
  await page.evaluate(id => window.RiffFamilies.open(id), family.family_id);
  assert(!(await detachedForm.evaluate(element => element.isConnected)), "The pending request outlives its original form");
  failureGate.open();
  await until(async () => await form.locator(".form-error").isVisible(), "the failure in the current form");
  const assertFailure = async () => {
    assert(await form.locator(".form-error").isVisible());
    assert.equal(await form.locator(".form-error").innerText(), "Fixture develop failure");
  };
  await assertFailure();
  await assertDraft(false);
  assert.equal((await call(`/api/v1/families/${family.family_id}`)).takes.length, 2);
  await page.unroute(developRoute);
  await form.locator("[name=lyrics]").evaluate(input => { input.focus(); input.setSelectionRange(7, 14); });
  console.log("PASS A failed develop submission retains every unsent draft and creates no take");

  // A real background job creates and then changes catalog revisions without discarding unsent edits.
  const beforeRevision = await page.evaluate(() => state.catalog_revision);
  await operation("study.create", { family_id: family.family_id, recipe: {
    title: "Background revision", style: "family fixture delay", lyrics: "Background words", mode: "lyrics",
    seed: "33", max_seconds: 9, steps: 8, cot: "off" } });
  await page.evaluate(() => refresh());
  await page.locator(".family-take").nth(2).waitFor();
  await until(async () => await page.evaluate(revision => state.catalog_revision !== revision, beforeRevision), "the queued catalog revision");
  const runningRevision = await page.evaluate(() => state.catalog_revision);
  const backgroundTake = page.locator(".family-take").nth(2);
  assert.notEqual((await call(`/api/v1/families/${family.family_id}`)).takes[2].status, "done");
  assert(await backgroundTake.getByRole("button", { name: "Select this take" }).isDisabled());
  assert(await backgroundTake.getByRole("button", { name: "Develop this study" }).isDisabled());
  await assertDraft();
  await assertFailure();
  await until(async () => (await call(`/api/v1/families/${family.family_id}`)).takes[2].status === "done", "the background study to finish");
  await page.evaluate(() => refresh());
  await until(async () => await page.evaluate(revision => state.catalog_revision !== revision, runningRevision), "the completed catalog revision");
  await until(async () => await page.locator(".family-take").nth(2).locator(".family-status").textContent() === "Ready", "the completed catalog revision");
  assert(!(await backgroundTake.getByRole("button", { name: "Select this take" }).isDisabled()));
  assert(!(await backgroundTake.getByRole("button", { name: "Develop this study" }).isDisabled()));
  console.log("PASS Unfinished takes remain unselectable across a real catalog redraw and become eligible when ready");
  await assertDraft();
  await assertFailure();
  console.log("PASS Repeated background catalog revisions retain develop, selection and rename drafts, checkbox state, focus and text selection");

  await form.locator("[name=retain][value=seed]").check();
  await form.getByRole("button", { name: "Develop", exact: true }).click();
  await page.locator(".family-take").nth(3).waitFor();
  const grown = await call(`/api/v1/families/${family.family_id}`);
  assert.equal(grown.takes.length, 4);
  const developed = grown.takes[3];
  assert.equal(developed.role, "develop");
  assert.equal(developed.seed, "22");
  assert.deepEqual(developed.parent, { kind: "operation", id: grown.takes[1].operation_id });
  assert.equal(developed.derivation.retained.seed, true);
  assert.equal(developed.derivation.selection, chosen.selection.current.id);
  assert.equal(developed.settings.max_seconds, 20);
  assert.equal(developed.settings.steps, 12);
  assert.equal(developed.settings.style, "brushed drums, warm bass");
  assert.equal(developed.settings.lyrics, "Words drafted while another take is running");
  assert.equal(grown.takes[1].settings.style, "");
  assert.deepEqual(developed.diff.find(item => item.field === "style").before, "");
  const first = grown.takes[0], original = await call(`/api/jobs/${grown.takes[1].job_id}`);
  assert.equal(original.recipe.max_seconds, 7);
  assert.equal(original.recipe.seed, "22");
  assert.match(await page.locator(".family-take").nth(3).innerText(), /Length\s+7 seconds\s+→\s+20 seconds/);
  assert.equal(await page.locator(".family-take .family-role").nth(3).textContent(), "Developed");
  assert.equal(first.role, "study");
  assert.equal(await page.locator(".family-take").first().locator("[name=rationale]").inputValue(), "Unsent first-take rationale");
  assert.equal(await page.locator("#family-name-input").inputValue(), "Unsent family rename");
  await second.getByRole("button", { name: "Develop this study" }).click();
  assert.equal(await form.locator("[name=duration]").inputValue(), "7");
  assert.equal(await form.locator("[name=steps]").inputValue(), "8");
  assert.equal(await form.locator("[name=title]").inputValue(), "");
  assert.equal(await form.locator("[name=style]").inputValue(), "");
  assert.equal(await form.locator("[name=lyrics]").inputValue(), grown.takes[1].settings.lyrics ?? "");
  assert(await form.locator("[name=retain][value=seed]").isChecked());
  await second.getByRole("button", { name: "Develop this study" }).click();
  console.log("PASS Select records the choice with its reason, and develop keeps the seed, applies the edited controls and records parent, family and selection; success clears the submitted draft");

  // Edits made after a request must survive that request's success and subsequent redraws.
  await second.getByRole("button", { name: "Develop this study" }).click();
  await form.locator("[name=title]").fill("Title submitted before the delay");
  let requestStarted, developRequests = 0;
  const started = new Promise(resolve => { requestStarted = resolve; });
  const responseGate = gate();
  await page.route(developRoute, async route => {
    developRequests++;
    const response = await route.fetch();
    requestStarted();
    await responseGate.promise;
    await route.fulfill({ response });
  });
  await form.getByRole("button", { name: "Develop", exact: true }).click();
  await started;
  await page.evaluate(() => refresh());
  await page.locator(".family-take").nth(4).waitFor();
  await form.locator("[name=title]").fill("Title typed while the request was in flight");
  assert(await form.getByRole("button", { name: "Develop", exact: true }).isDisabled());
  await form.evaluate(element => element.requestSubmit());
  await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 100)));
  assert.equal(developRequests, 1, "A redraw and repeated submit reuse the in-flight operation");
  responseGate.open();
  await until(async () => !(await form.getByRole("button", { name: "Develop", exact: true }).isDisabled()), "the delayed submission to settle");
  await page.unroute(developRoute);
  assert.equal(developRequests, 1);
  assert.equal((await call(`/api/v1/families/${family.family_id}`)).takes.length, 5);
  assert.equal(await page.locator(".family-take").nth(4).locator("h4").innerText(), "Title submitted before the delay");
  assert.equal(await form.locator("[name=title]").inputValue(), "Title typed while the request was in flight");
  await second.getByRole("button", { name: "Develop this study" }).click();
  console.log("PASS A delayed success survives a real catalog redraw, rejects a duplicate submit and clears only its submitted snapshot");

  // The board fits narrow screens and works from the keyboard.
  for (const width of [390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    assert(await overflow() <= 0, `The families dialog overflows at ${width}px`);
  }
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.locator("#family-name-input").fill("Orchard, first week");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.querySelector("#family-title").textContent === "Orchard, first week");
  assert.equal((await call("/api/v1/families")).families[0].name, "Orchard, first week");
  await page.keyboard.press("Escape");
  assert(!(await page.locator("#families-dialog").evaluate(dialog => dialog.open)));
  console.log("PASS The board fits 390px and 320px screens, renames with the keyboard and closes with Escape");

  // A Studio refresh keeps the family choice, including a family that is only listed after the page loads.
  await page.locator("#study-family").selectOption(family.family_id);
  const handoff = await page.evaluate(() => ({ format: 1, recipe: formRecipe(), fields: Object.fromEntries(
    ["study-family", "study-family-name", "audition-length"].map(id => [id, { value: document.getElementById(id).value }])),
    panels: { ".study-controls": true, ".creative-controls": true }, view: "studio" }));
  assert.equal(handoff.fields["study-family"].value, family.family_id);
  await page.evaluate(value => sessionStorage.setItem("riff.studioRefresh", JSON.stringify(value)), handoff);
  await page.reload();
  await page.waitForFunction(id => document.getElementById("study-family").value === id, family.family_id);
  assert.equal(await page.locator("#study-family-name").isHidden(), true);
  await page.locator("#study-family").selectOption("__new");
  await page.locator("#study-family-name").fill("Kept across refresh");
  const named = await page.evaluate(() => ({ format: 1, recipe: formRecipe(), fields: Object.fromEntries(
    ["study-family", "study-family-name"].map(id => [id, { value: document.getElementById(id).value }])),
    panels: { ".study-controls": true, ".creative-controls": true }, view: "studio" }));
  await page.evaluate(value => sessionStorage.setItem("riff.studioRefresh", JSON.stringify(value)), named);
  await page.reload();
  await page.waitForFunction(() => document.getElementById("study-family").value === "__new");
  assert.equal(await page.locator("#study-family-name").inputValue(), "Kept across refresh");
  assert.equal(await page.locator("#study-family-name").isVisible(), true);
  console.log("PASS A Studio refresh keeps the study family choice and a new family's name");

  // One-off studies and plain generation are unchanged.
  await page.locator("#study-family").selectOption("");
  const before = (await call("/api/v1/families")).families.length;
  await page.locator("#audition").click();
  await until(async () => (await call("/api/v1/families")).families.length === before + 1, "the one-off study");
  const unnamed = (await call("/api/v1/families")).families.find(item => !item.named);
  assert.equal(unnamed.takes, 1);
  console.log("PASS A study with no family stays a one-off take");
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
  server.kill("SIGTERM");
}
