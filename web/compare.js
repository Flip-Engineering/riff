/* Audition two takes without changing the composition draft. */
(() => {
  const panel = $("#take-comparison"), slots = { a: "", b: "" };
  let signature = "", detailSignature = "", switching = 0, looping = false, playIntent = false;
  try { Object.assign(slots, JSON.parse(localStorage.getItem("riff.comparison")) || {}); } catch {}
  const tracks = () => state.tracks.filter(track => !track.archived);
  function save() { try { localStorage.setItem("riff.comparison", JSON.stringify(slots)); } catch {} }
  function passage() {
    const start = Number($("#compare-start").value), end = Number($("#compare-end").value);
    const durations = tracks().filter(track => track.id === slots.a || track.id === slots.b).map(track => track.duration);
    const duration = durations.length ? Math.min(...durations) : 0;
    return { start, end, valid: Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start && end <= duration + .05 };
  }
  function loopState() {
    if (!passage().valid || ![slots.a, slots.b].includes(selected?.id)) looping = false;
    $("#compare-loop").setAttribute("aria-pressed", String(looping));
    $("#compare-loop").disabled = !selected || !passage().valid;
  }
  async function details() {
    const key = JSON.stringify(slots);
    if (key === detailSignature || !panel.open) return;
    detailSignature = key;
    if (!slots.a || !slots.b) { $("#compare-changes").textContent = "Choose two takes to compare their musical inputs."; return; }
    try {
      const [a, b] = await Promise.all([api(`/api/tracks/${slots.a}`), api(`/api/tracks/${slots.b}`)]);
      if (key !== detailSignature) return;
      const changes = [];
      for (const [field, label] of [["style", "Direction"], ["lyrics", "Words"], ["abc", "Score"]]) {
        const left = field === "abc" ? a.recipe.abc || a.recipe.symbolic_plan?.abc || "" : a.recipe[field] || "";
        const right = field === "abc" ? b.recipe.abc || b.recipe.symbolic_plan?.abc || "" : b.recipe[field] || "";
        if (left !== right) changes.push([label, field === "lyrics" ? "Rewritten" : field === "abc" ? "Different composition" : "New musical direction"]);
      }
      for (const [field, label] of [["seed", "Seed"], ["cfg_scale", "Guidance"], ["temperature", "Variation"], ["steps", "Detail"], ["solver", "Synthesis method"], ["max_seconds", "Length"], ["cot", "Planning"]]) {
        const left = field === "solver" ? a.recipe.solver || "midpoint" : a.recipe[field];
        const right = field === "solver" ? b.recipe.solver || "midpoint" : b.recipe[field];
        const display = value => field === "solver" ? value === "ab2" ? "Multistep" : "Midpoint" : value ?? "Default";
        if (left !== right) changes.push([label, `${display(left)} → ${display(right)}`]);
      }
      if (JSON.stringify(a.recipe.refinement || {}) !== JSON.stringify(b.recipe.refinement || {})) changes.push(["Fine tuning", "Sampling controls changed"]);
      $("#compare-changes").innerHTML = changes.length ? `<dl>${changes.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>` : '<p class="quiet-text">The musical inputs are the same.</p>';
    } catch (error) { detailSignature = ""; $("#compare-changes").textContent = error.message; }
  }
  function render() {
    const available = tracks(), ids = new Set(available.map(track => track.id));
    if (!ids.has(slots.a)) slots.a = ids.has(selected?.id) ? selected.id : available[0]?.id || "";
    if (!ids.has(slots.b) || slots.b === slots.a) slots.b = available.find(track => track.id === selected?.recipe.parent_track_id && track.id !== slots.a)?.id
      || available.find(track => track.id !== slots.a)?.id || "";
    const next = JSON.stringify([available.map(track => [track.id, track.title]), slots]);
    if (next !== signature) {
      signature = next;
      for (const slot of ["a", "b"]) {
        $("#compare-" + slot).innerHTML = '<option value="">Choose a take</option>' + available.map(track => `<option value="${track.id}">${esc(track.title)}</option>`).join("");
        $("#compare-" + slot).value = slots[slot];
      }
      details();
    }
    for (const slot of ["a", "b"]) {
      const button = $(`[data-compare="${slot}"]`);
      button.disabled = !slots[slot];
      button.setAttribute("aria-pressed", String(!!slots[slot] && selected?.id === slots[slot]));
    }
    if (!Number($("#compare-end").value) && selected) {
      const durations = available.filter(track => track.id === slots.a || track.id === slots.b).map(track => track.duration);
      $("#compare-end").value = (durations.length ? Math.min(...durations) : selected.audio.duration).toFixed(1);
    }
    loopState();
  }
  async function choose(slot) {
    const id = slots[slot];
    if (!id) return;
    const operation = ++switching, time = audio.currentTime, playing = !audio.paused;
    await selectTrack(id);
    if (operation !== switching || selected?.id !== id) return;
    try {
      audio.currentTime = Math.min(time, selected.audio.duration);
      if (playing) await audio.play();
      updatePlayback();
      $("#compare-status").textContent = `Take ${slot.toUpperCase()} · ${formatTime(audio.currentTime)}`;
      render();
    } catch (error) { notify(error.message); }
  }
  for (const slot of ["a", "b"]) {
    $("#compare-" + slot).addEventListener("change", event => {
      slots[slot] = event.target.value;
      if (slots[slot] && slots[slot] === slots[slot === "a" ? "b" : "a"]) slots[slot === "a" ? "b" : "a"] = "";
      save(); render();
    });
    $(`[data-compare="${slot}"]`).addEventListener("click", () => choose(slot));
  }
  panel.addEventListener("toggle", () => {
    if (panel.open) {
      if (selected && selected.id !== slots.a && selected.id !== slots.b) slots.a = selected.id;
      render(); details();
    }
    else { looping = false; loopState(); }
  });
  $("#compare-loop").addEventListener("click", () => {
    looping = !looping && passage().valid;
    if (looping && (audio.currentTime < passage().start || audio.currentTime >= passage().end)) audio.currentTime = passage().start;
    loopState();
  });
  for (const id of ["compare-start", "compare-end"]) $("#" + id).addEventListener("input", () => {
    if (!passage().valid) looping = false;
    loopState();
  });
  for (const [button, input] of [["compare-mark-in", "compare-start"], ["compare-mark-out", "compare-end"]]) {
    $("#" + button).addEventListener("click", () => { $("#" + input).value = audio.currentTime.toFixed(1); loopState(); });
  }
  const loop = () => {
    if (audio.paused && !(audio.ended && playIntent)) return;
    const range = passage(), duration = selected?.audio.duration || 0;
    if (looping && panel.open && range.valid && duration > range.start && (audio.currentTime >= Math.min(range.end, duration) || audio.ended)) {
      audio.currentTime = range.start;
      if (audio.paused) audio.play().catch(() => {});
    }
  };
  audio.addEventListener("timeupdate", loop);
  audio.addEventListener("ended", loop);
  audio.addEventListener("play", () => { playIntent = true; });
  audio.addEventListener("pause", () => { if (!audio.ended) playIntent = false; });
  window.RiffCompare = { render, passage: () => ({ ...passage(),
    valid: passage().valid && [slots.a, slots.b].includes(selected?.id) }) };
  render();
})();
