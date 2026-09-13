/* A tab keeps its loaded build until the listener chooses to refresh. */
(() => {
  "use strict";
  const key = "riff.studioRefresh", loadedBuild = document.querySelector('meta[name="riff-studio-build"]')?.content;
  const banner = document.querySelector("#studio-update"), button = document.querySelector("#refresh-studio");
  const hint = document.querySelector("#studio-update-hint");
  // Only creative inputs belong in the handoff. Credentials and settings do not.
  const creative = ["title", "lyrics", "style", "duration", "custom-steps", "solver", "planning", "seed", "abc",
    "creative-brief", "idea-engine", "writer-tokens", "guidance", "temperature", "idea-theme", "energy", "texture",
    "render-mode", "audition-length", "audition-steps"];
  const panels = [".creative-controls", ".refinement-controls", ".study-controls", "#producer-panel", "#take-comparison"];
  let handoff = null, changed = false, saveError = false, refreshing = false, lastCreative = "", fieldsRestored = false;
  try {
    const value = JSON.parse(sessionStorage.getItem(key) || "null");
    if (value?.format === 1 && value.recipe && typeof value.recipe === "object" && !Array.isArray(value.recipe)) handoff = value;
  } catch {}
  const inputs = () => [...creative.map(id => document.getElementById(id)),
    ...document.querySelectorAll('#refinement-fields input[id^="control-"]')].filter(Boolean);
  const blocked = () => {
    if (typeof videoExport !== "undefined" && videoExport) return "Your video is still being exported.";
    if (!audio.paused) return "Pause playback to refresh.";
    if (ideaBusy || compassPending) return "Your draft is still being written.";
    return window.RiffScore?.refreshWait?.() || (busySubmit ? "Your take is being queued." : "");
  };

  function render(next) {
    if (/^[a-f0-9]{64}$/.test(loadedBuild) && /^[a-f0-9]{64}$/.test(next.studio?.build))
      changed = next.studio.build !== loadedBuild;
    banner.hidden = !changed;
    if (!changed) return;
    const waiting = blocked();
    button.disabled = !!waiting || refreshing;
    hint.textContent = saveError ? "Your draft couldn’t be saved for refresh. Please try again."
      : waiting || "Your draft and place in the music will be kept.";
  }

  function restoreFields() {
    if (!handoff) return;
    for (const input of inputs()) {
      const value = handoff.fields?.[input.id];
      if (!value || typeof value.value !== "string") continue;
      if (fieldsRestored && !input.id.startsWith("control-")) continue;
      input.value = value.value;
      if (Number.isInteger(value.start) && Number.isInteger(value.end)) {
        try { input.setSelectionRange(value.start, value.end); } catch {}
      }
      if (Number.isFinite(value.scroll)) input.scrollTop = value.scroll;
    }
    fieldsRestored = true;
    updateForm();
  }

  function restoreContext() {
    if (!handoff) return;
    const saved = handoff;
    handoff = null;
    try { sessionStorage.removeItem(key); } catch {}
    revisionUndo = saved.undo || null;
    document.querySelector("#revision-undo").hidden = !revisionUndo;
    ideaUndo = saved.ideaUndo || null;
    document.querySelector("#undo-idea").disabled = !ideaUndo;
    if (["all", "favorites", "archive"].includes(saved.library?.filter)) filter = saved.library.filter;
    document.querySelectorAll("[data-filter]").forEach(input => {
      const active = input.dataset.filter === filter;
      input.classList.toggle("active", active); input.setAttribute("aria-pressed", String(active));
    });
    if (typeof saved.library?.search === "string") document.querySelector("#search").value = saved.library.search;
    if (["newest", "oldest", "name"].includes(saved.library?.sort)) document.querySelector("#sort").value = saved.library.sort;
    for (const selector of panels) {
      const panel = document.querySelector(selector);
      if (panel && typeof saved.panels?.[selector] === "boolean") panel.open = saved.panels[selector];
    }
    showView(saved.view === "library" ? "library" : "studio", false);
    if (saved.listening?.track === selected?.id) {
      const restorePosition = () => {
        audio.removeEventListener("loadedmetadata", restorePosition); audio.removeEventListener("error", restorePosition);
        const seconds = saved.listening.seconds;
        if (saved.listening.track === selected?.id && Number.isFinite(seconds) && seconds >= 0 && Number.isFinite(audio.duration)) {
          audio.currentTime = Math.min(seconds, audio.duration); updatePlayback();
        }
      };
      if (audio.readyState >= 1 || audio.error) restorePosition();
      else {
        audio.addEventListener("loadedmetadata", restorePosition, { once: true });
        audio.addEventListener("error", restorePosition, { once: true });
      }
      if (Number.isFinite(saved.listening.volume)) audio.volume = Math.min(1, Math.max(0, saved.listening.volume));
      audio.muted = saved.listening.muted === true;
      document.querySelector("#volume").value = audio.volume;
      updatePlayback();
    }
    saveDraft();
    if (creative.includes(saved.focus)) document.getElementById(saved.focus)?.focus({ preventScroll: true });
    if (Array.isArray(saved.scroll) && saved.scroll.every(Number.isFinite)) window.scrollTo({ left: saved.scroll[0], top: saved.scroll[1], behavior: "instant" });
    notify("Studio refreshed · your draft is ready");
  }

  document.addEventListener("focusin", event => { if (creative.includes(event.target.id)) lastCreative = event.target.id; });
  button.addEventListener("click", () => {
    if (!changed || refreshing || blocked()) { render(state); return; }
    saveError = false;
    const saved = { format: 1, recipe: formRecipe(), undo: revisionUndo, ideaUndo, fields: Object.fromEntries(inputs().map(input => [input.id,
      { value: input.value, start: input.selectionStart, end: input.selectionEnd, scroll: input.scrollTop }])),
      listening: { track: selected?.id || "", seconds: audio.currentTime, volume: audio.volume, muted: audio.muted },
      view: document.querySelector("#library-view").hidden ? "studio" : "library",
      library: { filter, search: document.querySelector("#search").value, sort: document.querySelector("#sort").value },
      panels: Object.fromEntries(panels.map(selector => [selector, document.querySelector(selector)?.open || false])),
      focus: lastCreative, scroll: [scrollX, scrollY] };
    try {
      const encoded = JSON.stringify(saved);
      sessionStorage.setItem(key, encoded);
      if (sessionStorage.getItem(key) !== encoded) throw new Error("The refresh handoff was not saved.");
    } catch { saveError = true; render(state); return; }
    refreshing = true; render(state); location.reload();
  });
  document.addEventListener("DOMContentLoaded", () => {
    for (const event of ["play", "pause", "ended"]) audio.addEventListener(event, () => render(state));
    render(state);
  });
  window.RiffUpdate = { render, restoreFields, restoreContext, draft: () => handoff?.recipe, preferredTrack: () => handoff?.listening?.track };
})();
