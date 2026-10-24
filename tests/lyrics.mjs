import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync } from "node:fs";
import { launch } from "./browser-launch.mjs";

// Own server, temporary library and fixture audio. No music model or network.
const server = spawn(process.env.RIFF_PYTHON || "python3", [
  "-u",
  "tests/review_browser_server.py",
], { env: { ...process.env, RIFF_LYRICS_FIXTURE: "1" } });
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
mkdirSync("test-results", { recursive: true });
const browser = await launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
try {
  const page = await browser.newPage({
    viewport: { width: 1365, height: 1050 },
    reducedMotion: "reduce",
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const apiLyrics = async (id) =>
    (await (await page.request.get(`${fixture.url}/api/tracks/${id}`)).json()).lyrics;
  const openLyrics = async () => {
    if (await page.locator("#lyrics-panel").isHidden())
      await page.locator("#lyrics-toggle").click();
    await page.locator("#lyrics-panel").waitFor({ state: "visible" });
  };

  await page.goto(`${fixture.url}/?recording=${fixture.track_id}#studio`);
  await page.locator("#play:not([disabled])").waitFor();
  const recorded = await apiLyrics(fixture.track_id);
  assert.equal(recorded.state, "recorded");
  assert(recorded.text.includes("\n"), "Saved lyrics retain their line breaks");
  assert(await page.locator("#lyrics-panel").isHidden(), "Lyrics start collapsed");
  assert.equal(await page.locator("#lyrics-toggle").getAttribute("aria-expanded"), "false");

  const transport = await page.locator("#play").boundingBox();
  await page.evaluate(() => {
    window.__lyricLoads = 0;
    audio.addEventListener("loadstart", () => { window.__lyricLoads += 1; });
  });
  await page.locator("#play").click();
  await page.waitForFunction(() => !audio.paused && audio.currentTime > 0.15);
  const before = await page.evaluate(() => audio.currentTime);
  await page.locator("#lyrics-toggle").click();
  await page.locator("#lyrics-panel").waitFor({ state: "visible" });
  assert.equal(await page.locator("#lyrics-text").textContent(), recorded.text);
  assert(await page.locator("#lyrics-note").isHidden());
  const during = await page.evaluate(() => ({
    time: audio.currentTime,
    paused: audio.paused,
    loads: window.__lyricLoads,
  }));
  assert.equal(during.paused, false, "Opening lyrics must not pause playback");
  assert(during.time >= before, "Opening lyrics must not seek or restart audio");
  assert.equal(during.loads, 0, "Opening lyrics must not reload the audio source");
  assert.equal(await page.locator("#play").getAttribute("aria-label"), "Pause recording");
  assert.equal(await page.locator("#lyrics-toggle").getAttribute("aria-expanded"), "true");
  const after = await page.locator("#play").boundingBox();
  assert(Math.abs(after.y - transport.y) < 1, "Lyrics must not displace the transport");

  // A later prompt edit belongs to the draft, never to the saved composition.
  await page.locator('[name="creation-mode"][value="lyrics"]').check();
  await page.locator("#lyrics").fill("A later prompt edit that must not replace recorded words.");
  assert.equal(await page.locator("#lyrics-text").textContent(), recorded.text);
  assert.equal((await apiLyrics(fixture.track_id)).text, recorded.text);

  // Closing is also playback-safe, and the open state survives a reload.
  await page.locator("#lyrics-toggle").click();
  await page.locator("#lyrics-panel").waitFor({ state: "hidden" });
  assert.equal(await page.evaluate(() => audio.paused), false);
  await page.locator("#lyrics-toggle").click();
  await page.reload();
  await page.waitForFunction(
    (title) => document.querySelector("#playing-title")?.textContent === title,
    "Reedlight",
  );
  await page.locator("#lyrics-panel").waitFor({ state: "visible" });
  assert.equal(await page.locator("#lyrics-text").textContent(), recorded.text);
  await page.getByRole("button", { name: /^Make a variation/ }).click();
  assert.equal(await page.locator("#lyrics").inputValue(), recorded.text);

  // Exact Unicode, punctuation and blank lines survive the API and the panel.
  await page.goto(`${fixture.url}/?recording=${fixture.lyrics.unicode}#studio`);
  await page.waitForFunction(
    (title) => document.querySelector("#playing-title")?.textContent === title,
    "Bilingual study",
  );
  await openLyrics();
  assert.equal(await page.locator("#lyrics-text").textContent(), fixture.lyrics.unicode_text);
  assert((await page.locator("#lyrics-text").textContent()).includes("\n\n[Choir]"));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#lyrics-panel").waitFor({ state: "visible" });
  assert(
    await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    "Lyrics stay within a 390px viewport",
  );
  await page.screenshot({ path: "test-results/lyrics-mobile.png" });

  // Known wordless takes and unrecorded history are distinct; neither is blank.
  await page.setViewportSize({ width: 1365, height: 1050 });
  await page.goto(`${fixture.url}/?recording=${fixture.lyrics.instrumental}#studio`);
  await page.waitForFunction(
    (title) => document.querySelector("#playing-title")?.textContent === title,
    "Wordless study",
  );
  await openLyrics();
  assert.equal(await page.locator("#lyrics-note").textContent(), "Instrumental");
  assert(await page.locator("#lyrics-text").isHidden());
  assert.equal((await apiLyrics(fixture.lyrics.instrumental)).state, "instrumental");

  await page.goto(`${fixture.url}/?recording=${fixture.lyrics.missing}#studio`);
  await page.waitForFunction(
    (title) => document.querySelector("#playing-title")?.textContent === title,
    "Older recording",
  );
  await openLyrics();
  assert.equal(
    await page.locator("#lyrics-note").textContent(),
    "Lyrics weren't retained for this recording",
  );
  assert(await page.locator("#lyrics-text").isHidden());
  assert.equal((await apiLyrics(fixture.lyrics.missing)).state, "missing");

  assert.deepEqual(errors, []);
  console.log("PASS Recorded lyrics match the saved composition exactly in the now-playing panel");
  console.log("PASS Opening and closing lyrics preserves playback time, play state, source and transport position");
  console.log("PASS Lyrics toggle state and exact text survive reload; variations use the recorded words");
  console.log("PASS Unicode and line breaks render exactly; instrumental and missing states are truthful");
} finally {
  await browser.close();
  server.kill("SIGTERM");
  await once(server, "exit");
}
