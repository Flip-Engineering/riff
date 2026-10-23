// Run serial, matched exports in an isolated library. Never point this at a live studio.
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync, statSync, mkdtempSync, rmSync, createReadStream, appendFileSync } from "node:fs";
import { resolve } from "node:path";
import { cpus, platform, release, loadavg } from "node:os";
import { chromium } from "playwright";
import { diskSpace, resourceSample, sampleNvidia } from "./video-benchmark-resources.mjs";

const output = resolve(process.argv[2] || `test-results/video-benchmark-${Date.now()}`);
mkdirSync(output); // A fresh directory protects earlier receipts.
const temporary = mkdtempSync(`${output}/temporary-`);
const fixtureEnv = { ...process.env, TMPDIR: temporary, TMP: temporary, TEMP: temporary };
const timeout = Number(process.env.RIFF_BENCH_TIMEOUT_MS || 0);
const requestedTransports = (process.env.RIFF_BENCH_TRANSPORTS || "png,h264").split(",");
assert(requestedTransports.length && new Set(requestedTransports).size === requestedTransports.length && requestedTransports.every(value => ["png", "h264"].includes(value)));
assert(Number.isFinite(timeout) && timeout >= 0);
const sourceRevision = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
const sourceDirty = Boolean(execFileSync("git", ["status", "--porcelain", "--untracked-files=no"], { encoding: "utf8" }).trim());
console.log(JSON.stringify({ sourceRevision, sourceDirty, temporary, disk: diskSpace(output) }));
const durations = (process.env.RIFF_BENCH_DURATIONS || "2,10").split(",").map(Number);
assert(durations.every(value => Number.isFinite(value) && value > 0));
const uploadMode = process.env.RIFF_BENCH_UPLOAD || "batch";
const repeats = Number(process.env.RIFF_BENCH_REPEATS || 1);
const latencyMs = Number(process.env.RIFF_BENCH_LATENCY_MS || 0);
const sampleResources = process.env.RIFF_BENCH_SAMPLE_RESOURCES !== "0";
const dimensions = (process.env.RIFF_BENCH_DIMENSIONS || "1920x1080x30,2160x2160x30,2160x2160x60")
  .split(",").map(value => value.split("x").map(Number));
