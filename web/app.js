// Preserve drafts and listening state across the Rill -> Riff rename.
try {
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith("rill.") && localStorage.getItem("riff." + key.slice(5)) === null)
      localStorage.setItem("riff." + key.slice(5), localStorage.getItem(key));
  }
} catch {}
"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const icons = {
  play: '<path d="m8 5 12 7-12 7Z"/>',
  pause: '<path d="M6 5h4v14H6zM14 5h4v14h-4z"/>',
  heart:
    '<path d="M20.8 4.8a5.4 5.4 0 0 0-7.6 0L12 6l-1.2-1.2a5.4 5.4 0 0 0-7.6 7.6L12 21l8.8-8.6a5.4 5.4 0 0 0 0-7.6Z"/>',
  download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4"/>',
  more: '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  up: '<path d="m6 15 6-6 6 6"/>',
  expand: '<path d="M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  volume:
    '<path d="m11 5-6 4H2v6h3l6 4ZM15 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
  wave: '<path d="M2 12h2l2-7 4 14 4-14 4 14 2-7h2"/>',
  spark:
    '<path d="m12 3 2.6 6.4L21 12l-6.4 2.6L12 21l-2.6-6.4L3 12l6.4-2.6ZM20 2v4m-2-2h4"/>',
  branch:
    '<path d="M6 3v12a4 4 0 0 0 4 4h8M6 8h6a4 4 0 0 0 4-4V3m-2 13 4 3-4 3"/>',
  document: '<path d="M14 2H5v20h14V7Zm0 0v6h5M8 12h8m-8 4h6"/>',
  image:
    '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
};
function icon(name) {
  return `<svg class="icon icon-${name}" viewBox="0 0 24 24" aria-hidden="true">${icons[name] || icons.wave}</svg>`;
}
$$("[data-icon]").forEach((el) =>
  el.replaceWith(fragment(icon(el.dataset.icon))),
);
function fragment(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  return template.content;
}
function hash(text) {
  let value = 2166136261;
  for (const c of String(text)) {
    value ^= c.charCodeAt(0);
    value = Math.imul(value, 16777619);
  }
  return value >>> 0;
}
const formatTime = (seconds) => {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
const formatDuration = (seconds) =>
  formatTime(Math.round(Number(seconds) || 0));
const formatDate = (timestamp) =>
  new Date(timestamp * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
const audio = $("#audio");
audio.volume = 0.8;
let state = {
  tracks: [],
  jobs: [],
  presets: [],
  live: null,
  engine: { ready: false },
};
let selected = null,
  detailTrack = null,
  filter = "all",
  toastTimer,
  pollTimer,
  savedSignature = "",
  busySubmit = false,
  connected = false,
  firstLoad = true;
let latestTrackIds = new Set();
let draftOrigin = {};

function renderReviews() {
  window.RiffReview?.render(selected, state);
}

function notify(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 4500);
}
function errorMessage(id, message = "") {
  $(id).textContent = message;
  $(id).hidden = !message;
}
async function api(path, method = "GET", data) {
  const options = { method, headers: { "X-Riff-Request": "1" } };
  if (data !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      result.error || "The studio could not complete this action.",
    );
  return result;
}
function showView(view, scroll = true) {
  const library = view === "library";
  $("#studio-view").hidden = library;
  $("#library-view").hidden = !library;
  $$(".main-nav [data-view]").forEach((el) => {
    const active = el.dataset.view === view;
    el.classList.toggle("active", active);
    if (active) el.setAttribute("aria-current", "page");
    else el.removeAttribute("aria-current");
  });
  history.replaceState(null, "", `#${library ? "library" : "studio"}`);
  if (scroll) window.scrollTo({ top: 0, behavior: "instant" });
  if (library) renderLibrary();
  updateMiniPlayer();
  synchronizeSoundField();
}
document.addEventListener("click", (event) => {
  const view = event.target.closest("[data-view]");
  if (view) showView(view.dataset.view);
  const close = event.target.closest("[data-close]");
  if (close) $(`#${close.dataset.close}`).close();
});
$(".brand").addEventListener("click", (event) => {
  event.preventDefault();
  showView("studio");
});
window.addEventListener("hashchange", () =>
  showView(location.hash === "#library" ? "library" : "studio"),
);
$$("dialog").forEach((dialog) =>
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      const r = dialog.getBoundingClientRect();
      if (
        event.clientX < r.left ||
        event.clientX > r.right ||
        event.clientY < r.top ||
        event.clientY > r.bottom
      )
        dialog.close();
    }
  }),
);
$("#about-open").addEventListener("click", () =>
  $("#about-dialog").showModal(),
);
$("#footer-about").addEventListener("click", () =>
  $("#about-dialog").showModal(),
);

