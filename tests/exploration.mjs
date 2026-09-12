import assert from "node:assert/strict";
import { existsSync, readdirSync, mkdirSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright";

// Separate local browser. --generate explicitly opts into three real previews.
const base = process.env.RIFF_URL || "http://127.0.0.1:7878";
const generate = process.argv.includes("--generate");
const write = process.argv.includes("--writer");
const cache = join(homedir(), "Library/Caches/ms-playwright");
const executablePath =
  process.env.RIFF_BROWSER_EXECUTABLE ||
  (existsSync(chromium.executablePath())
    ? undefined
    : readdirSync(cache)
        .filter((name) => name.startsWith("chromium_headless_shell-"))
        .sort((a, b) => b.localeCompare(a, undefined, { numeric: true }))
        .map((name) =>
          join(
            cache,
            name,
            "chrome-headless-shell-mac-arm64/chrome-headless-shell",
          ),
        )
        .find(existsSync));
const browser = await chromium.launch({ headless: true, executablePath });
const page = await browser.newPage({
  viewport: { width: 1365, height: 1050 },
  reducedMotion: "no-preference",
});
page.setDefaultTimeout(15000);
const report = { generated: generate, checks: [], takes: [] };
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
page.on("console", (message) => {
  if (message.type() === "error") errors.push(message.text());
});
mkdirSync("test-results", { recursive: true });
function check(label) {
  report.checks.push(label);
  console.log(`PASS ${label}`);
}
async function snap(name) {
  if (!(await page.locator("dialog[open]").count()))
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: "instant" }));
  await page.screenshot({
    path: `test-results/${name}.png`,
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
async function mode(value) {
  await page.locator(`[name=creation-mode][value=${value}]`).check();
}
async function shuffle(button) {
  const response = page.waitForResponse(
    (response) => response.url().endsWith("/api/inspiration"),
    { timeout: 120000 },
  );
  await page.locator(button).click();
  const result = await response;
  assert.equal(result.status(), 200, await result.text());
  await page.waitForFunction(
    () => !document.querySelector("#surprise").disabled,
  );
  return result.json();
}
async function take(label) {
  const pending = page.waitForResponse((response) =>
    response.url().endsWith("/api/generations"),
  );
  await page.locator("#generate").click();
  const response = await pending;
  assert.equal(response.status(), 201, await response.text());
  const job = await response.json();
  console.log(`Generating ${label}: ${job.id}`);
  await page.locator("#queue-panel").waitFor({ state: "visible" });
  const observed = new Set();
  const started = Date.now();
  for (;;) {
    const state = await (await page.request.get(`${base}/api/state`)).json();
    if (state.live?.id === job.id) observed.add(state.live.stage);
    const result = state.jobs.find((item) => item.id === job.id);
    if (!["queued", "running", "cancelling"].includes(result.status)) {
      assert.equal(result.status, "done", JSON.stringify(result));
      break;
    }
    assert(
      Date.now() - started < 600000,
      "Preview exceeded the browser test deadline",
    );
    await page.waitForTimeout(1000);
  }
  const track = await (
    await page.request.get(`${base}/api/tracks/${job.id}`)
  ).json();
  assert(
    track.audio.duration > 1 &&
      track.audio.waveform.some((value) => value > 0.001),
  );
  await page.waitForFunction((id) => selected?.id === id, job.id);
  report.takes.push({
    id: job.id,
    title: track.title,
    recipe: track.recipe,
    duration: track.audio.duration,
    metrics: track.metrics,
    stages: [...observed],
  });
  check(
    `Real ${label}: ${track.audio.duration.toFixed(2)}s WAV in ${track.metrics.wall_seconds.toFixed(2)}s`,
  );
  return track;
}

try {
  await page.goto(base);
  await page.waitForFunction(
    () => !document.querySelector("#generate").disabled,
  );
  assert.equal(
    await page.locator("[name=creation-mode]:checked").inputValue(),
    "free",
  );
  assert.equal(await page.locator("#style").inputValue(), "");
  assert(await page.locator("#words-space").isHidden());
  await snap("exploration-desktop");

  if (generate) {
    await page.locator("#title").fill("Unwritten current");
    await page.locator("#duration").fill("12");
    await page.getByText("More creative control", { exact: true }).click();
    await page.locator("#custom-steps").fill("9");
    await page.locator("#temperature").fill("1.3");
    await page.locator("#seed").fill("728512");
    const free = await take("blank Free play");
    assert.equal(free.recipe.lyrics, "");
    assert.equal(free.recipe.style, "");
    assert.equal(free.recipe.steps, 9);
    assert.equal(free.recipe.temperature, 1.3);
    assert.equal(
      free.metrics.command[free.metrics.command.indexOf("--lyrics") + 1],
      "",
    );
    assert(!free.metrics.command.some((value) => value.startsWith("style=")));
  }

  await mode("lyrics");
  await page
    .locator("#lyrics")
    .fill("Two moons talk over tea.\nThe teaspoons disagree.");
  await page
    .locator("#style")
    .fill("An impossible genre, bass clarinet and irregular silences");
  await page.locator("#idea-engine").selectOption("phrases");
  await page.locator("#hold-words").click();
  const oldWords = await page.locator("#lyrics").inputValue();
  const oldSound = await page.locator("#style").inputValue();
  await shuffle("#surprise");
  assert.equal(await page.locator("#lyrics").inputValue(), oldWords);
  assert.notEqual(await page.locator("#style").inputValue(), oldSound);
  await page.locator("#undo-idea").click();
  assert.equal(await page.locator("#style").inputValue(), oldSound);
  await page.locator("#hold-words").click();
  await page.locator("#hold-sound").click();
  await shuffle("#surprise");
  assert.equal(await page.locator("#style").inputValue(), oldSound);
  assert.notEqual(await page.locator("#lyrics").inputValue(), oldWords);
  await page.locator("#hold-sound").click();
  check(
    "Independent surprises, word/sound holds, and undo preserve chosen material",
  );

  const words = await page.locator("#lyrics").inputValue();
  await mode("free");
  await mode("lyrics");
  assert.equal(await page.locator("#lyrics").inputValue(), words);
  await page.locator("#energy").focus();
  const compass = page.waitForResponse((response) =>
    response.url().endsWith("/api/inspiration"),
  );
  await page.keyboard.press("ArrowRight");
  await compass;
  await page.waitForFunction(() => !document.querySelector("#spark").disabled);
  const pad = await page.locator("#compass-pad").boundingBox();
  const pointed = page.waitForResponse((response) =>
    response.url().endsWith("/api/inspiration"),
  );
  await page.mouse.click(pad.x + pad.width * 0.8, pad.y + pad.height * 0.25);
  await pointed;
  await page.waitForFunction(() => !document.querySelector("#spark").disabled);
  assert(Number(await page.locator("#texture").inputValue()) > 70);
  check(
    "Switching modes keeps hidden drafts; keyboard and pointer compass create editable suggestions",
  );

  await page.locator("#style").fill("Prepared piano, unhurried space");
  await page.locator("#save-sound").click();
  const soundName = `Exploration check ${Date.now()}`;
  await page.locator("#preset-name").fill(soundName);
  await page.locator("#preset-form button[type=submit]").click();
  await page.locator("#preset-dialog").waitFor({ state: "hidden" });
  await page.locator("#manage-sounds").click();
  await page
    .locator("#sound-list button")
    .filter({ hasText: soundName })
    .click();
  await page.locator("#sound-edit-name").fill(soundName + " edited");
  await page
    .locator("#sound-edit-style")
    .fill("Prepared piano, bowed glass, no fixed pulse");
  await page.locator("#sound-edit-form button[type=submit]").click();
  await page
    .locator("#sound-list button")
    .filter({ hasText: soundName + " edited" })
    .waitFor();
  await snap("sound-manager");
  await page.locator("#use-managed-sound").click();
  assert.equal(
    await page.locator("#style").inputValue(),
    "Prepared piano, bowed glass, no fixed pulse",
  );
  await page.locator("#manage-sounds").click();
  await page
    .locator("#sound-list button")
    .filter({ hasText: soundName + " edited" })
    .click();
  const removed = page.waitForResponse(
    (response) => response.request().method() === "DELETE",
  );
  await page.locator("#remove-sound").click();
  assert.equal((await removed).status(), 200);
  await page.waitForFunction(
    (name) => !document.querySelector("#sound-list").textContent.includes(name),
    soundName,
  );
  await page.keyboard.press("Escape");
  check("Saved sounds create, browse, update, use, and remove through the UI");

  await mode("surprise");
  await page.locator("#idea-engine").selectOption("ai");
  await page
    .locator("#creative-brief")
    .fill(
      "A playful little song about a satellite cooking supper for the moon. Original, strange, tender words.",
    );
  await page.locator("#lyrics").fill("");
  await page.locator("#style").fill("");
  await page.locator("#title").fill("Satellite supper");
  // The in-progress creative draft should survive a reload.
  await page.reload();
  await page.waitForFunction(
    () => !document.querySelector("#generate").disabled,
  );
  if (generate) {
    await page.locator("#duration").fill("12");
    if (!(await page.locator("#custom-steps").isVisible()))
      await page.getByText("More creative control", { exact: true }).click();
    await page.locator("#custom-steps").fill("8");
    await page.locator("#seed").fill("828512");
    const song = await take("AI-written surprise song");
    assert(song.recipe.lyrics.length > 10);
    assert.equal(song.recipe.lyrics_source, "ai");
    assert.equal(song.title, "Satellite supper");
    assert(song.recipe.writer_model.includes("Qwen3"));
    assert(
      report.takes.at(-1).stages.some((stage) => stage.includes("Writing")),
    );
    await page.getByRole("button", { name: /Make a variation/ }).click();
    assert.equal(
      await page.locator("#lyrics").inputValue(),
      song.recipe.lyrics,
    );

    await mode("instrumental");
    await page.locator("#title").fill("Paper lanterns");
    await page
      .locator("#style")
      .fill(
        "Instrumental, bass clarinet, prepared piano, delicate percussion, unusual drifting rhythm, no vocals",
      );
    await page.locator("#duration").fill("10");
    await page.locator("#seed").fill("928512");
    assert(await page.locator("#instrumental-warning").isVisible());
    const instrumental = await take("instrumental request");
    assert.equal(instrumental.recipe.lyrics, "");
    assert.equal(
      instrumental.recipe.style,
      "Instrumental, bass clarinet, prepared piano, delicate percussion, unusual drifting rhythm, no vocals",
    );
  } else {
    if (write) {
      const idea = await shuffle("#surprise");
      assert.equal(idea.lyrics_source, "ai");
      assert.equal(await page.locator("#lyrics").inputValue(), idea.lyrics);
      assert(idea.lyrics.length > 10);
      check(
        "Real standalone AI writer returns editable words and sound through the UI",
      );
      const before = await page.locator("#lyrics").inputValue();
      const pending = page.waitForResponse(
        (response) => response.url().endsWith("/api/inspiration"),
        { timeout: 120000 },
      );
      await page.locator("#surprise").click();
      await page.locator("#stop-writing").waitFor({ state: "visible" });
      await page.waitForTimeout(700);
      await page.locator("#stop-writing").click();
      assert.equal((await (await pending).json()).cancelled, true);
      await page.waitForFunction(
        () => !document.querySelector("#surprise").disabled,
      );
      assert.equal(await page.locator("#lyrics").inputValue(), before);
      check(
        "Stop writing cancels an actual MLX child without replacing the draft",
      );
    } else {
      await page.locator("#idea-engine").selectOption("phrases");
      await shuffle("#surprise");
    }
  }

  await page.locator("[data-visual=sound]").click();
  await page.locator("#play").click();
  await page.waitForFunction(
    () => soundMotion?.frames.some(frame => frame[0] > 0) && audio.currentTime > 0.2,
  );
  await snap("live-sound");
  await page.getByRole("navigation").locator("[data-view=library]").click();
  await page.locator("#mini-player").waitFor({ state: "visible" });
  await page.waitForFunction(() => soundAnimation === null);
  const oldTitle = await page.locator("#mini-title").textContent();
  await page.locator("#next-track").click();
  await page.waitForFunction(
    (title) => document.querySelector("#mini-title").textContent !== title,
    oldTitle,
  );
  await page.waitForFunction(
    () => !audio.paused,
  );
  await page.locator("#mini-play").click();
  await page.waitForFunction(
    () => audio.paused,
  );
  await page.locator("#mini-play").click();
  await page.waitForFunction(
    () => !audio.paused,
  );
  await snap("exploration-library");
  await page.locator("#mini-title").click();
  await page.waitForFunction(() => soundAnimation !== null);
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.waitForFunction(() => soundAnimation === null);
  await page.locator("#play").click();
  await page.waitForFunction(() => audio.paused && soundAnimation === null);
  check(
    "Seeded audio animation, continuous library listening, next track, pause/resume, and reduced motion",
  );
  await page.locator("[data-visual=art]").click();
  await page.setViewportSize({ width: 390, height: 844 });
  await snap("exploration-mobile");
  await page.setViewportSize({ width: 320, height: 740 });
  await snap("exploration-narrow");
  assert.deepEqual(errors, []);
  check("Desktop, 390px, and 320px layouts without overflow or browser errors");
} finally {
  writeFileSync(
    `test-results/exploration-${generate ? "generation" : write ? "writer" : "ui"}-report.json`,
    JSON.stringify({ ...report, errors }, null, 2) + "\n",
  );
  await browser.close();
}
