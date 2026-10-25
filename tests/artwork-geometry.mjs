// A renderer-independent guard for seed artwork geometry.
//
// RiffArtwork.scene(seed, seconds, motion) is pure arithmetic: no canvas, no WebGL, no GPU. This
// test loads web/artwork.js in a bare Node context, builds the scene for a fixed set of seeds and
// conditions, and compares a SHA-256 digest of the canonical, rounded result with the committed
// digests in tests/fixtures/artwork-geometry.json. WebGL, the Canvas fallback and the cached
// poster all paint from this geometry, so a changed digest means every renderer's picture of that
// seed changed, whichever renderer ran. Pixel comparisons differ between renderers and GPUs and are
// deliberately not used here.
//
// What the digest covers, and its precision (stated so a failure can be read):
//   palette strings; phase, twist and light phase (6 decimals); every vertex and projected
//   coordinate (3 decimals); every stress value and face chromatic value (4 decimals); the face
//   draw order and each face's integer shade; the shade and edge colour tables.
// What it does not cover: how a renderer paints the geometry (shadow, texture, glow, film grain).
//
// To change the artwork on purpose, run
//     node tests/artwork-geometry.mjs --update
// review the diff of tests/fixtures/artwork-geometry.json together with the artwork change, and
// commit both. A mismatch without --update never rewrites the file.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import vm from "node:vm";

const source = readFileSync(new URL("../web/artwork.js", import.meta.url), "utf8");
const fixtureUrl = new URL("./fixtures/artwork-geometry.json", import.meta.url);

// The conditions every seed is built under. Motion frames are 8 numbers; history frames carry a waveform.
const frame = phase => Object.assign([.55, .6 + .1 * Math.sin(phase), .45, .2, .1, .35, .4 + .2 * Math.cos(phase), .25],
  { waveform: Array.from({ length: 64 }, (_, i) => Math.sin(i / 64 * 2 * Math.PI * 3 + phase) * .6) });
const CASES = {
  rest: () => [0, []],
  moving: () => [3.7, [.6, .7, .5, .2, .1, .4, .3, .2]],
  history: () => [12.25, Object.assign([.6, .7, .5, .2, .1, .4, .3, .2], { history: [frame(0), frame(1.3), frame(2.6)] })],
};
const SEEDS = ["15961", "15962", "0", "1", "9223372036854775807", "", "Night Orchard", "Ça va — 音楽"];

function load(text) {
  const context = vm.createContext({ window: {}, IntersectionObserver: class { observe() {} unobserve() {} },
    setTimeout, performance, document: {}, Math });
  return vm.runInContext(text + "\n;RiffArtwork", context);
}

const fixed = (value, places) => {
  const text = Number(value).toFixed(places);
  return text.startsWith("-") && Number(text) === 0 ? text.slice(1) : text;
};

function canonical(scene) {
  const lines = [`palette ${["paper", "ink", "accent", "glow"].map(key => scene.palette[key]).join(" ")}`,
    `scalars ${fixed(scene.phase, 6)} ${fixed(scene.twist, 6)} ${fixed(scene.lightPhase, 6)}`,
    `sound ${[...scene.sound].map(value => fixed(value, 6)).join(" ")}`,
    `counts ${scene.vertices.length / 3} ${scene.faces.length}`,
    `vertices ${Array.from(scene.vertices, value => fixed(value, 3)).join(" ")}`,
    `projected ${Array.from(scene.projected, value => fixed(value, 3)).join(" ")}`,
    `stress ${Array.from(scene.stress, value => fixed(value, 4)).join(" ")}`,
    `faces ${scene.faces.map(face => [face.a, face.b, face.c, face.d, face.ring, face.shade, fixed(face.chromatic, 4)].join(",")).join(" ")}`,
    `shades ${scene.shades.map(row => row.join("|")).join(" ")}`,
    `edges ${scene.edges.map(row => row.join("|")).join(" ")}`];
  return lines.join("\n");
}

// Each seed and condition gets a fresh load of the artwork: scene() sorts a cached face list in
// place, so a shared instance would let one condition's face order leak into the next one's ties.
export function digests(text) {
  const result = {};
  for (const seed of SEEDS) {
    for (const [name, make] of Object.entries(CASES)) {
      const [seconds, motion] = make();
      const scene = load(text).scene(seed, seconds, motion);
      result[`${JSON.stringify(seed)} ${name}`] = createHash("sha256").update(canonical(scene)).digest("hex");
    }
  }
  return result;
}

const current = digests(source);
const description = {
  about: "SHA-256 of the canonical, rounded RiffArtwork.scene() result per seed and condition. Regenerate with `node tests/artwork-geometry.mjs --update`.",
  precision: "scalars 6 decimals; vertices and projected coordinates 3; stress and face chromatic 4; face order and shade exact",
  conditions: { rest: "t=0 s, silence", moving: "t=3.7 s, one fixed 8-band frame", history: "t=12.25 s, one frame plus a three-frame waveform history" },
};

if (process.argv.includes("--update")) {
  writeFileSync(fixtureUrl, JSON.stringify({ ...description, digests: current }, null, 2) + "\n");
  console.log(`Wrote ${Object.keys(current).length} digests to tests/fixtures/artwork-geometry.json`);
  process.exit(0);
}

const expected = JSON.parse(readFileSync(fixtureUrl, "utf8")).digests;
const changed = Object.keys({ ...expected, ...current }).filter(key => expected[key] !== current[key]);
assert.deepEqual(changed, [], "Seed artwork geometry changed for: " + changed.join("; ") +
  ". If the artwork was changed on purpose, run `node tests/artwork-geometry.mjs --update`, review the "
  + "fixture diff with the artwork change, and commit both.");
console.log(`PASS Seed artwork geometry digests match for ${SEEDS.length} seeds under ${Object.keys(CASES).length} conditions`);

// The guard must bite: perturbing the geometry in any of these ways has to change digests.
const perturbations = {
  "twist constant": ["twist: .28 +", "twist: .29 +"],
  "radius scale": ["100 + layer * 60.75", "100 + layer * 61.75"],
  "ring count": ["rings = 40", "rings = 41"],
  "palette colour": ['ink: "#526eab"', 'ink: "#526eac"'],
  "phase derivation": ["(h % 1000) / 159", "(h % 1000) / 160"],
  "face shading light": ["nx * -.42 + ny * -.63 + nz * .65", "nx * -.42 + ny * -.63 + nz * .66"],
};
for (const [label, [from, to]] of Object.entries(perturbations)) {
  assert(source.includes(from), `Perturbation "${label}" no longer matches web/artwork.js; update this list: ${from}`);
  const altered = digests(source.replace(from, to));
  const differing = Object.keys(current).filter(key => altered[key] !== current[key]);
  assert(differing.length > 0, `Perturbing ${label} did not change any digest`);
}
console.log(`PASS ${Object.keys(perturbations).length} perturbations of the geometry each change the digests`);

// Different seeds must not collapse to the same picture, and equal inputs must repeat exactly.
assert.equal(new Set(Object.values(current)).size, Object.keys(current).length, "Two seed/condition pairs share a digest");
assert.deepEqual(digests(source), current, "Geometry is not deterministic for the same input");
console.log("PASS Digests are distinct per seed and condition and repeat exactly");
