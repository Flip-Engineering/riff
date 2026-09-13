/* Creative controls share the small app's classic-script scope. Optional provider writing uses the configured connection. */
let draftLyricsSource = "provided";
let ideaUndo = null;
let ideaBusy = false;
let ideaCancelled = false;
let managedSound = null;
let compassUsed = false;
let compassPending = null;

function creationMode() {
  return $("[name=creation-mode]:checked").value;
}

function styleForMode(style) {
  return style;
}

function updateCompass() {
  const energy = Number($("#energy").value) / 100;
  const texture = Number($("#texture").value) / 100;
  for (const id of ["#compass-point", "#compass-halo"]) {
    $(id).setAttribute("cx", String(26 + texture * 308));
    $(id).setAttribute("cy", String(112 - energy * 90));
  }
  const energyName =
    energy < 0.34 ? "Calm" : energy < 0.67 ? "Easy groove" : "Driving";
  const textureName =
    texture < 0.34 ? "acoustic" : texture < 0.67 ? "blended" : "electronic";
  $("#compass-reading").textContent = `${energyName} / ${textureName}`;
  $("#energy").setAttribute(
    "aria-valuetext",
    `${Math.round(energy * 100)} percent, ${energyName}`,
  );
  $("#texture").setAttribute(
    "aria-valuetext",
    `${Math.round(texture * 100)} percent electronic`,
  );
}

function updateExploration() {
  const instrumental = creationMode() === "instrumental",
    open = instrumental || creationMode() === "free";
  $("#words-space").hidden = open;
  $("#instrumental-invitation").hidden = !open;
  $("#invitation-title").textContent = instrumental
    ? "An instrumental starting point."
    : "Leave room for the unexpected.";
  $("#instrumental-warning").hidden = !instrumental;
  $("#lyrics").required = false;
  $("#words-hint").textContent =
    draftLyricsSource === "ai"
      ? "An AI-written draft. Keep the lines you love and make it yours."
      : draftLyricsSource === "generated"
        ? "A phrasebook idea to make your own."
        : creationMode() === "surprise"
          ? "Riff can write the words when you generate, or start with your own."
          : "Write freely, or add sections as your song takes shape.";
  for (const id of ["#hold-words", "#hold-sound"]) {
    $(id).classList.toggle(
      "held",
      $(id).getAttribute("aria-pressed") === "true",
    );
  }
  updateCompass();
}

