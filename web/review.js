"use strict";

(() => {
  let currentTrack = null,
    signature = "",
    requests = [],
    refreshing = false;
  let submitting = false;
  const panel = $("#producer-panel");
  $("#review-settings-open").hidden = false;
  let attached = [];
  const saves = new Set();
  const headerJSON = (value) =>
    JSON.stringify(value).replace(
      /[\u007f-\uffff]/g,
      (char) => "\\u" + char.charCodeAt(0).toString(16).padStart(4, "0"),
    );
  const megabytes = (bytes) => `${Math.round((bytes / 1048576) * 100) / 100} MB`;
  const shortHash = (value) => String(value).slice(0, 8);
  const sourceText = (item) => item.link || item.note || "";
  function referenceError(message = "") {
    errorMessage("#review-reference-error", message);
  }
  function describeEstimate(estimate) {
    $("#review-reference-hint").textContent =
      "Reference clips are sent with this recording to OpenRouter and the review model’s provider, where usage charges apply. Riff keeps them on this computer only.";
    if (!estimate) return;
    const notices = (estimate.notices || []).join(" ");
    $("#review-reference-estimate").textContent = estimate.clips
      ? `This review would send about ${megabytes(estimate.estimated_sent_bytes)} of audio, ${formatDuration(estimate.audio_seconds)} in all, as MP3. ${notices}`
      : "";
  }
  async function refreshEstimate() {
    if (!currentTrack) return;
    try {
      describeEstimate((await api(`/api/review/references?track_id=${currentTrack.id}`)).estimate);
    } catch {}
  }
  function drawReferences() {
    $("#review-reference-list").innerHTML = attached
      .map(
        (item) => `<li class="review-reference" data-reference="${item.id}">
          <label>Label<input data-reference-label value="${esc(item.label)}" maxlength="200" autocomplete="off" /></label>
          <label>Source link or note<input data-reference-source value="${esc(sourceText(item))}" placeholder="https://… or where this came from" autocomplete="off" /></label>
          <span class="quiet-text review-reference-meta">${esc(formatDuration(item.duration))} · ${esc(megabytes(item.bytes))} · ${esc(item.format.toUpperCase())} · ${shortHash(item.sha256)}</span>
          <button type="button" class="text-button" data-remove-reference="${item.id}" aria-label="Remove reference ${esc(item.label)}">Remove</button>
        </li>`,
      )
      .join("");
    refreshEstimate();
  }
  async function loadReferences() {
    try {
      const result = await api("/api/review/references");
      attached = result.references;
      drawReferences();
      describeEstimate(result.estimate);
    } catch (error) {
      referenceError(error.message);
    }
  }
  function saveReference(row) {
    const id = row.dataset.reference;
    const source = $("[data-reference-source]", row).value.trim();
    const link = /^https?:\/\//i.test(source);
    const task = api(`/api/review/references/${id}`, "PATCH", {
      label: $("[data-reference-label]", row).value,
      link: link ? source : "",
      note: link ? "" : source,
    })
      .then((item) => {
        attached = attached.map((entry) => (entry.id === id ? item : entry));
        $("[data-remove-reference]", row)?.setAttribute("aria-label", `Remove reference ${item.label}`);
        referenceError();
      })
      .catch((error) => referenceError(error.message))
      .finally(() => saves.delete(task));
    saves.add(task);
    return task;
  }
  $("#review-reference-list").addEventListener("change", (event) => {
    const row = event.target.closest("[data-reference]");
    if (row) saveReference(row);
  });
  $("#review-reference-list").addEventListener("click", async (event) => {
    const remove = event.target.closest("[data-remove-reference]");
    if (!remove) return;
    remove.disabled = true;
    try {
      await Promise.all(saves);
      await api(`/api/review/references/${remove.dataset.removeReference}`, "DELETE", {});
      attached = attached.filter((item) => item.id !== remove.dataset.removeReference);
      referenceError();
      drawReferences();
      $("#add-review-reference").focus();
    } catch (error) {
      remove.disabled = false;
      referenceError(error.message);
    }
  });
  $("#add-review-reference").addEventListener("click", () =>
    $("#review-reference-file").click(),
  );
  let checking = null;
  $("#cancel-review-reference").addEventListener("click", () => {
    checking?.abort();
    api("/api/review/references/cancel", "POST", {}).catch(() => {});
  });
  $("#review-reference-file").addEventListener("change", async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    const button = $("#add-review-reference");
    button.disabled = true;
    $("#cancel-review-reference").hidden = false;
    referenceError();
    checking = new AbortController();
    try {
      const response = await fetch(
        "/api/review/references?" + new URLSearchParams({ name: file.name }),
        {
          method: "POST",
          signal: checking.signal,
          headers: {
            "X-Riff-Request": "1",
            "Content-Type": "application/octet-stream",
            "X-Riff-Reference-Metadata": headerJSON({}),
          },
          body: file,
        },
      );
      const value = await response.json();
      if (!response.ok) throw new Error(value.error);
      attached = [...attached, value];
      drawReferences();
    } catch (error) {
      referenceError(error.name === "AbortError" ? "Checking this clip was cancelled." : error.message);
    } finally {
      checking = null;
      event.target.value = "";
      button.disabled = false;
      $("#cancel-review-reference").hidden = true;
    }
  });
  loadReferences();

  function readFocus(id) {
    try {
      return JSON.parse(
        localStorage.getItem(`riff.review-focus.${id}`) || "null",
      );
    } catch {
      return null;
    }
  }
  function saveFocus() {
    if (!currentTrack) return;
    try {
      localStorage.setItem(
        `riff.review-focus.${currentTrack.id}`,
        JSON.stringify({
          focus: $("#review-focus").value,
          keep_lyrics: $("#review-keep-lyrics").checked,
        }),
      );
    } catch {}
  }
  $("#review-focus").addEventListener("input", saveFocus);
  $("#review-keep-lyrics").addEventListener("change", saveFocus);

  async function openSettings() {
    errorMessage("#review-settings-error");
    $("#review-api-key").value = "";
    try {
      const connection = await api("/api/review/settings");
      $("#reviews-enabled").checked = connection.enabled;
      $("#review-model").value = connection.model;
      $("#review-probe-seconds").value = connection.reference_probe_seconds;
      $("#remove-review-key").disabled = !connection.has_key;
      $("#review-key-status").textContent = connection.has_key
        ? `Your key is saved in ${connection.key_storage}.`
        : connection.key_storage_available === false ? "Install libsecret-tools and unlock your desktop keyring to save a key." : `Your key will be saved in ${connection.key_storage}.`;
      $("#review-settings-dialog").showModal();
    } catch (error) {
      notify(error.message);
    }
  }
  $("#review-settings-open").addEventListener("click", openSettings);
  $("#producer-settings").addEventListener("click", openSettings);
  $("#review-settings-dialog").addEventListener("close", () => {
    $("#review-api-key").value = "";
  });
  $("#review-settings-dialog").addEventListener("cancel", () => {
    $("#review-api-key").value = "";
  });

  $("#review-settings-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", event.currentTarget);
    button.disabled = true;
    const payload = {
      enabled: $("#reviews-enabled").checked,
      model: $("#review-model").value,
      reference_probe_seconds: Number($("#review-probe-seconds").value),
    };
    if ($("#review-api-key").value)
      payload.api_key = $("#review-api-key").value;
    $("#review-api-key").value = "";
    try {
      await api("/api/review/settings", "POST", payload);
      $("#review-settings-dialog").close();
      await refresh();
      notify("Review settings saved.");
    } catch (error) {
      errorMessage("#review-settings-error", error.message);
    } finally {
      button.disabled = false;
    }
  });
  $("#remove-review-key").addEventListener("click", async () => {
    const button = $("#remove-review-key");
    button.disabled = true;
    try {
      await api("/api/review/key", "DELETE", {});
      $("#reviews-enabled").checked = false;
      $("#review-key-status").textContent =
        "Add a key whenever you want to reconnect.";
      await refresh();
      notify("Key removed from macOS Keychain.");
    } catch (error) {
      button.disabled = false;
      errorMessage("#review-settings-error", error.message);
    }
  });
  $("#refresh-review-models").addEventListener("click", async () => {
    $("#review-model-status").textContent = "Finding listening models…";
    try {
      const result = await api("/api/review/models?refresh=1");
      $("#review-models").replaceChildren(
        ...result.models.map((model) => {
          const option = document.createElement("option");
          option.value = model.id;
          option.label = model.name;
          return option;
        }),
      );
      $("#review-model-status").textContent =
        `${result.models.length} audio ${result.models.length === 1 ? "model" : "models"} available. Choose in the model field, or enter a model ID.`;
      $("#review-model").focus();
    } catch (error) {
      $("#review-model-status").textContent = error.message;
    }
  });

  $("#review-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!currentTrack || submitting) return;
    submitting = true;
    $("#request-review").disabled = true;
    errorMessage("#review-error");
    try {
      await Promise.all(saves);
      await api(`/api/tracks/${currentTrack.id}/reviews`, "POST", {
        focus: $("#review-focus").value,
        keep_lyrics: $("#review-keep-lyrics").checked,
        references: attached.map((item) => item.id),
      });
      attached = [];
      drawReferences();
      signature = "";
      await refresh();
    } catch (error) {
      errorMessage("#review-error", error.message);
    } finally {
      submitting = false;
      $("#request-review").disabled = !state.review_settings?.enabled;
    }
  });

  function withCues(notes) {
    return esc(notes)
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/\b(?:\d+:)?\d{1,2}:[0-5]\d\b/g, (stamp) => {
        const seconds = stamp
          .split(":")
          .reduce((total, part) => total * 60 + Number(part), 0);
        return seconds < currentTrack.audio.duration
          ? `<button type="button" class="review-cue" data-review-time="${seconds}" aria-label="Play from ${stamp}">${stamp}</button>`
          : stamp;
      });
  }
  function recommendedTake(review) {
    const take = review.generation;
    if (!take?.mode) return "";
    const planning = { off: "Direct", melody: "Melody", full: "Melody + harmony" }[take.cot];
    return `<section class="review-take"><p class="review-take-label">Recommended take</p><h3>${esc(take.title)}</h3>
      <div class="review-take-settings"><span>${esc(take.max_seconds)} seconds</span><span>${esc(take.steps)} steps</span><span>${take.solver === "ab2" ? "Multistep AB2" : "Midpoint"}</span><span>${esc(planning)}</span><span>Guidance ${esc(take.cfg_scale)}</span></div>
      ${take.acoustic_source ? '<p class="control-hint">Keeps the exact sound synthesis and renders another audio take.</p>' : take.performance_source ? '<p class="control-hint">Reuses this take’s saved performance and phrasing.</p>' : take.render_mode === "plan" ? '<p class="control-hint">Composes an editable score.</p>' : take.render_mode === "sound" ? '<p class="control-hint">Saves the sound to finish as audio later.</p>' : ""}
      <p class="review-take-direction">${esc(take.style)}</p>
      ${take.lyrics ? `<details><summary>Lyrics${review.keep_lyrics ? " · kept" : ""}</summary><pre dir="auto">${esc(take.lyrics)}</pre></details>` : ""}
      ${take.abc ? `<details data-review-score="${review.id}"><summary>Score</summary><div class="review-score-preview" aria-label="Recommended score"></div></details>` : ""}
      <details><summary>Generation recipe</summary><pre class="review-recipe-json">${esc(JSON.stringify(take, null, 2))}</pre><button type="button" class="text-button" data-export-review="${review.id}">Download recipe</button></details>
      <div class="review-take-actions"><button type="button" class="secondary-button solid" data-generate-review="${review.id}">${take.acoustic_source ? "Finish audio" : take.render_mode === "plan" ? "Compose this score" : take.render_mode === "sound" ? "Create this sound" : take.render_mode === "performance" ? "Create this performance" : take.performance_source ? "Render this performance" : "Generate this take"}</button><button type="button" class="text-button" data-use-review="${review.id}">Edit in studio</button></div></section>`;
  }

  function sentAs(review, item) {
    const sent = (review.sent_audio || []).find((entry) => entry.source === item.id);
    return sent ? ` · sent as ${esc(sent.format.toUpperCase())}` : "";
  }
  function referencesBlock(review, open = false) {
    if (!review.references?.length) return "";
    const names = {
      generated_take: "Generated take",
      ...Object.fromEntries(review.references.map((item) => [item.id, item.label])),
    };
    const clips = review.references
      .map(
        (item) => `<li><strong>${esc(item.label)}</strong> <span class="quiet-text">${esc(formatDuration(item.duration))} · ${esc(item.format.toUpperCase())} · ${esc(megabytes(item.bytes))} · ${shortHash(item.sha256)}${sentAs(review, item)}</span>${item.link ? `<a class="review-reference-link" href="${esc(item.link)}" target="_blank" rel="noopener noreferrer">${esc(item.link)}</a>` : ""}${item.note ? `<span class="review-reference-note">${esc(item.note)}</span>` : ""}</li>`,
      )
      .join("");
    const groups = Object.keys(names)
      .map((source) => [source, (review.observations || []).filter((item) => item.source === source)])
      .filter(([, items]) => items.length)
      .map(
        ([source, items]) => `<section class="review-observation-group" data-observation-source="${esc(source)}"><h4>${esc(names[source])}</h4><ul>${items
          .map((item) => `<li>${item.timestamp ? `<span class="review-observation-time">${esc(item.timestamp)}</span> ` : ""}${esc(item.note)}</li>`)
          .join("")}</ul></section>`,
      )
      .join("");
    return `${(review.warnings || []).map((warning) => `<p class="review-warning" role="status">${esc(warning)}</p>`).join("")}
      ${groups ? `<details class="review-observations" open><summary>Observations by recording</summary>${groups}</details>` : ""}
      <details class="review-references-used" ${open ? "open" : ""}><summary>Reference clips · ${review.references.length}</summary><ul class="review-reference-receipt">${clips}</ul><button type="button" class="text-button" data-reuse-review="${review.id}">Use these clips in another review</button></details>`;
  }

  function takeRecipe(review) {
    if (review.generation?.mode) return {
      ...review.generation, parent_track_id: review.track_id, review_id: review.id,
    };
    // Earlier reviews remain editable; they predate complete take recommendations.
    const recipe = {
      ...review.source_recipe, ...review.revision,
      title: review.revision.title || `${currentTrack.title} — revision`,
      seed: String(review.source_recipe.seed ?? review.revision.seed ?? ""), parent_track_id: review.track_id, review_id: review.id,
      acoustic_source: "", decoder: {},
    };
    if (review.keep_lyrics) recipe.lyrics = review.source_recipe.lyrics;
    else if (typeof review.revision.lyrics === "string") {
      recipe.lyrics_source = "review";
      if (recipe.lyrics.trim()) recipe.mode = "lyrics";
    }
    if (recipe.abc && recipe.cot === "off") recipe.cot = "full";
    return recipe;
  }

  function drawNotes() {
    const names = {
      queued: "Waiting to listen",
      running: "Listening…",
      cancelling: "Stopping…",
      cancelled: "Cancelled",
      interrupted: "Interrupted",
      failed: "Review unfinished",
    };
    $("#producer-notes").innerHTML = requests.length
      ? requests
          .map((review) => {
            const active = ["queued", "running", "cancelling"].includes(
              review.status,
            );
            return `<article class="producer-note"><div class="producer-note-meta"><span>${esc(review.model)}</span><span>${esc(formatDate(review.created))}</span></div>${
              review.status === "done"
                ? ` ${review.summary ? `<p class="review-summary">${esc(review.summary)}</p>` : ""}${recommendedTake(review) || `<button type="button" class="secondary-button" data-use-review="${review.id}">${icon("branch")}${Object.keys(review.revision).length ? "Use revision" : "Start another take"}</button>`}${referencesBlock(review)}<details class="review-listening-notes"><summary>Listening notes<span aria-hidden="true">＋</span></summary><div class="review-note-text">${withCues(review.notes)}</div>${review.focus ? `<p class="review-focus-label">Review focus: ${esc(review.focus)}</p>` : ""}</details>`
                : `<p class="review-state">${active ? '<span class="spinner" aria-hidden="true"></span>' : ""}${names[review.status] || esc(review.status)}</p>${review.error ? `<p class="form-error">${esc(review.error)}</p>` : ""}${active ? `<button type="button" class="text-button" data-cancel-review="${review.id}" ${review.status === "cancelling" ? "disabled" : ""}>Cancel review</button>` : ""}${referencesBlock(review, true)}`
            }</article>`;
          })
          .join("")
      : '<p class="review-empty">A fresh perspective on this take. Give the listener a focus, or invite an open review.</p>';
    $$("[data-review-score]", panel).forEach((details) => details.addEventListener("toggle", () => {
      if (!details.open || !window.ABCJS) return;
      const review = requests.find((item) => item.id === details.dataset.reviewScore);
      if (review?.generation?.abc) {
        const notation = $(".review-score-preview", details);
        try {
          ABCJS.renderAbc(notation, review.generation.abc, {
            responsive: "resize", staffwidth: Math.max(240, notation.clientWidth - 25),
            paddingtop: 12, paddingbottom: 12, add_classes: true,
          });
        } catch {
          notation.textContent = review.generation.abc;
        }
      }
    }));
  }
  panel.addEventListener("click", async (event) => {
    const cancel = event.target.closest("[data-cancel-review]");
    const use = event.target.closest("[data-use-review]");
    const generate = event.target.closest("[data-generate-review]");
    const download = event.target.closest("[data-export-review]");
    const cue = event.target.closest("[data-review-time]");
    const reuse = event.target.closest("[data-reuse-review]");
    try {
      if (reuse) {
        reuse.disabled = true;
        try {
          const result = await api("/api/review/references/reuse", "POST", {
            review_id: reuse.dataset.reuseReview,
          });
          attached = [...attached, ...result.references];
          referenceError();
          drawReferences();
          notify("The reference clips are attached to your next review.");
          $("#review-focus").focus();
        } finally {
          reuse.disabled = false;
        }
      }
      if (cancel) {
        cancel.disabled = true;
        await api(
          `/api/reviews/${cancel.dataset.cancelReview}/cancel`,
          "POST",
          {},
        );
        signature = "";
        await refresh();
      }
      if (cue) {
        audio.currentTime = Number(cue.dataset.reviewTime);
        await audio.play();
      }
      if (use) {
        const review = requests.find(
          (item) => item.id === use.dataset.useReview,
        );
        if (!review) return;
        const recipe = takeRecipe(review);
        openRecipe(recipe);
        $("#title").focus();
        notify("The proposed take is on your writing desk.");
      }
      if (generate) {
        const review = requests.find((item) => item.id === generate.dataset.generateReview);
        if (!review?.generation?.mode || generate.disabled) return;
        generate.disabled = true;
        try {
          const job = await queueTake("/api/generations", takeRecipe(review));
          notify(`“${job.recipe.title}” is on its way.`);
          await refresh();
        } finally { generate.disabled = false; }
      }
      if (download) {
        const review = requests.find((item) => item.id === download.dataset.exportReview);
        if (!review?.generation?.mode) return;
        const url = URL.createObjectURL(new Blob([JSON.stringify(takeRecipe(review), null, 2) + "\n"], { type: "application/json" }));
        const link = document.createElement("a");
        link.href = url; link.download = "riff-recommended-take.json"; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (error) {
      errorMessage("#review-error", error.message);
    }
  });
  async function render(track, snapshot) {
    panel.hidden = !track;
    if (!track) return;
    if (currentTrack?.id !== track.id) {
      currentTrack = track;
      signature = "";
      requests = [];
      const draft = readFocus(track.id);
      $("#review-focus").value = draft?.focus || "";
      const hasLyrics = !!track.recipe.lyrics?.trim();
      $("#review-keep-lyrics").closest("label").hidden = !hasLyrics;
      $("#review-keep-lyrics").checked =
        hasLyrics && (draft?.keep_lyrics ?? true);
      errorMessage("#review-error");
      drawNotes();
      refreshEstimate();
    } else currentTrack = track;
    $("#review-parent").hidden = !track.recipe.parent_track_id;
    if (track.recipe.parent_track_id)
      $("#review-parent").href = listeningLink(track.recipe.parent_track_id);
    const enabled = !!snapshot.review_settings?.enabled;
    $("#producer-connection").textContent = enabled
      ? snapshot.review_settings.model
      : "Connect OpenRouter in Settings to invite a review.";
    $("#request-review").disabled = !enabled || submitting;
    const list = (snapshot.reviews || []).filter(
      (item) => item.track_id === track.id,
    );
    $("#review-count").textContent = list.length
      ? `${list.length} ${list.length === 1 ? "review" : "reviews"}`
      : "";
    const nextSignature = JSON.stringify([track.id, list]);
    if (signature === nextSignature || refreshing) return;
    refreshing = true;
    try {
      const result = await api(`/api/tracks/${track.id}/reviews`);
      if (currentTrack?.id === track.id) {
        requests = result.reviews;
        signature = nextSignature;
        drawNotes();
      }
    } catch (error) {
      if (currentTrack?.id === track.id)
        errorMessage("#review-error", error.message);
    } finally {
      refreshing = false;
    }
  }
  window.RiffReview = { render };
  render(selected, state);
})();
