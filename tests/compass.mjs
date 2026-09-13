import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 }, hasTouch: true });
const errors = []; page.on("pageerror", e => errors.push(e.message));
let requests = [], held = null, defer = false;
async function point(texture, energy) {
  await page.locator("#compass-pad").scrollIntoViewIfNeeded();
  return page.locator("#compass-pad svg").evaluate((svg, { texture, energy }) => {
    const p = new DOMPoint(26 + texture * 308, 112 - energy * 90).matrixTransform(svg.getScreenCTM());
    return { x: p.x, y: p.y };
  }, { texture, energy });
}
async function click(texture, energy) {
  const p = await point(texture, energy);
  const hit = await page.evaluate(p => document.elementFromPoint(p.x, p.y)?.outerHTML.slice(0, 180), p);
  assert(await page.evaluate(p => !!document.elementFromPoint(p.x, p.y)?.closest("#compass-pad"), p), JSON.stringify({ p, hit }));
  await page.mouse.click(p.x, p.y);
}
async function values(texture, energy) {
  const actual = await page.evaluate(() => ({ texture: document.querySelector("#texture").value, energy: document.querySelector("#energy").value,
    busy: ideaBusy, scroll: scrollY, point: document.querySelector("#compass-point").getBoundingClientRect().toJSON() }));
  assert(Math.abs(Number(actual.texture) - texture * 100) <= 1, JSON.stringify({ expected: { texture, energy }, actual, requests }));
  assert(Math.abs(Number(actual.energy) - energy * 100) <= 1, JSON.stringify({ expected: { texture, energy }, actual, requests }));
}
try {
  await page.goto(process.env.RIFF_URL); await page.locator("#play:not([disabled])").waitFor();
  await page.locator("#idea-engine").selectOption("phrases");
  await page.route("**/api/inspiration", async route => {
    requests.push(route.request().postDataJSON());
    if (defer) { defer = false; await new Promise(resolve => { held = resolve; }); }
    await route.fulfill({ response: await route.fetch() });
  });
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    await click(.18, .72);
    await page.waitForFunction(() => !ideaBusy);
    await values(.18, .72);
    const start = await point(.18, .72), end = await point(.81, .26);
    const count = requests.length;
    await page.mouse.move(start.x, start.y); await page.mouse.down();
    await page.mouse.move(end.x, end.y, { steps: 8 });
    await values(.81, .26); assert.equal(requests.length, count, "Dragging moves the point without a request for every move");
    await page.mouse.up(); await page.waitForFunction(() => !ideaBusy);
    await values(.81, .26); assert.equal(requests.length, count + 1);
    const screen = await point(.81, .26);
    const marker = await page.locator("#compass-point").boundingBox();
    assert(Math.abs(marker.x + marker.width / 2 - screen.x) < 2);
    assert(Math.abs(marker.y + marker.height / 2 - screen.y) < 2);
  }
  console.log("PASS Compass clicks and drags follow the visible SVG point at desktop and mobile sizes; one request per release");
  await page.setViewportSize({ width: 1440, height: 1050 });
  const count = requests.length; defer = true;
  await click(.2, .3);
  while (!held) await new Promise(resolve => setTimeout(resolve, 10));
  await click(.7, .8); await values(.7, .8);
  await click(.9, .4); await values(.9, .4);
  assert.equal(requests.length, count + 1, "Only one writer runs at a time");
  held(); held = null;
  await page.waitForFunction(() => !ideaBusy && compassPending === null);
  assert.equal(requests.length, count + 2, "Only the most recent released position is written next");
  assert.equal(requests.at(-1).texture, .9); assert.equal(requests.at(-1).energy, .4);
  await values(.9, .4);
  const saved = await page.evaluate(() => JSON.parse(localStorage.getItem("riff.draft")));
  assert.equal(saved.texture, .9); assert.equal(saved.energy, .4);
  await page.locator("#energy").focus(); await page.keyboard.press("ArrowUp");
  await page.waitForFunction(() => !ideaBusy); await values(.9, .41);
  console.log("PASS Compass stays responsive while writing, coalesces later gestures, preserves the final position and supports keyboard sliders");
  await page.setViewportSize({ width: 390, height: 1000 });
  const touch = await page.context().newCDPSession(page);
  async function finger(type, texture, energy) {
    const p = await point(texture, energy);
    await touch.send("Input.dispatchTouchEvent", { type, touchPoints: ["touchEnd", "touchCancel"].includes(type) ? [] : [{ ...p, id: 1 }] });
  }
  const beforeTouch = requests.length;
  await finger("touchStart", .25, .7); await finger("touchMove", .65, .35);
  await values(.65, .35); assert.equal(requests.length, beforeTouch);
  await finger("touchEnd", .65, .35); await page.waitForFunction(() => !ideaBusy);
  assert.equal(requests.length, beforeTouch + 1); await values(.65, .35);
  await finger("touchStart", .4, .4); await finger("touchMove", .55, .55);
  await finger("touchCancel", .55, .55);
  assert.equal(requests.length, beforeTouch + 1, "A cancelled gesture does not write an idea");
  await finger("touchStart", .8, .2); await finger("touchEnd", .8, .2);
  await page.waitForFunction(() => !ideaBusy); await values(.8, .2);
  const captured = await point(.8, .2), outside = await point(1.2, -.5);
  await page.mouse.move(captured.x, captured.y); await page.mouse.down(); await page.mouse.move(outside.x, outside.y);
  await page.mouse.up(); await page.waitForFunction(() => !ideaBusy); await values(1, 0);
  await click(.5, .5); await page.waitForFunction(() => !ideaBusy); await values(.5, .5);
  console.log("PASS Touch dragging, cancellation and pointer capture outside the pad recover correctly without repeated requests");
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
