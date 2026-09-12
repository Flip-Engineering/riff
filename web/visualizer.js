/* The recording's seed contours move on the audio clock, including after seeks. */
let soundAnimation = null;
let visualization = "art";
let soundMotion = null;
let soundMotionTrack = null;
let soundSnapshotPending = false;
const quietMotion = matchMedia("(prefers-reduced-motion: reduce)");
const motionCache = new Map();
let soundAppearance = { ...RiffArtwork.defaults };
try {
  const saved = JSON.parse(localStorage.getItem("riff.soundAppearance") || "{}");
  for (const [key, maximum] of [["surface", 1], ["motion", 2]]) {
    if (Number.isFinite(saved[key])) soundAppearance[key] = Math.max(0, Math.min(maximum, saved[key]));
  }
} catch {}
function artworkAppearance() { return { ...soundAppearance }; }

async function preparePlayback() {}

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
  const frame = offset => data.frames[Math.min(data.frames.length - 1, Math.max(0, offset))];
  // A symmetric window softens frame boundaries without accumulating state.
  const smooth = offset => Array.from({ length: 8 }, (_, band) =>
    ((frame(offset - 1)[band] || 0) + 2 * (frame(offset)[band] || 0) + (frame(offset + 1)[band] || 0)) / 4);
  const at = time => {
    if (time < 0) return Array(8).fill(0);
    const position = time * data.fps, index = Math.floor(position), blend = position - index;
    const a = smooth(index), b = smooth(index + 1);
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
  motion.history = Array.from({ length: 20 }, (_, i) => at(seconds - i / 19));
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
    motionAt(soundMotionTrack === selected?.id ? soundMotion : null, audio.currentTime), artworkAppearance());
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
  for (const key of ["surface", "motion"]) {
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
  audio.addEventListener("play", () => {
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