async function shuffleIdea(part = "all", fromCompass = false) {
  if (ideaBusy) {
    if (fromCompass) compassPending = JSON.stringify(formRecipe());
    return;
  }
  compassPending = null;
  const before = formRecipe();
  if (
    before.idea_engine === "phrases" && part === "all" &&
    before.hold_sound &&
    (["free", "instrumental"].includes(before.mode) || before.hold_words)
  ) {
    notify("Release a hold to explore a new direction.");
    return;
  }
  ideaBusy = true;
  ideaCancelled = false;
  $("#stop-writing").hidden = before.idea_engine === "phrases";
  const buttons = ["#surprise", "#spark", "#shuffle-lyrics"];
  buttons.forEach((id) => ($(id).disabled = true));
  try {
    const request = { ...before, write_scope: part };
    // Draft-only text stays in this tab. Active notation and all generation
    // controls accompany every writer request, including focused revisions.
    delete request.abc_draft; delete request.lyrics_draft;
    if (before.idea_engine === "phrases" && part !== "words" && !before.hold_sound) delete request.style;
    $("#writer-status").textContent =
      before.idea_engine === "phrases" ? "Shuffling…" : "Writing a new idea…";
    if (fromCompass)
      Object.assign(request, {
        energy: before.energy,
        texture: before.texture,
        seed: before.seed || undefined,
      });
    const idea = await api("/api/inspiration", "POST", request);
    if (idea.cancelled) {
      notify("Writing stopped. Your draft is kept.");
      return;
    }
    // A fast response must still respect typing, holds, or mode changes made during it.
    if (JSON.stringify(formRecipe()) !== JSON.stringify(before)) {
      notify("You changed the draft. Your edits are kept.");
      return;
    }
    ideaUndo = before;
    const next = { ...before, ...(idea.generation || {}) };
    if (idea.generation) {
      for (const key of ["writer_model", "writer_summary", "lyrics_source"]) next[key] = idea[key];
      next.lyrics_draft = ["free", "instrumental"].includes(next.mode) ? before.lyrics_draft : next.lyrics;
      next.abc_draft = next.cot === "off" ? before.abc_draft : next.abc;
      // Source lineage and UI holds belong to the artist, not the response.
      for (const key of ["parent_track_id", "review_id", "hold_words", "hold_sound"]) next[key] = before[key];
    }
    const changeWords =
      part === "words" || (part === "all" && !before.hold_words);
    const changeSound =
      part === "sound" || (part === "all" && !before.hold_sound);
    if (!idea.generation && changeWords) {
      next.title = idea.title;
      if (!["free", "instrumental"].includes(before.mode)) {
        next.lyrics = idea.lyrics;
        next.lyrics_draft = idea.lyrics;
        next.lyrics_source = idea.lyrics_source;
      }
    }
    if (!idea.generation && changeSound)
      Object.assign(next, {
        style: idea.style,
        energy: idea.energy,
        texture: idea.texture,
      });
    fillRecipe(next);
    saveDraft();
    $("#undo-idea").disabled = false;
    $("#idea-caption").textContent = idea.theme_name;
    notify(
      part === "words"
        ? "New words to work with."
        : part === "sound"
          ? "A new sound to explore."
          : "A fresh direction. Keep what you like.",
    );
  } catch (error) {
    notify(
      ideaCancelled ? "Writing stopped. Your draft is kept." : error.message,
    );
  } finally {
    ideaBusy = false;
    $("#stop-writing").hidden = true;
    $("#writer-status").textContent = "Ready for an idea";
    buttons.forEach((id) => ($(id).disabled = false));
    const pending = compassPending; compassPending = null;
    if (!ideaCancelled && pending && pending === JSON.stringify(formRecipe()))
      queueMicrotask(() => shuffleIdea("sound", true));
  }
}

function drawSoundManager(preferred) {
  const sounds = state.presets;
  $("#sounds-empty").hidden = sounds.length > 0;
  $("#sound-edit-form").hidden = !sounds.length;
  $("#sound-list").innerHTML = sounds
    .map(
      (sound) =>
        `<button type="button" data-edit-sound="${esc(sound.id)}">${esc(sound.name)}</button>`,
    )
    .join("");
  managedSound =
    sounds.find((sound) => sound.id === preferred) || sounds[0] || null;
  if (managedSound) selectManagedSound(managedSound.id);
}

function selectManagedSound(id) {
  managedSound = state.presets.find((sound) => sound.id === id);
  if (!managedSound) return;
  $("#sound-edit-name").value = managedSound.name;
  $("#sound-edit-style").value = managedSound.style;
  $$("[data-edit-sound]").forEach((button) =>
    button.setAttribute(
      "aria-pressed",
      String(button.dataset.editSound === id),
    ),
  );
  errorMessage("#sounds-error");
}

