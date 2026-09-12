/* A seeded, folded surface. Poster, playback and film use the same geometry. */
const RiffArtwork = (() => {
  const palettes = [
    { paper: "#dfe7f4", ink: "#4d70b4", shadow: "#a9bedc", warm: "#b9cba9" },
    { paper: "#e5e1ee", ink: "#7970a3", shadow: "#c3b6d6", warm: "#a9b4ce" },
    { paper: "#e1e7e1", ink: "#6a8a7e", shadow: "#adbfba", warm: "#d2c1a0" },
    { paper: "#eae1df", ink: "#90757d", shadow: "#c7b5bb", warm: "#b7bfc2" },
  ];
  const defaults = Object.freeze({ surface: .72, motion: 1 });
  const cache = new Map(), rings = 40, steps = 128;
  let surfaceRenderer;
  const clamp = (x, low = 0, high = 1) => Math.max(low, Math.min(high, Number(x) || 0));
  const rgb = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
  const mix = (a, b, amount) => a.map((v, i) => Math.round(v + (b[i] - v) * amount));
  const color = values => `rgb(${values.join(",")})`;
  function seedHash(seed) {
    let h = 2166136261;
    for (const c of String(seed ?? "")) h = Math.imul(h ^ c.charCodeAt(0), 16777619);
    return h >>> 0;
  }

  function geometry(seed) {
    const key = String(seed ?? "");
    if (cache.has(key)) return cache.get(key);
    const h = seedHash(key), palette = palettes[h % palettes.length];
    const ink = rgb(palette.ink), paper = rgb(palette.paper), warm = rgb(palette.warm);
    const shades = [], edges = [];
    for (let i = 0; i <= 100; i++) {
      const light = i / 100;
      const material = light < .38 ? mix(ink, [22, 31, 33], (.38 - light) * 1.25)
        : mix(ink, paper, (light - .38) / .62);
      shades.push(color(mix(material, warm, Math.max(0, light - .65) * .35)));
      edges.push(color(mix(material, light > .72 ? paper : ink, .38)));
    }
    const faces = [];
    for (let ring = 0; ring < rings - 1; ring++) {
      for (let point = 0; point < steps; point++) {
        const next = (point + 1) % steps;
        faces.push({ a: ring * steps + point, b: (ring + 1) * steps + point,
          c: (ring + 1) * steps + next, d: ring * steps + next, ring, depth: 0, shade: 0 });
      }
    }
    const item = { palette, phase: (h % 1000) / 159, twist: .28 + (h % 37) / 110,
      vertices: new Float32Array(rings * steps * 3), projected: new Float32Array(rings * steps * 2),
      faces, shades, edges };
    // Retain the existing artwork cache footprint rather than a frame history.
    if (cache.size >= 24) cache.delete(cache.keys().next().value);
    cache.set(key, item);
    return item;
  }

  function scene(seed, seconds = 0, motion = []) {
    const g = geometry(seed), { vertices: v, projected: p, phase } = g;
    const level = clamp(motion[0]), bass = clamp(motion[1]), middle = clamp(motion[2]), air = clamp(motion[3]);
    const balance = clamp(motion[4], -1, 1), spread = clamp(motion[5]), attack = clamp(motion[6]), crest = clamp(motion[7]);
    const time = Number.isFinite(seconds) ? seconds : 0;
    const tilt = .59 + balance * .17 + bass * .06;
    const yaw = -.25 + spread * .15 + middle * .035 * Math.sin(time * .45);
    const turn = g.twist - .22 + level * .025 * Math.sin(time * .32);
    const ct = Math.cos(tilt), st = Math.sin(tilt), cy = Math.cos(yaw), sy = Math.sin(yaw);
    const cz = Math.cos(turn), sz = Math.sin(turn);
    for (let ring = 0; ring < rings; ring++) {
      const f = ring / (rings - 1), fold = Math.sin(Math.PI * f);
      const history = motion.history?.[Math.round(f * (motion.history.length - 1))] || motion;
      const arrival = clamp(history[6]), resonance = clamp(history[1]) - bass;
      const trace = history.waveform || motion.waveform;
      for (let point = 0; point < steps; point++) {
        const t = point / steps * Math.PI * 2;
        const location = point / (steps - 1) * ((trace?.length || 1) - 1), sample = Math.floor(location);
        const signal = trace?.length ? (trace[sample] || 0) + ((trace[Math.min(sample + 1, trace.length - 1)] || 0)
          - (trace[sample] || 0)) * (location - sample) : 0;
        // The original seed contour is the plan view of this acoustic shell.
        const strain = middle * 6 * Math.sin(3 * t - time * 1.4 + phase)
          + arrival * 7 * Math.sin(2 * t + phase + f * 4) + resonance * 12;
        const radius = (100 + f * 60.75 + 12 * Math.sin(3 * t + phase) * f) * (1 + bass * .095) + strain;
        const x = Math.cos(t) * radius;
        const y = (Math.sin(t) * (68 + f * 35.1) + 30 * Math.sin(2 * t + phase) * f) * (1 + bass * .065);
        const wave = middle * 4 * Math.sin(3 * t + phase - time * 1.2 + f * 2)
          + air * 1.2 * Math.sin(15 * t - time * 5.5 + f * 8)
          + attack * 4 * Math.sin(6 * t - time * 3.8 + f * 13)
          + crest * .7 * Math.sin(27 * t - time * 6 + f * 15)
          + signal * 15 * Math.sin(t / 2) ** 2;
        const z = -22 + f * 40 + fold * (22 + 22 * Math.sin(2 * t + phase))
          + 10 * Math.sin(3 * t + phase) * f + (.28 + fold * .72) * wave + arrival * 7 * fold;
        const ry = y * ct - z * st, rz = y * st + z * ct;
        const rx = x * cy + rz * sy, depth = -x * sy + rz * cy;
        const tx = rx * cz - ry * sz, ty = rx * sz + ry * cz;
        const index = ring * steps + point, perspective = 630 / (630 - depth);
        v[index * 3] = tx; v[index * 3 + 1] = ty; v[index * 3 + 2] = depth;
        p[index * 2] = 220 + tx * perspective * .93;
        p[index * 2 + 1] = 143 + ty * perspective * .93;
      }
    }
    for (const face of g.faces) {
      const a = face.a * 3, b = face.b * 3, d = face.d * 3;
      const ux = v[b] - v[a], uy = v[b + 1] - v[a + 1], uz = v[b + 2] - v[a + 2];
      const vx = v[d] - v[a], vy = v[d + 1] - v[a + 1], vz = v[d + 2] - v[a + 2];
      let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      const length = Math.hypot(nx, ny, nz) || 1;
      const side = nz < 0 ? -1 : 1;
      nx *= side / length; ny *= side / length; nz *= side / length;
      const light = Math.max(0, nx * -.42 + ny * -.63 + nz * .65);
      const glint = Math.pow(Math.max(0, nx * -.22 + ny * -.34 + nz * .91), 26);
      const cavity = .07 * Math.sin(face.ring / (rings - 1) * Math.PI);
      face.shade = Math.round(clamp(.2 + light * .65 + glint * (.26 + air * .15) - cavity) * 100);
      face.depth = (v[a + 2] + v[b + 2] + v[face.c * 3 + 2] + v[d + 2]) / 4;
    }
    g.faces.sort((a, b) => a.depth - b.depth);
    return g;
  }

  function createSurfaceRenderer() {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl2", { alpha: true, antialias: true, depth: true, preserveDrawingBuffer: true });
    if (!gl) return null;
    canvas.addEventListener("webglcontextlost", event => event.preventDefault());
    canvas.addEventListener("webglcontextrestored", () => { surfaceRenderer = undefined; });
    const compile = (type, text) => {
      const shader = gl.createShader(type); gl.shaderSource(shader, text); gl.compileShader(shader);
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader));
      return shader;
    };
    const vertex = compile(gl.VERTEX_SHADER, `#version 300 es
      layout(location=0) in vec3 position;
      layout(location=1) in vec3 normal;
      layout(location=2) in vec2 uv;
      out vec3 surfaceNormal;
      out vec2 surfaceUV;
      void main() { gl_Position=vec4(position,1.0); surfaceNormal=normal; surfaceUV=uv; }
    `);
    const fragment = compile(gl.FRAGMENT_SHADER, `#version 300 es
      precision highp float;
      in vec3 surfaceNormal;
      in vec2 surfaceUV;
      uniform vec3 ink;
      uniform vec3 paper;
      uniform vec3 warm;
      uniform float surfacePresence;
      out vec4 pixel;
      void main() {
        vec3 n=normalize(surfaceNormal); if(n.z<0.0) n=-n;
        float diffuse=max(0.0,dot(n,normalize(vec3(-0.42,-0.63,0.65))));
        float sheen=pow(max(0.0,dot(n,normalize(vec3(-0.22,-0.34,0.91)))),32.0);
        float light=clamp(0.24+0.67*diffuse+0.26*sheen,0.0,1.0);
        float grazing=pow(1.0-abs(n.z),2.0);
        vec3 material=mix(ink*0.48,paper*1.035,light);
        material=mix(material,warm,0.19*grazing+0.10*sheen);
        float band=surfaceUV.y*39.0;
        float distance=abs(fract(band+0.5)-0.5);
        float openSide=smoothstep(-0.3,0.8,sin(surfaceUV.x*6.2831853));
        float presence=clamp(surfacePresence*1.45-openSide*0.42,0.0,1.0);
        float width=mix(0.022,0.51,pow(presence,2.1));
        float coverage=1.0-smoothstep(width,width+fwidth(band)*0.8,distance);
        if(coverage<0.05) discard;
        float trace=1.0-smoothstep(0.01,0.01+fwidth(band)*1.2,distance);
        material=mix(material,mix(ink*0.55,paper,light*0.5),trace*0.56);
        pixel=vec4(material,coverage);
      }
    `);
    const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment);
    gl.linkProgram(program); gl.deleteShader(vertex); gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    const data = new Float32Array(rings * steps * 8);
    gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);
    for (const [location, size, offset] of [[0, 3, 0], [1, 3, 12], [2, 2, 24]]) {
      gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, size, gl.FLOAT, false, 32, offset);
    }
    const indices = new Uint16Array((rings - 1) * steps * 6);
    let cursor = 0;
    for (let ring = 0; ring < rings - 1; ring++) for (let point = 0; point < steps; point++) {
      const a = ring * steps + point, b = (ring + 1) * steps + point;
      const c = (ring + 1) * steps + (point + 1) % steps, d = ring * steps + (point + 1) % steps;
      for (const i of [a, b, c, a, c, d]) indices[cursor++] = i;
    }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    gl.enable(gl.DEPTH_TEST); gl.enable(gl.BLEND); gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA); gl.clearColor(0, 0, 0, 0);
    const uniforms = Object.fromEntries(["ink", "paper", "warm"].map(name => [name, gl.getUniformLocation(program, name)]));
    const presence = gl.getUniformLocation(program, "surfacePresence");
    return (context, g, scale) => {
      if (gl.isContextLost()) return false;
      const width = Math.ceil(440 * scale), height = Math.ceil(340 * scale);
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      const v = g.vertices, p = g.projected;
      for (let ring = 0; ring < rings; ring++) for (let point = 0; point < steps; point++) {
        const index = ring * steps + point, row = index * 8;
        const before = (ring * steps + (point + steps - 1) % steps) * 3;
        const after = (ring * steps + (point + 1) % steps) * 3;
        const inner = (Math.max(0, ring - 1) * steps + point) * 3;
        const outer = (Math.min(rings - 1, ring + 1) * steps + point) * 3;
        const ux = v[outer] - v[inner], uy = v[outer + 1] - v[inner + 1], uz = v[outer + 2] - v[inner + 2];
        const vx = v[after] - v[before], vy = v[after + 1] - v[before + 1], vz = v[after + 2] - v[before + 2];
        data[row] = p[index * 2] / 220 - 1; data[row + 1] = 1 - p[index * 2 + 1] / 170;
        data[row + 2] = -v[index * 3 + 2] / 300;
        data[row + 3] = uy * vz - uz * vy; data[row + 4] = uz * vx - ux * vz; data[row + 5] = ux * vy - uy * vx;
        data[row + 6] = point / steps; data[row + 7] = ring / (rings - 1);
      }
      gl.viewport(0, 0, width, height); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      for (const name of Object.keys(uniforms)) gl.uniform3fv(uniforms[name], rgb(g.palette[name]).map(v => v / 255));
      gl.uniform1f(presence, g.presence);
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer); gl.bufferSubData(gl.ARRAY_BUFFER, 0, data);
      gl.drawElements(gl.TRIANGLES, indices.length, gl.UNSIGNED_SHORT, 0);
      context.drawImage(canvas, 0, 0, 440, 340);
      return true;
    };
  }

  function drawSurface(context, g, scale) {
    if (surfaceRenderer === undefined) {
      try { surfaceRenderer = createSurfaceRenderer(); } catch { surfaceRenderer = null; }
    }
    return surfaceRenderer?.(context, g, scale) || false;
  }

  function drawThreads(context, g, motion) {
    if (!motion.waveform?.length) return;
    const p = g.projected, left = steps / 2 * 2, right = 0;
    context.save();
    const gradient = context.createLinearGradient(p[left], p[left + 1], p[right], p[right + 1]);
    gradient.addColorStop(0, g.palette.ink + "00");
    gradient.addColorStop(.16, g.palette.ink); gradient.addColorStop(.84, g.palette.ink);
    gradient.addColorStop(1, g.palette.ink + "00");
    context.strokeStyle = gradient;
    for (let layer = 4; layer >= 0; layer--) {
      const history = motion.history?.[layer * 3] || motion, trace = history.waveform || motion.waveform;
      const points = [];
      for (let i = 0; i < trace.length; i++) {
        const u = i / (trace.length - 1), envelope = Math.sin(Math.PI * u) ** 2;
        points.push([p[left] + (p[right] - p[left]) * u,
          p[left + 1] + (p[right + 1] - p[left + 1]) * u + envelope * (trace[i] * 22 + (layer - 2) * 2.8)]);
      }
      context.globalAlpha = (.62 - layer * .10) * clamp(motion[0]);
      context.lineWidth = layer ? .34 : .75;
      context.beginPath(); context.moveTo(...points[0]);
      for (let i = 1; i < points.length - 1; i++) context.quadraticCurveTo(...points[i],
        (points[i][0] + points[i + 1][0]) / 2, (points[i][1] + points[i + 1][1]) / 2);
      context.lineTo(...points[points.length - 1]); context.stroke();
    }
    context.restore();
  }

  function draw(context, width, height, seed, seconds = 0, motion = [], appearance = defaults) {
    if (!context || width <= 0 || height <= 0) return;
    const strength = clamp(appearance.motion ?? defaults.motion, 0, 2);
    if (strength !== 1) {
      const scaled = frame => Object.assign(frame.map(value => value * strength),
        { waveform: frame.waveform?.map(value => value * strength) });
      const history = motion.history?.map(scaled);
      motion = Object.assign(scaled(motion), { history });
    }
    const g = scene(seed, seconds, motion), { palette: c, projected: p } = g;
    g.presence = clamp(appearance.surface ?? defaults.surface);
    const scale = Math.min(width / 440, height / 340);
    context.save();
    context.setTransform(1, 0, 0, 1, 0, 0); context.globalAlpha = 1;
    context.fillStyle = c.paper; context.fillRect(0, 0, width, height);
    context.translate((width - 440 * scale) / 2, (height - 340 * scale) / 2);
    context.scale(scale, scale);
    context.save(); context.translate(220, 264); context.scale(1, .17);
    const shadow = context.createRadialGradient(0, 0, 18, 0, 0, 151);
    shadow.addColorStop(0, c.ink + "38"); shadow.addColorStop(.48, c.ink + "1a"); shadow.addColorStop(1, c.ink + "00");
    context.fillStyle = shadow; context.fillRect(-152, -152, 304, 304); context.restore();
    context.lineJoin = "round";
    drawThreads(context, g, motion);
    if (!drawSurface(context, g, scale)) for (const face of g.faces) {
      const a = face.a * 2, b = face.b * 2, cc = face.c * 2, d = face.d * 2;
      context.beginPath(); context.moveTo(p[a], p[a + 1]); context.lineTo(p[b], p[b + 1]);
      context.lineTo(p[cc], p[cc + 1]); context.lineTo(p[d], p[d + 1]); context.closePath();
      context.globalAlpha = g.presence;
      context.fillStyle = g.shades[face.shade]; if (g.presence) context.fill();
      // Fill shared raster edges; the separate contour is the fine physical ridge.
      context.strokeStyle = g.shades[face.shade]; context.lineWidth = .3; if (g.presence) context.stroke();
      context.globalAlpha = 1;
      context.beginPath(); context.moveTo(p[a], p[a + 1]); context.lineTo(p[d], p[d + 1]);
      context.strokeStyle = g.edges[face.shade]; context.lineWidth = face.ring % 5 === 0 ? .6 : .34;
      context.stroke();
    }
    context.setTransform(1, 0, 0, 1, 0, 0);
    const unit = Math.min(width, height);
    context.fillStyle = c.ink; context.globalAlpha = .76;
    context.font = `600 ${unit * .061}px Arial, sans-serif`;
    context.fillText("riff.", unit * .064, height - unit * .064);
    context.restore();
  }

  function svg(seed) {
    const g = geometry(seed);
    if (g.poster) return g.poster;
    const canvas = document.createElement("canvas"); canvas.width = 880; canvas.height = 680;
    draw(canvas.getContext("2d"), canvas.width, canvas.height, seed);
    g.poster = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 440 340" preserveAspectRatio="xMidYMid slice" aria-hidden="true"><image width="440" height="340" href="${canvas.toDataURL("image/png")}"/></svg>`;
    return g.poster;
  }
  return { draw, svg, scene, defaults };
})();

