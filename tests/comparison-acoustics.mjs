import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const server = spawn(process.env.RIFF_PYTHON || "python3", ["-B", "-u", "tests/review_browser_server.py"], {
  env: { ...process.env, RIFF_MULTI_TRACK_FIXTURE: "1" },
});
let diagnostics = "", browser;
server.stderr.on("data", value => diagnostics += value);
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", value => { output += value; if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
  server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});
try {
  const state = await (await fetch(fixture.url + "/api/state")).json();
  const ids = state.tracks.map(track => track.id);
  assert.equal(ids.length, 2);
  const original = await (await fetch(`${fixture.url}/api/tracks/${ids[0]}`)).json();
  const settings = { decode_core_frames: 1024, decode_halo_frames: 16, vae_storage: 0 };
  const scenarios = [
    { name: "Decoder changes explain an audio refinement while the musical inputs remain unchanged",
      left: { acoustic: settings }, right: { acoustic: settings, decoder: { core_frames: 512, halo_frames: 0, storage: 1 } },
      rows: [["Audio sections", "1024 frames → 512 frames"], ["Audio overlap", "16 frames → 0 frames"], ["Decoder precision", "Model default → 32-bit float"]] },
    { name: "Explicit values matching the captured settings are the same effective finish",
      left: { acoustic: settings, refinement: { semantic_top_k: 50, semantic_top_p: 0.9 } },
      right: { acoustic: settings, decoder: { core_frames: 1024, halo_frames: 16, storage: 0 }, refinement: { semantic_top_p: 0.9, semantic_top_k: 50 } }, rows: [] },
    { name: "Explicit zero overlap and native precision override a nonzero captured setting",
      left: { acoustic: { ...settings, vae_storage: 1 } },
      right: { acoustic: { ...settings, vae_storage: 1 }, decoder: { halo_frames: 0, storage: 0 } },
      rows: [["Audio overlap", "16 frames → 0 frames"], ["Decoder precision", "32-bit float → Model default"]] },
    { name: "Older recordings do not acquire invented decoder settings", left: {}, right: {}, rows: [] },
    { name: "An older take's missing settings remain distinct from known captured settings",
      left: {}, right: { acoustic: settings },
      rows: [["Audio sections", "Not recorded → 1024 frames"], ["Audio overlap", "Not recorded → 16 frames"], ["Decoder precision", "Not recorded → Model default"]] },
  ];
  browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
  mkdirSync("test-results", { recursive: true });
  for (const scenario of scenarios) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, reducedMotion: "reduce" });
    const errors = []; page.on("pageerror", error => errors.push(error.message));
    await page.addInitScript(({ ids }) => localStorage.setItem("riff.comparison", JSON.stringify({ a: ids[0], b: ids[1] })), { ids });
    await page.route(/\/api\/tracks\/[a-f0-9]{32}$/, async route => {
      const response = await route.fetch(), item = await response.json();
      const extra = item.id === ids[0] ? scenario.left : scenario.right;
      item.recipe = { ...original.recipe, acoustic_source: "", decoder: {}, ...extra };
      await route.fulfill({ response, json: item });
    });
    await page.goto(fixture.url);
    await page.locator("#play:not([disabled])").waitFor();
    const before = await page.evaluate(() => formRecipe());
    await page.locator("#take-comparison > summary").click();
    await page.locator("#compare-a").selectOption(ids[0]);
    await page.locator("#compare-b").selectOption(ids[1]);
    await page.locator(".comparison-changes > summary").click();
    const changes = page.locator("#compare-changes");
    if (scenario.rows.length) {
      await changes.getByText(scenario.rows[0][0], { exact: true }).waitFor();
      const rows = await changes.locator("dl > div").evaluateAll(nodes => nodes.map(node => [node.querySelector("dt").textContent, node.querySelector("dd").textContent]));
      assert.deepEqual(rows, scenario.rows);
    } else {
      await changes.getByText("The recorded settings are the same.", { exact: true }).waitFor();
      assert.equal(await changes.locator("dl").count(), 0);
    }
    assert.deepEqual(await page.evaluate(() => formRecipe()), before);
    if (scenario === scenarios[0]) {
      await changes.scrollIntoViewIfNeeded();
      await page.screenshot({ path: "test-results/comparison-acoustic-desktop.png" });
      await page.setViewportSize({ width: 320, height: 844 });
      await changes.scrollIntoViewIfNeeded();
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
      await page.screenshot({ path: "test-results/comparison-acoustic-mobile.png" });
    }
    assert.deepEqual(errors, []);
    console.log("PASS " + scenario.name);
    await page.close();
  }
} finally {
  await browser?.close();
  const stopped = once(server, "exit"); server.kill("SIGTERM"); await stopped;
}