assert(["single", "batch", "compare"].includes(uploadMode));
assert(Number.isInteger(repeats) && repeats > 0);
assert(Number.isFinite(latencyMs) && latencyMs >= 0);
assert(dimensions.every(value => value.length === 3 && value.every(n => Number.isFinite(n) && n > 0)));
async function audioHash(url) {
  const response = await fetch(url); assert(response.ok);
  const hash = createHash("sha256");
  for await (const chunk of response.body) hash.update(chunk);
  return hash.digest("hex");
}
async function fileHash(file) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(file)) hash.update(chunk);
  return hash.digest("hex");
}
async function frameHashes(file, destination) {
  const child = spawn("ffmpeg", ["-v", "error", "-y", "-threads", "2", "-i", file,
    "-map", "0:v:0", "-f", "framemd5", destination], { stdio: ["ignore", "ignore", "pipe"] });
  let diagnostics = "";
  child.stderr.on("data", bytes => { diagnostics = (diagnostics + bytes).slice(-16000); });
  const [code] = await once(child, "exit"); assert.equal(code, 0, diagnostics);
  const hash = createHash("sha256");
  let tail = "", frames = 0;
  for await (const chunk of createReadStream(destination)) {
    hash.update(chunk);
    const lines = (tail + chunk.toString()).split("\n"); tail = lines.pop();
    frames += lines.filter(line => line && !line.startsWith("#")).length;
  }
  if (tail && !tail.startsWith("#")) frames++;
  return { sha256: hash.digest("hex"), frames };
}
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"], { env: fixtureEnv });
let diagnostics = "";
server.stderr.on("data", bytes => { diagnostics = (diagnostics + bytes).slice(-8000); });
let browser;
let page;
let blobPage;
let blobSampler;
let blobTail = Promise.resolve();
const blobSamples = [];
const results = [];
let sampler;
let samplingError;
let gpuSampler;
let activeRun;
let interrupted = false;
const interrupt = () => {
  interrupted = true;
  void browser?.close().catch(() => {});
  server.kill("SIGTERM");
};
process.once("SIGINT", interrupt);
process.once("SIGTERM", interrupt);
try {
  const fixture = await new Promise((resolve, reject) => {
    let line = "";
    server.stdout.on("data", bytes => {
      line += bytes;
      if (line.includes("\n")) {
        try { resolve(JSON.parse(line.split("\n")[0])); } catch (error) { reject(error); }
      }
    });
    server.once("error", reject);
    server.once("exit", code => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
  });
  const launchOptions = { headless: true, viewport: { width: 1365, height: 1050 },
    executablePath: process.env.RIFF_BROWSER_EXECUTABLE, env: fixtureEnv,
    args: [...(process.platform === "darwin" ? ["--use-angle=metal"] : []),
      ...JSON.parse(process.env.RIFF_BENCH_BROWSER_ARGS || "[]")] };
  const incognito = process.env.RIFF_BENCH_CONTEXT === "incognito";
  let context;
  if (incognito) {
    browser = await chromium.launch(launchOptions);
    context = await browser.newContext({ viewport: launchOptions.viewport });
  } else {
    context = await chromium.launchPersistentContext(`${temporary}/browser-profile`, launchOptions);
    browser = context.browser();
  }
  assert(!interrupted, "Benchmark interrupted");
  const browserSession = await browser.newBrowserCDPSession();
  const graphics = await browserSession.send("SystemInfo.getInfo");
  page = await context.newPage();
  const profileEveryFrame = process.env.RIFF_BENCH_BLOB_INTERNALS === "frames";
  if (["1", "frames"].includes(process.env.RIFF_BENCH_BLOB_INTERNALS)) {
    blobPage = await context.newPage();
    const sampleBlobs = metadata => {
      const capture = async () => { try {
        await blobPage.goto("chrome://blob-internals/");
        const body = await blobPage.locator("body").innerText();
        const lengths = [...body.matchAll(/Length: ([\d,]+)/g)].map(match => Number(match[1].replaceAll(",", "")));
        const png = [...body.matchAll(/Content Type: image\/png\nType: data\nLength: ([\d,]+)/g)].map(match => Number(match[1].replaceAll(",", "")));
        const sample = { seconds: performance.now() / 1000, ...metadata, count: lengths.length,
          bytes: lengths.reduce((a, b) => a + b, 0), png_count: png.length,
          png_bytes: png.reduce((a, b) => a + b, 0), body };
        blobSamples.push(sample);
        appendFileSync(`${output}/blob-samples.jsonl`, JSON.stringify(sample) + "\n");
        if (metadata?.kind !== "before_frame") console.log(JSON.stringify({ blobs: sample.count, blob_bytes: sample.bytes, png_bytes: sample.png_bytes, ...metadata }));
      } catch (error) { blobSamples.push({ error: String(error) }); } };
      const pending = blobTail.then(capture); blobTail = pending.catch(() => {});
      return pending;
    };
    await sampleBlobs();
    if (!profileEveryFrame) blobSampler = setInterval(() => { void sampleBlobs(); }, 5000);
    else await page.exposeFunction("benchmarkBlobSnapshot", sampleBlobs);
  }
  let currentUploadMode = uploadMode;
  await page.route(`${fixture.url}/api/capabilities`, async route => {
    const response = await route.fetch(), value = await response.json();
    if (currentUploadMode === "single") delete value.video_frame_batch;
    await route.fulfill({ response, json: value });
  });
  let frameRequests = 0;
  await page.route(/\/api\/video-exports\/[a-f0-9]+\/frames$/, async route => {
    frameRequests++;
    if (latencyMs) await new Promise(resolve => setTimeout(resolve, latencyMs));
    await route.continue();
  });
  const sourceAudioHash = await audioHash(`${fixture.url}/api/tracks/${fixture.track_id}/audio`);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const environment = { platform: platform(), release: release(), cpu: cpus()[0].model,
    source_revision: sourceRevision, source_dirty: sourceDirty, graphics: graphics.gpu,
    browser_context: incognito ? "incognito (no Blob disk spill)" : "isolated persistent profile (disk-backed Blob storage enabled)",
    browser_args: JSON.parse(process.env.RIFF_BENCH_BROWSER_ARGS || "[]"),
    browser: browser.version(), source_audio_sha256: sourceAudioHash, load_average_start: loadavg(),
    frame_upload: uploadMode, simulated_request_latency_ms: latencyMs, resource_sampling: sampleResources,
    gpu_note: "Per-run system-wide NVIDIA samples; null where unavailable. Graphics identity is not utilization.",
    temporary_note: "500 ms sampled files in the isolated browser/fixture TMPDIR, including output MP4s and WAV. Linux includes owned open unlinked files there; other platforms do not. Excludes memory-backed memfd and files outside TMPDIR.",
    memory_note: "500 ms sampled sum of descendant RSS, including shared pages; not physical footprint.",
    cpu_note: "500 ms sampled sum of ps CPU percentages; not integrated CPU time.",
    comparison: "SSIM compares decoded H.264 transport against the existing PNG/libx264 path, not lossless source frames." };
  const flush = () => writeFileSync(`${output}/results.json`, JSON.stringify({ environment, results }, null, 2) + "\n");
  flush();
  for (const [width, height, fps] of dimensions) {
    for (const duration of durations) {
      // Alternate order to avoid always favoring the warmed second encoder.
      const transports = results.length % 4 ? [...requestedTransports].reverse() : requestedTransports;
      const pair = [];
      for (const transport of transports) {
        for (let round = 0; round < repeats; round++) {
          const modes = uploadMode === "compare" ? ((round + transports.indexOf(transport)) % 2
            ? ["batch", "single"] : ["single", "batch"]) : [uploadMode];
          for (const mode of modes) {
            currentUploadMode = mode;
            const target = `${fixture.url}/?recording=${fixture.track_id}#studio`;
            if (page.url() === target) await page.reload(); else await page.goto(target);
            await page.locator("#download-video:not([disabled])").waitFor();
            // Every path receives the same already-computed motion data. Otherwise
            // the first encoder would also pay for analysis of the source audio.
            await page.evaluate(async () => { await visualizationFor(selected.id); });
            await page.evaluate(transport => { preferredVideoTransport = async () => transport; }, transport);
            await page.locator("#download-video").click();
            await page.locator("#video-options summary").click();
            await page.locator("#video-width").fill(String(width));
            await page.locator("#video-height").fill(String(height));
            await page.locator("#video-fps").fill(String(fps));
            await page.locator("#video-selection").selectOption("passage");
            await page.locator("#video-end").fill(String(duration));
            await page.evaluate(profileEveryFrame => {
              window.benchmarkWorkers = [];
              const NativeWorker = Worker;
              window.Worker = class extends NativeWorker {
                constructor(...args) {
                  super(...args);
                  const record = { index: benchmarkWorkers.length, opened_ms: performance.now(), ready_ms: null,
                    retired_ms: null, encoded_bytes: 0, stopped: false };
                  benchmarkWorkers.push(record);
                  const stop = this.terminate.bind(this), post = this.postMessage.bind(this);
                  this.terminate = () => { record.stopped = true; stop(); };
                  this.postMessage = (...values) => {
                    const data = values[0];
                    if (data.type === "init") record.byte_budget = data.byteBudget;
                    if (!profileEveryFrame || data.type !== "frame") return post(...values);
                    void benchmarkBlobSnapshot({ kind: "before_frame", worker: record.index, request: data.id }).then(() => {
                      if (record.stopped) data.bitmap?.close(); else post(...values);
                    });
                  };
                  this.addEventListener("message", ({ data }) => {
                    if (data.ready) record.ready_ms = performance.now() - record.opened_ms;
                    if (data.frame) record.encoded_bytes += data.frame.byteLength;
                    if (data.retire) {
                      record.retired_ms = performance.now();
                      if (profileEveryFrame) void benchmarkBlobSnapshot({ kind: "after_retirement", worker: record.index });
                    }
                  });
                }
              };
            }, profileEveryFrame);
            const samples = [];
            const sample = () => { if (sampleResources) samples.push(resourceSample(temporary)); };
            sample();
            gpuSampler = sampleResources ? sampleNvidia() : null;
            sampler = sampleResources ? setInterval(() => {
              try { sample(); }
              catch (error) {
                samplingError = error;
                clearInterval(sampler); sampler = null;
                void browser.close().catch(() => {});
              }
            }, 500) : null;
            // Start without pointer movement. Sample actual DOM progress and an
            // ordinary timer while drawing, encoding and uploading are active.
            await page.evaluate(() => {
              window.benchmarkProgress = { samples: [], longest_task_gap_ms: 0 };
              let previous = performance.now();
              window.benchmarkTimer = setInterval(() => {
                const now = performance.now();
                benchmarkProgress.longest_task_gap_ms = Math.max(benchmarkProgress.longest_task_gap_ms, now - previous);
                previous = now;
                benchmarkProgress.samples.push({ milliseconds: now, progress: document.querySelector("#video-progress").value });
              }, 100);
            });
            const creation = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/video-exports"));
            const downloadReady = page.waitForEvent("download", { timeout }); downloadReady.catch(() => {});
            const began = performance.now();
            frameRequests = 0;
            await page.evaluate(() => document.querySelector("#video-form").requestSubmit());
            const job = await (await creation).json();
            activeRun = { width, height, fps, duration, transport, mode, round, job, samples, url: `${fixture.url}/api/video-exports/${job.id}` };
            console.log(JSON.stringify({ started: activeRun.url, frames: job.frames }));
            assert.equal(job.transport, transport, "Requested codec must run, not silently fall back in a benchmark");
            await page.waitForFunction(() => videoExport === null, null, { timeout });
            const elapsed = (performance.now() - began) / 1000;
            clearInterval(sampler); sampler = null;
            sample(); gpuSampler?.stop();
            const progress = await page.evaluate(() => { clearInterval(benchmarkTimer); return benchmarkProgress; });
            await blobTail;
            const workers = await page.evaluate(() => benchmarkWorkers);
            assert(progress.samples.some(sample => sample.progress > 0 && sample.progress < 1), "Progress advances without pointer input");
            assert(await page.locator("#video-error").isHidden(), await page.locator("#video-error").textContent());
            const name = `${width}x${height}-${fps}fps-${duration}s-${transport}` +
              (uploadMode === "compare" ? `-${mode}` : "") + (repeats > 1 ? `-r${round + 1}` : "");
            const file = `${output}/${name}.mp4`;
            await (await downloadReady).saveAs(file);
            const completed = await (await page.request.get(`${fixture.url}/api/video-exports/${job.id}`)).json();
            assert.equal(completed.status, "done"); assert(completed.receipt);
            assert.equal(await audioHash(`${fixture.url}${completed.original_audio.download_url}`), sourceAudioHash,
              "The original WAV beside the MP4 must remain byte-exact");
            const probe = JSON.parse(execFileSync("ffprobe", ["-v", "error", "-show_streams", "-of", "json", file]));
            const visual = probe.streams.find(stream => stream.codec_type === "video");
            const sound = probe.streams.find(stream => stream.codec_type === "audio");
            assert.equal(Number(visual.nb_frames), Math.ceil(duration * fps));
            assert.equal(visual.width, width); assert.equal(visual.height, height);
            assert(Math.abs(Number(visual.duration) - duration) < 1 / fps);
            assert(Math.abs(Number(sound.duration) - duration) < .04);
            const hashes = await frameHashes(file, `${output}/${name}.framemd5`);
            assert.equal(hashes.frames, job.frames);
            const result = { name, width, height, fps, duration, transport, upload_mode: mode, repeat: round + 1,
              original_audio_sha256: sourceAudioHash, wall_seconds: elapsed,
              resources: samples, progress, workers, retirements: workers.filter(worker => worker.retired_ms !== null).length, blob_samples: blobSamples.slice(), system_gpu_samples: gpuSampler?.samples || null,
              system_gpu_note: gpuSampler?.note() || "Sampling disabled",
              sampled_peak_temporary_allocated_bytes: samples.length ? samples.reduce((peak, sample) => Math.max(peak, sample.temporary_allocated_bytes), 0) : null,
              frames_per_second: job.frames / elapsed, final_bytes: statSync(file).size, final_sha256: await fileHash(file), frame_requests: frameRequests,
              sampled_peak_rss_bytes: samples.length ? samples.reduce((peak, sample) => Math.max(peak, sample.rss), 0) : null,
              sampled_mean_cpu_percent: samples.length ? samples.reduce((sum, sample) => sum + sample.cpu, 0) / samples.length : null,
              decoded_frames_sha256: hashes.sha256, receipt: completed.receipt };
            activeRun = null; gpuSampler = null;
            results.push(result); pair.push({ transport, mode, round, file, result }); flush();
            console.log(JSON.stringify({ name, wall_seconds: elapsed, frames_per_second: result.frames_per_second, final_bytes: result.final_bytes, client: result.receipt.client_reported }));
          }
          if (uploadMode === "compare") {
            const matched = pair.filter(item => item.transport === transport && item.round === round);
            assert.equal(matched[0].result.decoded_frames_sha256, matched[1].result.decoded_frames_sha256,
              "Changing upload grouping must preserve every decoded frame");
            assert.deepEqual(matched[0].result.receipt.video, matched[1].result.receipt.video);
            console.log(`UPLOAD PAIR ${transport}/${duration}s/${round + 1}: identical decoded frames`);
          }
        }
      }
      if (requestedTransports.length === 2) for (const mode of uploadMode === "compare" ? ["single", "batch"] : [uploadMode]) {
        for (let round = 0; round < repeats; round++) {
          const matched = pair.filter(item => item.mode === mode && item.round === round);
          const compare = spawn("ffmpeg", ["-hide_banner", "-threads", "2", "-i", matched.find(item => item.transport === "png").file,
            "-threads", "2", "-i", matched.find(item => item.transport === "h264").file,
            "-filter_complex_threads", "2", "-lavfi", "ssim", "-f", "null", "-"], { stdio: ["ignore", "ignore", "pipe"] });
          let log = ""; compare.stderr.on("data", bytes => { log = (log + bytes).slice(-16000); });
          const [code] = await once(compare, "exit"); assert.equal(code, 0, log);
          const ssim = Number(log.match(/All:([\d.]+)/)?.[1]); assert(Number.isFinite(ssim));
          for (const item of matched) item.result.relative_ssim = ssim;
          flush(); console.log(`PAIR SSIM ${width}x${height}/${fps}/${duration}s/${mode}/${round + 1}: ${ssim}`);
        }
      }
    }
  }
  assert.deepEqual(errors, []);
  console.log(`Receipts: ${output}/results.json`);
} catch (error) {
  if (activeRun && page && !page.isClosed()) {
    activeRun.ui_error = await page.locator("#video-error").textContent().catch(() => null);
    activeRun.final_job = await page.request.get(activeRun.url).then(response => response.json()).catch(() => null);
  }
  writeFileSync(`${output}/failure.json`, JSON.stringify({ error: String(samplingError || error), interrupted, diagnostics, active_run: activeRun,
    system_gpu_samples: gpuSampler?.samples || null, blob_samples: blobSamples }, null, 2) + "\n");
  throw error;
} finally {
  if (sampler) clearInterval(sampler);
  if (blobSampler) clearInterval(blobSampler);
  gpuSampler?.stop();
  await browser?.close();
  if (server.exitCode === null && server.signalCode === null && server.pid) {
    const stopped = once(server, "exit"); server.kill("SIGTERM"); await stopped;
  }
  rmSync(temporary, { recursive: true, force: true });
  process.removeListener("SIGINT", interrupt);
  process.removeListener("SIGTERM", interrupt);
}
