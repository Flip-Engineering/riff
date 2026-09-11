/* Creative controls share the small app's classic-script scope. Optional provider writing uses the configured connection. */
let draftLyricsSource = "provided";
let ideaUndo = null;
let ideaBusy = false;
let ideaCancelled = false;
let managedSound = null;
let compassUsed = false;

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
  if (ideaBusy) return;
  const before = formRecipe();
  if (
    part === "all" &&
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
    const request = {
      mode: before.mode,
      theme: before.theme,
      idea_engine: before.idea_engine,
      brief: before.brief,
      writer_tokens: before.writer_tokens,
    };
    if (part === "words" || before.hold_sound) request.style = before.style;
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
    const next = { ...before };
    const changeWords =
      part === "words" || (part === "all" && !before.hold_words);
    const changeSound =
      part === "sound" || (part === "all" && !before.hold_sound);
    if (changeWords) {
      next.title = idea.title;
      if (!["free", "instrumental"].includes(before.mode)) {
        next.lyrics = idea.lyrics;
        next.lyrics_draft = idea.lyrics;
        next.lyrics_source = idea.lyrics_source;
      }
    }
    if (changeSound)
      Object.assign(next, {
        style: idea.style,
        energy: idea.energy,
        texture: idea.texture,
      });
    // Keep the user's duration, quality, seed, and score controls intact.
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
  function movePoint(event) {
    compassUsed = true;
    const rect = pad.getBoundingClientRect();
    $("#texture").value = Math.round(
      Math.max(
        0,
        Math.min(
          1,
          (((event.clientX - rect.left) / rect.width) * 360 - 26) / 308,
        ),
      ) * 100,
    );
    $("#energy").value = Math.round(
      Math.max(
        0,
        Math.min(
          1,
          (112 - ((event.clientY - rect.top) / rect.height) * 134) / 90,
        ),
      ) * 100,
    );
    updateCompass();
  }
  pad.addEventListener("pointerdown", (event) => {
    if (ideaBusy) return;
    pad.setPointerCapture(event.pointerId);
    movePoint(event);
  });
  pad.addEventListener("pointermove", (event) => {
    if (pad.hasPointerCapture(event.pointerId)) movePoint(event);
  });
  pad.addEventListener("pointerup", (event) => {
    if (!pad.hasPointerCapture(event.pointerId)) return;
    pad.releasePointerCapture(event.pointerId);
    saveDraft();
    shuffleIdea("sound", true);
  });
  for (const id of ["#energy", "#texture"]) {
    $(id).addEventListener("input", () => {
      compassUsed = true;
      updateCompass();
    });
    $(id).addEventListener("change", () => shuffleIdea("sound", true));
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
