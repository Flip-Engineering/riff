import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

// Own server, database, credential double, provider double and browser. The
// provider double reads each attached clip and answers without any network call.
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"], { env: process.env });
let diagnostics = "";
server.stderr.on("data", (chunk) => { diagnostics += chunk; });
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", (chunk) => {
    output += chunk;
    if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0]));
  });
  server.once("exit", (code) => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});

function wav(seconds, rate = 8000) {
  const samples = Math.round(seconds * rate);
  const buffer = Buffer.alloc(44 + samples * 2);
  buffer.write("RIFF", 0); buffer.writeUInt32LE(36 + samples * 2, 4); buffer.write("WAVEfmt ", 8);
  buffer.writeUInt32LE(16, 16); buffer.writeUInt16LE(1, 20); buffer.writeUInt16LE(1, 22);
  buffer.writeUInt32LE(rate, 24); buffer.writeUInt32LE(rate * 2, 28); buffer.writeUInt16LE(2, 32);
  buffer.writeUInt16LE(16, 34); buffer.write("data", 36); buffer.writeUInt32LE(samples * 2, 40);
  for (let index = 0; index < samples; index++) buffer.writeInt16LE(index % 2 ? 400 : -400, 44 + index * 2);
  return buffer;
}
const folder = join("test-results", "review-references");
mkdirSync(folder, { recursive: true });
const clips = {};
for (const [name, seconds] of [["Broadside", 1], ["Hall mix", 2], ["Porch tape", 1.5]]) {
  clips[name] = join(folder, `${name}.wav`);
  writeFileSync(clips[name], wav(seconds));
}
const notAudio = join(folder, "notes.wav");
writeFileSync(notAudio, "This is text, not a recording.");

