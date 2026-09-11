"use strict";

(() => {
  let controlSchema = [], system = null, settingsFilled = false;
  const dialog = $("#system-dialog");
  const engineFields = { backend: "engine-backend", threads: "engine-threads", device: "engine-device",
    binary: "engine-binary", model_root: "engine-model-root", model_file: "engine-model-file", vae_file: "engine-vae-file" };

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
    $("#setup-invitation").hidden = next.engine.ready;
    $("#system-task").textContent = system.task.message || (system.engine.ready ? "Music engine ready" : "Choose an engine to get started");
    $("#system-task").classList.toggle("form-error", system.task.status === "failed");
    $("#system-progress").hidden = system.task.status !== "running";
    $("#system-version").textContent = "Riff " + system.version;
    $("#system-update-state").textContent = system.pending ? `Version ${system.pending.version} is ready to use.`
      : system.release ? `Version ${system.release.version} is available.` : "";
    $("#install-update").hidden = !system.release || !!system.pending;
    $("#install-update").disabled = !system.managed || system.task.status === "running";
    $("#restart-update").hidden = !system.pending;
    $("#restart-update").disabled = system.busy || system.task.status === "running";
    $("#managed-hint").hidden = system.managed;
    $("#setup-engine").disabled = system.busy || system.task.status === "running";
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
    settingsFilled = false;
    // Fill synchronously before the first interaction; a slower refresh must
    // not replace a backend or path the user has just entered.
    render(state);
    dialog.showModal();
    try { render({ maintenance: await api("/api/system"), engine: state.engine }); }
    catch (error) { $("#system-task").textContent = error.message; }
  }
  $("#system-open").addEventListener("click", openSettings);
  $("#setup-open").addEventListener("click", openSettings);
  function writerDisclosure() { $("#cloud-writer-hint").hidden = $("#idea-engine").value !== "openrouter"; }
  $("#idea-engine").addEventListener("change", writerDisclosure);
  writerDisclosure();
  $("#reset-refinement").addEventListener("click", () => { draftRefinement = {}; fill({}); saveDraft(); });
  $("#engine-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const payload = Object.fromEntries(Object.entries(engineFields).map(([key, id]) => [key, ["device", "threads"].includes(key) ? Number($("#" + id).value) : $("#" + id).value]));
    try { await api("/api/system/engine", "POST", payload); await refresh(); notify("Engine settings saved"); }
    catch (error) { $("#system-task").textContent = error.message; }
  });
  for (const [button, action] of [["setup-engine", "setup"], ["check-update", "check"], ["install-update", "update"], ["restart-update", "restart"]]) {
    $("#" + button).addEventListener("click", async () => {
      try {
        await api("/api/system/" + action, "POST", action === "setup" ? { backend: $("#engine-backend").value } : {});
        settingsFilled = false;
        await refresh();
      } catch (error) { $("#system-task").textContent = error.message; }
    });
  }
  for (const id of ["automatic-checks", "automatic-downloads"]) $("#" + id).addEventListener("change", async () => {
    try { await api("/api/system/preferences", "POST", { automatic_checks: $("#automatic-checks").checked, automatic_downloads: $("#automatic-downloads").checked }); }
    catch (error) { notify(error.message); }
  });

  let selection = "";
  for (const id of ["lyrics", "writing-pad"]) $("#" + id).addEventListener("select", (event) => {
    if (id === "writing-pad" && expandedField !== $("#lyrics")) return;
    selection = event.target.value.slice(event.target.selectionStart, event.target.selectionEnd).trim();
    $("#audition-selection").textContent = selection ? "Selected lines" : "Whole draft";
  });
  $("#clear-audition-selection").addEventListener("click", () => { selection = ""; $("#audition-selection").textContent = "Whole draft"; });
  $("#audition").addEventListener("click", async () => {
    const recipe = formRecipe();
    if (selection) { recipe.lyrics = selection; recipe.mode = "lyrics"; }
    recipe.title = (recipe.title || "Untitled") + " — study";
    recipe.max_seconds = Number($("#audition-length").value);
    recipe.steps = Number($("#audition-steps").value);
    try { await api("/api/generations", "POST", recipe); await refresh(); notify("Sound study queued"); }
    catch (error) { errorMessage("#form-error", error.message); }
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
