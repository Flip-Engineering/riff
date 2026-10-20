/* The recording's seed contours move on the audio clock, including after seeks. */
let soundAnimation = null;
let visualization = "sound";
let soundMotion = null;
let soundMotionTrack = null;
let soundSnapshotPending = false;
const quietMotion = matchMedia("(prefers-reduced-motion: reduce)");
const motionCache = new Map();
const settledMotion = new WeakMap();
let soundAppearance = { ...RiffArtwork.defaults };
try {
  const saved = JSON.parse(localStorage.getItem("riff.soundAppearance") || "{}");
  for (const [key, maximum] of [["surface", 1], ["motion", 2], ["color", 1], ["texture", 1]]) {
    if (Number.isFinite(saved[key])) soundAppearance[key] = Math.max(0, Math.min(maximum, saved[key]));
  }
} catch {}
function artworkAppearance() { return { ...soundAppearance }; }

function visualizationFor(trackId) {
  if (!motionCache.has(trackId)) {
    if (motionCache.size >= 4) motionCache.delete(motionCache.keys().next().value);
    const request = api(`/api/tracks/${trackId}/visualization`).catch(error => {
      if (motionCache.get(trackId) === request) motionCache.delete(trackId);
      throw error;
    });
    motionCache.set(trackId, request);
  }
  return motionCache.get(trackId);
}

function motionAt(data, seconds) {
  if (!data?.frames?.length) return Array(8).fill(0);
  if (!settledMotion.has(data)) {
    // Let the material carry an impulse instead of redrawing its silhouette
    // at waveform speed. The filter uses seconds, independent of analysis fps.
    // Cache the causal result, so seeking and export need no playback history.
    const dt = 1 / data.fps, frames = [], waveforms = [];
    let body = Array(8).fill(0), trace = Array(data.waveforms?.[0]?.length || 0).fill(0);
    for (let i = 0; i < data.frames.length; i++) {
      // Each frame is the state at its timestamp, before that sample's
      // interval. This avoids an analysis-rate-dependent head start.
      frames.push(body);
      if (data.waveforms?.[i]) waveforms.push(trace);
      body = body.map((previous, band) => {
        const input = data.frames[i][band] || 0;
        const time = band === 4 || band === 5 ? .75 : input > previous ? .14 : .65;
        return previous + (input - previous) * (1 - Math.exp(-dt / time));
      });
      if (data.waveforms?.[i]) {
        trace = trace.map((previous, point) => previous + (data.waveforms[i][point] - previous) * (1 - Math.exp(-dt / .3)));
      }
    }
    settledMotion.set(data, { fps: data.fps, frames, waveforms });
  }
  data = settledMotion.get(data);
  const frame = offset => data.frames[Math.min(data.frames.length - 1, Math.max(0, offset))];
  const at = time => {
    if (time < 0) return Array(8).fill(0);
    const position = time * data.fps, index = Math.floor(position), blend = position - index;
    const a = frame(index), b = frame(index + 1);
    const values = a.map((value, band) => value + (b[band] - value) * blend);
    if (data.waveforms?.length) {
      const first = data.waveforms[Math.min(index, data.waveforms.length - 1)];
      const next = data.waveforms[Math.min(index + 1, data.waveforms.length - 1)];
      values.waveform = first.map((value, point) => value + ((next[point] || 0) - value) * blend);
    }
    return values;
  };
  const motion = at(seconds);
  // A beat travels through the surface using its real, recent envelope.
  // Sampling the audio clock makes seeking and exported frames reproducible.
  motion.history = Array.from({ length: 20 }, (_, i) => at(seconds - i * 1.8 / 19));
  return motion;
}

function updateMiniPlayer() {
  $("#next-track").disabled = state.tracks.filter(track => !track.archived).length < 2;
  const visible = !!selected && $("#studio-view").hidden;
  $("#mini-player").hidden = !visible;
  document.body.classList.toggle("with-mini-player", visible);
  $("#mini-title").textContent = selected?.title || "Listening room";
  $("#mini-title").setAttribute("aria-label", `Return to the studio: ${selected?.title || "Listening room"}`);
  $("#mini-time").textContent = formatTime(audio.currentTime);
  $("#mini-play").innerHTML = icon(audio.paused ? "play" : "pause");
  $("#mini-play").setAttribute("aria-label", audio.paused ? "Play library recording" : "Pause library playback");
}