document.addEventListener("DOMContentLoaded", () => {
  $("#stop-writing").addEventListener("click", async () => {
    try {
      await api("/api/inspiration/cancel", "POST", {});
      ideaCancelled = true;
    } catch (error) {
      notify(error.message);
    }
  });
  $("#surprise").addEventListener("click", () => shuffleIdea());
  $("#spark").addEventListener("click", () => shuffleIdea("sound"));
  $("#shuffle-lyrics").addEventListener("click", () => shuffleIdea("words"));
  $("#undo-idea").addEventListener("click", () => {
    if (!ideaUndo) return;
    fillRecipe(ideaUndo);
    saveDraft();
    ideaUndo = null;
    $("#undo-idea").disabled = true;
    $("#idea-caption").textContent = "Previous idea restored";
    notify("Your previous idea is back.");
  });
  $("#lyrics").addEventListener("input", () => {
    draftLyricsSource = "provided";
    saveDraft();
  });
  for (const id of ["#hold-words", "#hold-sound"]) {
    $(id).addEventListener("click", () => {
      $(id).setAttribute(
        "aria-pressed",
        String($(id).getAttribute("aria-pressed") !== "true"),
      );
      saveDraft();
    });
  }
  $$("[name=creation-mode]").forEach((input) =>
    input.addEventListener("change", () => {
      if (creationMode() === "surprise" && !$("#lyrics").value.trim())
        shuffleIdea();
      else saveDraft();
    }),
  );
  const pad = $("#compass-pad");
  let compassPointer = null;
  function movePoint(event) {
    const svg = $("svg", pad), matrix = svg.getScreenCTM();
    if (!matrix) return;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    compassUsed = true;
    $("#texture").value = Math.round(Math.max(0, Math.min(1, (point.x - 26) / 308)) * 100);
    $("#energy").value = Math.round(Math.max(0, Math.min(1, (112 - point.y) / 90)) * 100);
    updateCompass();
  }
  pad.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0 || compassPointer !== null) return;
    event.preventDefault();
    compassPointer = event.pointerId;
    pad.setPointerCapture(event.pointerId);
    movePoint(event);
  });
  pad.addEventListener("pointermove", (event) => {
    if (compassPointer === event.pointerId) movePoint(event);
  });
  pad.addEventListener("pointerup", (event) => {
    if (compassPointer !== event.pointerId) return;
    movePoint(event); compassPointer = null;
    if (pad.hasPointerCapture(event.pointerId)) pad.releasePointerCapture(event.pointerId);
    saveDraft();
    shuffleIdea("sound", true);
  });
  for (const name of ["pointercancel", "lostpointercapture"]) pad.addEventListener(name, event => {
    if (compassPointer !== event.pointerId) return;
    compassPointer = null; saveDraft();
  });
  for (const id of ["#energy", "#texture"]) {
    $(id).addEventListener("input", () => {
      compassUsed = true;
      updateCompass();
    });
    $(id).addEventListener("change", () => { saveDraft(); shuffleIdea("sound", true); });
  }
  $("#manage-sounds").addEventListener("click", () => {
    drawSoundManager();
    $("#sounds-dialog").showModal();
  });
  $("#sound-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-edit-sound]");
    if (button) selectManagedSound(button.dataset.editSound);
  });
  $("#sound-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!managedSound) return;
    const button = $("button[type=submit]", $("#sound-edit-form"));
    button.disabled = true;
    try {
      await api(`/api/presets/${managedSound.id}`, "PATCH", {
        name: $("#sound-edit-name").value,
        style: $("#sound-edit-style").value,
      });
      await refresh();
      drawSoundManager(managedSound.id);
      notify("Saved sound updated.");
    } catch (error) {
      errorMessage("#sounds-error", error.message);
    } finally {
      button.disabled = false;
    }
  });
  $("#remove-sound").addEventListener("click", async () => {
    if (!managedSound) return;
    $("#remove-sound").disabled = true;
    try {
      await api(`/api/presets/${managedSound.id}`, "DELETE", {});
      await refresh();
      drawSoundManager();
      notify("Saved sound removed.");
    } catch (error) {
      errorMessage("#sounds-error", error.message);
    } finally {
      $("#remove-sound").disabled = false;
    }
  });
  $("#use-managed-sound").addEventListener("click", () => {
    if (!managedSound) return;
    $("#style").value = styleForMode($("#sound-edit-style").value);
    saveDraft();
    $("#sounds-dialog").close();
    showView("studio");
  });
  updateExploration();
});
