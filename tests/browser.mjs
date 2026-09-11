import assert from "node:assert/strict";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

// This launches a separate headless browser, never an existing user browser.
const base = process.env.RIFF_URL || "http://127.0.0.1:7878";
const generate = process.argv.includes("--generate");
const existingId = process.env.RIFF_TEST_TRACK;
const resultDir = "test-results";
mkdirSync(resultDir, { recursive: true });
let executablePath = process.env.RIFF_BROWSER_EXECUTABLE;
if (!executablePath && !existsSync(chromium.executablePath())) {
  const cache = join(homedir(), "Library/Caches/ms-playwright");
  if (existsSync(cache)) {
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
}
const browser = await chromium.launch({ headless: true, executablePath });
const context = await browser.newContext({
  viewport: { width: 1365, height: 1050 },
  reducedMotion: "reduce",
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});
const report = { generated: generate, checks: [] };
const check = (label) => {
  report.checks.push(label);
  console.log(`PASS ${label}`);
};

async function snapshot(name) {
  await page.screenshot({
    path: `${resultDir}/${name}.png`,
    fullPage: !(await page.locator("dialog[open]").count()),
    animations: "disabled",
  });
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    `${name}: horizontal overflow`,
  );
}
async function waitText(locator, text) {
  await page.waitForFunction(
    ({ selector, text }) =>
      document.querySelector(selector)?.textContent === text,
    { selector: locator, text },
  );
}
async function downloadFrom(action, name) {
  const pending = page.waitForEvent("download");
  await action();
  const download = await pending;
  assert.equal(await download.failure(), null);
  const path = `${resultDir}/${name}`;
  await download.saveAs(path);
  return path;
}