// Read the playing element without waiting for whole-recording analysis.
// Keep the graph connected once created; recreating a media source silences it.
const liveSound = (() => {
  let context, source, analyser, channels, starting, lastTime = null, last = null;
  let body = Array(8).fill(0), trace = Array(64).fill(0), history = [];
  let frequency, samples;
  const available = !!(window.AudioContext || window.webkitAudioContext);
  function reset() { lastTime = null; last = null; body = Array(8).fill(0); trace = Array(64).fill(0); history = []; }
  async function start() {
    if (!available) return false;
    if (starting) return starting;
    starting = (async () => {
      try {
        context ||= new (window.AudioContext || window.webkitAudioContext)();
        if (context.state !== "running") await context.resume();
        if (context.state !== "running") return false;
        if (!source) {
          analyser = context.createAnalyser(); analyser.fftSize = 2048; analyser.smoothingTimeConstant = 0;
          const splitter = context.createChannelSplitter(2);
          channels = [context.createAnalyser(), context.createAnalyser()];
          for (let i = 0; i < 2; i++) { channels[i].fftSize = 2048; splitter.connect(channels[i], i); }
          source = context.createMediaElementSource(audio);
          source.connect(analyser); analyser.connect(context.destination); source.connect(splitter);
          frequency = new Float32Array(analyser.frequencyBinCount);
          samples = channels.map(channel => new Float32Array(channel.fftSize));
        }
        return true;
      } catch { return false; }
    })();
    try { return await starting; } finally { starting = null; }
  }
  function read(seconds) {
    if (!source || context.state !== "running") return null;
    if (audio.paused) return last;
    if (lastTime !== null && (seconds < lastTime || seconds - lastTime > .5)) reset();
    const dt = lastTime === null ? 1 / 60 : Math.max(0, seconds - lastTime);
    if (!dt) return last;
    lastTime = seconds;
    analyser.getFloatFrequencyData(frequency);
    channels.forEach((channel, i) => channel.getFloatTimeDomainData(samples[i]));
    const bands = [0, 0, 0], energy = [0, 0];
    let cross = 0, peak = 0;
    for (let i = 0; i < frequency.length; i++) {
      const hz = i * context.sampleRate / analyser.fftSize;
      bands[hz < 180 ? 0 : hz < 1800 ? 1 : 2] += 10 ** (frequency[i] / 10);
    }
    for (let i = 0; i < samples[0].length; i++) {
      const left = samples[0][i], right = samples[1][i];
      energy[0] += left * left; energy[1] += right * right; cross += left * right;
      peak = Math.max(peak, Math.abs(left), Math.abs(right));
    }
    const rms = Math.sqrt((energy[0] + energy[1]) / (samples[0].length * 2));
    const level = Math.min(1, rms ** .6 * 1.8), left = Math.sqrt(energy[0]), right = Math.sqrt(energy[1]);
    const input = [level, ...bands.map(value => Math.min(1, Math.sqrt(value) ** .6 * 1.8)),
      (left + right ? (right - left) / (right + left) : 0) * level,
      Math.sqrt(Math.max(0, Math.min(1, (energy[0] + energy[1] - 2 * cross) / (2 * (energy[0] + energy[1]) || 1)))) * level,
      Math.max(0, level - body[0]) * 3.8, rms ? Math.min(1, Math.max(0, (peak / rms - 1) / 4)) : 0];
    body = body.map((value, i) => value + (input[i] - value) * (1 - Math.exp(-dt / (input[i] > value ? .06 : .25))));
    const audible = samples[energy[1] > energy[0] ? 1 : 0];
    trace = trace.map((previous, point) => {
      const start = Math.floor(point * audible.length / trace.length), end = Math.floor((point + 1) * audible.length / trace.length);
      let value = 0;
      for (let i = start; i < end; i++) value += audible[i];
      const signal = Math.max(-1, Math.min(1, value / (end - start) * 2.5));
      return previous + (signal - previous) * (1 - Math.exp(-dt / .08));
    });
    const frame = [...body]; frame.waveform = [...trace];
    history.push({time: seconds, motion: frame});
    while (history.length > 1 && history[1].time < seconds - 1.8) history.shift();
    const motion = [...frame]; motion.waveform = frame.waveform;
    motion.history = Array.from({length: 20}, (_, i) => {
      const time = seconds - i * 1.8 / 19;
      // Read from the end: the current frame is the common case.
      for (let j = history.length - 1; j >= 0; j--) if (history[j].time <= time) return history[j].motion;
      return Array(8).fill(0);
    });
    last = motion;
    return motion;
  }
  return {start, read, reset};
})();

