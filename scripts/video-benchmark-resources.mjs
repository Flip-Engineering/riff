// Measurements belong only to the benchmark, never to the Studio runtime.
import { execFileSync, spawn } from "node:child_process";
import { lstatSync, statSync, readdirSync, readlinkSync, statfsSync } from "node:fs";
import { join, resolve } from "node:path";
import { loadavg } from "node:os";

export function diskSpace(path) {
  const value = statfsSync(path);
  return { available_bytes: value.bavail * value.bsize, total_bytes: value.blocks * value.bsize };
}

export function resourceSample(root, processId = process.pid) {
  const rows = execFileSync("ps", ["-axo", "pid=,ppid=,rss=,%cpu="], { encoding: "utf8", timeout: 2000 })
    .trim().split("\n").map(line => line.trim().split(/\s+/).map(Number));
  const owned = new Set([processId]);
  let previous;
  do { previous = owned.size; for (const [pid, parent] of rows) if (owned.has(parent)) owned.add(pid); } while (previous !== owned.size);
  const memory = rows.filter(([pid]) => owned.has(pid)).reduce((sum, [, , rss, cpu]) =>
    ({ rss: sum.rss + rss * 1024, cpu: sum.cpu + cpu }), { rss: 0, cpu: 0 });
  const seen = new Set();
  let logical = 0, allocated = 0, unlinked = 0;
  const inaccessible = new Set();
  const count = (path, deleted = false) => {
    try {
      const stat = deleted ? statSync(path) : lstatSync(path);
      if (!stat.isFile()) return;
      const key = `${stat.dev}:${stat.ino}`;
      if (seen.has(key)) return;
      seen.add(key); logical += stat.size; allocated += stat.blocks * 512;
      if (deleted) unlinked += stat.size;
    } catch (error) {
      if (error.code === "EACCES") inaccessible.add(path);
      else if (!["ENOENT", "ESRCH"].includes(error.code)) throw error;
    }
  };
  const walk = path => {
    let entries;
    try { entries = readdirSync(path, { withFileTypes: true }); }
    catch (error) { if (error.code === "ENOENT") return; throw error; }
    for (const entry of entries) {
      const file = join(path, entry.name);
      if (entry.isDirectory()) walk(file); else count(file);
    }
  };
  walk(root);
  // Chromium can unlink a backing file while keeping it open. Include those
  // owned descriptors on Linux, but exclude memfd and unrelated filesystems.
  if (process.platform === "linux") for (const pid of owned) {
    let descriptors;
    try { descriptors = readdirSync(`/proc/${pid}/fd`); }
    catch (error) {
      if (error.code === "EACCES") { inaccessible.add(`/proc/${pid}/fd`); continue; }
      if (["ENOENT", "ESRCH"].includes(error.code)) continue;
      throw error;
    }
    for (const fd of descriptors) {
      const path = `/proc/${pid}/fd/${fd}`;
      try {
        const target = readlinkSync(path);
        if (target.startsWith(resolve(root) + "/") && target.endsWith(" (deleted)")) count(path, true);
      } catch (error) {
        if (error.code === "EACCES") inaccessible.add(path);
        else if (!["ENOENT", "ESRCH"].includes(error.code)) throw error;
      }
    }
  }
  return { seconds: performance.now() / 1000, ...memory, load_average: loadavg(),
    temporary_logical_bytes: logical, temporary_allocated_bytes: allocated,
    temporary_unlinked_bytes: process.platform === "linux" && !inaccessible.size ? unlinked : null,
    inaccessible_descriptors: [...inaccessible],
    disk_available_bytes: diskSpace(root).available_bytes };
}

export function sampleNvidia() {
  const samples = [];
  let note = "System-wide NVIDIA counters; includes unrelated processes, not export GPU cost.";
  const child = spawn("nvidia-smi", ["--query-gpu=index,uuid,utilization.gpu,memory.used", "--format=csv,noheader,nounits", "--loop-ms=500"]);
  let buffered = "", diagnostics = "";
  child.stdout.on("data", chunk => {
    buffered += chunk;
    const lines = buffered.split("\n"); buffered = lines.pop();
    for (const line of lines) {
      const [index, uuid, utilization, memory] = line.split(",").map(value => value.trim());
      if (uuid) samples.push({ seconds: performance.now() / 1000, index: Number(index), uuid,
        utilization_percent: Number.isFinite(Number(utilization)) ? Number(utilization) : null,
        memory_bytes: Number.isFinite(Number(memory)) ? Number(memory) * 1024 * 1024 : null });
    }
  });
  child.stderr.on("data", chunk => { diagnostics += chunk; });
  child.on("error", error => { note = `NVIDIA sampling unavailable: ${error.message}`; });
  child.on("exit", code => { if (code) note = `NVIDIA sampling failed (${code}): ${diagnostics}`; });
  return { samples, note: () => note, stop: () => child.kill("SIGTERM") };
}
