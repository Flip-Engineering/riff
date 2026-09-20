import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync } from "node:fs";
import { chromium } from "playwright";

const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"],
  {env: {...process.env, RIFF_LINEAGE_FIXTURE: "1"}});
let log = "", browser;
server.stderr.on("data", data => log += data);
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", data => { output += data; if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
  server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${log}`)));
});
try {
  browser = await chromium.launch({headless: true});
  const page = await browser.newPage({viewport: {width: 1365, height: 1050}});
  const errors = []; page.on("pageerror", error => errors.push(error.message));
  await page.goto(`${fixture.url}/?recording=${fixture.track_id}#studio`);
  await page.locator("#track-details:not([disabled])").waitFor();
  const draft = await page.evaluate(() => formRecipe());
  const graph = await (await page.request.get(`${fixture.url}/api/tracks/${fixture.track_id}/lineage`)).json();
  assert.equal(graph.nodes.length, 3); assert.equal(graph.edges.length, 2);
  assert.equal(graph.version, 1);
  await page.locator("#track-details").click();
  await page.locator("#edit-notes").fill("Unsubmitted listening note");
  await page.locator("#detail-lineage > summary").click();
  await page.locator("#lineage-content").getByText("Alternate study", {exact: true}).waitFor();
  assert.match(await page.locator("#lineage-content").textContent(), /archived/);
  await page.locator(".lineage-origin details").first().evaluate(element => { element.open = true; });
  assert.match(await page.locator(".lineage-origin").first().textContent(), /Length/);
  const popupReady = page.waitForEvent("popup");
  await page.locator("#lineage-content").getByRole("link", {name: "Open Long development in a new tab", exact: true}).click();
  const popup = await popupReady;
  await popup.locator("#play:not([disabled])").waitFor();
  assert.equal(await page.locator("#edit-notes").inputValue(), "Unsubmitted listening note");
  assert.deepEqual(await page.evaluate(() => formRecipe()), draft);
  await popup.close();
  mkdirSync("test-results", {recursive: true});
  for (const width of [1365, 390, 320]) {
    await page.setViewportSize({width, height: width === 1365 ? 1050 : 844});
    await page.locator("#detail-lineage").scrollIntoViewIfNeeded();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    assert(await page.locator("#track-dialog").evaluate(element => element.scrollWidth <= element.clientWidth));
    await page.screenshot({path: `test-results/lineage-${width}.png`});
  }
  assert.deepEqual(errors, []);
  console.log("PASS Recorded branches and input changes include archived takes; opening a relative preserves notes/draft; narrow layouts fit");
} finally {
  await browser?.close();
  const stopped = once(server, "exit"); server.kill("SIGTERM"); await stopped;
}
