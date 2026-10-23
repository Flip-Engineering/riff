import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, linkSync, openSync, unlinkSync, closeSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { chromium } from "playwright";
import { diskSpace, resourceSample } from "../scripts/video-benchmark-resources.mjs";

const root = mkdtempSync(join(tmpdir(), "riff-video-resources-"));
let descriptor;
try {
  const linked = join(root, "linked"), deleted = join(root, "deleted");
  writeFileSync(linked, Buffer.alloc(8193));
  linkSync(linked, join(root, "same-inode"));
  writeFileSync(deleted, Buffer.alloc(16385));
  descriptor = openSync(deleted, "r");
  unlinkSync(deleted);
  const sample = resourceSample(root);
  assert.equal(sample.temporary_logical_bytes, 8193 + (process.platform === "linux" ? 16385 : 0));
  assert.equal(sample.temporary_unlinked_bytes, process.platform === "linux" ? 16385 : null);
  assert(sample.temporary_allocated_bytes > 0);
  assert(sample.rss > 0 && sample.load_average.length === 3);
  assert(sample.processes.some(value => value.pid === process.pid && value.cpu_seconds >= 0));
  assert(!sample.processes.some(value => value.parent === process.pid && value.command === "ps"));
  assert(sample.wall_time);
  assert(diskSpace(root).available_bytes > 0);
  console.log("PASS Temporary-storage sampler deduplicates inodes and includes owned unlinked files on Linux");
  if (process.platform === "linux") {
    const browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE });
    try {
      const page = await browser.newPage();
      await page.evaluate(() => new Promise(resolve => {
        const url = URL.createObjectURL(new Blob(["self.postMessage('ready');self.onmessage=()=>{};"], { type: "text/javascript" }));
        window.resourceWorker = new Worker(url);
        resourceWorker.onmessage = () => { URL.revokeObjectURL(url); resolve(); };
      }));
      const processes = resourceSample(root).processes;
      assert(processes.some(value => value.role === "harness" && value.pid === process.pid));
      const renderers = processes.filter(value => value.role === "renderer");
      assert(renderers.length, "Real Chromium renderer roles must be identified");
      assert(renderers.some(value => value.threads?.some(thread => thread.name.includes("DedicatedWorker") && thread.cpu_seconds >= 0)),
        "Real dedicated-worker thread CPU counters must be retained");
      console.log("PASS Chromium renderer and dedicated-worker CPU labels survive flattened process titles");
    } finally { await browser.close(); }
  }
} finally {
  if (descriptor !== undefined) closeSync(descriptor);
  rmSync(root, { recursive: true, force: true });
}