let draftRefinement = {};
function formRecipe() {
  return {
    ...draftOrigin,
    render_mode: $("#render-mode").value,
    title: $("#title").value.trim(),
    lyrics: ["free", "instrumental"].includes(creationMode())
      ? ""
      : $("#lyrics").value,
    lyrics_draft: $("#lyrics").value,
    style: $("#style").value,
    max_seconds: Number($("#duration").value),
    steps: Number($("#custom-steps").value),
    cot: $("#planning").value,
    seed: $("#seed").value.trim(),
    abc: $("#abc").value,
    refinement: { ...draftRefinement },
    mode: $("[name=creation-mode]:checked").value,
    lyrics_source: draftLyricsSource,
    brief: $("#creative-brief").value,
    idea_engine: $("#idea-engine").value,
    writer_tokens: Number($("#writer-tokens").value),
    cfg_scale: Number($("#guidance").value),
    temperature: Number($("#temperature").value),
    theme: $("#idea-theme").value,
    energy: compassUsed ? Number($("#energy").value) / 100 : null,
    texture: compassUsed ? Number($("#texture").value) / 100 : null,
    hold_words: $("#hold-words").getAttribute("aria-pressed") === "true",
    hold_sound: $("#hold-sound").getAttribute("aria-pressed") === "true",
  };
}
function updateForm() {
  updateExploration();
  $("#local-writer-limit").hidden = $("#idea-engine").value !== "ai";
  $("#performance-context").hidden = !draftOrigin.performance_source;
  $("#performance-caption").textContent = draftOrigin.performance_source
    ? `Performance from ${state.tracks.find((track) => track.id === draftOrigin.performance_source)?.title || "a saved take"}` : "";
  $("#duration").readOnly = !!draftOrigin.performance_source;
  const lines = $("#lyrics")
    .value.split("\n")
    .filter((line) => line.trim() && !/^\[.*\]$/.test(line.trim())).length;
  $("#line-count").textContent = `${lines} ${lines === 1 ? "line" : "lines"}`;
  $("#generate-length").textContent = formatTime($("#duration").value);
  $("#abc-field").hidden = $("#planning").value === "off";
  const quality = Number($("#custom-steps").value);
  $$("[name=quality]").forEach(
    (input) => (input.checked = Number(input.value) === quality),
  );
  $("#quality-hint").textContent =
    ({
      8: "A quick first listen.",
      16: "A little more time to shape the details.",
      32: "More time to shape the performance.",
    }[quality] || `${quality} solver steps.`) +
    " Choose extra room for the full arrangement.";
  $$(".preset-chip").forEach((button) => {
    const preset = state.presets.find((p) => p.id === button.dataset.preset);
    button.classList.toggle("active", preset?.style === $("#style").value);
    button.setAttribute(
      "aria-pressed",
      String(preset?.style === $("#style").value),
    );
  });
}
function saveDraft() {
  updateForm();
  try {
    localStorage.setItem("riff.draft", JSON.stringify(formRecipe()));
    $("#draft-status").textContent = "Draft saved";
  } catch {
    $("#draft-status").textContent = "Draft in this tab";
  }
}
let expandedField = null;
function updateWritingCount() {
  const words = $("#writing-pad")
    .value.trim()
    .split(/\s+/)
    .filter(Boolean).length;
  $("#writing-count").textContent =
    `${words} ${words === 1 ? "word" : "words"}`;
}
for (const button of $$("[data-expand]")) {
  button.addEventListener("click", () => {
    expandedField = $(`#${button.dataset.expand}`);
    $("#writing-heading").textContent =
      button.dataset.expand === "lyrics"
        ? "The lyric sheet"
        : "The musical direction";
    $("#writing-song").textContent = $("#title").value || "A song taking shape";
    $("#writing-pad").value = expandedField.value;
    $("#writing-status").textContent = $("#draft-status").textContent;
    updateWritingCount();
    $("#writing-dialog").showModal();
    $("#writing-pad").focus();
  });
}
$("#writing-pad").addEventListener("input", () => {
  if (!expandedField) return;
  expandedField.value = $("#writing-pad").value;
  expandedField.dispatchEvent(new Event("input", { bubbles: true }));
  $("#writing-status").textContent = $("#draft-status").textContent;
  updateWritingCount();
});
function listeningLink(id) {
  const url = new URL(location.href);
  url.searchParams.set("recording", id);
  url.hash = "studio";
  return url.href;
}
function fillRecipe(recipe, variation = false) {
  draftOrigin = {};
  for (const name of ["parent_track_id", "review_id", "performance_source"]) {
    if (recipe[name]) draftOrigin[name] = recipe[name];
  }
  const mode = ["free", "instrumental", "lyrics", "surprise"].includes(
    recipe.mode,
  )
    ? recipe.mode
    : "lyrics";
  $(`[name=creation-mode][value="${mode}"]`).checked = true;
  draftLyricsSource = recipe.lyrics_source || "provided";
  $("#creative-brief").value = recipe.brief || "";
  $("#idea-engine").value = ["phrases", "openrouter"].includes(recipe.idea_engine) ? recipe.idea_engine : "ai";
  $("#writer-tokens").value = recipe.writer_tokens || 768;
  $("#custom-steps").value = recipe.steps || 8;
  $("#render-mode").value = recipe.render_mode || "music";
  $("#guidance").value = recipe.cfg_scale ?? 1;
  $("#temperature").value = recipe.temperature ?? 1;
  $("#idea-theme").value = [
    "anywhere",
    "water",
    "city",
    "elsewhere",
    "wonder",
    "weather",
    "return",
  ].includes(recipe.theme)
    ? recipe.theme
    : "anywhere";
  compassUsed = recipe.energy != null || recipe.texture != null;
  $("#energy").value = (recipe.energy ?? 0.4) * 100;
  $("#texture").value = (recipe.texture ?? 0.5) * 100;
  $("#hold-words").setAttribute("aria-pressed", String(!!recipe.hold_words));
  $("#hold-sound").setAttribute("aria-pressed", String(!!recipe.hold_sound));
  $("#title").value = variation
    ? `${recipe.title || "Untitled recording"} — variation`
    : recipe.title || "";
  $("#lyrics").value = recipe.lyrics_draft ?? recipe.lyrics ?? "";
  $("#style").value = recipe.style || "";
  $("#duration").value = recipe.max_seconds || 30;
  $("#planning").value = recipe.cot || "off";
  $("#seed").value = variation ? "" : recipe.seed || "";
  $("#abc").value = recipe.abc || "";
  draftRefinement = { ...(recipe.refinement || {}) };
  window.RiffControls?.fill(draftRefinement);

  updateForm();
}
$("#generation-form").addEventListener("input", (event) => {
  if (event.target.name === "quality")
    $("#custom-steps").value = event.target.value;
  saveDraft();
});
$("#generation-form").addEventListener("change", saveDraft);
$("#render-mode").addEventListener("change", () => {
  if ($("#render-mode").value === "plan" && $("#planning").value === "off") $("#planning").value = "full";
  if ($("#render-mode").value === "plan") delete draftOrigin.performance_source;
  saveDraft(); renderQueue();
});
$("#fresh-performance").addEventListener("click", () => {
  delete draftOrigin.performance_source;
  saveDraft(); $("#style").focus();
});
$$("[data-section]").forEach((button) =>
  button.addEventListener("click", () => {
    const input = $("#lyrics"),
      start = input.selectionStart,
      end = input.selectionEnd;
    const prefix = input.value.slice(0, start),
      suffix = input.value.slice(end);
    const insert = `${prefix && !prefix.endsWith("\n\n") ? "\n\n" : ""}[${button.dataset.section}]\n`;
    input.value = prefix + insert + suffix;
    input.focus();
    input.setSelectionRange(start + insert.length, start + insert.length);
    saveDraft();
  }),
);

