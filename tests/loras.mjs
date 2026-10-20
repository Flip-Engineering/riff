import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

// The real importer, HTTP boundary and queue use small architecture-correct
// tensors. Hub transport, admission and audio generation are explicit fixtures.
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"],
  { env: { ...process.env, RIFF_LORA_FIXTURE: "1", RIFF_LORA_CACHE_SECONDS: "300" } });
let diagnostics = "";
server.stderr.on("data", data => diagnostics += data);
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", data => {
    output += data;
    if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0]));
  });
  server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});
const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1365, height: 1050 }, reducedMotion: "reduce" });
page.setDefaultTimeout(15000);
async function eventually(predicate, message, timeout = 15000) {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await predicate();
    if (value) return value;
    if (Date.now() >= deadline) throw new Error(message);
    await page.waitForTimeout(50);
  }
}
const errors = [], requests = [], checks = [];
page.on("pageerror", error => errors.push(error.message));
page.on("request", request => requests.push(request.url()));
const check = name => { checks.push(name); console.log(`PASS ${name}`); };
const dialog = page.locator("#lora-dialog");
const localAPI = async (path, method = "GET", body) => {
  const response = await page.request.fetch(fixture.url + path, {
    method, headers: { "X-Riff-Request": "1" }, ...(body === undefined ? {} : { data: body }),
  });
  assert(response.ok(), `${path}: ${await response.text()}`);
  return response.json();
};
async function openLibrary() {
  await page.locator("#lora-composer").evaluate(node => node.open = true);
  await page.locator("#lora-open").click();
  await dialog.waitFor({ state: "visible" });
}
async function shot(name) {
  const overflow = await page.evaluate(() => [...document.querySelectorAll("body *")].filter(node => {
    const box = node.getBoundingClientRect();
    return box.width > 0 && box.right > innerWidth + 1;
  }).slice(0, 12).map(node => ({tag: node.tagName, id: node.id, class: node.className, right: node.getBoundingClientRect().right})));
  // The existing page header/player have a separate minimum-width issue at
  // 320px. At that size this test scopes layout checks to the new modal.
  if (page.viewportSize().width >= 360)
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${name}: page overflow ${JSON.stringify(overflow)}`);
  assert(await dialog.evaluate(node => !node.open || node.scrollWidth <= node.clientWidth), `${name}: dialog overflow`);
  await page.screenshot({ path: `test-results/${name}.png`, fullPage: false });
}
mkdirSync("test-results", { recursive: true });
try {
  const initial = await page.goto(`${fixture.url}/?recording=${fixture.track_id}#studio`);
  await page.locator("#play:not([disabled])").waitFor();
  assert(initial.headers()["content-security-policy"].includes("connect-src 'self'"));
  assert(!requests.some(url => url.includes("/api/loras/hub")));
  check("Startup loads only the installed library; original CSP remains in force");

  await openLibrary();
  await page.locator("[data-lora-hub]").waitFor();
  await page.locator("#lora-query").fill("no matching sound");
  await page.locator("#lora-search-form").getByRole("button", { name: "Search", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#lora-status").textContent.includes("No styles"));
  await page.locator("#lora-query").fill("folk");
  await page.locator("#lora-search-form").getByRole("button", { name: "Search", exact: true }).click();
  await page.locator("[data-lora-hub]").waitFor();
  const sorted = page.waitForResponse(response => response.url().includes("/api/loras/hub?") && response.url().includes("trendingScore"));
  await page.locator("#lora-sort").selectOption("trendingScore");
  assert.equal((await sorted).status(), 200);
  await page.locator("#lora-filters > summary").click();
  const filtered = page.waitForResponse(response => response.url().includes("/api/loras/hub?") && response.url().includes("stage=ar%2Bnar"));
  await page.locator("#lora-stage-filter").selectOption("ar+nar");
  assert.equal((await filtered).status(), 200);
  await page.waitForFunction(() => document.querySelector("#lora-results").textContent.includes("Composition"));
  assert((await page.locator("#lora-results").textContent()).includes("Sound"));
  const compatible = page.waitForResponse(response => response.url().includes("/api/loras/hub?") && response.url().includes("compatibility=compatible"));
  await page.locator("#lora-compatibility-filter").selectOption("compatible");
  assert.equal((await compatible).status(), 200);
  await page.locator("#lora-filters > summary").click();
  await shot("loras-browse-desktop");
  check("Browse, search, empty results, trending sort and stage/compatibility filters render usable cards");

  await page.locator("[data-lora-hub]").click();
  await page.locator("#lora-install").waitFor();
  assert((await page.locator("#lora-detail").textContent()).includes("apache-2.0"));
  assert((await page.locator("#lora-detail").textContent()).includes("unsupported"));
  assert(await page.locator("#lora-detail input:disabled").count() >= 1);
  await page.locator(".lora-readme > summary").click();
  assert.equal(await page.locator("#lora-detail script, #lora-detail iframe, #lora-detail img").count(), 0);
  assert.equal(await page.evaluate(() => window.loraXSS), undefined);
  assert(!(await page.locator("#lora-detail").innerHTML()).includes('href="javascript:'));
  assert((await page.getByRole("link", { name: "Pinned prompt" }).getAttribute("href")).includes(`/blob/${fixture.loras.revision}/`));
  assert((await page.locator("#lora-detail").textContent()).includes("Fingerpicked folk, warm room, gentle reeds."));
  const preview = page.locator("#lora-detail audio").first();
  const media = page.waitForResponse(response => response.url().includes("/api/loras/hub/media?"));
  await preview.evaluate(audio => audio.play());
  assert([200, 206].includes((await media).status()));
  await page.waitForFunction(() => document.querySelector("#lora-detail audio").currentTime > 0);
  await preview.evaluate(audio => audio.pause());
  assert(requests.every(url => new URL(url).origin === fixture.url), "No browser-side Hub or model-card request");
  check("Details expose license and unsupported-file reasons; sanitized README and same-origin audio preview work");

  await page.locator("#lora-install").click();
  await eventually(async () => (await localAPI("/api/loras")).loras.some(item => item.source?.type === "huggingface"), "Installed adapter did not appear");
  await page.locator("#lora-installed-tab").click();
  const items = await localAPI("/api/loras");
  const paired = items.loras.find(item => item.source?.type === "huggingface");
  assert.deepEqual(paired.stages, ["ar", "nar"]);
  await page.locator(`[data-lora-edit="${paired.id}"]`).click();
  await page.locator("#lora-name").fill("Folk room");
  await page.locator("#lora-notes").fill("A browser-tested library entry.");
  await page.locator("#lora-words").fill("trbdr-test");
  await page.locator("#lora-default").fill("0.8");
  await page.locator("#lora-edit-form").getByRole("button", { name: "Save changes" }).click();
  await page.waitForFunction(() => document.querySelector("#lora-status").textContent.includes("details saved"));
  await page.locator(`[data-lora-use="${paired.id}"]`).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await page.locator("#lora-ar").inputValue(), paired.id);
  assert.equal(await page.locator("#lora-nar").inputValue(), paired.id);
  await page.locator("#lora-ar-scale").fill("0.65");
  await page.locator("#lora-nar-scale").fill("1.2");
  await page.locator("#lora-trigger").click();
  assert((await page.locator("#style").inputValue()).includes("trbdr-test"));
  assert.deepEqual((await page.evaluate(() => formRecipe())).loras, [{ id: paired.id, ar_scale: .65, nar_scale: 1.2 }]);
  check("Verified install, metadata editing, trigger insertion and independent composer strengths work");

  await page.locator("#duration").fill("2");
  const accepted = page.waitForResponse(response => response.url().endsWith("/api/generations") && response.request().method() === "POST");
  await page.locator("#generate").click();
  const queued = await accepted;
  assert.equal(queued.status(), 201, await queued.text());
  const job = await queued.json();
  assert.equal(job.recipe.lora_provenance[0].files.length, 2);
  assert.equal(job.recipe.lora_provenance[0].ar_scale, .65);
  await eventually(async () => (await localAPI("/api/state")).jobs.some(item => item.id === job.id && item.status === "done"), "Queued adapter take did not finish");
  const saved = await page.evaluate(() => formRecipe());
  await page.reload();
  await page.locator("#play:not([disabled])").waitFor();
  assert.deepEqual((await page.evaluate(() => formRecipe())).loras, saved.loras);
  assert.deepEqual((await page.evaluate(() => formRecipe())).lora_provenance, saved.lora_provenance);
  check("Queued take captures exact adapter files and strengths; draft reload restores its receipt");

  await openLibrary();
  await page.locator("#lora-installed-tab").click();
  await page.locator(`[data-lora-edit="${paired.id}"]`).click();
  await page.locator("#lora-check-update").click();
  await page.waitForFunction(() => document.querySelector("#lora-detail").textContent.includes("bbbbbbbb"));
  await page.locator("#lora-install").click();
  const versions = await eventually(async () => {
    const items = (await localAPI("/api/loras")).loras.filter(item => item.source?.type === "huggingface");
    return items.some(item => item.source.revision === "b".repeat(40)) ? items : null;
  }, "Updated LoRA revision did not install");
  assert(versions.some(item => item.id === paired.id));
  assert(versions.some(item => item.source.revision === "b".repeat(40)));
  check("Update check installs the new pinned revision beside the old recording's version");

  await page.locator("#lora-import-open").click();
  await page.locator("#lora-import-config").setInputFiles({ name: "adapter_config.json", mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify({ r: 1, lora_alpha: 1, base_model_name_or_path: "m-a-p/YuE2-3B", note: "音乐" })) });
  await page.locator("#lora-import-file").setInputFiles(fixture.loras.upload);
  await page.waitForFunction(() => document.querySelector("#lora-status").textContent.includes("imported"));
  const imported = (await localAPI("/api/loras")).loras.find(item => item.files.some(file => file.original_name === "local-study.safetensors"));
  assert(imported);
  await page.setViewportSize({ width: 390, height: 844 });
  await shot("loras-installed-mobile");
  await page.setViewportSize({ width: 320, height: 740 });
  await shot("loras-installed-narrow");
  await page.locator(`[data-lora-edit="${paired.id}"]`).click();
  let warning = "";
  page.once("dialog", async prompt => { warning = prompt.message(); await prompt.accept(); });
  await page.locator("#lora-delete").click();
  await eventually(async () => !(await localAPI("/api/loras")).loras.some(item => item.id === paired.id), "Deleted LoRA remained in the library");
  assert(warning.includes("saved references"));
  await page.getByRole("button", { name: "Close LoRA library", exact: true }).click();
  assert((await page.locator("#lora-ar option:checked").textContent()).includes("Unavailable"));
  assert.equal((await page.evaluate(() => formRecipe())).lora_provenance[0].id, paired.id);
  check("Local file import and phone layouts work; deletion warns about saved takes and retains their identity");

  // Read-only UI states use explicit response fixtures while the real HTTP and
  // importer behavior above remain exercised without route replacements.
  await page.route("**/api/loras", async route => {
    const response = await route.fetch(), value = await response.json();
    value.enabled = false;
    value.loras.push({ id: "f".repeat(64), name: "Unavailable adapter", stages: [], files: [], tags: [], error: "Fixture manifest is corrupt" });
    await route.fulfill({ response, json: value });
  });
  await openLibrary();
  await page.locator("#lora-installed-tab").click();
  await page.getByText("Fixture manifest is corrupt", { exact: true }).waitFor();
  for (const button of await page.locator("[data-lora-use]").all()) assert(await button.isDisabled());
  assert((await page.locator("#lora-status").textContent()).includes("Update the music engine"));
  await page.unroute("**/api/loras");
  await page.locator("#lora-browse-tab").click();
  await page.locator("#lora-query").fill("offline");
  await page.locator("#lora-search-form").getByRole("button", { name: "Search", exact: true }).click();
  await page.waitForFunction(() => document.querySelector("#lora-status").textContent.includes("Hub is offline"));
  await page.locator("#lora-installed-tab").click();
  await page.locator(`[data-lora-use="${imported.id}"]`).waitFor();
  check("Old-engine, corrupt-entry and offline states preserve the usable library without JavaScript errors");

  await page.locator("#lora-browse-tab").click();
  await page.locator("#lora-query").fill("folk");
  await page.locator("#lora-search-form").getByRole("button", { name: "Search", exact: true }).click();
  await page.locator("[data-lora-hub]").waitFor();
  await page.locator("[data-lora-hub]").click();
  await page.locator("#lora-install").waitFor();
  await page.route("**/api/loras/tasks/*", route => route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: "LoRA download task was not found." }) }));
  await page.locator("#lora-install").click();
  await page.waitForFunction(() => document.querySelector("#lora-status").textContent.includes("no longer active"));
  await page.unroute("**/api/loras/tasks/*");
  const resumed = page.waitForResponse(response => response.url().endsWith("/api/loras") && response.request().method() === "POST");
  await page.locator("#lora-install").click();
  assert.equal((await resumed).status(), 202);
  await page.waitForFunction(() => document.querySelector("#lora-status").textContent.includes("Installation finished"));
  assert.deepEqual(errors, []);
  check("A task lost after a studio restart releases the controls for same-revision retry");
  console.log(`PASS ${checks.length} LoRA browser scenarios`);
} catch (error) {
  await page.screenshot({ path: "test-results/loras-failure.png", fullPage: false });
  console.error(diagnostics.slice(-6000));
  throw error;
} finally {
  await browser.close();
  const stopped = once(server, "exit"); server.kill("SIGTERM"); await stopped;
}
