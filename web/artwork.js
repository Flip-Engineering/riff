/* A seeded, folded surface. Poster, playback and film use the same geometry. */
const RiffArtwork = (() => {
  const palettes = [
    { paper: "#dfe7f4", ink: "#526eab", accent: "#648f87", glow: "#b9a6ce" },
    { paper: "#e5e1ee", ink: "#79679d", accent: "#5f8e95", glow: "#b3c7bd" },
    { paper: "#e1e7e1", ink: "#527e70", accent: "#79749e", glow: "#b1ced0" },
    { paper: "#eae1df", ink: "#956c7c", accent: "#5e8286", glow: "#c0becf" },
  ];
  const defaults = Object.freeze({ surface: .72, motion: 1, color: .7, texture: .48 });
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
    const ink = rgb(palette.ink), paper = rgb(palette.paper), accent = rgb(palette.accent);
    const shades = [], edges = [];
    // A small material lookup also gives Canvas the same chromatic depth.
    for (let tint = 0; tint <= 16; tint++) {
      const body = mix(ink, accent, tint / 16), lights = [], ridges = [];
      for (let i = 0; i <= 100; i++) {
        const light = i / 100;
        const material = light < .32 ? mix(body, [23, 30, 39], (.32 - light) * 1.6)
          : mix(body, paper, (light - .32) / .68);
        lights.push(color(material));
        ridges.push(color(mix(material, light > .72 ? paper : body, .32)));
      }
      shades.push(lights); edges.push(ridges);
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
      vertices: new Float32Array(rings * steps * 3), stress: new Float32Array(rings * steps), projected: new Float32Array(rings * steps * 2),
      faces, shades, edges };
    // Retain the existing artwork cache footprint rather than a frame history.
    if (cache.size >= 24) cache.delete(cache.keys().next().value);
    cache.set(key, item);
    return item;
  }

  function scene(seed, seconds = 0, motion = [], withFaces = true) {
    const g = geometry(seed), { vertices: v, projected: p, phase } = g;
    const fields = new Map();
    const level = clamp(motion[0]), bass = clamp(motion[1]), middle = clamp(motion[2]), air = clamp(motion[3]);
    const balance = clamp(motion[4], -1, 1), spread = clamp(motion[5]), attack = clamp(motion[6]), crest = clamp(motion[7]);
    const time = Number.isFinite(seconds) ? seconds : 0;
    g.sound = [bass, middle, air, attack];
    g.lightPhase = phase + Math.sin(time * .19) * level * .24;
    const tilt = .59 + balance * .08 + bass * .045;
    const yaw = -.25 + spread * .10 + middle * .028 * Math.sin(time * .18);
    const turn = g.twist - .22 + level * .014 * Math.sin(time * .12);
    const ct = Math.cos(tilt), st = Math.sin(tilt), cy = Math.cos(yaw), sy = Math.sin(yaw);
    const cz = Math.cos(turn), sz = Math.sin(turn);
    for (let ring = 0; ring < rings; ring++) {
      const f = ring / (rings - 1), fold = Math.sin(Math.PI * f);
      const historyAt = f * Math.max(0, (motion.history?.length || 1) - 1);
      const historyIndex = Math.floor(historyAt), blend = historyAt - historyIndex;
      const before = motion.history?.[historyIndex] || motion;
      const after = motion.history?.[historyIndex + 1] || before;
      const arrival = clamp(before[6]) * (1 - blend) + clamp(after[6]) * blend;
      const resonance = clamp(before[1]) * (1 - blend) + clamp(after[1]) * blend - bass;
      const fieldAt = frame => {
        const trace = frame.waveform;
        if (trace && !fields.has(trace)) fields.set(trace, bendingField(trace));
        return fields.get(trace);
      };
      const first = fieldAt(before), next = fieldAt(after);
      // The delayed pressure travels through the same contours as the signed
      // signal. Their endpoints stay attached while the material opens within.
      const pressure = arrival - attack + resonance * .6;
      const layer = f + Math.sin(2 * Math.PI * f) * pressure * .04;
      // The low register opens a fold from within. Each contour receives the
      // same breath at its own delay; the silhouette retains one gesture.
      const carriedBass = bass + resonance;
      const opening = fold * carriedBass * 8;
      for (let point = 0; point < steps; point++) {
        const angle = point / steps * Math.PI * 2;
        const position = (point / steps + phase / (Math.PI * 2)) % 1;
        const signal = fieldSample(first, position) * (1 - blend) + fieldSample(next, position) * blend;
        const t = angle + fold * signal * .014 + fold * pressure * .018;
        // One connected acoustic shell: sound changes curvature, spacing and
        // material tension together instead of overlaying a separate trace.
        const strain = middle * 15 * Math.sin(2 * t - time * .32 + phase)
          + arrival * 6.5 * Math.sin(2 * t + phase + f * 2) + resonance * 10 + signal * (4 + 6 * fold);
        const radius = (100 + layer * 60.75 + 12 * Math.sin(3 * t + phase) * layer) * (1 + bass * .13) + strain;
        const x = Math.cos(t) * radius;
        const y = (Math.sin(t) * (68 + layer * 35.1) + 30 * Math.sin(2 * t + phase) * layer) * (1 + bass * .075)
          + Math.sin(t) * signal * 4 * fold;
        // Broad movement belongs to the phrase. Higher frequencies alter a
        // gentle surface fold and its light, without independent fast ripples.
        const wave = middle * 14 * Math.sin(2 * t + phase - time * .28 + f)
          + (air * .6 + attack * 1.5 + crest * .3) * Math.sin(4 * t + phase + f * 2)
          + signal * (6 + 10 * fold);
        const z = -22 + layer * 40 + fold * (22 + 22 * Math.sin(2 * t + phase))
          + 10 * Math.sin(3 * t + phase) * f + (.28 + fold * .72) * wave + arrival * 3 * fold + opening * (0.7 + 0.3 * Math.cos(2 * t + phase));
        const ry = y * ct - z * st, rz = y * st + z * ct;
        const rx = x * cy + rz * sy, depth = -x * sy + rz * cy;
        const tx = rx * cz - ry * sz, ty = rx * sz + ry * cz;
        const index = ring * steps + point, perspective = 630 / (630 - depth);
        g.stress[index] = clamp(signal * 1.8 + pressure * .7, -1, 1) * fold;
        v[index * 3] = tx; v[index * 3 + 1] = ty; v[index * 3 + 2] = depth;
        p[index * 2] = 220 + tx * perspective * .93;
        p[index * 2 + 1] = 143 + ty * perspective * .93;
      }
    }
    orderFaces(g);
    if (withFaces) shadeFaces(g);
    return g;
  }

  function orderFaces(g) {
    const v = g.vertices;
    for (const face of g.faces) {
      face.depth = (v[face.a * 3 + 2] + v[face.b * 3 + 2] + v[face.c * 3 + 2] + v[face.d * 3 + 2]) / 4;
    }
    g.faces.sort((a, b) => a.depth - b.depth);
  }

  function shadeFaces(g) {
    const v = g.vertices, air = g.sound[2];
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
      const angle = face.a % steps / steps * Math.PI * 2;
      face.chromatic = .5 + .5 * Math.sin(Math.cos(angle) * 2.1 + Math.sin(angle) * 1.6
        + face.ring / (rings - 1) * 3.2 + g.lightPhase + g.stress[face.a] * .8);
    }
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
      layout(location=3) in float stress;
      out float surfaceStress;
      out vec3 surfaceNormal;
      out vec2 surfaceUV;
      out vec3 materialCoordinate;
      void main() {
        gl_Position=vec4(position,1.0); surfaceNormal=normal; surfaceUV=uv; surfaceStress=stress;
        // Circular coordinates keep color and fibres continuous at the seam.
        materialCoordinate=vec3(cos(uv.x*6.2831853),sin(uv.x*6.2831853),uv.y);
      }
    `);
    const fragment = compile(gl.FRAGMENT_SHADER, `#version 300 es
      precision highp float;
      in vec3 surfaceNormal;
      in vec2 surfaceUV;
      in float surfaceStress;
      in vec3 materialCoordinate;
      uniform vec3 ink;
      uniform vec3 paper;
      uniform vec3 accent;
      uniform vec3 glow;
      uniform vec4 sound;
      uniform vec2 materialAmount;
      uniform float lightPhase;
      uniform float surfacePresence;
      out vec4 pixel;
      void main() {
        vec3 n=normalize(surfaceNormal); if(n.z<0.0) n=-n;
        float diffuse=max(0.0,dot(n,normalize(vec3(-0.42,-0.63,0.65))));
        vec3 c=materialCoordinate;
        float flow=c.x*2.1+c.y*1.6+c.z*3.2+lightPhase+surfaceStress*0.8;
        float veil=0.5+0.5*sin(flow);
        float fibrePhase=c.z*980.0+sin(c.x*9.0+c.y*7.0+c.z*14.0)*2.4+surfaceStress*3.0;
        float fibre=sin(fibrePhase)*exp(-0.5*pow(fwidth(fibrePhase),2.0));
        float weave=sin(c.x*73.0+c.y*51.0+c.z*33.0)*sin(c.y*93.0-c.x*29.0);
        float texture=materialAmount.y*(fibre*0.6+weave*0.2);
        float sheen=pow(max(0.0,dot(n,normalize(vec3(-0.22+sound.y*0.13,-0.34,0.91)))),
          24.0+18.0*materialAmount.y);
        float light=clamp(0.16+0.69*diffuse+0.38*sheen+surfaceStress*0.045+texture*0.006,0.0,1.0);
        float grazing=pow(1.0-abs(n.z),2.0);
        vec3 pigment=mix(ink,accent,veil*materialAmount.x*0.72);
        vec3 material=mix(pigment*0.48,paper*1.035,light);
        float transmission=grazing*(0.075+sound.x*0.035)+max(0.0,surfaceStress)*0.035;
        material=mix(material,mix(pigment,glow,0.46),transmission);
        vec3 pearl=mix(paper*1.035,glow,(0.05+0.08*veil)*materialAmount.x);
        material=mix(material,pearl,sheen*(0.08+sound.z*0.035));
        float band=surfaceUV.y*39.0;
        float distance=abs(fract(band+0.5)-0.5);
        float openSide=smoothstep(-0.3,0.8,c.y);
        float presence=clamp(surfacePresence*1.45-openSide*0.42+surfaceStress*0.085,0.0,1.0);
        float width=mix(0.022,0.51,pow(presence,2.1));
        float coverage=1.0-smoothstep(width,width+fwidth(band)*0.8,distance);
        if(coverage<0.05) discard;
        float trace=1.0-smoothstep(0.01,0.01+fwidth(band)*1.2,distance);
        material=mix(material,mix(pigment*0.46,pearl,light*0.50),trace*(0.37+materialAmount.y*0.08)*(1.0-sheen*0.6));
        pixel=vec4(material,coverage);
      }
    `);
    const program = gl.createProgram(); gl.attachShader(program, vertex); gl.attachShader(program, fragment);
    gl.linkProgram(program); gl.deleteShader(vertex); gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program);
    const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    const data = new Float32Array(rings * steps * 9);
    gl.bufferData(gl.ARRAY_BUFFER, data.byteLength, gl.DYNAMIC_DRAW);
    for (const [location, size, offset] of [[0, 3, 0], [1, 3, 12], [2, 2, 24], [3, 1, 32]]) {
      gl.enableVertexAttribArray(location); gl.vertexAttribPointer(location, size, gl.FLOAT, false, 36, offset);
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
    const uniforms = Object.fromEntries(["ink", "paper", "accent", "glow"].map(name => [name, gl.getUniformLocation(program, name)]));
    const presence = gl.getUniformLocation(program, "surfacePresence");
    const sound = gl.getUniformLocation(program, "sound"), amount = gl.getUniformLocation(program, "materialAmount");
    const phase = gl.getUniformLocation(program, "lightPhase");
    return (context, g, scale) => {
      if (gl.isContextLost()) return false;
      const width = Math.ceil(440 * scale), height = Math.ceil(340 * scale);
      if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
      const v = g.vertices, p = g.projected;
      for (let ring = 0; ring < rings; ring++) for (let point = 0; point < steps; point++) {
        const index = ring * steps + point, row = index * 9;
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
        data[row + 8] = g.stress[index];
      }
      gl.viewport(0, 0, width, height); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
      for (const name of Object.keys(uniforms)) gl.uniform3fv(uniforms[name], rgb(g.palette[name]).map(v => v / 255));
      gl.uniform1f(presence, g.presence);
      gl.uniform4fv(sound, g.sound); gl.uniform2f(amount, g.color, g.texture); gl.uniform1f(phase, g.lightPhase);
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

  function bendingField(trace) {
    // Broader curvature joins signed audio motion to the material. Circular
    // smoothing keeps the contour seam continuous, without amplifying silence.
    if (!trace.length) return [];
    const mean = trace.reduce((sum, value) => sum + value, 0) / trace.length;
    let field = trace.map(value => value - mean);
    for (let pass = 0; pass < 3; pass++) {
      const prior = field, n = prior.length;
      field = prior.map((value, i) => (prior[(i + n - 2) % n] + 4 * prior[(i + n - 1) % n]
        + 6 * value + 4 * prior[(i + 1) % n] + prior[(i + 2) % n]) / 16);
    }
    return field;
  }

  function fieldSample(field, position) {
    if (!field?.length) return 0;
    const location = position * field.length, sample = Math.floor(location);
    return field[sample] + (field[(sample + 1) % field.length] - field[sample]) * (location - sample);
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
    // WebGL computes its own material from vertex normals. Prepare Canvas
    // shading only on fallback; retain depth ordering across renderer changes.
    const g = scene(seed, seconds, motion, false), { palette: c, projected: p } = g;
    g.presence = clamp(appearance.surface ?? defaults.surface);
    g.color = clamp(appearance.color ?? defaults.color);
    g.texture = clamp(appearance.texture ?? defaults.texture);
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
    if (!drawSurface(context, g, scale)) {
      shadeFaces(g);
      for (const face of g.faces) {
        const a = face.a * 2, b = face.b * 2, cc = face.c * 2, d = face.d * 2;
        const tint = Math.round(face.chromatic * g.color * 16);
        context.beginPath(); context.moveTo(p[a], p[a + 1]); context.lineTo(p[b], p[b + 1]);
        context.lineTo(p[cc], p[cc + 1]); context.lineTo(p[d], p[d + 1]); context.closePath();
        context.globalAlpha = g.presence;
        context.fillStyle = g.shades[tint][face.shade]; if (g.presence) context.fill();
        // Fill shared raster edges; the separate contour is the fine physical ridge.
        context.strokeStyle = g.shades[tint][face.shade]; context.lineWidth = .3; if (g.presence) context.stroke();
        context.globalAlpha = 1;
        context.beginPath(); context.moveTo(p[a], p[a + 1]); context.lineTo(p[d], p[d + 1]);
        context.strokeStyle = g.edges[tint][face.shade];
        context.lineWidth = (face.ring % 5 === 0 ? .48 : .30) + g.texture * .14;
        context.stroke();
      }
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
