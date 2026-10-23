import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { chromium } from "playwright";

// Study families through the Studio's own controls, against a real server: start a family from a study,
// add another, see what they share and how each differs, select one and develop it with edited controls.
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"], { env: { ...process.env } });
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
  await form.getByRole("button", { name: "Develop", exact: true }).click();
  await page.locator(".family-take").nth(2).waitFor();
  const grown = await call(`/api/v1/families/${family.family_id}`);
  assert.equal(grown.takes.length, 3);
  const developed = grown.takes[2];
  assert.equal(developed.role, "develop");
  assert.equal(developed.seed, "22");
  assert.deepEqual(developed.parent, { kind: "operation", id: grown.takes[1].operation_id });
  assert.equal(developed.derivation.retained.seed, true);
  assert.equal(developed.derivation.selection, chosen.selection.current.id);
  assert.equal(developed.settings.max_seconds, 20);
  assert.equal(developed.settings.steps, 12);
  const first = grown.takes[0], original = await call(`/api/jobs/${grown.takes[1].job_id}`);
  assert.equal(original.recipe.max_seconds, 7);
  assert.equal(original.recipe.seed, "22");
  assert.match(await page.locator(".family-take").nth(2).innerText(), /Length\s+7 seconds\s+→\s+20 seconds/);
  assert.equal(await page.locator(".family-take .family-role").nth(2).textContent(), "Developed");
  assert.equal(first.role, "study");
  console.log("PASS Select records the choice with its reason, and develop keeps the seed, applies the edited controls and records parent, family and selection");

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
