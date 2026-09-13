"use strict";
(() => {
  const dialog = $("#score-dialog"), source = $("#score-source"), abc = $("#abc");
  let tunes = [], selectedNote = null, audioContext = null, voices = [], finishTimer = null;
  let pendingPlan = null, planInput = "", knownPlans = "", undo = [];
  let proposal = null, revising = false;
  let writtenSeconds = 0, auditionParts = new Set(), partSignature = "";
  let candidate = null, restoring = false, lastDraft = scoreFields();
  const useScore = document.createElement("button");
  useScore.id = "score-use"; useScore.type = "button"; useScore.className = "text-button";
  useScore.textContent = "Use this score"; useScore.hidden = true;
  $("#score-status").before(useScore);

  function scoreFields() {
    const recipe = formRecipe();
    return { score_source: recipe.score_source || "", abc: recipe.abc,
      abc_draft: recipe.abc_draft, cot: recipe.cot };
  }
  function attachedStatus() {
    if (candidate?.display_error) return "Saved score attached · notation unavailable";
    return candidate?.truncated ? "An unfinished score, ready to develop." : "Saved score attached";
  }
  function updateUseScore() {
    const attached = scoreFields().score_source;
    const inUse = attached && attached === candidate?.artifact_id;
    useScore.hidden = !candidate?.artifact_id || !!inUse;
    const available = state.engine?.capabilities?.exact_score_replay === true;
    useScore.disabled = !available;
    useScore.title = available ? "" : "Available after updating the music engine.";
    if (inUse && !pendingPlan) {
      const message = attachedStatus();
      if ($("#score-status").textContent !== message) $("#score-status").textContent = message;
    }
  }
  function syncDraft() {
    const next = scoreFields();
    if (JSON.stringify(next) !== JSON.stringify(lastDraft)) {
      if (!restoring) undo.push(lastDraft);
      lastDraft = next;
      if (dialog.open) {
        stop();
        if (source.value !== next.abc_draft) source.value = next.abc_draft;
        $("#score-mode").value = next.cot === "off" ? "melody" : next.cot;
        render();
      }
    }
    if (next.score_source && next.score_source !== candidate?.artifact_id)
      candidate = { artifact_id: next.score_source, abc: next.abc_draft, cot: next.cot };
    $("#score-undo").disabled = !undo.length;
    updateUseScore();
  }
  function restore(fields, remember = true) {
    restoring = !remember;
    try { fillRecipe({ ...formRecipe(), ...fields }); }
    finally { restoring = false; }
    saveDraft();
  }
  function openSaved(item) {
    const plan = item.recipe?.symbolic_plan;
    const text = plan?.abc ?? item.recipe?.abc ?? "";
    const cot = ["melody", "full"].includes(item.recipe?.cot) ? item.recipe.cot : "melody";
    candidate = { artifact_id: plan?.artifact_id || "", abc: text, cot, truncated: !!plan?.truncated,
      token_count: plan?.token_count, display_error: !!plan?.display_error };
    const attached = scoreFields().score_source;
    restore({ score_source: candidate.artifact_id === attached ? attached : "", abc: text, abc_draft: text, cot });
    $("#score-status").textContent = `Score from ${item.title}`;
    updateUseScore();
  }
  useScore.addEventListener("click", () => {
    if (!candidate?.artifact_id || state.engine?.capabilities?.exact_score_replay !== true) return;
    const mode = scoreFields().cot;
    restore({ score_source: candidate.artifact_id, abc: "", abc_draft: candidate.abc,
      cot: mode === "off" ? candidate.cot : mode });
    $("#score-status").textContent = attachedStatus();
  });

  function overview() {
    const host = $("#score-overview"), rows = $("#score-voices");
    rows.replaceChildren(); host.hidden = !tunes.length;
    writtenSeconds = 0;
    if (!tunes.length) return;
    try {
      const sequence = tunes[0].setUpAudio({});
      const parts = sequence.tracks.map((track, index) => ({ index,
        name: track.find((event) => event.cmd === "text" && event.type === "name")?.text?.trim(),
        notes: track.filter((note) => note.cmd === "note") })).filter((part) => part.notes.length);
      const end = Math.max(sequence.totalDuration || 0, ...parts.flatMap((part) => part.notes.map((note) => note.start + note.duration)));
      writtenSeconds = end * 240 / sequence.tempo;
      $("#score-fit-duration").disabled = !!formRecipe().performance_source;
      const signature = parts.map((part) => part.index).join(",");
      if (signature !== partSignature) { auditionParts = new Set(parts.map((part) => part.index)); partSignature = signature; }
      const durations = `${formatDuration(writtenSeconds)} written · ${parts.length} ${parts.length === 1 ? "voice" : "voices"}`;
      $("#score-dimensions").textContent = durations;
      for (const [position, part] of parts.entries()) {
        const name = part.name || `Voice ${position + 1}`;
        const low = Math.min(...part.notes.map((note) => note.pitch)), high = Math.max(...part.notes.map((note) => note.pitch));
        const button = document.createElement("button"); button.type = "button"; button.className = "score-voice";
        button.setAttribute("aria-pressed", String(auditionParts.has(part.index)));
        button.setAttribute("aria-label", `${name}, ${part.notes.length} notes, toggle in tone preview`);
        const label = document.createElement("span"); label.textContent = name; button.append(label);
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"); svg.setAttribute("viewBox", "0 0 800 40"); svg.setAttribute("preserveAspectRatio", "none"); svg.setAttribute("aria-hidden", "true");
        for (const note of part.notes) {
          const mark = document.createElementNS(svg.namespaceURI, "rect");
          mark.setAttribute("x", String(note.start / end * 800)); mark.setAttribute("width", String(Math.max(1, note.duration / end * 800)));
          mark.setAttribute("y", String(3 + (high - note.pitch) / Math.max(1, high - low) * 30)); mark.setAttribute("height", "3"); mark.setAttribute("rx", "1.5"); svg.append(mark);
        }
        button.append(svg);
        button.addEventListener("click", () => {
          stop();
          if (auditionParts.has(part.index)) auditionParts.delete(part.index); else auditionParts.add(part.index);
          button.setAttribute("aria-pressed", String(auditionParts.has(part.index)));
        });
        rows.append(button);
      }
    } catch { host.hidden = true; }
  }

  function header(name, fallback = "") {
    return source.value.match(new RegExp("^" + name + ":\\s*(.*)$", "m"))?.[1] || fallback;
  }
  function render() {
    selectedNote = null;
    $("#note-editor").hidden = true;
    $("#score-empty").hidden = !!source.value.trim();
    const attached = scoreFields().score_source;
    const sameCandidate = candidate?.abc === source.value && (!attached || candidate.artifact_id === attached);
    $("#score-empty").textContent = sameCandidate && candidate.display_error
      ? "Notation is unavailable. You can still use this saved score."
      : attached ? sameCandidate && candidate.token_count === 0
        ? "A blank score, ready for a new performance."
        : "Saved score attached. Its notation is not shown."
      : "Compose a score from your current sound and lyrics, import one, or start a sketch.";
    $("#score-tempo").value = header("Q").match(/(?:=|^)(\d+(?:\.\d+)?)\s*$/)?.[1] || "";
    $("#score-key").value = header("K");
    $("#score-meter").value = header("M");
    try {
      tunes = source.value.trim() ? ABCJS.renderAbc("score-notation", source.value, {
        responsive: "resize", add_classes: true, foregroundColor: "currentColor", selectTypes: ["note"],
        staffwidth: Math.max(240, $("#score-notation").clientWidth - 30),
        wrap: { minSpacing: 1.8, maxSpacing: 2.7, preferredMeasuresPerLine: 4 },
        clickListener: (element) => {
          if (element.startChar == null || element.endChar == null) return;
          const text = source.value.slice(element.startChar, element.endChar).trimEnd();
          selectedNote = { start: element.startChar, end: element.startChar + text.length, text };
          $("#note-token").value = selectedNote.text;
          $("#note-editor").hidden = false;
          source.setSelectionRange(selectedNote.start, selectedNote.end);
          $("#score-note-description").textContent = element.pitches?.length > 1 ? "Selected chord" : element.rest ? "Selected rest" : "Selected note";
        },
      }) : [];
      // Make the whole note group a usable target, including the space beside
      // its stem. Staff lines and tiny glyphs otherwise swallow taps.
      for (const note of document.querySelectorAll("#score-notation .abcjs-note")) {
        const box = note.getBBox(), hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        const width = Math.max(18, box.width), height = Math.max(24, box.height);
        hit.setAttribute("x", box.x + (box.width - width) / 2);
        hit.setAttribute("y", box.y + (box.height - height) / 2);
        hit.setAttribute("width", width); hit.setAttribute("height", height);
        hit.setAttribute("fill", "transparent"); hit.setAttribute("pointer-events", "all");
        note.prepend(hit);
      }
      if (!source.value.trim()) $("#score-notation").replaceChildren();
      const warnings = tunes.flatMap((tune) => tune.warnings || []);
      // Parser warnings contain markup; display as text, never insert untrusted HTML.
      $("#score-warning").textContent = warnings.map((warning) => warning.replace(/<[^>]+>/g, "")).join("\n");
    } catch (error) { $("#score-warning").textContent = error.message; tunes = []; }
    $("#audition-score").disabled = !tunes.length;
    $("#export-midi").disabled = !tunes.length;
    overview();
  }
  function replace(value, remember = true, fresh = false) {
    const previous = scoreFields();
    restore({ score_source: !fresh && value === previous.abc_draft ? previous.score_source : "",
      abc: value, abc_draft: value,
      cot: value.trim() && previous.cot === "off" ? "melody" : previous.cot }, remember);
  }
  function open() {
    source.value = abc.value;
    $("#score-mode").value = $("#planning").value === "off" ? "melody" : $("#planning").value;
    dialog.showModal();
    syncDraft(); render();
  }
  $("#score-open").addEventListener("click", open);
  $("#score-open-player").addEventListener("click", async () => {
    const track = selected;
    if (!track?.recipe?.symbolic_plan?.artifact_id && !track?.recipe?.symbolic_plan?.abc && !track?.recipe?.abc) return;
    open();
    openSaved(track);
  });
  $("#score-mode").addEventListener("change", () => restore({ ...scoreFields(), cot: $("#score-mode").value }));
  source.addEventListener("input", () => replace(source.value));
  $("#apply-score-header").addEventListener("click", () => {
    let value = source.value || "X:1\nT:" + ($("#title").value.trim() || "Untitled") + "\nL:1/8\nK:C\n";
    const bpm = Number($("#score-tempo").value);
    if ($("#score-tempo").value && (!Number.isFinite(bpm) || bpm <= 0)) { $("#score-warning").textContent = "Tempo must be positive."; return; }
    for (const [key, text] of [["Q", bpm ? "1/4=" + bpm : ""], ["M", $("#score-meter").value.trim()], ["K", $("#score-key").value.trim()]]) {
      if (!text) continue;
      const pattern = new RegExp("^" + key + ":.*$", "m");
      value = pattern.test(value) ? value.replace(pattern, key + ":" + text) : value.replace(/^K:/m, key + ":" + text + "\nK:");
    }
    replace(value);
  });
  $("#apply-note").addEventListener("click", () => {
    if (!selectedNote || source.value.slice(selectedNote.start, selectedNote.end) !== selectedNote.text) return;
    replace(source.value.slice(0, selectedNote.start) + $("#note-token").value + source.value.slice(selectedNote.end));
  });
  for (const [id, steps] of [["transpose-down", -1], ["transpose-up", 1]]) $("#" + id).addEventListener("click", () => {
    if (!tunes.length) return;
    try { replace(ABCJS.strTranspose(source.value, tunes, steps)); }
    catch (error) { $("#score-warning").textContent = error.message; }
  });
  $("#score-undo").addEventListener("click", () => { if (undo.length) restore(undo.pop(), false); });
  $("#score-fit-duration").addEventListener("click", () => {
    if (writtenSeconds > 0) {
      $("#duration").value = Math.ceil(writtenSeconds);
      saveDraft(); $("#score-status").textContent = "Generation duration follows the written score.";
    }
  });
  $("#new-score").addEventListener("click", () => replace("X:1\nT:" + ($("#title").value.trim() || "Untitled") + "\nM:4/4\nL:1/8\nQ:1/4=108\nK:Dm\nD2 F2 A2 G2 | F2 E2 D4 |\n", true, true));

  function stop() {
    for (const voice of voices) { try { voice.stop(); } catch {} }
    voices = [];
    if (audioContext) { audioContext.close(); audioContext = null; }
    clearTimeout(finishTimer);
    $("#stop-score").disabled = true;
  }
  $("#audition-score").addEventListener("click", async () => {
    stop();
    if (!tunes.length) return;
    try {
      const sequence = tunes[0].setUpAudio({});
      const secondsPerWhole = 240 / sequence.tempo;
      audioContext = new AudioContext();
      await audioContext.resume();
      const output = audioContext.createGain(); output.gain.value = .13 / Math.sqrt(Math.max(1, sequence.tracks.length)); output.connect(audioContext.destination);
      let duration = 0;
      for (const [part, track] of sequence.tracks.entries()) for (const note of track) {
        if (!auditionParts.has(part)) continue;
        if (note.cmd !== "note") continue;
        const start = audioContext.currentTime + .04 + note.start * secondsPerWhole;
        const length = Math.max(.01, (note.duration - (note.gap || 0)) * secondsPerWhole);
        const oscillator = audioContext.createOscillator(), gain = audioContext.createGain();
        oscillator.type = "triangle"; oscillator.frequency.value = 440 * 2 ** ((note.pitch - 69) / 12);
        gain.gain.setValueAtTime(0, start); gain.gain.linearRampToValueAtTime(note.volume / 127, start + Math.min(.01, length / 3)); gain.gain.linearRampToValueAtTime(0, start + length);
        oscillator.connect(gain); gain.connect(output); oscillator.start(start); oscillator.stop(start + length); voices.push(oscillator);
        duration = Math.max(duration, note.start * secondsPerWhole + length);
      }
      $("#stop-score").disabled = false;
      finishTimer = setTimeout(stop, (duration + .2) * 1000);
    } catch (error) { stop(); $("#score-warning").textContent = error.message; }
  });
  $("#stop-score").addEventListener("click", stop);
  $("#revise-score").addEventListener("click", async () => {
    revising = true;
    window.RiffUpdate?.render(state);
    $("#revise-score").disabled = true; $("#stop-score-edit").hidden = false;
    $("#score-edit-status").textContent = "Writing a score revision…";
    try {
      const result = await api("/api/composition/revise", "POST", { ...formRecipe(),
        cot: $("#score-mode").value, brief: $("#score-change").value });
      if (result.cancelled) { $("#score-edit-status").textContent = "Writing stopped"; return; }
      proposal = result;
      $("#score-proposal-summary").textContent = result.summary;
      $("#score-proposal").hidden = false;
      ABCJS.renderAbc("score-proposal-notation", result.abc, { responsive: "resize", foregroundColor: "currentColor" });
      $("#score-edit-status").textContent = "Suggested edit ready";
    } catch (error) { $("#score-edit-status").textContent = error.message; }
    finally { revising = false; $("#revise-score").disabled = false; $("#stop-score-edit").hidden = true; window.RiffUpdate?.render(state); }
  });
  $("#apply-score-proposal").addEventListener("click", () => { if (proposal) replace(proposal.abc); $("#score-proposal").hidden = true; proposal = null; window.RiffUpdate?.render(state); });
  $("#dismiss-score-proposal").addEventListener("click", () => { $("#score-proposal").hidden = true; proposal = null; window.RiffUpdate?.render(state); });
  $("#stop-score-edit").addEventListener("click", async () => {
    try { await api("/api/inspiration/cancel", "POST", {}); }
    catch (error) { $("#score-edit-status").textContent = error.message; }
  });
  dialog.addEventListener("close", stop);
  function download(data, name, type) {
    const link = document.createElement("a"), url = URL.createObjectURL(new Blob([data], { type }));
    link.href = url; link.download = name; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  $("#export-abc").addEventListener("click", () => download(source.value, "riff-score.abc", "text/vnd.abc"));
  $("#export-midi").addEventListener("click", () => {
    try { const files = ABCJS.synth.getMidiFile(source.value, { midiOutputType: "binary" }); download(files[0], "riff-score.mid", "audio/midi"); }
    catch (error) { $("#score-warning").textContent = error.message; }
  });
  $("#import-abc").addEventListener("change", async (event) => {
    if (event.target.files[0]) replace(await event.target.files[0].text());
    event.target.value = "";
  });
  $("#compose-score").addEventListener("click", async () => {
    try {
      const before = JSON.stringify(scoreFields());
      const recipe = { ...formRecipe(), cot: $("#score-mode").value, score_source: "", abc: "", abc_draft: "", performance_source: "", acoustic_source: "", decoder: {}, render_mode: "plan" };
      const job = await api("/api/plans", "POST", recipe);
      const unchanged = JSON.stringify(scoreFields()) === before;
      if (unchanged) restore({ score_source: "", abc: "", abc_draft: "", cot: recipe.cot });
      pendingPlan = job.id; planInput = unchanged ? JSON.stringify(scoreFields()) : null;
      $("#score-status").textContent = "Composing a score…";
      await refresh();
    } catch (error) { $("#score-status").textContent = error.message; }
  });
  function update(next) {
    const plans = next.plans || [], signature = JSON.stringify(plans.map((plan) => [plan.id, plan.recipe?.symbolic_plan, plan.recipe?.cot]));
    if (signature !== knownPlans) {
      knownPlans = signature; $("#score-history").replaceChildren();
      for (const plan of plans) {
        const button = document.createElement("button"); button.type = "button"; button.className = "score-history-item";
        button.textContent = `${plan.title} · ${new Date(plan.finished * 1000).toLocaleString()}`;
        button.addEventListener("click", () => openSaved(plan));
        $("#score-history").append(button);
      }
    }
    if (pendingPlan) {
      const plan = plans.find((item) => item.id === pendingPlan), job = next.jobs.find((item) => item.id === pendingPlan);
      if (plan) {
        if (JSON.stringify(scoreFields()) === planInput) openSaved(plan);
        $("#score-status").textContent = plan.recipe.symbolic_plan.truncated ? "An unfinished score, ready to develop." : "Score ready";
        pendingPlan = null;
      } else if (job && ["failed", "cancelled", "interrupted"].includes(job.status)) { $("#score-status").textContent = job.error || "Composition stopped"; pendingPlan = null; }
    }
    $("#score-open-player").hidden = !(selected?.recipe?.symbolic_plan?.artifact_id || selected?.recipe?.symbolic_plan?.abc || selected?.recipe?.abc);
    syncDraft();
  }
  window.RiffScore = { update, open, syncDraft,
    refreshWait: () => revising ? "Your score is still being written." : proposal ? "Use or dismiss the score suggestion to refresh." : "" };
  let width = 0;
  new ResizeObserver(() => {
    const next = $("#score-notation").clientWidth;
    if (dialog.open && next !== width) { width = next; render(); }
  }).observe($("#score-notation"));
})();
