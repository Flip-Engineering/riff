// Run serial, matched exports in an isolated library. Never point this at a live studio.
import assert from "node:assert/strict";
import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { cpus, platform, release, loadavg } from "node:os";
import { chromium } from "playwright";

const output = resolve(process.argv[2] || `test-results/video-benchmark-${Date.now()}`);
mkdirSync(output); // A fresh directory protects earlier receipts.
const durations = (process.env.RIFF_BENCH_DURATIONS || "2,10").split(",").map(Number);
assert(durations.every(value => Number.isFinite(value) && value > 0));
const uploadMode = process.env.RIFF_BENCH_UPLOAD || "batch";
const repeats = Number(process.env.RIFF_BENCH_REPEATS || 1);
const latencyMs = Number(process.env.RIFF_BENCH_LATENCY_MS || 0);
const sampleResources = process.env.RIFF_BENCH_SAMPLE_RESOURCES !== "0";
const dimensions = (process.env.RIFF_BENCH_DIMENSIONS || "1920x1080x30,2160x2160x30,2160x2160x60")
  .split(",").map(value => value.split("x").map(Number));
assert(["single", "batch", "compare"].includes(uploadMode));
assert(Number.isInteger(repeats) && repeats > 0 && repeats <= 10);
assert(Number.isFinite(latencyMs) && latencyMs >= 0 && latencyMs <= 1000);
assert(dimensions.every(value => value.length === 3 && value.every(n => Number.isFinite(n) && n > 0)));
async function audioHash(url) {
  const response = await fetch(url); assert(response.ok);
  const hash = createHash("sha256");
  for await (const chunk of response.body) hash.update(chunk);
  return hash.digest("hex");
}
const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"], { env: process.env });
let diagnostics = "";
server.stderr.on("data", bytes => { diagnostics = (diagnostics + bytes).slice(-8000); });
let browser;
const results = [];
let sampler;
let samplingError;
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
  browser = await chromium.launch({ headless: true, executablePath: process.env.RIFF_BROWSER_EXECUTABLE,
    args: process.platform === "darwin" ? ["--use-angle=metal"] : [] });
  assert(!interrupted, "Benchmark interrupted");
  const page = await browser.newPage({ viewport: { width: 1365, height: 1050 } });
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
    browser: browser.version(), source_audio_sha256: sourceAudioHash, load_average_start: loadavg(),
    frame_upload: uploadMode, simulated_request_latency_ms: latencyMs, resource_sampling: sampleResources,
    gpu_utilization: null, gpu_note: "Not sampled; requires a separate platform GPU profiler.",
    memory_note: "500 ms sampled sum of descendant RSS, including shared pages; not physical footprint.",
    cpu_note: "500 ms sampled sum of ps CPU percentages; not integrated CPU time.",
    comparison: "SSIM compares decoded H.264 transport against the existing PNG/libx264 path, not lossless source frames." };
  const flush = () => writeFileSync(`${output}/results.json`, JSON.stringify({ environment, results }, null, 2) + "\n");
  flush();
  for (const [width, height, fps] of dimensions) {
    for (const duration of durations) {
      // Alternate order to avoid always favoring the warmed second encoder.
      const transports = results.length % 4 ? ["h264", "png"] : ["png", "h264"];
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
            const samples = [];
            const sample = () => {
              if (!sampleResources) return;
              const rows = execFileSync("ps", ["-axo", "pid=,ppid=,rss=,%cpu="], { encoding: "utf8", timeout: 2000 }).trim().split("\n")
                .map(line => line.trim().split(/\s+/).map(Number));
              const owned = new Set([process.pid]);
              let previous;
              do { previous = owned.size; for (const [pid, parent] of rows) if (owned.has(parent)) owned.add(pid); } while (previous !== owned.size);
              samples.push(rows.filter(([pid]) => owned.has(pid)).reduce((sum, [, , rss, cpu]) => ({ rss: sum.rss + rss * 1024, cpu: sum.cpu + cpu }), { rss: 0, cpu: 0 }));
            };
            sample(); sampler = sampleResources ? setInterval(() => {
              try { sample(); }
              catch (error) {
                samplingError = error;
                clearInterval(sampler); sampler = null;
                // Abort this owned benchmark so a blocked sampler cannot yield a
                // misleading export timing or leave its encoder behind.
                void browser.close().catch(() => {});
              }
            }, 500) : null;
            const creation = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith("/video-exports"));
            const downloadReady = page.waitForEvent("download", { timeout: 600000 }); downloadReady.catch(() => {});
            const began = performance.now();
            frameRequests = 0;
            await page.locator("#render-video").click();
            const job = await (await creation).json();
            assert.equal(job.transport, transport, "Requested codec must run, not silently fall back in a benchmark");
            await page.waitForFunction(() => videoExport === null, null, { timeout: 600000 });
            const elapsed = (performance.now() - began) / 1000;
            clearInterval(sampler); sampler = null;
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
            const hashes = execFileSync("ffmpeg", ["-v", "error", "-threads", "2", "-i", file, "-map", "0:v:0", "-f", "framemd5", "-"], { maxBuffer: 8 * 1024 * 1024 });
            assert.equal(hashes.toString().split("\n").filter(line => line && !line.startsWith("#")).length, job.frames);
            writeFileSync(`${output}/${name}.framemd5`, hashes);
            const result = { name, width, height, fps, duration, transport, upload_mode: mode, repeat: round + 1,
              original_audio_sha256: sourceAudioHash, wall_seconds: elapsed,
              frames_per_second: job.frames / elapsed, final_bytes: statSync(file).size, frame_requests: frameRequests,
              sampled_peak_rss_bytes: samples.length ? Math.max(...samples.map(sample => sample.rss)) : null,
              sampled_mean_cpu_percent: samples.length ? samples.reduce((sum, sample) => sum + sample.cpu, 0) / samples.length : null,
              decoded_frames_sha256: createHash("sha256").update(hashes).digest("hex"), receipt: completed.receipt };
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
      for (const mode of uploadMode === "compare" ? ["single", "batch"] : [uploadMode]) {
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
  writeFileSync(`${output}/failure.json`, JSON.stringify({ error: String(samplingError || error), interrupted, diagnostics }, null, 2) + "\n");
  throw error;
} finally {
  if (sampler) clearInterval(sampler);
  await browser?.close();
  if (server.exitCode === null && server.signalCode === null && server.pid) {
    const stopped = once(server, "exit"); server.kill("SIGTERM"); await stopped;
  }
  process.removeListener("SIGINT", interrupt);
  process.removeListener("SIGTERM", interrupt);
}
