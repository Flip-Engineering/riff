// Run the current measurement harness against a clean, exact source revision.
// Dependencies (including Playwright) are resolved beside this script.
import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { once } from "node:events";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const [revision, destination] = process.argv.slice(2);
assert(revision && destination, "Usage: node scripts/benchmark_video_revision.mjs COMMIT NEW_RESULTS_DIRECTORY");
const commit = execFileSync("git", ["rev-parse", "--verify", `${revision}^{commit}`], { encoding: "utf8" }).trim();
const output = resolve(destination);
mkdirSync(output);
const checkout = `${output}/source`;
const driverScript = process.env.RIFF_BENCH_DRIVER === "cdp" ? "benchmark_video_cdp.mjs" : "benchmark_video.mjs";
const driver = { source_revision: commit, driver_script: driverScript,
  harness_revision: execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(),
  harness_dirty: Boolean(execFileSync("git", ["status", "--porcelain", "--untracked-files=all"], { encoding: "utf8" }).trim()) };
writeFileSync(`${output}/driver.json`, JSON.stringify(driver, null, 2) + "\n");
execFileSync("git", ["worktree", "add", "--detach", checkout, commit], { stdio: "inherit" });
let child;
const interrupt = signal => child?.kill(signal);
const onInt = () => interrupt("SIGINT"), onTerm = () => interrupt("SIGTERM");
process.once("SIGINT", onInt); process.once("SIGTERM", onTerm);
try {
  child = spawn(process.execPath, [fileURLToPath(new URL(driverScript, import.meta.url)), `${output}/measurements`],
    { cwd: checkout, env: process.env, stdio: "inherit" });
  const [code, signal] = await once(child, "exit");
  assert.equal(code, 0, `Benchmark exited ${code ?? signal}; retained receipts in ${output}`);
} finally {
  process.removeListener("SIGINT", onInt); process.removeListener("SIGTERM", onTerm);
  // Git refuses removal if this temporary source has acquired unfinished work.
  execFileSync("git", ["worktree", "remove", checkout], { stdio: "inherit" });
}