function renderPresets() {
  $("#presets").innerHTML = state.presets
    .map(
      (p) =>
        `<button type="button" class="preset-chip" data-preset="${esc(p.id)}" aria-pressed="false"><i aria-hidden="true"></i>${esc(p.name)}</button>`,
    )
    .join("");
  $$(".preset-chip").forEach((button) => {
    const preset = state.presets.find((p) => p.id === button.dataset.preset);
    $("i", button).style.backgroundColor = preset.color;
    button.addEventListener("click", () => {
      $("#style").value = styleForMode(preset.style);
      saveDraft();
    });
  });
  updateForm();
}
$("#save-sound").addEventListener("click", () => {
  if (!$("#style").value.trim()) {
    notify("Describe a sound first, then save it.");
    $("#style").focus();
    return;
  }
  $("#preset-name").value = "";
  errorMessage("#preset-error");
  $("#preset-dialog").showModal();
});
$("#preset-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("button[type=submit]", $("#preset-form"));
  button.disabled = true;
  try {
    await api("/api/presets", "POST", {
      name: $("#preset-name").value,
      style: $("#style").value,
    });
    await refresh();
    $("#preset-dialog").close();
    notify("Sound saved for another day.");
  } catch (error) {
    errorMessage("#preset-error", error.message);
  } finally {
    button.disabled = false;
  }
});

