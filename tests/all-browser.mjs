import { spawn } from "node:child_process";
import { once } from "node:events";

const server = spawn(process.env.RIFF_PYTHON || "python3", ["-u", "tests/review_browser_server.py"],
  { env: { ...process.env, RIFF_MULTI_TRACK_FIXTURE: "1" } });
let diagnostics = "";
server.stderr.on("data", (data) => diagnostics += data);
const fixture = await new Promise((resolve, reject) => {
  let output = "";
  server.stdout.on("data", (data) => { output += data; if (output.includes("\n")) resolve(JSON.parse(output.split("\n")[0])); });
  server.once("exit", (code) => reject(new Error(`Fixture exited ${code}: ${diagnostics}`)));
});
try {
  const selected = process.argv.slice(2);
  for (const file of selected.length ? selected : ["browser", "exploration", "writer", "compass", "reviews", "suite", "studio-flow", "performance", "score-replay", "generation-paths", "desktop-controls", "video-encoder", "video", "artwork-webgl"]) {
    const child = spawn(process.execPath, [`tests/${file}.mjs`], { stdio: "inherit",
      env: { ...process.env, RIFF_URL: fixture.url, RIFF_TEST_TRACK: fixture.track_id } });
    const [code] = await once(child, "exit");
    if (code !== 0) throw new Error(`${file} failed (${code})`);
  }
} finally {
  const stopped = once(server, "exit"); server.kill("SIGTERM"); await stopped;
}
