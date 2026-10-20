/* The LoRA shelf uses the same studio origin for discovery, files and audio. */
(() => {
  "use strict";
  const el = selector => document.querySelector(selector);
  const copy = value => JSON.parse(JSON.stringify(value));
  const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const bytes = value => Number.isFinite(value) ? `${(value / 1024 / 1024).toFixed(1)} MB` : "";
  const headerJSON = value => JSON.stringify(value).replace(/[\u007f-\uffff]/g, char => "\\u" + char.charCodeAt(0).toString(16).padStart(4, "0"));
  const stageName = stage => stage === "ar" ? "Composition" : "Sound";
  let installed = [], enabled = false, tab = "browse", page = 0, results = [];
  let listRequest = 0, searchRequest = 0, detailRequest = 0, activeTask = null, taskTimer = null, currentDetail = null;
  let saved = null, loaded = false, installing = false, taskView = null;
  let choices = {ar: {id: "", scale: 1}, nar: {id: "", scale: 1}};
  const status = message => { el("#lora-status").textContent = message; };

  function selections() {
    const grouped = new Map();
    for (const stage of ["ar", "nar"]) {
      const choice = choices[stage];
      if (!choice.id) continue;
      if (!grouped.has(choice.id)) {
        const manifest = installed.find(item => item.id === choice.id) || saved?.find(item => item.id === choice.id);
        grouped.set(choice.id, {id: choice.id, ...Object.fromEntries((manifest?.stages || [stage]).map(role => [role + "_scale", 0]))});
      }
      grouped.get(choice.id)[stage + "_scale"] = choice.scale;
    }
    return [...grouped.values()];
  }

  function recipe(value) {
    const loras = selections();
    const next = {...value, loras};
    if (saved && saved.length === loras.length && saved.every(item => loras.some(selection => selection.id === item.id)))
      next.lora_provenance = copy(saved);
    else delete next.lora_provenance;
    return next;
  }

  function fill(value) {
    saved = value.lora_provenance ? copy(value.lora_provenance) : null;
    choices = {ar: {id: "", scale: 1}, nar: {id: "", scale: 1}};
    for (const selection of value.loras || []) {
      const manifest = installed.find(item => item.id === selection.id) || saved?.find(item => item.id === selection.id);
      const roles = manifest?.stages || (selection.strength !== undefined ? ["ar", "nar"] : ["ar", "nar"].filter(stage => selection[stage + "_scale"] !== undefined));
      for (const stage of roles) {
        const scale = selection[stage + "_scale"] ?? selection.strength ?? manifest?.[stage + "_scale"] ?? manifest?.default_strength ?? 1;
        if (scale !== 0 || !choices[stage].id) choices[stage] = {id: selection.id, scale};
      }
    }
    renderSelectors();
  }

  function renderSelectors() {
    for (const stage of ["ar", "nar"]) {
      const select = el("#lora-" + stage), choice = choices[stage];
      const items = installed.filter(item => item.stages.includes(stage));
      select.innerHTML = `<option value="">Original ${stage === "ar" ? "composition" : "sound"}</option>` +
        items.map(item => `<option value="${escape(item.id)}">${escape(item.name)}</option>`).join("");
      if (choice.id && !items.some(item => item.id === choice.id))
        select.insertAdjacentHTML("beforeend", `<option value="${escape(choice.id)}">Unavailable adapter · ${escape(choice.id.slice(0, 8))}</option>`);
      select.value = choice.id;
      el("#lora-" + stage + "-scale").value = choice.scale;
      el("#lora-" + stage + "-scale").disabled = !choice.id;
    }
    const picked = installed.filter(item => selections().some(selection => selection.id === item.id));
    const missing = selections().some(selection => !installed.some(item => item.id === selection.id));
    el("#lora-trigger").hidden = !picked.some(item => item.trigger_words?.length);
    el("#lora-composer-hint").textContent = !enabled
      ? "Browse and install styles now. Update the music engine in Studio settings to use them."
      : missing ? "A saved adapter is unavailable. Reinstall its exact revision or select another style."
      : picked.length ? "Zero turns a stage off. Each take keeps its exact adapter and strength." : "Choose a community style, or keep the original sound.";
  }

  async function refreshInstalled(quiet = false) {
    const serial = ++listRequest;
    const value = await api("/api/loras");
    if (serial !== listRequest) return;
    installed = value.loras || []; enabled = !!value.enabled; loaded = true;
    renderSelectors();
    if (tab === "installed") renderInstalled(!quiet);
  }

  const badge = value => `<span class="lora-badge">${escape(value)}</span>`;
  function compatibility(file) {
    const item = file.compatibility;
    if (typeof item === "string") return {status: item, reason: file.reason || ""};
    return item || {status: file.status || "unknown", reason: file.reason || ""};
  }
  const supported = file => file.compatible === true || ["compatible", "convertible", "header-compatible"].includes(String(compatibility(file).status).toLowerCase());
  const fileName = file => typeof file === "string" ? file : file.path || file.name || file.rfilename;
  const repoName = item => item.repo || item.id || item.modelId;
  const licenseOf = item => item.license || item.cardData?.license || "License not declared";

  function renderInstalled(announce = true) {
    results = installed;
    el("#lora-results").innerHTML = installed.map(item => `<article class="lora-card">
      <p class="quiet-text">${escape(item.source?.repo || "Imported file")}</p><h3>${escape(item.name)}</h3>
      <div class="lora-badges">${badge(item.license || "License not declared")}${item.stages.map(stage => badge(stageName(stage))).join("")}</div>
      <p class="control-hint">${escape(item.error || (item.tags || []).join(" · "))}</p>
      <div class="lora-card-actions"><button type="button" class="secondary-button" data-lora-use="${item.id}" ${enabled && !item.error ? "" : "disabled"}>Use this style</button><button type="button" class="text-button" data-lora-edit="${item.id}" ${item.error ? "disabled" : ""}>Edit & details</button></div></article>`).join("");
    if (!installed.length) el("#lora-results").innerHTML = '<p class="dialog-description">Your style shelf is ready. Browse community adapters or import a safetensors file.</p>';
    if (announce) status(enabled ? `${installed.length} installed ${installed.length === 1 ? "adapter" : "adapters"}.` : "The library is ready. Update the music engine to activate styles.");
  }

  async function search() {
    const serial = ++searchRequest;
    status("Finding community styles…");
    const query = new URLSearchParams({q: el("#lora-query").value, sort: el("#lora-sort").value,
      genre: el("#lora-genre").value, license: el("#lora-license").value,
      stage: el("#lora-stage-filter").value, compatibility: el("#lora-compatibility-filter").value,
      page: String(page), limit: "20"});
    try {
      const value = await api("/api/loras/hub?" + query);
      if (serial !== searchRequest || tab !== "browse") return;
      results = value.models || [];
      el("#lora-results").innerHTML = results.map((item, index) => `<article class="lora-card">
        <p class="quiet-text">${escape(repoName(item)?.split("/")[0])}</p><h3>${escape(item.name || repoName(item)?.split("/").slice(1).join("/"))}</h3>
        <div class="lora-badges">${badge(licenseOf(item))}${badge(item.compatibility === "inspect-required" || !item.compatibility ? "Inspect compatibility" : item.compatibility)}${(item.stages || []).map(stage => badge(stageName(stage))).join("")}</div>
        <p class="control-hint">${escape((item.tags || []).filter(tag => !tag.includes(":") && !["safetensors", "lora"].includes(tag)).slice(0, 6).join(" · "))}</p>
        <p class="quiet-text">${Number(item.likes || 0).toLocaleString()} likes · ${Number(item.downloads || 0).toLocaleString()} downloads</p>
        ${item.updated_at ? `<p class="quiet-text">Updated ${escape(String(item.updated_at).slice(0, 10))}</p>` : ""}
        <button type="button" class="text-button" data-lora-hub="${index}">Listen & details ↗</button></article>`).join("");
      el("#lora-previous").disabled = page === 0;
      el("#lora-next").disabled = value.has_more === false || (value.has_more === undefined && results.length < 20);
      el("#lora-page").textContent = `Page ${page + 1}`;
      status(results.length ? "Open a style to hear examples and check its files before installing." : "No styles match this search. Try another sound or genre.");
    } catch (error) { if (serial === searchRequest) status(`Hugging Face is unavailable: ${error.message} Your installed styles are still available.`); }
  }

  async function showTab(next) {
    tab = next; ++searchRequest; ++detailRequest;
    el("#lora-browse-tab").setAttribute("aria-pressed", String(tab === "browse"));
    el("#lora-installed-tab").setAttribute("aria-pressed", String(tab === "installed"));
    el("#lora-search-form").hidden = tab !== "browse";
    el("#lora-filters").hidden = tab !== "browse";
    el("#lora-network-hint").hidden = tab !== "browse";
    el("#lora-pages").hidden = tab !== "browse";
    el("#lora-detail").hidden = true;
    if (tab === "installed") { await refreshInstalled(); } else await search();
  }

  function inlineMarkdown(text, repo, revision) {
    let html = "", position = 0;
    for (const match of text.matchAll(/!?\[([^\]]*)\]\(([^\s)]+)\)/g)) {
      html += escape(text.slice(position, match.index));
      let address;
      try { address = new URL(match[2], `https://huggingface.co/${repo}/blob/${revision}/README.md`); } catch {}
      const label = escape(match[1]);
      html += !match[0].startsWith("!") && ["http:", "https:"].includes(address?.protocol)
        ? `<a href="${escape(address.href)}" target="_blank" rel="noreferrer noopener">${label}</a>` : label;
      position = match.index + match[0].length;
    }
    return html + escape(text.slice(position));
  }

  function markdown(text, repo, revision) {
    // Only these generated elements are admitted. Raw HTML, remote image loads,
    // scripts and embeds remain text; external links require an explicit click.
    let code = false, output = "";
    for (const line of String(text || "").replace(/^---\n[\s\S]*?\n---\n/, "").split("\n")) {
      if (line.startsWith("```")) { output += code ? "</code></pre>" : "<pre><code>"; code = !code; continue; }
      if (code) { output += escape(line) + "\n"; continue; }
      const heading = /^(#{1,6})\s+(.*)/.exec(line);
      if (heading) { const level = Math.min(heading[1].length + 2, 6); output += `<h${level}>${inlineMarkdown(heading[2], repo, revision)}</h${level}>`; }
      else if (line.trim()) output += `<p>${inlineMarkdown(line, repo, revision)}</p>`;
    }
    return output + (code ? "</code></pre>" : "");
  }

  function mediaURL(item, path) {
    return "/api/loras/hub/media?" + new URLSearchParams({repo: item.repo, revision: item.revision, file: path});
  }

  async function openHub(repo, revision = "main") {
    const serial = ++detailRequest; status("Checking the style and its adapter headers…");
    try {
      const detail = await api("/api/loras/hub/detail?" + new URLSearchParams({repo, revision}));
      if (serial !== detailRequest) return;
      currentDetail = detail;
      const files = detail.files || [];
      const demos = (detail.demos || []).filter(file => /\.(mp3|flac|wav|ogg|m4a|opus|mp4|webm)$/i.test(fileName(file)));
      el("#lora-detail").innerHTML = `<div class="field-heading"><h3>${escape(detail.name || detail.repo)}</h3><button type="button" class="text-button" data-lora-detail-close>Close details</button></div>
        <div class="lora-badges">${badge(licenseOf(detail))}${badge("Revision " + detail.revision.slice(0, 8))}</div>
        <p class="control-hint">${escape(detail.license === "cc-by-nc-4.0" ? "This adapter is offered for noncommercial use with attribution." : "Check the model card for this adapter’s usage terms.")}</p>
        <div class="lora-demos">${demos.map(file => {
          const tag = /\.(mp4|webm)$/i.test(fileName(file)) ? "video" : "audio";
          return `<div><p>${escape(fileName(file))}</p><${tag} controls preload="none" src="${escape(mediaURL(detail, fileName(file)))}" aria-label="Preview ${escape(fileName(file))}"></${tag}>${file.prompt ? `<p class="control-hint">${escape(typeof file.prompt === "object" ? file.prompt.text : file.prompt)}</p>` : ""}</div>`;
        }).join("")}</div>
        <fieldset class="lora-files"><legend>Choose adapter files</legend>${files.map((file, index) => {
          const state = compatibility(file);
          const dimensions = [(file.stages || []).map(stageName).join(" + "), file.rank ? `Rank ${file.rank}` : "", file.format || ""].filter(Boolean).join(" · ");
          return `<label><input type="checkbox" data-lora-file="${index}" ${supported(file) ? "" : "disabled"} /><span>${escape(fileName(file))}<small>${escape(state.status)} · ${escape(bytes(file.bytes || file.size))}${state.reason ? " · " + escape(state.reason) : ""}</small>${dimensions ? `<small>${escape(dimensions)}</small>` : ""}${file.metadata?.pair_with ? `<small>Publisher’s companion: ${escape(file.metadata.pair_with)}. Check the model card for when it is required.</small>` : ""}</span></label>`;
        }).join("") || '<p class="control-hint">No adapter files were found in this revision.</p>'}</fieldset>
        <button type="button" class="secondary-button" id="lora-install" ${files.some(supported) ? "" : "disabled"}>Install selected files</button>
        <details class="lora-readme"><summary>Model card</summary><div>${markdown(detail.readme, detail.repo, detail.revision)}</div></details>`;
      // Choose the sole supported export; multi-checkpoint repos need an explicit selection.
      if (files.filter(supported).length === 1) el("#lora-detail input[data-lora-file]:not(:disabled)").checked = true;
      currentDetail.selectableFiles = files;
      el("#lora-detail").hidden = false; el("#lora-detail").scrollIntoView({block: "nearest", behavior: "smooth"});
      status("Compatibility was checked from file headers. Installation also verifies every tensor and the download hash.");
    } catch (error) { if (serial === detailRequest) status(error.message); }
  }

  async function edit(id) {
    const serial = ++detailRequest;
    try {
      const item = await api(`/api/loras/${id}`);
      if (serial !== detailRequest) return;
      currentDetail = item;
      el("#lora-detail").innerHTML = `<h3>${escape(item.name)}</h3><form id="lora-edit-form" class="lora-edit-form">
        <label for="lora-name">Name</label><input id="lora-name" value="${escape(item.name)}" required />
        <label for="lora-notes">Notes</label><textarea id="lora-notes" rows="2">${escape(item.notes)}</textarea>
        <label for="lora-tags">Tags, separated by commas</label><input id="lora-tags" value="${escape((item.tags || []).join(", "))}" />
        <label for="lora-words">Trigger words, separated by commas</label><input id="lora-words" value="${escape((item.trigger_words || []).join(", "))}" />
        <label for="lora-default">Default strength</label><input id="lora-default" type="number" step="any" value="${item.default_strength}" required />
        <button type="submit" class="secondary-button">Save changes</button></form>
        <p class="control-hint">${escape(item.license || "License not declared")} · ${escape(item.source.repo || "Local import")} ${escape(item.source.revision?.slice(0, 12) || "")}</p>
        <p class="control-hint">${item.references?.length || 0} saved references. Existing takes retain their original adapter identity.</p>
        <div class="lora-card-actions">${item.source.repo ? '<button type="button" id="lora-check-update" class="text-button">Check for a new revision</button>' : ""}<button type="button" id="lora-delete" class="text-button">Remove from library</button></div>`;
      el("#lora-detail").hidden = false; el("#lora-detail").scrollIntoView({block: "nearest"});
    } catch (error) { status(error.message); }
  }

  function use(id) {
    const item = installed.find(value => value.id === id);
    if (!enabled || !item || item.error) return;
    for (const stage of item.stages) choices[stage] = {id, scale: item.default_strength};
    saved = null; renderSelectors(); el("#lora-composer").open = true;
    el("#lora-dialog").close(); saveDraft(); notify(`${item.name} is ready for the next take.`);
  }

  function showProgress(task) {
    el("#lora-install-progress").hidden = false;
    const progress = el("#lora-progress");
    if (task.total) { progress.max = task.total; progress.value = task.downloaded || 0; } else progress.removeAttribute("value");
    const phase = {queued: "Waiting to download", inspecting: "Checking file headers", downloading: "Downloading", validating: "Checking tensors", completed: "Installed", cancelled: "Cancelled", failed: "Installation failed"}[task.status] || task.status || "Preparing adapter…";
    el("#lora-progress-text").textContent = phase + (task.total ? ` · ${bytes(task.downloaded || 0)} / ${bytes(task.total)}` : "");
  }

  function taskNotice(message) {
    if (taskView?.tab === tab && taskView.search === searchRequest && taskView.detail === detailRequest)
      status(message);
    else notify(message);
  }

  async function pollTask() {
    if (!activeTask) return;
    const id = activeTask;
    try {
      const task = await api(`/api/loras/tasks/${id}`);
      if (id !== activeTask) return;
      showProgress(task);
      if (["complete", "completed", "done", "installed", "succeeded", "error", "failed", "cancelled"].includes(task.status)) {
        activeTask = null; el("#lora-install-progress").hidden = true;
        await refreshInstalled(true);
        taskNotice(task.error || (task.status === "cancelled" ? "Installation cancelled. No partial adapter was activated." : "Installation finished. Your style is in Installed."));
      } else taskTimer = setTimeout(pollTask, 750);
    } catch (error) {
      if (id !== activeTask) return;
      if (error.message.includes("task was not found")) {
        activeTask = null; el("#lora-install-progress").hidden = true;
        taskNotice("This installation is no longer active. Install the same revision again to resume a saved download.");
      } else { status(error.message); taskTimer = setTimeout(pollTask, 2000); }
    }
  }

  function render(snapshot) {
    const changed = enabled !== !!snapshot.engine?.lora;
    enabled = !!snapshot.engine?.lora;
    if (changed) renderSelectors();
    if (!loaded) { loaded = true; refreshInstalled().catch(() => { loaded = false; }); }
  }

  document.addEventListener("DOMContentLoaded", () => {
    el("#lora-open").addEventListener("click", () => { el("#lora-dialog").showModal(); showTab(tab).catch(error => status(error.message)); });
    el("#lora-browse-tab").addEventListener("click", () => showTab("browse").catch(error => status(error.message)));
    el("#lora-installed-tab").addEventListener("click", () => showTab("installed").catch(error => status(error.message)));
    el("#lora-search-form").addEventListener("submit", event => { event.preventDefault(); page = 0; search(); });
    el("#lora-sort").addEventListener("change", () => { page = 0; search(); });
    for (const id of ["lora-stage-filter", "lora-compatibility-filter", "lora-license", "lora-genre"])
      el("#" + id).addEventListener("change", () => { page = 0; search(); });
    el("#lora-next").addEventListener("click", () => { page++; search(); });
    el("#lora-previous").addEventListener("click", () => { page = Math.max(0, page - 1); search(); });
    for (const stage of ["ar", "nar"]) {
      el("#lora-" + stage).addEventListener("change", event => {
        const item = installed.find(value => value.id === event.target.value);
        choices[stage] = {id: event.target.value, scale: item?.default_strength ?? 1}; saved = null;
        renderSelectors(); saveDraft();
      });
      el("#lora-" + stage + "-scale").addEventListener("input", event => { choices[stage].scale = Number(event.target.value); saveDraft(); });
    }
    el("#lora-trigger").addEventListener("click", () => {
      const words = [...new Set(installed.filter(item => selections().some(selection => selection.id === item.id)).flatMap(item => item.trigger_words || []))];
      const field = el("#style"), additions = words.filter(word => !field.value.includes(word));
      if (additions.length) field.value = [field.value.trim(), additions.join(", ")].filter(Boolean).join(", ");
      saveDraft(); notify(additions.length ? "Trigger words added to the sound." : "The trigger words are already in your sound.");
    });
    el("#lora-results").addEventListener("click", event => {
      const button = event.target.closest("button"); if (!button) return;
      if (button.dataset.loraHub !== undefined) {
        const item = results[Number(button.dataset.loraHub)];
        openHub(repoName(item), item.revision || "main");
      }
      if (button.dataset.loraEdit) edit(button.dataset.loraEdit);
      if (button.dataset.loraUse) use(button.dataset.loraUse);
    });
    el("#lora-detail").addEventListener("click", async event => {
      const button = event.target.closest("button"); if (!button) return;
      try {
        if (button.hasAttribute("data-lora-detail-close")) { el("#lora-detail").hidden = true; ++detailRequest; }
        if (button.id === "lora-install") {
          if (activeTask || installing) throw new Error("Finish or cancel the current installation first.");
          const files = [...el("#lora-detail").querySelectorAll("input[data-lora-file]:checked")].map(input => fileName(currentDetail.selectableFiles[Number(input.dataset.loraFile)]));
          if (!files.length) throw new Error("Choose an adapter file to install.");
          installing = true; button.disabled = true;
          try {
            const task = await api("/api/loras", "POST", {repo: currentDetail.repo, revision: currentDetail.revision, files});
            activeTask = task.id; taskView = {tab, search: searchRequest, detail: detailRequest};
            showProgress(task); pollTask();
          } finally { installing = false; button.disabled = false; }
        }
        if (button.id === "lora-check-update") {
          const update = await api(`/api/loras/${currentDetail.id}/updates`);
          if (update.update_available || update.available) {
            status("A new revision is available. Installing it keeps the old version for existing takes.");
            await openHub(currentDetail.source.repo, update.revision);
          } else status("You have the current revision.");
        }
        if (button.id === "lora-delete") {
          const item = await api(`/api/loras/${currentDetail.id}`);
          const count = item.references?.length || 0;
          if (!window.confirm(count ? `Remove ${item.name}? ${count} saved references keep its identity, but rendering them again will need this exact adapter reinstalled.` : `Remove ${item.name} from your library?`)) return;
          const result = await api(`/api/loras/${item.id}`, "DELETE", {confirm: count > 0});
          if (result.confirmation_required) {
            status("A take started referencing this adapter while its details were open. Review its references and choose Remove again.");
            return;
          }
          el("#lora-detail").hidden = true; await refreshInstalled();
        }
      } catch (error) { status(error.message); }
    });
    el("#lora-detail").addEventListener("submit", async event => {
      if (event.target.id !== "lora-edit-form") return;
      event.preventDefault();
      const words = selector => el(selector).value.split(",").map(value => value.trim()).filter(Boolean);
      try {
        await api(`/api/loras/${currentDetail.id}`, "PATCH", {name: el("#lora-name").value, notes: el("#lora-notes").value, tags: words("#lora-tags"), trigger_words: words("#lora-words"), default_strength: Number(el("#lora-default").value)});
        await refreshInstalled(); status("Style details saved. Your current take’s strengths are unchanged.");
      } catch (error) { status(error.message); }
    });
    el("#lora-cancel").addEventListener("click", async () => {
      if (activeTask) { try { await api(`/api/loras/tasks/${activeTask}/cancel`, "POST", {}); } catch (error) { status(error.message); } }
    });
    el("#lora-import-open").addEventListener("click", () => { el("#lora-import-options").hidden = !el("#lora-import-options").hidden; });
    el("#lora-import-choose").addEventListener("click", () => el("#lora-import-file").click());
    el("#lora-import-file").addEventListener("change", async event => {
      const file = event.target.files[0]; if (!file) return;
      status("Importing and checking every tensor…");
      try {
        const config = el("#lora-import-config").files[0];
        const metadata = config ? {adapter_config: JSON.parse(await config.text())} : {};
        const response = await fetch("/api/loras/import?" + new URLSearchParams({name: file.name}), {method: "POST", headers: {"X-Riff-Request": "1", "Content-Type": "application/octet-stream", "X-Riff-LoRA-Metadata": headerJSON(metadata)}, body: file});
        const value = await response.json(); if (!response.ok) throw new Error(value.error);
        await showTab("installed"); status(`${value.name} imported. Select it for composition, sound, or both.`);
      } catch (error) { status(error.message); } finally { event.target.value = ""; }
    });
    el("#lora-dialog").addEventListener("close", () => {
      ++detailRequest; ++searchRequest;
      for (const media of el("#lora-dialog").querySelectorAll("audio, video")) media.pause();
    });
    el("#lora-dialog").addEventListener("play", event => {
      if (!["AUDIO", "VIDEO"].includes(event.target.tagName)) return;
      el("#audio").pause();
      for (const media of el("#lora-dialog").querySelectorAll("audio, video")) if (media !== event.target) media.pause();
    }, true);
  });

  window.RiffLoRAs = {recipe, fill, render, refresh: refreshInstalled};
})();