function renderPlayerInfo() {
  const has = !!selected;
  $("#refine-performance").hidden = !selected?.recipe?.performance;
  $("#playing-title").textContent = selected?.title || "Room for a first take";
  $("#playing-style").textContent = selected
    ? selected.recipe.style || "Original recording"
    : "Generate a take to start listening.";
  $("#playing-style").title = selected?.recipe.style || "";
  $("#take-tag").textContent = selected
    ? `${formatDuration(selected.audio.duration)} / stereo`
    : "Your collection";
  for (const id of [
    "#play",
    "#seek",
    "#player-favorite",
    "#variation",
    "#track-details",
    "#download-video",
  ])
    $(id).disabled = !has;
  $("#player-favorite").setAttribute(
    "aria-pressed",
    String(!!selected?.favorite),
  );
  $("#player-favorite").setAttribute(
    "aria-label",
    selected?.favorite
      ? "Remove recording from favorites"
      : "Favorite this recording",
  );
  $("#download").setAttribute("aria-disabled", String(!has));
  if (has) $("#download").href = `/api/tracks/${selected.id}/audio?download=1`;
  else $("#download").removeAttribute("href");
  renderReviews();
  window.RiffCompare?.render();
}
function renderWaveform() {
  const peaks = selected?.audio.waveform || Array(160).fill(0.05),
    maximum = Math.max(...peaks, 0.01);
  const bars = peaks
    .map((peak, index) => {
      const h = Math.max(2, (peak / maximum) * 44);
      return `<rect x="${(index * 640) / peaks.length}" y="${(54 - h) / 2}" width="${Math.max(1, 640 / peaks.length - 1.5)}" height="${h}" rx=".6"/>`;
    })
    .join("");
  $("#waveform").innerHTML =
    `<defs><clipPath id="played-clip"><rect id="played-width" width="0" height="54"/></clipPath></defs><g fill="#cbd4e3">${bars}</g><g fill="#5b75b6" clip-path="url(#played-clip)">${bars}</g>`;
  updatePlayback();
}
let selectionRequest = 0;
async function selectTrack(id, autoplay = false) {
  const request = ++selectionRequest;
  try {
    if (selected?.id !== id) {
      const track = await api(`/api/tracks/${id}`);
      if (request !== selectionRequest) return;
      selected = track;
      audio.src = `/api/tracks/${id}/audio`;
      audio.load();
      $("#cover-art").innerHTML = artContent(track.recipe.seed);
      $("#cover-art").setAttribute(
        "aria-label",
        `Seed artwork for ${track.title}`,
      );
      renderPlayerInfo();
      renderWaveform();
      synchronizeSoundField();
      try {
        localStorage.setItem("riff.selected", id);
      } catch {}
      const address = new URL(location.href);
      address.searchParams.set("recording", id);
      history.replaceState(null, "", address.href);
    }
    if (autoplay) {
      await preparePlayback();
      await audio.play();
    }
  } catch (error) {
    notify(error.message || "The recording could not be opened.");
  }
}
function updatePlayback() {
  const duration = Number.isFinite(audio.duration)
      ? audio.duration
      : selected?.audio.duration || 0,
    current = audio.currentTime || 0;
  $("#current-time").textContent = formatTime(current);
  $("#total-time").textContent = formatDuration(duration);
  $("#seek").max = duration || 1;
  $("#seek").value = current;
  $("#seek").setAttribute(
    "aria-valuetext",
    `${formatTime(current)} of ${formatDuration(duration)}`,
  );
  $("#played-width")?.setAttribute(
    "width",
    String(duration ? (640 * current) / duration : 0),
  );
  $("#play").innerHTML = icon(audio.paused ? "play" : "pause");
  $("#play").setAttribute(
    "aria-label",
    audio.paused ? "Play recording" : "Pause recording",
  );
  updateMiniPlayer();
}
audio.addEventListener("timeupdate", updatePlayback);
audio.addEventListener("loadedmetadata", updatePlayback);
audio.addEventListener("play", updatePlayback);
audio.addEventListener("pause", updatePlayback);
audio.addEventListener("ended", updatePlayback);
audio.addEventListener("error", () => {
  if (audio.getAttribute("src"))
    notify(
      "This audio file could not be opened. Check that it is still in the library folder.",
    );
});
async function togglePlay() {
  if (!selected) return;
  try {
    if (audio.paused) {
      await preparePlayback();
      await audio.play();
    } else audio.pause();
  } catch {
    notify("Press play again to begin listening.");
  }
}
$("#play").addEventListener("click", togglePlay);
$("#seek").addEventListener("input", () => {
  audio.currentTime = Number($("#seek").value);
  updatePlayback();
});
$("#volume").addEventListener(
  "input",
  () => (audio.volume = Number($("#volume").value)),
);
document.addEventListener("keydown", (event) => {
  if (event.key === " " && event.target === document.body) {
    event.preventDefault();
    togglePlay();
  }
  if (
    event.key === "Enter" &&
    (event.metaKey || event.ctrlKey) &&
    !$("#studio-view").hidden &&
    !$("dialog[open]")
  ) {
    event.preventDefault();
    $("#generation-form").requestSubmit();
  }
});

