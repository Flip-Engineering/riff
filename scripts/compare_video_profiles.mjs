// Compare completed matched benchmark directories without modifying their evidence.
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";

const [masterPath, sharePath] = process.argv.slice(2).map(path => resolve(path));
assert(masterPath && sharePath, "Supply master and share benchmark directories");
assert(!existsSync(`${masterPath}/failure.json`) && !existsSync(`${sharePath}/failure.json`), "Cannot compare failed runs");
const master = JSON.parse(readFileSync(`${masterPath}/results.json`));
const share = JSON.parse(readFileSync(`${sharePath}/results.json`));
assert.equal(master.environment.profile, "master");
assert.equal(share.environment.profile, "share");
assert.equal(master.environment.source_audio_sha256, share.environment.source_audio_sha256);
assert(master.results.length > 0);
assert.equal(master.results.length, share.results.length, "Only compare complete matching runs");
const result = [];
for (const reference of master.results) {
  const candidate = share.results.find(item => item.name === reference.name);
  assert(candidate, `Missing Share case ${reference.name}`);
  for (const key of ["width", "height", "fps", "duration", "transport", "original_audio_sha256"]) assert.equal(candidate[key], reference[key], key);
  const compared = spawnSync("ffmpeg", ["-hide_banner", "-threads", "2", "-i", `${masterPath}/${reference.name}.mp4`,
    "-threads", "2", "-i", `${sharePath}/${candidate.name}.mp4`, "-filter_complex_threads", "2", "-lavfi", "ssim", "-f", "null", "-"],
    {stdio: ["ignore", "pipe", "pipe"], encoding: "utf8"});
  assert.equal(compared.status, 0, compared.stderr);
  const ssim = Number(compared.stderr.match(/All:([\d.]+)/)?.[1]);
  assert(Number.isFinite(ssim), "Missing SSIM statistic");
  result.push({name: reference.name, ssim_against_lossy_master: ssim, meets_relative_ssim_floor: ssim >= .99,
    master_bytes: reference.final_bytes, share_bytes: candidate.final_bytes,
    share_to_master_bytes: candidate.final_bytes / reference.final_bytes,
    master_wall_seconds: reference.wall_seconds, share_wall_seconds: candidate.wall_seconds});
}
console.log(JSON.stringify({relative_ssim_floor: .99, reference: "Lossy Master, not lossless source",
  source_audio_sha256: master.environment.source_audio_sha256, results: result}, null, 2));
if (result.some(item => !item.meets_relative_ssim_floor)) process.exitCode = 1;
