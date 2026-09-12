/* The recording's seed contours move on the audio clock, including after seeks. */
let soundAnimation = null;
let visualization = "art";
let soundMotion = null;
let soundMotionTrack = null;
let soundSnapshotPending = false;
const quietMotion = matchMedia("(prefers-reduced-motion: reduce)");
const motionCache = new Map();

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
  if (!data?.frames?.length) return [0, 0, 0, 0];
  const position = Math.max(0, seconds * data.fps), index = Math.floor(position);
  const blend = position - index;
  const frame = offset => data.frames[Math.min(data.frames.length - 1, Math.max(0, offset))];
  // A symmetric window softens frame boundaries without accumulating state.
  const smooth = offset => [0, 1, 2, 3].map(band =>
    (frame(offset - 1)[band] + 2 * frame(offset)[band] + frame(offset + 1)[band]) / 4);
  const a = smooth(index), b = smooth(index + 1);
  return a.map((value, band) => value + (b[band] - value) * blend);
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
  const canvas = $("#sound-field"), context = canvas.getContext("2d");
  if (!context || canvas.hidden || document.hidden || $("#studio-view").hidden) return;
  const bounds = canvas.getBoundingClientRect(), scale = devicePixelRatio || 1;
  if (!bounds.width || !bounds.height) return;
  const width = Math.round(bounds.width * scale), height = Math.round(bounds.height * scale);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const seed = selected?.recipe.seed ?? "reed0f_cobalt8d82";
  drawSeedArtwork(context, width, height, seed, audio.currentTime,
    motionAt(soundMotionTrack === selected?.id ? soundMotion : null, audio.currentTime));
  if (!audio.paused && !quietMotion.matches) soundAnimation = requestAnimationFrame(drawSoundField);
}

function synchronizeSoundField() {
  if (soundAnimation !== null) cancelAnimationFrame(soundAnimation);
  soundAnimation = null;
  if (visualization !== "sound" || document.hidden || $("#studio-view").hidden) return;
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