async function toggleFavorite(id) {
  const track = state.tracks.find((track) => track.id === id);
  if (!track) return;
  try {
    const updated = await api(`/api/tracks/${id}`, "PATCH", {
      favorite: !track.favorite,
    });
    if (selected?.id === id) {
      selected.favorite = updated.favorite;
      renderPlayerInfo();
    }
    await refresh();
  } catch (error) {
    notify(error.message);
  }
}
$("#player-favorite").addEventListener(
  "click",
  () => selected && toggleFavorite(selected.id),
);
$("#variation").addEventListener("click", () => {
  if (!selected) return;
  fillRecipe(
    {
      ...selected.recipe,
      title: selected.title,
      parent_track_id: selected.id,
      review_id: "",
      performance_source: "",
      render_mode: "music",
    },
    true,
  );
  saveDraft();
  showView("studio");
  $("#title").focus();
  notify("A new take, starting from the same feeling.");
});
$("#refine-performance").addEventListener("click", () => {
  if (!selected?.recipe?.performance) return;
  fillRecipe({ ...selected.recipe, title: selected.title,
    abc: selected.recipe.abc || selected.recipe.symbolic_plan?.abc || "",
    max_seconds: selected.recipe.performance.frames / 25,
    parent_track_id: selected.id, performance_source: selected.id, review_id: "", render_mode: "music" }, true);
  saveDraft(); showView("studio"); $("#style").focus();
  notify("The performance is kept. Explore its sound.");
});

function renderRecent() {
  const tracks = state.tracks.filter((t) => !t.archived).slice(0, 3);
  $("#recent-tracks").innerHTML = tracks.length
    ? tracks
        .map(
          (t) =>
            `<div class="recent-track"><button class="mini-cover" type="button" data-play="${t.id}" aria-label="Play ${esc(t.title)}">${artThumbnail(t.seed)}</button><div class="recent-track-info"><button type="button" data-select="${t.id}">${esc(t.title)}</button><p>${esc(formatDate(t.created))} / ${esc(t.style.split(",").slice(1, 3).join(",").trim() || "Original recording")}</p></div><span class="recent-track-time">${formatDuration(t.duration)}</span><button type="button" class="icon-button" data-detail="${t.id}" aria-label="Details for ${esc(t.title)}">${icon("more")}</button></div>`,
        )
        .join("")
    : '<p class="empty-recent">Start a take. The ones you make will collect here.</p>';
  observeArtPosters($("#recent-tracks"));
}
function renderLibrary() {
  const query = $("#search").value.trim().toLocaleLowerCase();
  let tracks = state.tracks.filter(
    (t) =>
      (filter === "archive" ? t.archived : !t.archived) &&
      (filter !== "favorites" || t.favorite) &&
      `${t.title} ${t.style}`.toLocaleLowerCase().includes(query),
  );
  tracks.sort((a, b) =>
    $("#sort").value === "name"
      ? a.title.localeCompare(b.title)
      : $("#sort").value === "oldest"
        ? a.created - b.created
        : b.created - a.created,
  );
  $("#library-description").textContent =
    `${state.tracks.filter((t) => !t.archived).length} ${state.tracks.filter((t) => !t.archived).length === 1 ? "recording" : "recordings"}, and room for whatever comes next.`;
  $("#library-grid").innerHTML = tracks
    .map(
      (t) =>
        `<article class="library-card"><button class="library-card-art" type="button" data-play="${t.id}" aria-label="Play ${esc(t.title)}">${artThumbnail(t.seed)}<span class="card-play-overlay">${icon("play")}</span></button><div class="library-card-body"><div class="library-card-title"><button type="button" data-select="${t.id}">${esc(t.title)}</button><button class="icon-button" type="button" data-favorite="${t.id}" aria-label="Favorite ${esc(t.title)}" aria-pressed="${t.favorite}">${icon("heart")}</button></div><p>${esc(t.style)}</p><div class="card-meta"><span>${formatDuration(t.duration)}</span><span>${esc(formatDate(t.created))}</span><button class="icon-button" type="button" data-detail="${t.id}" aria-label="Details for ${esc(t.title)}">${icon("more")}</button></div></div></article>`,
    )
    .join("");
  observeArtPosters($("#library-grid"));
  $("#library-empty").hidden = tracks.length > 0;
  $("#empty-title").textContent = query
    ? "Try a different search."
    : filter === "favorites"
      ? "Keep the ones you love close."
      : filter === "archive"
        ? "A place for earlier takes."
        : "A collection starts with a sound.";
  $("#empty-description").textContent = query
    ? "Try another title, instrument, or sound."
    : filter === "favorites"
      ? "Tap a heart on any recording to collect it here."
      : filter === "archive"
        ? "Return to earlier takes whenever you like."
        : "Make your first recording in the studio.";
  renderHistory();
}
$$("[data-filter]").forEach((button) =>
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    $$("[data-filter]").forEach((b) => {
      b.classList.toggle("active", b === button);
      b.setAttribute("aria-pressed", String(b === button));
    });
    renderLibrary();
  }),
);
$("#search").addEventListener("input", renderLibrary);
$("#sort").addEventListener("change", renderLibrary);
document.addEventListener("click", async (event) => {
  const play = event.target.closest("[data-play]"),
    select = event.target.closest("[data-select]"),
    favorite = event.target.closest("[data-favorite]"),
    detail = event.target.closest("[data-detail]");
  if (play) {
    await selectTrack(play.dataset.play, true);
  }
  if (select) {
    await selectTrack(select.dataset.select);
    showView("studio");
  }
  if (favorite) await toggleFavorite(favorite.dataset.favorite);
  if (detail) await openDetails(detail.dataset.detail);
});

