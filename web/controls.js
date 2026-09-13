"use strict";

(() => {
  let controlSchema = [], system = null, settingsFilled = false;
  const dialog = $("#system-dialog");
  const engineFields = { backend: "engine-backend", threads: "engine-threads", device: "engine-device",
    binary: "engine-binary", model_root: "engine-model-root", model_file: "engine-model-file", vae_file: "engine-vae-file" };

  function settingsError(message = "") {
    $("#system-error").textContent = message;
    $("#system-error").hidden = !message;
  }

  function fill(values) {
    for (const field of controlSchema) {
      const input = $("#control-" + field.key);
      if (input) input.value = values[field.key] ?? "";
    }
  }

  function render(next) {
    if (!controlSchema.length && next.engine?.refinement_controls?.length) {
      controlSchema = next.engine.refinement_controls;
      for (const group of ["Music", "Melody"]) {
        const fieldset = document.createElement("fieldset");
        fieldset.className = "refinement-group";
        fieldset.innerHTML = `<legend>${group === "Music" ? "Performance sampling" : "Melody planning"}</legend><div class="advanced-grid"></div>`;
        for (const field of controlSchema.filter((f) => f.group === group)) {
          const wrapper = document.createElement("div"), input = document.createElement("input"), label = document.createElement("label");
          label.htmlFor = input.id = "control-" + field.key;
          label.textContent = field.label;
          input.type = "number";
          input.step = field.integer ? "1" : "any";
          input.min = field.min;
          if (field.max !== null) input.max = field.max;
          input.placeholder = field.default;
          input.addEventListener("input", () => {
            if (input.value === "") delete draftRefinement[field.key];
            else draftRefinement[field.key] = Number(input.value);
            saveDraft();
          });
          wrapper.append(label, input);
          $(".advanced-grid", fieldset).append(wrapper);
        }
        $("#refinement-fields").append(fieldset);
      }
      fill(draftRefinement);
    }
    const finishedSetup = system?.task.status === "running" && system.task.action === "setup" && next.maintenance?.task.status === "done";
    system = next.maintenance || system;
    if (finishedSetup) settingsFilled = false;
    if (!system) return;
    const updateTask = ["check", "update", "activate"].includes(system.task.action);
    const feedback = $("#system-feedback"), slot = $(updateTask ? "#update-task-slot" : "#engine-task-slot");
    if (feedback.parentElement !== slot) slot.append(feedback);
    $("#setup-invitation").hidden = next.engine.ready;
    $("#system-task").textContent = system.task.message || (system.engine.ready ? "Music engine ready" : "Choose an engine to get started");
    $("#system-task").classList.toggle("form-error", system.task.status === "failed");
    $("#system-progress").hidden = system.task.status !== "running";
    $("#system-progress").setAttribute("aria-label", updateTask ? "Update progress" : "Setup progress");
    $("#system-log").textContent = updateTask ? "Update log" : "Setup log";
    $("#system-log").hidden = !["setup", "update", "activate"].includes(system.task.action);
    $("#cancel-update").hidden = system.task.status !== "running" || system.task.action !== "update" || !system.desktop;
    $("#system-version").textContent = "Riff " + system.version;
    $("#system-update-state").textContent = system.pending ? `Version ${system.pending.version} is ready to use.`
      : system.release ? `Version ${system.release.version} is available.` : "";
    $("#install-update").hidden = (!system.release && !system.pending) || (!!system.pending && system.task.status !== "failed");
    $("#install-update").textContent = system.pending ? "Prepare again" : "Prepare update";
    $("#install-update").disabled = !system.managed || system.task.status === "running";
    $("#check-update").disabled = system.task.status === "running";
    $("#restart-update").hidden = !system.pending;
    $("#restart-update").disabled = system.busy || system.task.status === "running";
    $("#managed-hint").hidden = system.managed;
    $("#setup-engine").disabled = system.busy || system.task.status === "running";
    $("#setup-engine").hidden = !!system.desktop;
    $("#setup-description").textContent = system.desktop
      ? "Model downloads are verified and reused when Riff updates."
      : "Setup downloads the music models and prepares audio.cpp for your selected accelerator. Verified downloads are reused.";
    $("#setup-description").hidden = system.desktop && system.engine.ready;
    if (!settingsFilled) {
      for (const [key, id] of Object.entries(engineFields)) $("#" + id).value = system.engine[key];
      $("#automatic-checks").checked = system.preferences.automatic_checks;
      $("#automatic-downloads").checked = system.preferences.automatic_downloads;
      settingsFilled = true;
    }
    $("#setup-requirements").replaceChildren();
    for (const item of system.prerequisites) {
      const line = document.createElement("p");
      line.textContent = `${item.name}: ${item.command}`;
      $("#setup-requirements").append(line);
    }
  }

  async function openSettings() {
    settingsError();
    settingsFilled = false;
    // Fill synchronously before the first interaction; a slower refresh must
    // not replace a backend or path the user has just entered.
    render(state);
    dialog.showModal();
    try { render({ maintenance: await api("/api/system"), engine: state.engine }); }
    catch (error) { settingsError(error.message); }
  }
  $("#system-open").addEventListener("click", openSettings);
  $("#setup-open").addEventListener("click", openSettings);
  $("#reset-refinement").addEventListener("click", () => { draftRefinement = {}; fill({}); saveDraft(); });
  $("#engine-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    settingsError();
    const payload = Object.fromEntries(Object.entries(engineFields).map(([key, id]) => [key, ["device", "threads"].includes(key) ? Number($("#" + id).value) : $("#" + id).value]));
    try { await api("/api/system/engine", "POST", payload); await refresh(); notify("Engine settings saved"); }
    catch (error) { settingsError(error.message); }
  });
  for (const [button, action] of [["setup-engine", "setup"], ["check-update", "check"], ["install-update", "update"], ["restart-update", "restart"], ["cancel-update", "cancel"]]) {
    $("#" + button).addEventListener("click", async () => {
      settingsError();
      try {
        await api("/api/system/" + action, "POST", action === "setup" ? { backend: $("#engine-backend").value } : {});
        await refresh();
      } catch (error) { settingsError(error.message); }
    });
  }
  for (const id of ["automatic-checks", "automatic-downloads"]) $("#" + id).addEventListener("change", async () => {
    settingsError();
    const previous = { ...system.preferences };
    const fields = { automatic_checks: "automatic-checks", automatic_downloads: "automatic-downloads" };
    for (const input of Object.values(fields)) $("#" + input).disabled = true;
    try {
      const saved = await api("/api/system/preferences", "POST", { automatic_checks: $("#automatic-checks").checked, automatic_downloads: $("#automatic-downloads").checked });
      system.preferences = saved.preferences;
    } catch (error) {
      for (const [key, input] of Object.entries(fields)) $("#" + input).checked = previous[key];
      settingsError(error.message);
    } finally {
      for (const input of Object.values(fields)) $("#" + input).disabled = false;
    }
  });

  let selection = null, studySubmitting = false;
  function studySelection() {
    const lyrics = $("#lyrics").value;
    return selection && selection.draft === lyrics && !["free", "instrumental"].includes(creationMode())
      ? lyrics.slice(selection.start, selection.end).trim() : "";
  }
  function showStudySelection() {
    const text = studySelection();
    $("#audition-selection").textContent = text ? "Selected lines" : "Whole draft";
    $("#audition-excerpt").textContent = text;
    $("#audition-excerpt").hidden = !text;
  }
  for (const id of ["lyrics", "writing-pad"]) $("#" + id).addEventListener("select", (event) => {
    if (id === "writing-pad" && expandedField !== $("#lyrics")) return;
    selection = { draft: event.target.value, start: event.target.selectionStart, end: event.target.selectionEnd };
    showStudySelection();
  });
  $("#generation-form").addEventListener("input", showStudySelection);
  $("#clear-audition-selection").addEventListener("click", () => { selection = null; showStudySelection(); });
  $("#audition").addEventListener("click", async () => {
    if (studySubmitting) return;
    const recipe = formRecipe();
    recipe.performance_source = "";
    recipe.acoustic_source = "";
    recipe.decoder = {};
    recipe.render_mode = "music";
    const passage = studySelection();
    if (passage) {
      recipe.lyrics = passage; recipe.mode = "lyrics"; recipe.lyrics_source = "provided";
      // A complete score is not the notation for an arbitrary lyric excerpt.
      // The chosen planning mode can compose a score for the selected passage.
      recipe.abc = "";
    }
    recipe.title = (recipe.title || "Untitled") + " — study";
    recipe.max_seconds = Number($("#audition-length").value);
    recipe.steps = Number($("#audition-steps").value);
    if (Number.isFinite(recipe.refinement.semantic_min_tokens))
      recipe.refinement.semantic_min_tokens = Math.min(recipe.refinement.semantic_min_tokens, Math.floor(recipe.max_seconds * 25));
    errorMessage("#study-error");
    if (!$("#audition-length").reportValidity() || !$("#audition-steps").reportValidity()) return;
    studySubmitting = true; $("#audition").disabled = true; $("#audition").textContent = "Queuing study…";
    try {
      const job = await api("/api/generations", "POST", recipe);
      await refresh(); notify(`“${job.recipe.title}” is on its way.`);
      $("#queue-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (error) { errorMessage("#study-error", error.message); }
    finally { studySubmitting = false; $("#audition").disabled = false; $("#audition").textContent = "Generate study"; }
  });
  $("#import-recipe").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      const recipe = JSON.parse(await file.text());
      if (!recipe || typeof recipe !== "object" || Array.isArray(recipe)) throw new Error("Choose a Riff recipe JSON file.");
      fillRecipe(recipe); saveDraft(); notify("Recipe opened");
    } catch (error) { notify(error.message); }
    event.target.value = "";
  });
  $("#export-draft").addEventListener("click", () => {
    const blob = new Blob([JSON.stringify(formRecipe(), null, 2)], { type: "application/json" });
    const link = document.createElement("a"), url = URL.createObjectURL(blob);
    link.href = url; link.download = ($("#title").value.trim() || "riff-draft") + ".json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  window.RiffControls = { fill, render };
  render(state);
})();