const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1365, height: 1050 }, reducedMotion: "reduce" });
const errors = [], checks = [];
page.on("pageerror", (error) => errors.push(error.message));
const check = (name) => { checks.push(name); console.log(`PASS ${name}`); };
const list = page.locator("#review-reference-list > li");
const noOverflow = async (name) => {
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: page overflow`);
  await page.screenshot({ path: join("test-results", `${name}.png`) });
};
async function attach(path) {
  const count = await list.count();
  const uploaded = page.waitForResponse((response) => response.url().includes("/api/review/references?") && response.request().method() === "POST");
  await page.locator("#review-reference-file").setInputFiles(path);
  assert.equal((await uploaded).status(), 201);
  await page.waitForFunction((expected) => document.querySelectorAll("#review-reference-list > li").length === expected, count + 1);
}
async function describe(row, label, source) {
  const saved = page.waitForResponse((response) => response.url().includes("/api/review/references/") && response.request().method() === "PATCH");
  await row.locator("[data-reference-label]").fill(label);
  await row.locator("[data-reference-source]").fill(source);
  await row.locator("[data-reference-source]").press("Tab");
  assert.equal((await saved).status(), 200);
}
const finished = (count) => page.waitForFunction(
  (expected) => document.querySelectorAll(".producer-note [data-use-review]").length === expected, count);

try {
  await page.goto(`${fixture.url}/?recording=${fixture.track_id}#studio`);
  await page.locator("#producer-panel").waitFor({ state: "visible" });
  await page.locator("#producer-panel > summary").click();
  await page.getByRole("button", { name: "Review settings", exact: true }).click();
  await page.locator("#reviews-enabled").check();
  await page.locator("#review-api-key").fill("browser-fixture-secret");
  assert.equal(await page.locator("#review-probe-seconds").inputValue(), "60");
  await page.getByRole("button", { name: "Save review settings", exact: true }).click();
  await page.locator("#review-settings-dialog").waitFor({ state: "hidden" });
  await page.waitForFunction(() => !document.querySelector("#request-review").disabled);
  const hint = await page.locator("#review-reference-hint").textContent();
  for (const text of ["OpenRouter", "provider", "usage charges", "on this computer only"])
    assert(hint.includes(text), `Disclosure must say: ${text}`);
  check("The clip check timeout is a setting with its default, and the network and privacy disclosure is shown");

  await attach(clips["Broadside"]);
  assert.equal(await list.first().locator("[data-reference-label]").inputValue(), "Broadside");
  await attach(clips["Hall mix"]);
  assert.equal(await list.count(), 2);
  await page.locator("#review-reference-file").setInputFiles(notAudio);
  await page.locator("#review-reference-error").waitFor({ state: "visible" });
  assert((await page.locator("#review-reference-error").textContent()).includes("could not read"));
  assert.equal(await list.count(), 2, "A rejected clip is never attached");
  check("Attach clips, and reject a file that is not audio with a clear message");

  // A check in progress can be cancelled from the form.
  let release;
  await page.route("**/api/review/references?*", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    await new Promise((resolve) => { release = resolve; });
    await route.abort().catch(() => {});
  });
  const cancelled = page.waitForRequest((request) => request.url().endsWith("/api/review/references/cancel") && request.method() === "POST");
  await page.locator("#review-reference-file").setInputFiles(clips["Porch tape"]);
  await page.getByRole("button", { name: "Cancel checking this clip", exact: true }).click();
  await cancelled;
  await page.locator("#review-reference-error", { hasText: "Checking this clip was cancelled." }).waitFor();
  assert.equal(await list.count(), 2, "A cancelled check attaches nothing");
  assert(await page.locator("#add-review-reference").isEnabled());
  assert(await page.locator("#cancel-review-reference").isHidden());
  release?.();
  await page.unroute("**/api/review/references?*");

  await describe(list.nth(0), "Broadside session", "https://example.org/broadside");
  await describe(list.nth(1), "Hall mix", "Recorded live in 1974");
  await attach(clips["Porch tape"]);
  assert(await page.locator("#add-review-reference").isEnabled(), "No clip count stops another clip");
  await page.waitForFunction(() => /would send about .* MB of audio, .* in all, as MP3/.test(document.querySelector("#review-reference-estimate").textContent));
  await page.getByRole("button", { name: "Remove reference Hall mix", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll("#review-reference-list > li").length === 2);
  assert(await page.locator("#add-review-reference").isEnabled());
  assert.deepEqual(
    await page.locator("[data-reference-label]").evaluateAll((inputs) => inputs.map((input) => input.value)),
    ["Broadside session", "Porch tape"]);
  assert.equal(await list.first().locator("[data-reference-source]").inputValue(), "https://example.org/broadside");
  check("Edit label, link and note, see the size estimate, and remove an attachment before submission");

  await noOverflow("review-references-desktop");
  await page.locator("#review-focus").fill("Match the room on the reference.");
  const submitted = page.waitForResponse((response) => response.url().endsWith(`/api/tracks/${fixture.track_id}/reviews`) && response.request().method() === "POST");
  await page.locator("#request-review").click();
  const created = await (await submitted).json();
  assert.deepEqual(created.references.map((item) => item.label), ["Broadside session", "Porch tape"]);
  assert.equal(created.references[0].link, "https://example.org/broadside");
  await finished(1);
  assert.equal(await list.count(), 0, "Submitted clips leave the attachment list");
  const note = page.locator(".producer-note").first();
  assert(await note.locator(".review-observation-group").count() >= 3);
  const groups = await note.locator(".review-observation-group h4").allTextContents();
  assert.deepEqual(groups, ["Generated take", "Broadside session", "Porch tape"]);
  await note.locator(".review-references-used > summary").click();
  const receipt = await note.locator(".review-reference-receipt").textContent();
  for (const text of ["Broadside session", "https://example.org/broadside", "WAV", "sent as MP3", "Porch tape"]) assert(receipt.includes(text), text);
  const saved = await page.evaluate(async (id) => (await fetch(`/api/reviews/${id}`)).text(), created.id);
  assert(!saved.includes("test-results") && !saved.includes("review-references"), "No file location reaches the page");
  assert(JSON.parse(saved).usage.seen.length === 2, "The provider double read both stored clips");
  check("Submit, then see attributed observations and a receipt with labels, links, hashes and formats");

  await note.getByRole("button", { name: "Use these clips in another review" }).click();
  await page.waitForFunction(() => document.querySelectorAll("#review-reference-list > li").length === 2);
  await page.getByRole("button", { name: "Remove reference Porch tape", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll("#review-reference-list > li").length === 1);
  await describe(list.first(), "Broadside, second pass", "");
  await page.locator("#review-focus").fill("fail");
  await page.locator("#request-review").click();
  await page.locator(".producer-note .form-error", { hasText: "Provider rejected" }).waitFor();
  assert(!(await page.locator("#producer-notes").textContent()).includes("browser-fixture-secret"));
  await page.locator(".producer-note").first().getByRole("button", { name: "Use these clips in another review" }).click();
  await page.waitForFunction(() => document.querySelectorAll("#review-reference-list > li").length === 1);
  await page.locator("#review-focus").fill("Try again.");
  await page.locator("#request-review").click();
  await finished(2);
  const first = page.locator(".producer-note").first();
  assert.deepEqual(await first.locator(".review-observation-group h4").allTextContents(), ["Generated take", "Broadside, second pass"]);
  check("A provider error is shown, keeps its clips for retry, and the retry finishes");

  await page.setViewportSize({ width: 390, height: 844 });
  await attach(clips["Hall mix"]);
  await attach(clips["Porch tape"]);
  await page.getByRole("button", { name: "Remove reference Hall mix", exact: true }).click();
  await page.waitForFunction(() => document.querySelectorAll("#review-reference-list > li").length === 1);
  await noOverflow("review-references-phone");
  await page.setViewportSize({ width: 320, height: 740 });
  await noOverflow("review-references-narrow");
  await page.locator("#review-focus").fill("Phone width.");
  await page.locator("#request-review").click();
  await finished(3);
  await page.locator(".producer-note").first().locator(".review-references-used > summary").click();
  await noOverflow("review-references-phone-receipt");
  assert((await page.locator(".producer-note").first().locator(".review-reference-receipt").textContent()).includes("Porch tape"));
  check("Attach, remove and submit at phone widths without horizontal overflow");

  const recording = await page.evaluate(async (id) => (await (await fetch(`/api/tracks/${id}`)).json()).recipe, fixture.track_id);
  assert.equal(recording.seed, "15961");
  assert.deepEqual(errors, []);
  writeFileSync(join("test-results", "review-references.json"), JSON.stringify({ fixture: true, checks, errors }, null, 2) + "\n");
} finally {
  await browser.close();
  server.kill("SIGTERM");
  await once(server, "exit");
}