function drawSoundField() {
  soundAnimation = null;
  const immersive = $("#sound-view-dialog").open;
  const canvas = immersive ? $("#sound-view-canvas") : $("#sound-field"), context = canvas.getContext("2d");
  if (!context || canvas.hidden || document.hidden || (!immersive && $("#studio-view").hidden)) return;
  const bounds = canvas.getBoundingClientRect(), scale = devicePixelRatio || 1;
  if (!bounds.width || !bounds.height) return;
  const width = Math.round(bounds.width * scale), height = Math.round(bounds.height * scale);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const seed = selected?.recipe.seed ?? "reed0f_cobalt8d82";
  drawSeedArtwork(context, width, height, seed, audio.currentTime,
    liveSound.read(audio.currentTime) || motionAt(soundMotionTrack === selected?.id ? soundMotion : null, audio.currentTime), artworkAppearance());
  if (!audio.paused && !quietMotion.matches) soundAnimation = requestAnimationFrame(drawSoundField);
}

function synchronizeSoundField() {
  if (soundAnimation !== null) cancelAnimationFrame(soundAnimation);
  soundAnimation = null;
  if ((!$("#sound-view-dialog").open && (visualization !== "sound" || $("#studio-view").hidden)) || document.hidden) return;
  if (selected && soundMotionTrack !== selected.id) {
    const id = selected.id;
    soundMotionTrack = id;
    soundMotion = null;
    visualizationFor(id).then(data => {
      if (soundMotionTrack !== id) return;
      soundMotion = data;
      synchronizeSoundField();
    }).catch(error => notify(error.message));
  }
  drawSoundField();
}

document.addEventListener("DOMContentLoaded", () => {
  const immersive = $("#sound-view-dialog");
  for (const key of ["surface", "motion", "color", "texture"]) {
    const control = $("#sound-" + key);
    control.value = soundAppearance[key];
    control.addEventListener("input", () => {
      soundAppearance[key] = Number(control.value);
      try { localStorage.setItem("riff.soundAppearance", JSON.stringify(soundAppearance)); } catch {}
      synchronizeSoundField();
    });
  }
  function immersivePlayback() {
    $("#sound-view-play").innerHTML = icon(audio.paused ? "play" : "pause");
    $("#sound-view-play").setAttribute("aria-label", audio.paused ? "Play recording" : "Pause recording");
    $("#sound-view-play").disabled = !selected;
    $("#sound-view-seek").disabled = !selected;
    $("#sound-view-seek").max = selected?.audio.duration || 1;
    $("#sound-view-seek").value = audio.currentTime;
    $("#sound-view-seek").setAttribute("aria-valuetext", `${formatTime(audio.currentTime)} of ${formatDuration(selected?.audio.duration)}`);
  }
  $("#open-sound-view").addEventListener("click", () => { immersive.showModal(); immersivePlayback(); synchronizeSoundField(); });
  $("#close-sound-view").addEventListener("click", () => immersive.close());
  immersive.addEventListener("close", synchronizeSoundField);
  $("#sound-view-play").addEventListener("click", togglePlay);
  $("#sound-view-seek").addEventListener("input", event => { audio.currentTime = Number(event.target.value); updatePlayback(); });
  for (const event of ["timeupdate", "play", "pause", "loadedmetadata"]) audio.addEventListener(event, immersivePlayback);
  new ResizeObserver(synchronizeSoundField).observe($("#sound-view-canvas"));
  $$("[data-visual]").forEach(button => button.addEventListener("click", () => {
    visualization = button.dataset.visual;
    $$("[data-visual]").forEach(item => item.setAttribute("aria-pressed", String(item === button)));
    $("#sound-field").hidden = visualization !== "sound";
    synchronizeSoundField();
  }));
  audio.addEventListener("emptied", liveSound.reset);
  audio.addEventListener("seeking", liveSound.reset);
  audio.addEventListener("play", () => {
    liveSound.start().then(synchronizeSoundField);
    soundSnapshotPending = quietMotion.matches;
    synchronizeSoundField();
  });
  audio.addEventListener("timeupdate", () => {
    if (soundSnapshotPending && !audio.paused) {
      soundSnapshotPending = false;
      synchronizeSoundField();
    }
  });
  audio.addEventListener("pause", synchronizeSoundField);
  audio.addEventListener("seeked", synchronizeSoundField);
  document.addEventListener("visibilitychange", synchronizeSoundField);
  document.addEventListener("click", event => {
    if (event.target.closest("[data-view], [data-play], [data-select], .brand")) synchronizeSoundField();
  });
  quietMotion.addEventListener("change", synchronizeSoundField);
  new ResizeObserver(synchronizeSoundField).observe($("#sound-field"));
  $("#mini-play").addEventListener("click", togglePlay);
  $("#next-track").addEventListener("click", () => {
    const tracks = state.tracks.filter(track => !track.archived);
    if (!tracks.length) return;
    const next = tracks[(tracks.findIndex(track => track.id === selected?.id) + 1) % tracks.length];
    selectTrack(next.id, true).then(updateMiniPlayer);
  });
});
