import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync, linkSync, openSync, unlinkSync, closeSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
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
  assert(diskSpace(root).available_bytes > 0);
  console.log("PASS Temporary-storage sampler deduplicates inodes and includes owned unlinked files on Linux");
} finally {
  if (descriptor !== undefined) closeSync(descriptor);
  rmSync(root, { recursive: true, force: true });
}