async function openDetails(id) {
  try {
    detailTrack = await api(`/api/tracks/${id}`);
    $("#edit-title").value = detailTrack.title;
    $("#edit-notes").value = detailTrack.notes;
    $("#detail-style").textContent = detailTrack.recipe.style;
    $("#detail-lyrics").textContent =
      detailTrack.recipe.lyrics ||
      (detailTrack.recipe.mode === "instrumental"
        ? "Instrumental"
        : "Open lyrics");
    $("#detail-settings").textContent =
      `Seed ${detailTrack.recipe.seed} / ${detailTrack.recipe.steps} steps / ${detailTrack.recipe.cot === "off" ? "Direct" : detailTrack.recipe.cot === "melody" ? "Melody" : "Melody & chords"}`;
    const peak =
      detailTrack.metrics.peak_process_memory_bytes || detailTrack.metrics.macos_lifetime_peak_footprint_bytes_observed;
    $("#track-facts").innerHTML =
      `<span>${formatDuration(detailTrack.audio.duration)}</span><span>${detailTrack.audio.sample_rate / 1000} kHz stereo</span>${peak ? `<span>${(peak / 1024 ** 3).toFixed(2)} GiB peak</span>` : ""}`;
    $("#archive-track").textContent = detailTrack.archived
      ? "Restore recording"
      : "Archive recording";
    $("#export-audio").href = `/api/tracks/${id}/audio?download=1`;
    $("#export-recipe").href = `/api/tracks/${id}/recipe`;
    errorMessage("#detail-error");
    $("#track-dialog").showModal();
  } catch (error) {
    notify(error.message);
  }
}
$("#track-details").addEventListener(
  "click",
  () => selected && openDetails(selected.id),
);
$("#copy-listening-link").addEventListener("click", async () => {
  if (!detailTrack) return;
  try {
    await navigator.clipboard.writeText(listeningLink(detailTrack.id));
    notify("Listening link copied.");
  } catch {
    errorMessage(
      "#detail-error",
      `Listening link: ${listeningLink(detailTrack.id)}`,
    );
  }
});
$("#track-edit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!detailTrack) return;
  const button = $("button[type=submit]", $("#track-edit-form"));
  button.disabled = true;
  try {
    const updated = await api(`/api/tracks/${detailTrack.id}`, "PATCH", {
      title: $("#edit-title").value,
      notes: $("#edit-notes").value,
    });
    if (selected?.id === updated.id) {
      selected = updated;
      renderPlayerInfo();
    }
    await refresh();
    $("#track-dialog").close();
    notify("Recording notes saved.");
  } catch (error) {
    errorMessage("#detail-error", error.message);
  } finally {
    button.disabled = false;
  }
});
$("#archive-track").addEventListener("click", async () => {
  if (!detailTrack) return;
  try {
    await api(`/api/tracks/${detailTrack.id}`, "PATCH", {
      archived: !detailTrack.archived,
    });
    $("#track-dialog").close();
    await refresh();
    notify(
      detailTrack.archived
        ? "Recording restored."
        : "Recording archived. You can restore it from the library.",
    );
  } catch (error) {
    errorMessage("#detail-error", error.message);
  }
});
$("#export-art").addEventListener("click", async () => {
  if (!detailTrack) return;
  const track = detailTrack, canvas = document.createElement("canvas");
  canvas.width = 1760; canvas.height = 1360;
  drawSeedArtwork(canvas.getContext("2d"), canvas.width, canvas.height, track.recipe.seed, 0, [], artworkAppearance());
  const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
  if (!blob) { notify("The artwork could not be exported."); return; }
  const url = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = url;
  link.download = `${track.title}.png`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

function renderQueue() {
  const active = state.jobs.filter((j) =>
    ["queued", "running", "cancelling"].includes(j.status),
  );
  $("#queue-panel").hidden = !active.length;
  const live = state.live,
    running = active.find((j) => j.status !== "queued");
  if (running) {
    const stage =
      running.status === "cancelling"
        ? "Stopping this take"
        : live?.stage || "Preparing the studio";
    const stageIndex = live?.stage_index || 0;
    const id = running.id;
    if ($("#live-job").dataset.jobId !== id) {
      $("#live-job").dataset.jobId = id;
      $("#live-job").innerHTML =
        `<div class="job-heading"><h3></h3><button type="button" class="text-button" data-cancel="${id}">Cancel take</button></div><div class="live-stage"><span class="spinner" aria-hidden="true"></span><span id="live-stage-text"></span></div><div class="stage-dots" aria-hidden="true">${Array(4).fill("<span></span>").join("")}</div><div class="live-metrics"><span id="live-time"></span><span id="live-memory"></span></div>`;
    }
    $("#live-job h3").textContent = running.title;
    const progress = live?.stage_progress;
    $("#live-stage-text").textContent =
      running.status !== "cancelling" && Number.isFinite(progress)
        ? `${stage} · ${Math.floor(progress * 100)}%`
        : stage;
    $("#live-time").textContent = `${formatTime(live?.elapsed || 0)} elapsed`;
    $("#live-memory").textContent = live?.footprint
      ? `${(live.footprint / 1024 ** 3).toFixed(2)} GiB in use`
      : "";
    $$(".stage-dots span").forEach((span, index) => {
      span.classList.toggle("done", index < stageIndex);
      span.classList.toggle("current", index === stageIndex);
    });
    $("[data-cancel]", $("#live-job")).disabled =
      running.status === "cancelling";
  } else {
    $("#live-job").innerHTML = "";
    $("#live-job").dataset.jobId = "";
  }
  const queued = active.filter((j) => j.status === "queued")
    .sort((a, b) => (a.queue_position ?? a.created) - (b.queue_position ?? b.created) || a.created - b.created || a.id.localeCompare(b.id));
  const signature = JSON.stringify(queued.map((j) => [j.id, j.title]));
  if ($("#queued-jobs").dataset.signature !== signature) {
    const focused = $("#queued-jobs").contains(document.activeElement) ? document.activeElement : null;
    const focusKey = focused?.dataset.queueMove ? `[data-queue-move="${focused.dataset.queueMove}"][data-job="${focused.dataset.job}"]`
      : focused?.dataset.cancel ? `[data-cancel="${focused.dataset.cancel}"]` : null;
    $("#queued-jobs").dataset.signature = signature;
    $("#queued-jobs").innerHTML = queued
      .map(
        (j, index) =>
          `<div class="queued-job" data-queued-job="${j.id}"><span class="queue-number" aria-label="Queue position ${index + 1}">${String(index + 1).padStart(2, "0")}</span><strong>${esc(j.title)}</strong><div class="queue-order" role="group" aria-label="Order ${esc(j.title)}"><button type="button" class="icon-button" data-queue-move="up" data-job="${j.id}" aria-label="Move ${esc(j.title)} earlier" aria-disabled="${index === 0}">${icon("up")}</button><button type="button" class="icon-button" data-queue-move="down" data-job="${j.id}" aria-label="Move ${esc(j.title)} later" aria-disabled="${index === queued.length - 1}">${icon("chevron")}</button></div><button type="button" class="icon-button" data-cancel="${j.id}" aria-label="Cancel ${esc(j.title)}">${icon("close")}</button></div>`,
      )
      .join("");
    if (focusKey) $(focusKey, $("#queued-jobs"))?.focus({ preventScroll: true });
  }
  $("#generate-label").textContent = busySubmit
    ? "Adding your take…"
    : active.length
      ? "Add to queue"
      : $("#render-mode").value === "plan" ? "Compose score" : draftOrigin.performance_source ? "Render performance" : "Generate take";
  $("#generate").disabled = busySubmit || !connected || !state.engine.ready;
  $("#engine-label").textContent = !connected
    ? "Reconnecting…"
    : running
      ? "Making music"
      : "";
  $("#engine-label").parentElement.hidden = connected && !running;
  $("#generation-footnote").textContent = !connected
    ? "Open Riff.command to reconnect to your studio."
    : active.length
      ? "Takes run one at a time. Keep writing while you wait."
      : "";
  $("#generation-footnote").parentElement.hidden = connected;
}
function renderHistory() {
  const jobs = state.jobs.slice().reverse();
  const names = {
    done: "Ready to play",
    failed: "Couldn’t finish",
    cancelled: "Cancelled",
    interrupted: "Interrupted",
    queued: "Queued",
    running: "Generating",
    cancelling: "Stopping",
  };
  $("#job-history").innerHTML = jobs.length
    ? jobs
        .map(
          (job) =>
            `<div class="history-item ${esc(job.status)}"><div><strong>${esc(job.title)}</strong><p>${esc(job.error || formatDate(job.created))}</p></div><span class="history-status">${names[job.status] || esc(job.status)}</span>${job.track_id ? `<button type="button" class="text-button" data-play="${job.track_id}">Listen</button>` : ["failed", "cancelled", "interrupted"].includes(job.status) ? `<button type="button" class="text-button" data-revisit="${job.id}">Reopen draft</button>` : ""}</div>`,
        )
        .join("")
    : '<p class="history-empty">New takes and their progress will appear here.</p>';
}
document.addEventListener("click", async (event) => {
  const move = event.target.closest("[data-queue-move]");
  if (move) {
    if (move.getAttribute("aria-disabled") === "true") return;
    move.setAttribute("aria-disabled", "true");
    try {
      const result = await api(`/api/jobs/${move.dataset.job}/move`, "POST", { direction: move.dataset.queueMove });
      await refresh();
      notify(`Queue position ${result.position}`);
    } catch (error) {
      move.setAttribute("aria-disabled", "false");
      notify(error.message);
      await refresh();
    }
    return;
  }
  const cancel = event.target.closest("[data-cancel]"),
    revisit = event.target.closest("[data-revisit]");
  if (cancel) {
    cancel.disabled = true;
    try {
      const result = await api(
        `/api/jobs/${cancel.dataset.cancel}/cancel`,
        "POST",
        {},
      );
      await refresh();
      notify(
        result.status === "cancelling"
          ? "Stopping this take. Your words are still here."
          : "Take cancelled. Your words are still here.",
      );
    } catch (error) {
      cancel.disabled = false;
      notify(error.message);
    }
  }
  if (revisit) {
    try {
      const job = await api(`/api/jobs/${revisit.dataset.revisit}`);
      fillRecipe(job.recipe);
      saveDraft();
      showView("studio");
      notify("The take is back on your writing desk.");
    } catch (error) {
      notify(error.message);
    }
  }
});
$("#generation-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busySubmit) return;
  busySubmit = true;
  errorMessage("#form-error");
  renderQueue();
  try {
    const submitted = formRecipe();
    const job = await api("/api/generations", "POST", submitted);
    if (JSON.stringify(formRecipe()) === JSON.stringify(submitted)) {
      fillRecipe({
        ...submitted,
        ...job.recipe,
        seed: submitted.seed,
        lyrics_draft: ["free", "instrumental"].includes(submitted.mode)
          ? submitted.lyrics_draft
          : job.recipe.lyrics,
      });
    }
    notify(`“${job.recipe.title}” is on its way.`);
    saveDraft();
    await refresh();
    if (matchMedia("(max-width:760px)").matches)
      $("#queue-panel").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    errorMessage("#form-error", error.message);
  } finally {
    busySubmit = false;
    renderQueue();
  }
});

async function refresh() {
  const next = await api("/api/state");
  connected = true;
  const signature = JSON.stringify([next.tracks, next.presets, next.jobs]);
  const added = next.tracks.filter((track) => !latestTrackIds.has(track.id));
  const oldJobs = new Map(state.jobs.map((job) => [job.id, job.status]));
  state = next;
  window.RiffControls?.render(next);
  window.RiffScore?.update(next);
  window.RiffCompare?.render();
  $("#library-count").textContent = state.tracks.filter(
    (t) => !t.archived,
  ).length;

  if (signature !== savedSignature) {
    renderRecent();
    renderLibrary();
    renderPresets();
    savedSignature = signature;
  }
  renderQueue();
  if (firstLoad) {
    let preferred = new URL(location.href).searchParams.get("recording");
    try {
      preferred ||= localStorage.getItem("riff.selected");
    } catch {}
    const first =
      state.tracks.find((t) => t.id === preferred) ||
      state.tracks.find((t) => !t.archived);
    if (first) await selectTrack(first.id);
    firstLoad = false;
  } else if (added.length) {
    notify(`“${added[0].title}” is ready to listen.`);
    if (audio.paused && !$("#producer-panel").open && !$("#take-comparison").open && !$("dialog[open]"))
      await selectTrack(added[0].id);
  }
  next.jobs
    .filter(
      (job) =>
        ["failed", "interrupted"].includes(job.status) &&
        oldJobs.get(job.id) &&
        oldJobs.get(job.id) !== job.status,
    )
    .forEach((job) => notify(`${job.title}: ${job.error}`));
  latestTrackIds = new Set(state.tracks.map((t) => t.id));
  renderReviews();
}
async function poll() {
  try {
    await refresh();
  } catch {
    connected = false;
    renderQueue();
  } finally {
    pollTimer = setTimeout(poll, document.hidden ? 8000 : 2000);
  }
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    clearTimeout(pollTimer);
    poll();
  }
});

const example = {
  title: "",
  lyrics: "",
  style: "",
  mode: "free",
  lyrics_source: "none",
  max_seconds: 30,
  steps: 8,
  cot: "off",
  seed: "",
  abc: "",
};
try {
  const stored = JSON.parse(localStorage.getItem("riff.draft") || "null");
  fillRecipe(stored || example);
  if (stored) $("#draft-status").textContent = "Draft restored";
} catch {
  fillRecipe(example);
  $("#draft-status").textContent = "Draft in this tab";
}
$("#cover-art").innerHTML = artContent("reed0f_cobalt8d82");
renderWaveform();
renderPlayerInfo();
showView(location.hash === "#library" ? "library" : "studio", false);
poll();
