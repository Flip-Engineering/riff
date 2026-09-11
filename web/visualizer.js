/* One media element, one optional analyser, and no second audio decode. */
let soundContext = null;
let soundAnalyser = null;
let soundSamples = null;
let soundAnimation = null;
let visualization = "art";
let soundTransition = Promise.resolve();
let soundSnapshotPending = false;
const quietMotion = matchMedia("(prefers-reduced-motion: reduce)");

async function preparePlayback() {
  soundTransition = soundTransition
    .catch(() => {})
    .then(async () => {
      if (soundContext?.state === "suspended") await soundContext.resume();
    });
  await soundTransition;
}

function restSoundContext() {
  soundTransition = soundTransition
    .catch(() => {})
    .then(async () => {
      if (audio.paused && soundContext?.state === "running")
        await soundContext.suspend();
    });
  return soundTransition;
}

function updateMiniPlayer() {
  $("#next-track").disabled = state.tracks.filter((track) => !track.archived).length < 2;
  const visible = !!selected && $("#studio-view").hidden;
  $("#mini-player").hidden = !visible;
  document.body.classList.toggle("with-mini-player", visible);
  $("#mini-title").textContent = selected?.title || "Listening room";
  $("#mini-title").setAttribute(
    "aria-label",
    `Return to the studio: ${selected?.title || "Listening room"}`,
  );
  $("#mini-time").textContent = formatTime(audio.currentTime);
  $("#mini-play").innerHTML = icon(audio.paused ? "play" : "pause");
  $("#mini-play").setAttribute(
    "aria-label",
    audio.paused ? "Play library recording" : "Pause library playback",
  );
}

function drawSoundField() {
  soundAnimation = null;
  const canvas = $("#sound-field"),
    context = canvas.getContext("2d");
  if (!context || canvas.hidden || document.hidden || $("#studio-view").hidden)
    return;
  const bounds = canvas.getBoundingClientRect(),
    scale = devicePixelRatio || 1;
  if (!bounds.width || !bounds.height) return;
  const width = Math.round(bounds.width * scale),
    height = Math.round(bounds.height * scale);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  context.setTransform(scale, 0, 0, scale, 0, 0);
  const w = bounds.width,
    h = bounds.height;
  context.fillStyle = "#e0e7f3";
  context.fillRect(0, 0, w, h);
  context.strokeStyle = "#cad5e8";
  context.lineWidth = 1;
  for (let y = h * 0.22; y < h * 0.86; y += h * 0.16) {
    context.beginPath();
    context.moveTo(w * 0.09, y);
    context.lineTo(w * 0.91, y);
    context.stroke();
  }
  if (soundAnalyser) soundAnalyser.getByteFrequencyData(soundSamples);
  const bands = Math.max(1, Math.floor((w * 0.82) / 7));
  const spacing = (w * 0.82) / bands;
  for (let band = 0; band < bands; band++) {
    const start = Math.floor((band / bands) ** 2 * (soundSamples?.length || 1));
    const end = Math.max(
      start + 1,
      Math.floor(((band + 1) / bands) ** 2 * (soundSamples?.length || 1)),
    );
    let value = 0;
    if (soundSamples && !audio.paused) {
      for (let i = start; i < end && i < soundSamples.length; i++)
        value += soundSamples[i];
      value /= (end - start) * 255;
    }
    const barHeight = Math.max(2, value * h * 0.59),
      x = w * 0.09 + band * spacing;
    context.fillStyle = band % 5 === 0 ? "#819d9c" : "#5878bb";
    context.globalAlpha = 0.55 + value * 0.4;
    context.fillRect(
      x,
      h * 0.74 - barHeight,
      Math.max(1, spacing - 2),
      barHeight,
    );
  }
  context.globalAlpha = 1;
  $("#sound-field-caption").textContent = audio.paused
    ? "Press play to see the sound"
    : quietMotion.matches
      ? "Sound snapshot / reduced motion"
      : "Live spectrum / low to high";
  if (!audio.paused && !quietMotion.matches)
    soundAnimation = requestAnimationFrame(drawSoundField);
}

function synchronizeSoundField() {
  if (soundAnimation !== null) cancelAnimationFrame(soundAnimation);
  soundAnimation = null;
  if (
    visualization === "sound" &&
    !document.hidden &&
    !$("#studio-view").hidden
  )
    drawSoundField();
}

document.addEventListener("DOMContentLoaded", () => {
  $$("[data-visual]").forEach((button) =>
    button.addEventListener("click", async () => {
      try {
        if (button.dataset.visual === "sound" && !soundContext) {
          const AudioContextClass =
            window.AudioContext || window.webkitAudioContext;
          if (!AudioContextClass)
            throw new Error(
              "This browser does not support live audio visualization.",
            );
          soundContext = new AudioContextClass();
          soundAnalyser = soundContext.createAnalyser();
          // A 1024-sample FFT resolves this compact display without storing a whole song.
          soundAnalyser.fftSize = 1024;
          soundSamples = new Uint8Array(soundAnalyser.frequencyBinCount);
          const source = soundContext.createMediaElementSource(audio);
          source.connect(soundAnalyser);
          soundAnalyser.connect(soundContext.destination);
        }
        if (!audio.paused) await preparePlayback();
        visualization = button.dataset.visual;
        $$("[data-visual]").forEach((item) =>
          item.setAttribute("aria-pressed", String(item === button)),
        );
        $("#sound-field").hidden = visualization !== "sound";
        $("#sound-field-caption").hidden = visualization !== "sound";
        $(".cover-stamp").hidden = visualization === "sound";
        synchronizeSoundField();
        if (audio.paused) await restSoundContext();
      } catch (error) {
        notify(error.message);
      }
    }),
  );
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
  audio.addEventListener("pause", () => {
    synchronizeSoundField();
    restSoundContext().catch(() => {});
  });
  audio.addEventListener("seeked", synchronizeSoundField);
  document.addEventListener("visibilitychange", synchronizeSoundField);
  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-view], [data-play], [data-select], .brand"))
      synchronizeSoundField();
  });
  quietMotion.addEventListener("change", synchronizeSoundField);
  new ResizeObserver(synchronizeSoundField).observe($("#sound-field"));
  $("#mini-play").addEventListener("click", togglePlay);
  $("#next-track").addEventListener("click", () => {
    const tracks = state.tracks.filter((track) => !track.archived);
    if (!tracks.length) return;
    const next =
      tracks[
        (tracks.findIndex((track) => track.id === selected?.id) + 1) %
          tracks.length
      ];
    selectTrack(next.id, true).then(updateMiniPlayer);
  });
});