function drawSeedArtwork(context, width, height, seed, seconds = 0, motion = [], appearance) {
  RiffArtwork.draw(context, width, height, seed, seconds, motion, appearance);
}
function artSVG(seed) { return RiffArtwork.svg(seed); }
function artContent(seed) { return artSVG(seed).replace(/^<svg[^>]*>/, "").replace(/<\/svg>$/, ""); }

// Library covers are painted only as they approach the viewport. Browsing a
// large collection should not render every 3D poster when the queue changes.
function artThumbnail(seed) {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 440 340" aria-hidden="true" data-art-seed="${encodeURIComponent(String(seed))}"><rect width="440" height="340" fill="currentColor" opacity=".08"/></svg>`;
}
const pendingPosters = new Set();
const posterObserver = new IntersectionObserver(entries => {
  for (const { target, isIntersecting } of entries) {
    if (!isIntersecting) continue;
    posterObserver.unobserve(target); pendingPosters.delete(target);
    if (target.isConnected) target.innerHTML = artContent(decodeURIComponent(target.dataset.artSeed));
  }
}, { rootMargin: "50% 0px" });
function observeArtPosters(root) {
  for (const target of pendingPosters) if (!target.isConnected) { posterObserver.unobserve(target); pendingPosters.delete(target); }
  for (const target of root.querySelectorAll("[data-art-seed]")) { pendingPosters.add(target); posterObserver.observe(target); }
}
