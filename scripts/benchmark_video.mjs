// Ordinary exports use page-only CDP; diagnostic controls need Playwright.
import assert from "node:assert/strict";

const requested = process.env.RIFF_BENCH_DRIVER;
assert(!requested || ["cdp", "playwright"].includes(requested), "RIFF_BENCH_DRIVER must be cdp or playwright.");
const diagnostics = process.env.RIFF_BENCH_INTERCEPT === "1" ||
  ["1", "frames"].includes(process.env.RIFF_BENCH_BLOB_INTERNALS) ||
  Boolean(process.env.RIFF_BENCH_UPLOAD && process.env.RIFF_BENCH_UPLOAD !== "batch") ||
  Boolean(process.env.RIFF_BENCH_LATENCY_MS && Number(process.env.RIFF_BENCH_LATENCY_MS) !== 0) ||
  Boolean(process.env.RIFF_BENCH_REPEATS && Number(process.env.RIFF_BENCH_REPEATS) !== 1) ||
  Boolean(process.env.RIFF_BENCH_TIMEOUT_MS && Number(process.env.RIFF_BENCH_TIMEOUT_MS) !== 0);
const driver = requested || (diagnostics ? "playwright" : "cdp");
assert(driver !== "cdp" || !diagnostics,
  "Interception, Blob observation, upload comparison, repeats and wait timeouts require RIFF_BENCH_DRIVER=playwright.");
await import(driver === "cdp" ? "./benchmark_video_cdp.mjs" : "./benchmark_video_playwright.mjs");
