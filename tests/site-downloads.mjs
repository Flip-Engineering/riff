import assert from "node:assert/strict";
import { createServer } from "node:http";
import { once } from "node:events";
import { mkdir, readFile } from "node:fs/promises";
import { resolve, sep, extname } from "node:path";
import { chromium } from "playwright";

const site = resolve("site");
const assetURL = version => `https://github.com/Flip-Engineering/riff/releases/download/${version}/Riff-Setup-macos-arm64.zip`;
const baseline = assetURL("v0.6.4");
const release = (tag, published, extra = {}) => ({
  tag_name: tag, published_at: published, draft: false, prerelease: false,
  assets: [{ name: "Riff-Setup-macos-arm64.zip", state: "uploaded", browser_download_url: assetURL(tag) }], ...extra,
});
const older = release("v9.2.0", "2026-08-01T00:00:00Z");
const completed = release("v9.3.0", "2026-08-02T00:00:00Z");
const pending = release("v9.4.0", "2026-08-03T00:00:00Z", { assets: [] });
const malformed = release("v9.5.0", "2026-08-04T00:00:00Z", {
  assets: [{ name: "Riff-Setup-macos-arm64.zip", state: "uploaded", browser_download_url: "https://example.invalid/Riff-Setup-macos-arm64.zip" }],
});
const draft = release("v9.6.0", "2026-08-05T00:00:00Z", { draft: true });
const unpublished = release("v9.7.0", null);
const prerelease = release("v9.8.0", "2026-08-06T00:00:00Z", { prerelease: true });
const mismatched = release("v9.9.0", "2026-08-07T00:00:00Z", {
  assets: [{ name: "Riff-Setup-macos-arm64.zip", state: "uploaded", browser_download_url: assetURL("v9.3.0") }],
});
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, "http://127.0.0.1").pathname;
  const file = resolve(site, "." + (pathname === "/" ? "/index.html" : pathname));
  if (!file.startsWith(site + sep)) { response.writeHead(404).end(); return; }
  try {
    const body = await readFile(file);
    const type = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png" }[extname(file)] || "application/octet-stream";
    response.writeHead(200, { "Content-Type": type }).end(body);
  } catch { response.writeHead(404).end(); }
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
const url = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
try {
  await mkdir("test-results", { recursive: true });
  const scenarios = [
    { name: "Completed latest release goes directly to its installer", latest: completed, expected: assetURL("v9.3.0"), requests: 1 },
    { name: "Pending source release keeps the newest completed installer", latest: pending, list: [older, draft, pending, malformed, unpublished, prerelease, mismatched, completed], expected: assetURL("v9.3.0"), requests: 2 },
    { name: "Malformed latest URL falls back to a completed release", latest: malformed, list: [malformed, completed], expected: assetURL("v9.3.0"), requests: 2 },
    { name: "Draft latest release is excluded", latest: draft, list: [draft, older], expected: assetURL("v9.2.0"), requests: 2 },
    { name: "Unpublished and invalid releases cannot replace the verified link", latest: pending, list: [draft, unpublished, malformed, prerelease, mismatched, { ...completed, published_at: 42 }], expected: baseline, requests: 2 },
    { name: "Latest API failure retains the verified graphical download", latestStatus: 503, expected: baseline, requests: 1 },
    { name: "Release-list API failure retains the verified graphical download", latest: pending, listStatus: 503, expected: baseline, requests: 2 },
    { name: "Invalid release-list response retains the verified link", latest: pending, list: { message: "unavailable" }, expected: baseline, requests: 2 },
    { name: "Network failure retains the verified graphical download", networkFailure: true, expected: baseline, requests: 1 },
  ];
  for (const scenario of scenarios) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [], requests = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("https://api.github.com/repos/Flip-Engineering/riff/**", async route => {
      const latest = route.request().url().endsWith("/latest");
      requests.push(route.request().url());
      if (scenario.networkFailure) return route.abort("failed");
      return route.fulfill({ status: (latest ? scenario.latestStatus : scenario.listStatus) || 200,
        json: (latest ? scenario.latest : scenario.list) ?? {} });
    });
    await page.goto(url, { waitUntil: "networkidle" });
    const links = page.getByRole("link", { name: "Download for Mac ↗", exact: true });
    assert.equal(await links.count(), 2, scenario.name);
    for (const link of await links.all()) {
      assert(await link.isVisible());
      assert.equal(await link.getAttribute("href"), scenario.expected, scenario.name);
    }
    assert.equal(requests.length, scenario.requests);
    assert.deepEqual(errors, []);
    if (scenario.latest === pending && scenario.expected === assetURL("v9.3.0"))
      await page.screenshot({ path: "test-results/site-downloads-desktop.png" });
    console.log("PASS " + scenario.name);
    await page.close();
  }
  const page = await browser.newPage({ javaScriptEnabled: false, viewport: { width: 390, height: 844 } });
  await page.goto(url, { waitUntil: "networkidle" });
  const links = page.getByRole("link", { name: "Download for Mac ↗", exact: true });
  assert.equal(await links.count(), 2);
  for (const link of await links.all()) assert.equal(await link.getAttribute("href"), baseline);
  assert(await page.getByText("macOS 15+ · Apple Silicon · Desktop preview", { exact: true }).isVisible());
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: "test-results/site-downloads-mobile.png" });
  console.log("PASS Mobile page retains its graphical installer without JavaScript");
  await page.close();
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