try {
  await page.goto(base);
  await page
    .getByRole("button", { name: "Play recording", exact: true })
    .waitFor({ state: "visible" });
  await page.waitForFunction(() => !document.querySelector("#play").disabled);
  await snapshot("studio-desktop");
  await page
    .getByRole("button", { name: "Play recording", exact: true })
    .click();
  await page.waitForFunction(
    () => document.querySelector("audio").currentTime > 0.1,
  );
  await page
    .getByRole("button", { name: "Pause recording", exact: true })
    .click();
  const waveform = page.getByRole("slider", { name: "Seek in recording" });
  const bounds = await waveform.boundingBox();
  await waveform.click({
    position: { x: bounds.width / 2, y: bounds.height / 2 },
  });
  assert(
    await page.evaluate(() => {
      const audio = document.querySelector("audio");
      return (
        audio.currentTime > audio.duration * 0.4 &&
        audio.currentTime < audio.duration * 0.6
      );
    }),
  );
  check("Real audio playback and seeking");

  await page.locator('[name="creation-mode"][value="lyrics"]').check();
  await page.locator("#idea-engine").selectOption("phrases");
  const suggestion = page.waitForResponse((response) =>
    response.url().endsWith("/api/inspiration"),
  );
  await page.getByRole("button", { name: "Find a spark", exact: true }).click();
  await suggestion;
  await page.waitForFunction(() => !document.querySelector("#spark").disabled);
  assert(
    (await page.getByLabel("The sound", { exact: true }).inputValue()).includes(
      "lead vocal",
    ),
  );
  await page.getByRole("button", { name: "Reed room", exact: true }).click();
  const style = await page
    .getByLabel("The sound", { exact: true })
    .inputValue();
  await page.getByLabel("Recording title", { exact: true }).fill("Reedlight");
  const lyrics =
    "[Verse]\nReeds lean low where the silver runs.\nWe carry the quiet into the sun.\nA little light, a little room.\nA song takes shape in the afternoon.";
  await page.getByLabel("The words", { exact: true }).fill(lyrics);
  const longDraft = Array.from(
    { length: 24 },
    (_, section) =>
      `[Part ${section + 1}]\n${lyrics.split("\n").slice(1).join("\n")}`,
  ).join("\n\n");
  await page
    .getByRole("button", { name: "Expand lyrics", exact: true })
    .click();
  await page.locator("#writing-pad").fill(longDraft);
  assert.equal(await page.locator("#lyrics").inputValue(), longDraft);
  await snapshot("writing-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await snapshot("writing-mobile");
  await page.setViewportSize({ width: 320, height: 740 });
  await snapshot("writing-narrow");
  await page.keyboard.press("Escape");
  await page.reload();
  assert.equal(await page.locator("#lyrics").inputValue(), longDraft);
  await page
    .getByRole("button", { name: "Expand direction", exact: true })
    .click();
  await page
    .locator("#writing-pad")
    .fill(`${style}\nA gradually expanding ensemble.`);
  await page.getByRole("button", { name: "Done", exact: true }).click();
  assert.equal(
    await page.locator("#style").inputValue(),
    `${style}\nA gradually expanding ensemble.`,
  );
  await page.locator("#style").fill(style);
  await page.locator("#lyrics").fill(lyrics);
  await page.setViewportSize({ width: 1365, height: 1050 });
  check(
    "Full-song writing, immediate draft persistence, and desktop/390px/320px editors",
  );
  await page.getByLabel("Length", { exact: true }).fill("12");
  await page.getByText("More creative control", { exact: true }).click();
  await page.getByLabel("Composition", { exact: true }).selectOption("melody");
  await page
    .getByLabel("ABC score", { exact: false })
    .waitFor({ state: "visible" });
  await page.getByLabel("Composition", { exact: true }).selectOption("off");
  await page.locator("#seed").fill("15961");
  await page.reload();
  assert.equal(
    await page.getByLabel("Recording title", { exact: true }).inputValue(),
    "Reedlight",
  );
  assert.equal(
    await page.getByLabel("The words", { exact: true }).inputValue(),
    lyrics,
  );
  assert.equal(await page.locator("#seed").inputValue(), "15961");
  check(
    "Sound suggestions, presets, optional score controls, and draft restoration",
  );

  if (generate || existingId) {
    if (
      !(await page
        .getByRole("button", { name: "Reedlight room", exact: true })
        .count())
    ) {
      await page
        .getByRole("button", { name: "Save this sound", exact: true })
        .click();
      await page
        .getByLabel("Sound name", { exact: true })
        .fill("Reedlight room");
      await page
        .getByRole("button", { name: "Save sound", exact: true })
        .click();
      await page
        .getByRole("button", { name: "Reedlight room", exact: true })
        .waitFor();
    }
    check("Saved reusable sound");
    let job = { id: existingId };
    if (generate) {
      const accepted = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/generations") &&
          response.status() === 201,
      );
      await page.getByRole("button", { name: /^Generate take/ }).click();
      job = await (await accepted).json();
      report.job_id = job.id;
      console.log(`Generating real YuE2 take ${job.id}`);
      await page.locator("#queue-panel").waitFor({ state: "visible" });
      await page
        .getByLabel("Recording title", { exact: true })
        .fill("Reedlight — unstarted variation");
      const queued = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/generations") &&
          response.status() === 201,
      );
      await page.getByRole("button", { name: /^Add to queue/ }).click();
      const queuedJob = await (await queued).json();
      await page.locator(`[data-cancel="${queuedJob.id}"]`).click();
      await page.waitForFunction(
        () => document.querySelector("#queued-jobs").textContent === "",
      );
      check("Queue another take and cancel it before it loads a model");
      await snapshot("studio-generating");
      await page.waitForFunction(
        () =>
          document.querySelector("#playing-title")?.textContent === "Reedlight",
        null,
        { timeout: 240000 },
      );
    } else {
      await page
        .getByRole("navigation")
        .getByRole("button", { name: /^Library/ })
        .click();
      await page.locator(`#library-grid [data-select="${job.id}"]`).click();
    }
    const trackResponse = await context.request.get(
      `${base}/api/tracks/${job.id}`,
    );
    assert.equal(trackResponse.status(), 200);
    const track = await trackResponse.json();
    assert.equal(track.recipe.lyrics, lyrics);
    assert.equal(track.recipe.style, style);
    assert.equal(track.recipe.seed, "15961");
    assert.equal(track.audio.sample_rate, 48000);
    assert.equal(track.audio.channels, 2);
    assert(track.audio.duration > 0 && track.audio.duration <= 12);
    assert(track.audio.waveform.some((peak) => peak > 0));
    report.track = track;
    check(
      generate
        ? "Real generation completes, retains its exact recipe, and enters the player"
        : "Previously generated take retains its recipe and enters the player",
    );
    await page
      .getByRole("button", { name: "Play recording", exact: true })
      .click();
    await page.waitForFunction(
      () => document.querySelector("audio").currentTime > 0.1,
    );
    await page
      .getByRole("button", { name: "Pause recording", exact: true })
      .click();
    if (
      await page
        .getByRole("button", { name: "Favorite this recording", exact: true })
        .count()
    ) {
      await page
        .getByRole("button", { name: "Favorite this recording", exact: true })
        .click();
    }
    await page
      .getByRole("button", {
        name: "Remove recording from favorites",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("button", { name: "Recording details", exact: true })
      .click();
    await page
      .getByLabel("Notes", { exact: true })
      .fill(
        "First take made in Riff. Reeds, brushed drums, and a little afternoon light.",
      );
    await page
      .getByRole("button", { name: "Save changes", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Recording details", exact: true })
      .click();
    const wavPath = await downloadFrom(
      () => page.getByRole("link", { name: "WAV", exact: true }).click(),
      "reedlight.wav",
    );
    assert.equal(readFileSync(wavPath).subarray(0, 4).toString(), "RIFF");
    const recipePath = await downloadFrom(
      () => page.getByRole("link", { name: "Recipe", exact: true }).click(),
      "reedlight.json",
    );
    assert.equal(JSON.parse(readFileSync(recipePath)).lyrics, lyrics);
    const artPath = await downloadFrom(
      () => page.getByRole("button", { name: "Artwork", exact: true }).click(),
      "reedlight.svg",
    );
    assert(readFileSync(artPath, "utf8").includes("<svg"));
    await snapshot("recording-details");
    await context.grantPermissions(["clipboard-read", "clipboard-write"], {
      origin: base,
    });
    await page
      .getByRole("button", { name: "Copy listening link", exact: true })
      .click();
    await page.getByText("Listening link copied.", { exact: true }).waitFor();
    const listeningUrl = await page.evaluate(() =>
      navigator.clipboard.readText(),
    );
    assert.equal(new URL(listeningUrl).searchParams.get("recording"), job.id);
    const linkPage = await context.newPage();
    await linkPage.goto(listeningUrl);
    await linkPage.waitForFunction(
      () =>
        document.querySelector("#playing-title")?.textContent === "Reedlight",
    );
    assert.equal(
      await linkPage.locator("audio").getAttribute("src"),
      `/api/tracks/${job.id}/audio`,
    );
    await linkPage.close();
    check("Copied listening link opens its recording in a fresh tab");
    await page
      .getByRole("button", { name: "Archive recording", exact: true })
      .click();
    await page
      .getByRole("navigation")
      .getByRole("button", { name: /^Library/ })
      .click();
    await page.getByRole("button", { name: "Archive", exact: true }).click();
    await page
      .getByRole("button", { name: "Details for Reedlight", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Restore recording", exact: true })
      .click();
    await page.getByRole("button", { name: "Favorites", exact: true }).click();
    await page.getByRole("button", { name: "Reedlight", exact: true }).click();
    await page.getByRole("button", { name: /^Make a variation/ }).click();
    assert.equal(
      await page.getByLabel("Recording title", { exact: true }).inputValue(),
      "Reedlight — variation",
    );
    assert.equal(await page.locator("#seed").inputValue(), "");
    assert.equal(
      await page.getByLabel("The words", { exact: true }).inputValue(),
      lyrics,
    );
    await page.reload();
    await waitText("#playing-title", "Reedlight");
    await page
      .getByRole("button", {
        name: "Remove recording from favorites",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("button", { name: "Recording details", exact: true })
      .click();
    await page
      .getByLabel("Notes", { exact: true })
      .waitFor({ state: "visible" });
    assert(
      (await page.getByLabel("Notes", { exact: true }).inputValue()).includes(
        "First take made in Riff.",
      ),
    );
    await page
      .getByRole("button", { name: "Close recording details", exact: true })
      .click();
    check(
      "Favorites, notes, archive/restore, WAV/recipe/art downloads, variations, and reload persistence",
    );
    await snapshot("studio-ready");
  }

  await page
    .getByRole("navigation")
    .getByRole("button", { name: /^Library/ })
    .click();
  await page
    .getByRole("button", { name: "All recordings", exact: true })
    .click();
  await page
    .getByRole("searchbox", { name: "Find a recording" })
    .fill("a title that is absent");
  await page
    .getByRole("heading", { name: "Try a different search.", exact: true })
    .waitFor();
  await page.getByRole("searchbox", { name: "Find a recording" }).fill("");
  await page
    .getByLabel("Sort recordings", { exact: true })
    .selectOption("oldest");
  await page
    .getByLabel("Sort recordings", { exact: true })
    .selectOption("newest");
  await snapshot("library-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  await snapshot("library-mobile");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "Studio", exact: true })
    .click();
  await snapshot("studio-mobile");
  await page.setViewportSize({ width: 320, height: 740 });
  await snapshot("studio-narrow");
  await page
    .getByRole("button", {
      name: "About Riff",
      exact: true,
    })
    .click();
  await page
    .getByRole("heading", { name: "About Riff", exact: true })
    .waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await page.locator("dialog[open]").count(), 0);
  check(
    "Search empty state, sorting, desktop/390px/320px layouts, and keyboard dialog dismissal",
  );
  assert.deepEqual(errors, [], "Browser errors");
  check("No JavaScript or browser console errors");
  writeFileSync(
    `${resultDir}/browser-report.json`,
    JSON.stringify(report, null, 2) + "\n",
  );
} finally {
  await browser.close();
}
