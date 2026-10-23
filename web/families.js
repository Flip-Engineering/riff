/* Study families: named groups of studies with their shared inputs, each take's own differences,
   selection and development. Everything here is read and written through the same operations an agent uses. */
(() => {
  const dialog = $("#families-dialog"), list = $("#families-list"), board = $("#family-board"), status = $("#families-status");
  const selector = $("#study-family"), nameInput = $("#study-family-name");
  const STATUS = { queued: "Waiting", running: "Making", cancelling: "Stopping", done: "Ready", planned: "Score ready",
    performed: "Performance ready", synthesized: "Sound ready", cancelled: "Cancelled", interrupted: "Interrupted", failed: "Failed" };
  const ROLE = { study: "Study", develop: "Developed", replay: "Replayed", resume: "Resumed", other: "Take" };
  const FINISHED = ["done", "planned", "performed", "synthesized"];
  let families = [], currentId = "", loadedCatalog, reload = 0, currentFamily = null;
  const operation = (name, body, key) => api(`/api/v1/operations/${name}`, "POST", body, key ? { headers: { "Idempotency-Key": key } } : {});

  function format(field, value) {
    if (value === null || value === undefined || value === "") return "Empty";
    if (field === "max_seconds") return `${Number(value)} seconds`;
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }
  const text = (field, value) => {
    const shown = format(field, value);
    return shown.length > 80 || shown.includes("\n")
      ? `<details><summary>${esc(shown.split("\n")[0].slice(0, 60))}…</summary><pre>${esc(shown)}</pre></details>` : esc(shown);
  };
  const label = (name) => name || "Unnamed family";

  function renderSelector() {
    const keep = selector.value;
    selector.innerHTML = '<option value="">None: a one-off study</option><option value="__new">A new family…</option>'
      + families.map(item => `<option value="${esc(item.family_id)}">${esc(label(item.name))} (${item.takes})</option>`).join("");
    selector.value = [...selector.options].some(option => option.value === keep) ? keep : "";
    nameInput.hidden = selector.value !== "__new";
  }
  function renderList() {
    list.innerHTML = families.length ? `<ul>${families.map(item => `<li><button type="button" data-family="${esc(item.family_id)}"${item.family_id === currentId ? ' aria-current="true"' : ""}>`
      + `<strong>${esc(label(item.name))}</strong><span>${item.takes} ${item.takes === 1 ? "take" : "takes"}`
      + `${item.latest_status ? ` · ${esc(STATUS[item.latest_status] || item.latest_status)}` : ""}${item.selected_take ? " · one selected" : ""}</span></button></li>`).join("")}</ul>`
      : '<p class="control-hint">No families yet. Choose “A new family…” under “Try a sound study”, or name a study’s family here once it exists.</p>';
  }
  async function loadFamilies() {
    const result = await api("/api/v1/families");
    families = result.families;
    renderSelector(); renderList();
  }

  function diffItem(take, item) {
    const shown = field => esc(format(item.field, field));
    return take.role === "study" || item.before === undefined
      ? `<li><span>${esc(item.label)}</span> ${item.recorded === false ? "<em>not recorded</em>" : text(item.field, item.value)}</li>`
      : `<li><span>${esc(item.label)}</span> ${item.before_recorded === false ? "<em>not recorded</em>" : shown(item.before)} → ${item.recorded === false ? "<em>not recorded</em>" : shown(item.value)}</li>`;
  }
  function takeHtml(take, family) {
    const finished = FINISHED.includes(take.status), canPerformance = take.stages.performance === "done", canScore = Boolean(take.outputs.score_source);
    const key = `take-${take.operation_id}`;
    const diff = take.diff === null ? '<p class="control-hint">The take it continues is unavailable, so changes cannot be shown.</p>'
      : take.diff.length ? `<ul class="family-diff" aria-label="What ${esc(take.title)} changes">${take.diff.map(item => diffItem(take, item)).join("")}</ul>`
      : '<p class="control-hint">No recorded input differs.</p>';
    return `<li class="family-take" data-operation="${esc(take.operation_id)}"${take.selected ? ' data-selected="true"' : ""}>
      <div class="family-take-head"><h4>${esc(take.title)}</h4><span class="family-role">${ROLE[take.role] || "Take"}</span>
        <span class="family-status">${esc(STATUS[take.status] || take.status)}</span>${take.selected ? '<span class="family-selected">Selected</span>' : ""}</div>
      <p class="family-seed">Seed ${esc(take.seed)}</p>${diff}
      <div class="family-actions">
        ${take.track_id ? `<button type="button" class="text-button" data-play="${esc(take.track_id)}" data-key="${key}-play">Play</button>` : ""}
        <button type="button" class="text-button" data-develop-open aria-expanded="false" aria-controls="develop-${esc(take.operation_id)}" data-key="${key}-develop"${finished ? "" : ' disabled title="Available when this take has finished"'}>Develop this study</button>
      </div>
      <form class="family-select" data-select><label class="sr-only" for="why-${esc(take.operation_id)}">Why ${esc(take.title)}</label>
        <input id="why-${esc(take.operation_id)}" name="rationale" type="text" placeholder="Why this one (optional)" autocomplete="off" />
        <button type="submit" class="secondary-button" data-key="${key}-select"${finished ? "" : " disabled"}>Select this take</button></form>
      <form class="family-develop" id="develop-${esc(take.operation_id)}" data-develop hidden>
        <div class="advanced-grid">
          <div><label for="length-${esc(take.operation_id)}">Length (seconds)</label><input id="length-${esc(take.operation_id)}" name="duration" type="number" min="0.04" step="any" value="${esc(take.settings.max_seconds ?? 30)}" required /></div>
          <div><label for="steps-${esc(take.operation_id)}">Solver steps</label><input id="steps-${esc(take.operation_id)}" name="steps" type="number" min="1" step="1" value="${esc(take.settings.steps ?? 8)}" required /></div>
          <div><label for="solver-${esc(take.operation_id)}">Synthesis method</label><select id="solver-${esc(take.operation_id)}" name="solver"><option value="midpoint"${take.settings.solver === "ab2" ? "" : " selected"}>Midpoint</option><option value="ab2"${take.settings.solver === "ab2" ? " selected" : ""}>Multistep (AB2)</option></select></div>
          <div><label for="title-${esc(take.operation_id)}">Title</label><input id="title-${esc(take.operation_id)}" name="title" type="text" placeholder="${esc(take.title)} — developed" /></div>
        </div>
        <details class="family-inputs"><summary>Edit inputs</summary>
          <label for="direction-${esc(take.operation_id)}">Sound direction</label><textarea id="direction-${esc(take.operation_id)}" name="style" rows="3">${esc(take.settings.style ?? "")}</textarea>
          <label for="words-${esc(take.operation_id)}">Words</label><textarea id="words-${esc(take.operation_id)}" name="lyrics" rows="6">${esc(take.settings.lyrics ?? "")}</textarea>
          <div class="advanced-grid">
            <div><label for="guidance-${esc(take.operation_id)}">Guidance</label><input id="guidance-${esc(take.operation_id)}" name="guidance" type="number" min="0" max="20" step="any" value="${esc(take.settings.cfg_scale ?? 1)}" /></div>
            <div><label for="variation-${esc(take.operation_id)}">Variation</label><input id="variation-${esc(take.operation_id)}" name="variation" type="number" min="0" max="5" step="any" value="${esc(take.settings.temperature ?? 1)}" /></div>
          </div>
          <p class="control-hint">Only what you change is sent. A kept score still applies to edited words.</p></details>
        <fieldset><legend>Keep from this take</legend>
          <label><input type="checkbox" name="retain" value="seed" checked /> Seed</label>
          <label><input type="checkbox" name="retain" value="score"${canScore ? " checked" : " disabled"} /> Composed score${canScore ? "" : " (this take has none)"}</label>
          <label><input type="checkbox" name="retain" value="performance"${canPerformance ? "" : " disabled"} /> Saved performance</label>
          <p class="control-hint">The seed and score keep the musical idea. A saved performance fixes the length, so it is kept only if you choose it.</p></fieldset>
        <p class="form-error" role="alert" hidden></p>
        <button type="submit" class="secondary-button">Develop</button>
      </form></li>`;
  }
  function boardHtml(family) {
    const history = family.selection.history;
    return `<header class="family-head"><h3 id="family-title" tabindex="-1">${esc(label(family.name))}</h3>
      <form data-rename class="family-rename"><label class="sr-only" for="family-name-input">Family name</label>
        <input id="family-name-input" name="name" type="text" value="${esc(family.name || "")}" placeholder="Name this family" required />
        <button type="submit" class="text-button">${family.named ? "Rename" : "Name it"}</button></form></header>
      ${Object.keys(family.context).length ? `<dl class="family-context">${Object.entries(family.context).map(([name, value]) => `<div><dt>${esc(name)}</dt><dd>${text(name, value)}</dd></div>`).join("")}</dl>` : ""}
      <section aria-labelledby="family-shared-heading"><h4 id="family-shared-heading">Shared by the studies</h4>
        ${family.common.length ? `<dl class="family-shared">${family.common.map(item => `<div><dt>${esc(item.label)}</dt><dd>${text(item.field, item.value)}</dd></div>`).join("")}</dl>`
          : '<p class="control-hint">The studies have no recorded input in common.</p>'}</section>
      <ol class="family-takes" aria-label="Takes in this family">${family.takes.map(take => takeHtml(take, family)).join("")}</ol>
      <details class="family-history"${history.length ? "" : " hidden"}><summary>Selection history (${history.length})</summary><ol>${history.map(item => {
        const take = family.takes.find(entry => entry.job_id === item.take_job_id || entry.track_id === item.take_job_id);
        return `<li>${esc(take ? take.title : "A take")}${item.rationale ? ` — ${esc(item.rationale)}` : ""}</li>`; }).join("")}</ol></details>`;
  }
  async function showFamily(id, focus) {
    currentId = id;
    const request = ++reload;
    const active = document.activeElement?.dataset?.key;
    status.textContent = "";
    try {
      const family = await api(`/api/v1/families/${encodeURIComponent(id)}`);
      if (request !== reload) return;
      currentFamily = family;
      const open = new Set([...board.querySelectorAll("form[data-develop]:not([hidden])")].map(form => form.id));
      board.innerHTML = boardHtml(family);
      for (const form of board.querySelectorAll("form[data-develop]")) if (open.has(form.id)) { form.hidden = false; board.querySelector(`[aria-controls="${form.id}"]`)?.setAttribute("aria-expanded", "true"); }
      renderList();
      if (focus) $("#family-title").focus();
      else if (active) board.querySelector(`[data-key="${active}"]`)?.focus();
    } catch (error) { if (request === reload) status.textContent = error.message; }
  }
  async function openDialog(id) {
    if (!dialog.open) dialog.showModal();
    await loadFamilies();
    const target = id || (families.some(item => item.family_id === currentId) ? currentId : families[0]?.family_id);
    if (target) await showFamily(target, true);
    else { board.innerHTML = ""; renderList(); }
  }

  const keys = new Map();
  async function keyed(request, send) {
    const key = keys.get(request) || submissionKey();
    try { const result = await send(key); keys.delete(request); return result; }
    catch (error) { if (error instanceof TypeError) keys.set(request, key); else keys.delete(request); throw error; }
  }

  board.addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.target, family = currentFamily;
    try {
      if (form.matches("[data-rename]")) {
        await operation("family.rename", { family_id: family.family_id, name: form.elements.name.value });
        await loadFamilies(); await showFamily(family.family_id, true);
      } else if (form.matches("[data-select]")) {
        const take = form.closest("[data-operation]").dataset.operation, rationale = form.elements.rationale.value.trim();
        await keyed(`select${take}${rationale}`, key => operation("take.select", { take, rationale, idempotency_key: key }));
        await loadFamilies(); await showFamily(family.family_id);
        status.textContent = "Selection recorded.";
      } else if (form.matches("[data-develop]")) {
        const item = form.closest("[data-operation]"), take = family.takes.find(entry => entry.operation_id === item.dataset.operation);
        const error = form.querySelector(".form-error"); error.hidden = true;
        const retain = [...form.querySelectorAll("[name=retain]:checked")].map(input => input.value);
        const changes = {};
        const length = Number(form.elements.duration.value), steps = Number(form.elements.steps.value);
        if (!retain.includes("performance") && length !== take.settings.max_seconds) changes.max_seconds = length;
        if (steps !== take.settings.steps) changes.steps = steps;
        if (form.elements.solver.value !== take.settings.solver) changes.solver = form.elements.solver.value;
        if (form.elements.title.value.trim()) changes.title = form.elements.title.value.trim();
        if (form.elements.style.value !== (take.settings.style ?? "")) changes.style = form.elements.style.value;
        if (form.elements.lyrics.value !== (take.settings.lyrics ?? "")) changes.lyrics = form.elements.lyrics.value;
        if (Number(form.elements.guidance.value) !== (take.settings.cfg_scale ?? 1)) changes.cfg_scale = Number(form.elements.guidance.value);
        if (Number(form.elements.variation.value) !== (take.settings.temperature ?? 1)) changes.temperature = Number(form.elements.variation.value);
        const button = form.querySelector("button[type=submit]"); button.disabled = true;
        try {
          const result = await keyed(`develop${take.operation_id}${JSON.stringify([retain, changes])}`,
            key => operation("take.develop", { source: take.operation_id, retain, changes, idempotency_key: key }));
          notify(`“${result.receipt.effective_recipe.title}” is on its way.`);
          form.hidden = true;
          await refresh(); await loadFamilies(); await showFamily(family.family_id);
        } catch (failure) { error.textContent = failure.message; error.hidden = false; }
        finally { button.disabled = false; }
      }
    } catch (error) { status.textContent = error.message; }
  });
  board.addEventListener("click", (event) => {
    const play = event.target.closest("[data-play]"), develop = event.target.closest("[data-develop-open]");
    if (play) { dialog.close(); selectTrack(play.dataset.play, true); }
    if (develop && !develop.disabled) {
      const form = $(`#${develop.getAttribute("aria-controls")}`);
      form.hidden = !form.hidden; develop.setAttribute("aria-expanded", String(!form.hidden));
      if (!form.hidden) form.elements.duration.focus();
    }
  });
  board.addEventListener("change", (event) => {
    if (event.target.name !== "retain") return;
    const form = event.target.form, performance = form.querySelector("[name=retain][value=performance]").checked;
    form.elements.duration.disabled = performance;
  });
  list.addEventListener("click", (event) => {
    const item = event.target.closest("[data-family]");
    if (item) showFamily(item.dataset.family, true);
  });
  for (const id of ["families-open", "families-open-list"]) $("#" + id)?.addEventListener("click", () => openDialog());
  selector.addEventListener("change", () => { nameInput.hidden = selector.value !== "__new"; if (!nameInput.hidden) nameInput.focus(); });

  window.RiffFamilies = {
    /* Queue a study, in the chosen family when there is one. Returns what the Studio expects of a queued take. */
    async queueStudy(recipe) {
      const choice = selector.value;
      if (!choice) return queueTake("/api/generations", recipe);
      if (choice === "__new") {
        const name = nameInput.value.trim();
        if (!name) { nameInput.focus(); throw new Error("Name the new family, or choose none."); }
        const result = await keyed(`family${name}${JSON.stringify(recipe)}`,
          key => operation("family.create", { name, takes: [recipe], idempotency_key: key }));
        currentId = result.family_id; nameInput.value = "";
        await loadFamilies(); selector.value = result.family_id;
        return { recipe: result.submissions[0].receipt.effective_recipe };
      }
      const result = await keyed(`study${choice}${JSON.stringify(recipe)}`,
        key => operation("study.create", { recipe, family_id: choice, idempotency_key: key }));
      await loadFamilies(); selector.value = choice;
      return { recipe: result.receipt.effective_recipe };
    },
    render(next) {
      if (next.catalog_revision === undefined || next.catalog_revision === loadedCatalog) return;
      loadedCatalog = next.catalog_revision;
      loadFamilies().then(() => { if (dialog.open && currentId) return showFamily(currentId); }).catch(() => {});
    },
    open: openDialog,
  };
})();
