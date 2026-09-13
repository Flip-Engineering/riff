import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, writeFile } from "node:fs/promises";
import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = []; page.on("pageerror", error => errors.push(error.message));
let backendBuild = null, pageBuild = null, navigationCount = 0, releaseFrame = null, releaseWriter = null, releaseScore = null;
const nextBuild = value => createHash("sha256").update(value).digest("hex");
await page.addInitScript(() => {
  window.__refreshSnapshot = sessionStorage.getItem("riff.studioRefresh");
  // Another tab can save a different shared draft while this tab refreshes.
  if (window.__refreshSnapshot) localStorage.setItem("riff.draft", JSON.stringify({ title: "Another tab’s work", mode: "free" }));
});
await page.route("**/api/state", async route => {
  const response = await route.fetch(), data = await response.json();
  backendBuild ||= data.studio.build;
  data.studio.build = backendBuild;
  await route.fulfill({ response, json: data });
});
await page.route("**/*", async route => {
  if (route.request().resourceType() !== "document" || new URL(route.request().url()).pathname !== "/") return route.fallback();
  navigationCount++;
  const response = await route.fetch();
  let body = await response.text();
  if (pageBuild) body = body.replace(/(name="riff-studio-build" content=")[a-f0-9]{64}/, "$1" + pageBuild);
  await route.fulfill({ response, body });
});
try {
  await page.goto(process.env.RIFF_URL);
  await page.locator("#play:not([disabled])").waitFor();
  const original = await page.locator('meta[name="riff-studio-build"]').getAttribute("content");
  assert.match(original, /^[a-f0-9]{64}$/);
  assert.equal(backendBuild, original);
  assert(await page.locator("#studio-update").isHidden());
  await page.locator("#title").fill("  An unfinished thought  ");
  await page.locator("[name=creation-mode][value=lyrics]").check();
  await page.locator("#lyrics").fill("[Verse]\nA phrase still forming — 열린 바다\nبين الضوء والماء");
  await page.locator("#style").fill("Oud, reeds, a small choir; leave room for silence.");
  await page.locator(".creative-controls > summary").click();
  await page.locator("#duration").fill("");
  await page.locator("#custom-steps").fill("19");
  await page.locator(".refinement-controls > summary").click();
  await page.locator("#control-semantic_top_p").fill("0.4100");
  await page.evaluate(() => {
    document.querySelectorAll('input[type="password"]').forEach(input => { input.value = "private-connection-value-not-a-draft"; });
    audio.loop = true;
  });
  await page.locator("#play").click();
  await page.waitForFunction(() => !audio.paused);
  await page.locator("#lyrics").focus();
  await page.locator("#lyrics").evaluate(input => input.setSelectionRange(8, 19));
  backendBuild = nextBuild(original + "same-version-updated-assets");
  await page.evaluate(() => refresh());
  assert(await page.locator("#studio-update").isVisible());
  assert(await page.locator("#refresh-studio").isDisabled());
  assert.match(await page.locator("#studio-update-hint").innerText(), /Pause playback/);
  assert.equal(await page.evaluate(() => document.activeElement.id), "lyrics");
  assert.deepEqual(await page.locator("#lyrics").evaluate(input => [input.selectionStart, input.selectionEnd]), [8, 19]);
  assert.equal(navigationCount, 1);
  await page.evaluate(() => { document.querySelector("#refresh-studio").dispatchEvent(new MouseEvent("click")); });
  assert.equal(navigationCount, 1);
  assert.equal(await page.locator("#duration").inputValue(), "");
  console.log("PASS Same-version asset update is detected from the page stamp without interrupting editing or playback");

  await page.evaluate(() => { audio.pause(); audio.loop = false; });
  await page.locator("#download-video").click();
  await page.locator("#video-preset").selectOption("custom");
  for (const [id, value] of [["width", "320"], ["height", "180"], ["fps", "2"]]) await page.locator("#video-" + id).fill(value);
  await page.locator("#video-selection").selectOption("passage");
  await page.locator("#video-start").fill("0:00");
  await page.locator("#video-end").fill("0:01");
  const frameArrived = new Promise(resolve => {
    page.route("**/api/video-exports/*/frames", async route => {
      if (!releaseFrame) {
        await new Promise(release => { releaseFrame = release; resolve(); });
      }
      await route.continue();
    });
  });
  await page.locator("#render-video").click();
  await frameArrived;
  assert(await page.locator("#refresh-studio").isDisabled());
  assert.match(await page.locator("#studio-update-hint").innerText(), /still being exported/);
  await page.evaluate(() => { document.querySelector("#refresh-studio").dispatchEvent(new MouseEvent("click")); });
  assert.equal(navigationCount, 1);
  releaseFrame();
  await page.locator("#save-video:not([hidden])").waitFor();
  await page.locator('[data-close="video-dialog"]').click();
  assert(await page.locator("#refresh-studio").isEnabled());
  console.log("PASS A real worker PNG/FFmpeg export completes before Refresh studio becomes available");

  await page.locator("#duration").fill("12");
  await page.locator("#idea-engine").selectOption("phrases");
  const writerBefore = await page.evaluate(() => formRecipe());
  const writerArrived = new Promise(resolve => {
    page.route("**/api/inspiration", async route => {
      await new Promise(release => { releaseWriter = release; resolve(); });
      await route.continue();
    });
  });
  await page.locator("#shuffle-lyrics").click();
  await writerArrived;
  assert(await page.locator("#refresh-studio").isDisabled());
  assert.match(await page.locator("#studio-update-hint").innerText(), /draft is still being written/);
  await page.evaluate(() => document.querySelector("#refresh-studio").dispatchEvent(new MouseEvent("click")));
  assert.equal(navigationCount, 1);
  releaseWriter();
  await page.waitForFunction(() => !ideaBusy);
  assert.notEqual(await page.locator("#lyrics").inputValue(), writerBefore.lyrics);
  assert(await page.locator("#refresh-studio").isEnabled());

  await page.locator("#score-open").click();
  await page.locator("#new-score").click();
  const scoreArrived = new Promise(resolve => {
    page.route("**/api/composition/revise", async route => {
      await new Promise(release => { releaseScore = release; resolve(); });
      await route.continue();
    });
  });
  await page.getByText("Describe a musical change", { exact: true }).click();
  await page.locator("#score-change").fill("Lift the opening phrase");
  await page.locator("#revise-score").click();
  await scoreArrived;
  await page.locator('[data-close="score-dialog"]').click();
  assert(await page.locator("#refresh-studio").isDisabled());
  assert.match(await page.locator("#studio-update-hint").innerText(), /score is still being written/);
  releaseScore();
  await page.waitForFunction(() => document.querySelector("#score-edit-status").textContent === "Suggested edit ready");
  assert(await page.locator("#refresh-studio").isDisabled());
  assert.match(await page.locator("#studio-update-hint").innerText(), /Use or dismiss/);
  await page.locator("#score-open").click();
  await page.locator("#dismiss-score-proposal").click();
  await page.locator('[data-close="score-dialog"]').click();
  assert(await page.locator("#refresh-studio").isEnabled());
  await page.locator("#duration").fill("");
  await page.locator("#control-semantic_top_p").fill("0.4100");
  console.log("PASS Delayed draft/score writer responses survive; pending score suggestions are kept for a decision before refresh");

  await page.evaluate(() => { audio.currentTime = 3.25; audio.volume = .37; audio.muted = true; showView("library"); });
  await page.locator("#search").fill("Reed");
  await page.locator("#sort").selectOption("name");
  const before = await page.evaluate(() => ({ recipe: formRecipe(), selected: selected.id, seconds: audio.currentTime,
    raw: [$("#title").value, $("#duration").value, $("#lyrics").value], version: state.studio.version }));
  // A failed handoff must keep this tab and all editable bytes intact.
  await page.evaluate(() => {
    window.__setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key === "riff.studioRefresh") throw new DOMException("Storage unavailable", "QuotaExceededError");
      return window.__setItem.call(this, key, value);
    };
  });
  await page.locator("#refresh-studio").click();
  assert.match(await page.locator("#studio-update-hint").innerText(), /couldn’t be saved/);
  assert.equal(navigationCount, 1);
  assert.deepEqual(await page.evaluate(() => formRecipe()), before.recipe);
  await page.evaluate(() => { Storage.prototype.setItem = window.__setItem; });
  await mkdir(process.env.RIFF_UPDATE_ARTIFACTS || "data/browser-update", { recursive: true });
  const artifact = process.env.RIFF_UPDATE_ARTIFACTS || "data/browser-update";
  await page.screenshot({ path: `${artifact}/update-storage-error.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.screenshot({ path: `${artifact}/update-mobile-storage-error.png` });
  pageBuild = backendBuild;
  await Promise.all([page.waitForEvent("load"), page.locator("#refresh-studio").click()]);
  await page.waitForFunction(() => !firstLoad && document.querySelector("#toast").textContent.includes("Studio refreshed"));
  await page.waitForFunction(seconds => Math.abs(audio.currentTime - seconds) < .01, before.seconds);
  assert.equal(navigationCount, 2);
  assert(await page.locator("#studio-update").isHidden());
  assert.equal(await page.evaluate(() => state.studio.version), before.version, "A same-version asset update still refreshes");
  assert.deepEqual(await page.evaluate(() => formRecipe()), before.recipe);
  assert.deepEqual(await page.evaluate(() => [$("#title").value, $("#duration").value, $("#lyrics").value]), before.raw);
  assert.equal(await page.evaluate(() => selected.id), before.selected);
  assert(await page.evaluate(() => audio.paused));
  assert.equal(await page.evaluate(() => audio.volume), .37);
  assert(await page.evaluate(() => audio.muted));
  assert(await page.locator("#library-view").isVisible());
  assert.equal(await page.locator("#search").inputValue(), "Reed");
  assert.equal(await page.locator("#sort").inputValue(), "name");
  assert(await page.locator(".creative-controls").evaluate(panel => panel.open));
  assert(await page.locator(".refinement-controls").evaluate(panel => panel.open));
  assert.equal(await page.locator("#control-semantic_top_p").inputValue(), "0.4100");
  assert.deepEqual(await page.evaluate(() => ideaUndo), writerBefore);
  assert(await page.locator("#undo-idea").isEnabled());
  assert.equal(await page.evaluate(() => sessionStorage.getItem("riff.studioRefresh")), null);
  const saved = await page.evaluate(() => window.__refreshSnapshot);
  assert(!saved.includes("private-connection-value-not-a-draft"));
  assert(!saved.includes("api_key"));
  assert(!saved.includes("engine-binary"));
  console.log("PASS Explicit refresh restores raw draft, selection, paused position and library context; credentials stay out of the handoff; failed storage keeps the old tab intact");

  // The first API response can already belong to a replacement server.
  backendBuild = nextBuild(backendBuild + "replaced-before-first-poll");
  await page.reload();
  await page.locator("#studio-update:not([hidden])").waitFor();
  assert.equal(await page.locator('meta[name="riff-studio-build"]').getAttribute("content"), pageBuild);
  assert.equal(await page.evaluate(() => state.studio.build), backendBuild);
  assert(await page.locator("#refresh-studio").isEnabled());
  await page.locator("#studio-update").scrollIntoViewIfNeeded();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await writeFile(`${artifact}/geometry.json`, JSON.stringify(await page.evaluate(() => {
    const banner = document.querySelector("#studio-update"), rect = banner.getBoundingClientRect(), style = getComputedStyle(banner);
    return { html: banner.outerHTML, rect: rect.toJSON(), display: style.display, opacity: style.opacity, visibility: style.visibility,
      scroll: [scrollX, scrollY], at: document.elementFromPoint(rect.x + 10, rect.y + 10)?.outerHTML,
      dialogs: [...document.querySelectorAll("dialog[open]")].map(dialog => dialog.id) };
  }), null, 2));
  assert(await page.locator("#studio-update").evaluate(banner => {
    const rect = banner.getBoundingClientRect();
    return rect.top >= document.querySelector(".app-header").getBoundingClientRect().bottom &&
      banner.contains(document.elementFromPoint(rect.x + 10, rect.y + 10));
  }), "The notice remains visible below the sticky header after scrolling");
  await page.screenshot({ path: `${artifact}/update-mobile.png` });
  await page.setViewportSize({ width: 1280, height: 1000 });
  await page.screenshot({ path: `${artifact}/update-desktop.png` });
  await page.evaluate(() => { document.documentElement.dataset.theme = "light"; });
  await page.screenshot({ path: `${artifact}/update-light.png` });
  assert.deepEqual(errors, []);
  console.log("PASS An update before the first state poll is detected; no automatic reload or navigation loop occurs");
} finally { releaseFrame?.(); releaseWriter?.(); releaseScore?.(); await browser.close(); }
