// Measurements belong only to the benchmark, never to the Studio runtime.
import { execFileSync, spawn } from "node:child_process";
import { lstatSync, statSync, readdirSync, readlinkSync, statfsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { loadavg } from "node:os";

export function diskSpace(path) {
  const value = statfsSync(path);
  return { available_bytes: value.bavail * value.bsize, total_bytes: value.blocks * value.bsize };
}

const clockTicks = process.platform === "linux"
  ? Number(execFileSync("getconf", ["CLK_TCK"], { encoding: "utf8", timeout: 2000 }).trim()) : null;
function cpuSeconds(value) {
  const parts = value.split("-");
  return (parts.length > 1 ? Number(parts[0]) * 86400 : 0) + parts.at(-1).split(":").reduce((n, part) => n * 60 + Number(part), 0);
}
export function resourceSample(root, processId = process.pid) {
  const rows = execFileSync("ps", ["-axo", "pid=,ppid=,rss=,%cpu=,time=,comm="], { encoding: "utf8", timeout: 2000 })
    .trim().split("\n").map(line => {
      const fields = line.trim().split(/\s+/);
      return [...fields.slice(0, 4).map(Number), cpuSeconds(fields[4]), fields.slice(5).join(" ")];
    });
  const owned = new Set([processId]);
  let previous;
  do { previous = owned.size; for (const [pid, parent] of rows) if (owned.has(parent)) owned.add(pid); } while (previous !== owned.size);
  const processes = rows.filter(([pid, parent, , , , command]) => owned.has(pid) && !(parent === process.pid && command === "ps"))
    .map(([pid, parent, rss, cpu, cpuTime, command]) => {
      const record = { pid, parent, command, rss_bytes: rss * 1024, cpu_percent: cpu, cpu_seconds: cpuTime, role: pid === processId ? "harness" : command, threads: null };
      if (process.platform !== "linux") return record;
      try {
        // Read only this benchmark's descendants, and retain a role rather than
        // command arguments (which can contain unrelated sensitive values).
        const cmdline = readFileSync(`/proc/${pid}/cmdline`, "utf8");
        // Chromium may flatten its argv into a single process-title string.
        const type = cmdline.match(/(?:^|[\s\0])--type=([a-z-]+)(?=$|[\s\0])/);
        if (type) record.role = type[1];
        else if (!cmdline) record.role = null;
        else if (/chrome|chromium/i.test(command)) record.role = "browser";
        if (record.role === "renderer") {
          record.threads = [];
          for (const tid of readdirSync(`/proc/${pid}/task`)) {
            try {
              const name = readFileSync(`/proc/${pid}/task/${tid}/comm`, "utf8").trim();
              if (!/DedicatedWorker|CrRendererMain|IOThread/.test(name)) continue;
              const stat = readFileSync(`/proc/${pid}/task/${tid}/stat`, "utf8");
              const fields = stat.slice(stat.lastIndexOf(")") + 2).split(" ");
              record.threads.push({ tid: Number(tid), name, cpu_seconds: (Number(fields[11]) + Number(fields[12])) / clockTicks });
            } catch (error) { if (!["ENOENT", "ESRCH"].includes(error.code)) record.thread_error = String(error); }
          }
        }
      } catch (error) { if (!["ENOENT", "ESRCH"].includes(error.code)) record.role_unavailable = String(error); }
      return record;
    });
  const memory = processes.reduce((sum, value) => ({ rss: sum.rss + value.rss_bytes, cpu: sum.cpu + value.cpu_percent }), { rss: 0, cpu: 0 });
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
  return { seconds: performance.now() / 1000, wall_time: new Date().toISOString(), ...memory, processes,
    clock_ticks_per_second: clockTicks, load_average: loadavg(),
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
