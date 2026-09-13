/* Saved synthesis keeps the music; finishing it creates another audio take. */
(() => {
  "use strict";
  const el = selector => document.querySelector(selector);
  const upstream = ["lyrics", "style", "abc", "score_source", "mode", "cot", "max_seconds", "steps",
    "solver", "cfg_scale", "temperature", "seed", "refinement", "performance_source"];
  const copy = value => JSON.parse(JSON.stringify(value));
  const ordered = value => value && typeof value === "object" && !Array.isArray(value)
    ? Object.fromEntries(Object.keys(value).sort().map(key => [key, ordered(value[key])])) : value;
  const music = recipe => JSON.stringify(ordered(Object.fromEntries(upstream.map(key =>
    [key, recipe[key] ?? (key.endsWith("_source") ? "" : key === "refinement" ? {} : null)]))));
  const precision = ["Original model", "32-bit float", "16-bit float", "Brain float 16", "8-bit", "4-bit", "4-bit K"];
  let draft = null, draftSerial = 0, detailSerial = 0, detail = null;
  const pending = new Set();

  function controls(prefix) {
    return `<details class="acoustic-refinements" id="${prefix}-refinements"><summary>Audio refinement</summary>
      <p class="control-hint">Adjust how this performance becomes audio. Leave a field empty to keep its original setting.</p>
      <div class="acoustic-grid">
        <label for="${prefix}-core">Section size <span class="quiet-text">frames</span><input id="${prefix}-core" type="number" min="1" step="1" inputmode="numeric" data-decoder="core_frames" /></label>
        <label for="${prefix}-halo">Overlap <span class="quiet-text">frames</span><input id="${prefix}-halo" type="number" min="0" step="1" inputmode="numeric" data-decoder="halo_frames" /></label>
        <label for="${prefix}-storage">Model precision<select id="${prefix}-storage" data-decoder="storage"><option value="">Keep original setting</option>${precision.map((name, value) => `<option value="${value}">${name}</option>`).join("")}</select></label>
      </div><p class="control-hint" id="${prefix}-captured"></p>
      <p class="control-hint">Section size and overlap balance working memory and context at each join. Lower model precision may use less storage; some audio operations still use full precision.</p>
      <button type="button" class="text-button" data-decoder-reset="${prefix}">Use original settings</button>
    </details>`;
  }
  el("#acoustic-controls").innerHTML = controls("audio");
  el("#detail-acoustic-controls").innerHTML = controls("detail-audio");

  function fillControls(prefix, values = {}, captured = null) {
    for (const field of el(`#${prefix}-refinements`).querySelectorAll("[data-decoder]")) {
      field.value = values[field.dataset.decoder] ?? "";
      field.setCustomValidity("");
    }
    describeControls(prefix, captured);
  }
  function describeControls(prefix, captured) {
    if (!captured) { el(`#${prefix}-captured`).textContent = ""; return; }
    el(`#${prefix}-core`).placeholder = String(captured.decode_core_frames);
    el(`#${prefix}-halo`).placeholder = String(captured.decode_halo_frames);
    el(`#${prefix}-storage option`).textContent = `Keep original · ${precision[captured.vae_storage] || "model setting"}`;
    const seconds = captured.decode_core_frames * captured.downsampling_ratio / captured.sample_rate;
    el(`#${prefix}-captured`).textContent = `Original: ${captured.decode_core_frames} frames per section${Number.isFinite(seconds) ? ` (about ${seconds.toFixed(1)} seconds)` : ""}, ${captured.decode_halo_frames} overlap.`;
  }
  function readControls(prefix, validate = false) {
    const values = {};
    for (const field of el(`#${prefix}-refinements`).querySelectorAll("[data-decoder]")) {
      field.setCustomValidity("");
      if (field.value !== "") {
        const value = Number(field.value);
        if (!Number.isSafeInteger(value)) field.setCustomValidity("Enter a whole number that can be represented exactly.");
        values[field.dataset.decoder] = value;
      }
      if (validate && !field.reportValidity()) throw new Error("Check the audio refinement values.");
    }
    return values;
  }
  function refreshUi() {
    window.RiffUpdate?.render(state);
    renderQueue();
    actions();
  }
  function actions() {
    for (const button of document.querySelectorAll("[data-finish-audio]")) {
      const job = state.jobs.find(item => item.id === button.dataset.finishAudio);
      button.disabled = pending.has(job?.acoustic_source);
    }
    el("#finish-audio").disabled = !detail?.compatible || pending.has(detail.id);
  }
  function reset() {
    draft = null; draftSerial++;
    el("#acoustic-context").hidden = true;
    fillControls("audio");
  }
  function detach(remember = true) {
    if (!draft) return;
    const previous = { ...draft.recipe, acoustic_source: draft.id, decoder: readControls("audio") };
    reset();
    delete draftOrigin.performance_source;
    if (remember) { revisionUndo = previous; el("#revision-undo").hidden = false; }
    notify(`New performance · the saved sound is detached.${remember ? " Undo brings it back." : ""}`);
    queueMicrotask(() => { updateForm(); refreshUi(); });
  }
  function adapt(recipe) {
    if (draft && recipe.acoustic_source === draft.id &&
        (music(recipe) !== music(draft.recipe) || recipe.render_mode !== "music")) {
      return { ...recipe, acoustic_source: "", decoder: {}, performance_source: "" };
    }
    return recipe;
  }
  function fill(recipe, normalized) {
    if (!recipe.acoustic_source) return;
    const serial = draftSerial;
    draft = { id: recipe.acoustic_source, recipe: copy(recipe), baseline: music(normalized), compatible: null };
    fillControls("audio", recipe.decoder, recipe.acoustic);
    el("#acoustic-context").hidden = false;
    el("#acoustic-caption").textContent = "Saved sound · keeping this exact performance";
    el("#acoustic-status").textContent = "Checking the saved sound…";
    api(`/api/acoustics/${encodeURIComponent(draft.id)}`).then(item => {
      if (!draft || serial !== draftSerial || item.id !== draft.id) return;
      // Complete producer/imported recipes must still describe the saved music.
      // A writer or edited import cannot smuggle changed inputs into a decode.
      if (music(recipe) !== music(item.inputs)) { detach(false); return; }
      draft.compatible = item.compatible === true;
      draft.captured = item;
      el("#acoustic-caption").textContent = `Saved sound from ${item.title}`;
      el("#acoustic-status").textContent = draft.compatible
        ? `${formatDuration(item.duration)} · the composition, voices and performance are kept.`
        : "This saved sound needs its original audio decoder. You can still start a new performance.";
      describeControls("audio", item);
      refreshUi();
    }).catch(error => {
      if (!draft || serial !== draftSerial) return;
      draft.compatible = false;
      el("#acoustic-status").textContent = error.message;
      refreshUi();
    });
  }
  function recipe(value) {
    const changed = draft && (music(value) !== draft.baseline || value.render_mode !== "music");
    if (changed) detach();
    const next = { ...value, ...(changed ? { performance_source: "" } : {}), acoustic_source: draft?.id || "", decoder: draft ? readControls("audio") : {} };
    if (draft) draft.recipe = copy(next);
    return next;
  }
  function validate() {
    if (!draft) return;
    if (!draft.compatible) throw new Error(el("#acoustic-status").textContent);
    readControls("audio", true);
  }
  async function enqueue(reference, values = {}, title, button) {
    if (pending.has(reference)) return;
    pending.add(reference);
    if (button) button.disabled = true;
    refreshUi();
    try {
      const job = await api("/api/generations", "POST", { acoustic_source: reference, decoder: values,
        ...(title === undefined ? {} : { title }) });
      notify(`“${job.recipe.title}” is on its way.`);
      await refresh();
    } finally {
      pending.delete(reference);
      if (button) button.disabled = false;
      refreshUi();
    }
  }
  async function details(track) {
    const serial = ++detailSerial;
    detail = null;
    const reference = track.acoustic_source || track.recipe?.acoustic?.artifact_id || track.recipe?.acoustic_source;
    el("#detail-acoustic").hidden = !reference;
    fillControls("detail-audio");
    el("#detail-audio-refinements").open = false;
    el("#finish-audio").disabled = true;
    if (!reference) return;
    el("#detail-acoustic-status").textContent = "Opening the saved sound…";
    try {
      const item = await api(`/api/acoustics/${encodeURIComponent(reference)}`);
      if (serial !== detailSerial || detailTrack?.id !== track.id) return;
      detail = item;
      fillControls("detail-audio", track.recipe?.decoder, item);
      el("#detail-acoustic-status").textContent = item.compatible
        ? `${formatDuration(item.duration)} · keep this exact performance and render another audio take.`
        : "This saved sound needs its original audio decoder.";
      actions();
    } catch (error) {
      if (serial === detailSerial) el("#detail-acoustic-status").textContent = error.message;
    }
  }
  function render(next) {
    const option = el('#render-mode option[value="sound"]');
    const available = next.engine?.capabilities?.acoustic_checkpoint === true;
    option.hidden = !available && el("#render-mode").value !== "sound";
    option.disabled = !available;
  }

  document.addEventListener("click", async event => {
    const finish = event.target.closest("[data-finish-audio]");
    const original = event.target.closest("[data-decoder-reset]");
    if (original) {
      fillControls(original.dataset.decoderReset, {}, original.dataset.decoderReset === "audio" ? draft?.captured || draft?.recipe.acoustic : detail);
      if (original.dataset.decoderReset === "audio") saveDraft();
    }
    if (!finish) return;
    const job = state.jobs.find(item => item.id === finish.dataset.finishAudio);
    if (!job?.acoustic_source || pending.has(job.acoustic_source)) return;
    try { await enqueue(job.acoustic_source, {}, undefined, finish); }
    catch (error) { notify(error.message); }
  });
  el("#finish-audio").addEventListener("click", async () => {
    if (!detail?.compatible) return;
    try { await enqueue(detail.id, readControls("detail-audio", true), el("#edit-title").value, el("#finish-audio")); }
    catch (error) { errorMessage("#detail-error", error.message); }
  });
  el("#fresh-sound").addEventListener("click", () => { detach(); saveDraft(); el("#style").focus(); });
  el("#acoustic-controls").addEventListener("input", () => readControls("audio"));
  el("#detail-acoustic-controls").addEventListener("input", () => readControls("detail-audio"));
  window.RiffAcoustics = { reset, adapt, fill, recipe, validate, details, render, actions,
    attached: () => !!draft, ready: () => draft?.compatible === true,
    refreshWait: () => pending.size ? "Your audio is being queued." : "" };
})();
