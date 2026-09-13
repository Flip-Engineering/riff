"use strict";
(() => {
  const $ = (selector) => document.querySelector(selector);
  const token = $('meta[name="riff-installer-token"]')?.content;
  const availableStates = new Set(["idle", "installing", "cancelling", "cancelled", "error", "prepared", "ready"]);
  const activeStates = new Set(["installing", "cancelling"]);
  const stageNames = {
    checking: "Checking your setup",
    downloading: "Downloading music models",
    verifying: "Checking downloads",
    installing: "Installing Riff",
    starting: "Opening your studio",
    complete: "Downloads complete",
    app_required: "Downloads verified",
  };
  const fileStateNames = {
    pending: "Waiting",
    checking: "Checking file",
    downloading: "Downloading",
    verifying: "Checking download",
    ready: "Ready",
    complete: "Ready",
    completed: "Ready",
    verified: "Ready",
    error: "Needs another try",
    cancelled: "Stopped",
  };
  let status = null;
  let pendingAction = null;
  let connected = false;
  let requestEpoch = 0;
  let pollTimer;
  let reading = false;
  let actionNotice = "";
  let finished = false;

  function bytes(value) {
    const size = Math.max(0, Number(value) || 0);
    if (size >= 1e9) return `${(size / 1e9).toFixed(1)} GB`;
    if (size >= 1e6) return `${(size / 1e6).toFixed(1)} MB`;
    if (size >= 1e3) return `${Math.round(size / 1e3)} KB`;
    return `${Math.round(size)} B`;
  }

  function text(selector, value) {
    const element = $(selector);
    const next = String(value ?? "");
    if (element.textContent !== next) element.textContent = next;
  }

  function message(selector, value) {
    text(selector, value);
    $(selector).hidden = !value;
  }

  // A folded set of sound lines: quiet negative space, no loading spinner or
  // independent ambient motion competing with actual download progress.
  const lines = $("#sound-lines");
  for (let layer = 0; layer < 13; layer++) {
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const points = [];
    for (let sample = 0; sample <= 180; sample++) {
      const t = sample / 180 * Math.PI * 2;
      const ring = 98 + layer * 3.6;
      const fold = Math.cos(t * 2 + .7) * (24 + layer * .9);
      const x = 219 + Math.cos(t) * (ring + fold) + Math.sin(t * 2) * 17;
      const y = 233 + Math.sin(t) * (119 + layer * 1.7) + Math.cos(t * 2 + .4) * 38 + Math.cos(t) * layer * 1.1;
      points.push(`${sample ? "L" : "M"}${x.toFixed(2)} ${y.toFixed(2)}`);
    }
    path.setAttribute("d", `${points.join(" ")}Z`);
    path.setAttribute("stroke-width", layer === 4 ? "1.05" : ".65");
    path.setAttribute("opacity", String(.28 + Math.sin(layer / 12 * Math.PI) * .58));
    lines.append(path);
  }

  function renderAssets(assets) {
    const list = $("#asset-list");
    const existing = new Map([...list.children].map((row) => [row.dataset.id, row]));
    const keep = new Set();
    for (const [index, asset] of assets.entries()) {
      const id = String(asset.id ?? index);
      keep.add(id);
      let row = existing.get(id);
      if (!row) {
        row = document.createElement("li");
        row.className = "asset";
        row.dataset.id = id;
        for (const name of ["name", "size", "state"]) {
          const part = document.createElement("span");
          part.className = `asset-${name}`;
          row.append(part);
        }
        list.append(row);
      }
      row.dataset.state = String(asset.state || "pending");
      row.querySelector(".asset-name").textContent = String(asset.label || "Music model");
      const total = Number(asset.total_bytes) || 0;
      const downloaded = Math.min(total || Infinity, Math.max(0, Number(asset.downloaded_bytes) || 0));
      row.querySelector(".asset-size").textContent = total > 0 ? bytes(total) : "";
      row.querySelector(".asset-state").textContent = asset.state === "downloading" && total > 0
        ? `${bytes(downloaded)} of ${bytes(total)}`
        : fileStateNames[asset.state] || "Waiting";
    }
    for (const row of [...list.children]) if (!keep.has(row.dataset.id)) row.remove();
    $("#details-empty").hidden = assets.length > 0;
  }

  function render() {
    const state = status?.state || "idle";
    const active = activeStates.has(state);
    const total = Math.max(0, Number(status?.progress?.total_bytes) || 0);
    const downloaded = Math.max(0, Math.min(total || Infinity, Number(status?.progress?.downloaded_bytes) || 0));
    const percent = total ? Math.min(100, downloaded / total * 100) : null;
    const assets = Array.isArray(status?.assets) ? status.assets : [];
    const modelBytes = assets.filter(asset => asset.id !== "riff-application")
      .reduce((sum, asset) => sum + Math.max(0, Number(asset.total_bytes) || 0), 0);
    document.body.dataset.state = state;

    const copy = {
      idle: ["Install Riff", "A space to write, generate, and shape music.", "Install Riff"],
      installing: ["Setting up Riff", "The studio and its music models are being prepared.", "Installing…"],
      cancelling: ["Stopping setup", "Verified downloads will be kept for next time.", "Stopping…"],
      cancelled: ["Continue when you’re ready", "Verified downloads are kept. Pick up from here whenever you like.", "Continue setup"],
      error: ["Setup needs attention", "Your verified downloads are kept.", "Try again"],
      prepared: ["Music models are ready", "The Riff application package is needed to finish setup.", "Check again"],
      ready: ["Ready to make music", "Open your studio and shape your next piece.", "Open Riff"],
    }[state];
    // Development builds can verify models before the packaged application is
    // available. Never present that narrower operation as a finished install.
    if (state === "idle" && status?.app?.available === false) {
      copy[0] = "Music models for Riff";
      copy[1] = "Download the music models. The Riff application is also needed to finish setup.";
      copy[2] = "Download music models";
    }
    if (state === "installing" && status?.app?.available === false) {
      copy[0] = "Preparing music models";
      copy[1] = "Your music models are downloading and being checked.";
    }
    if (finished) {
      copy[0] = "Riff is open";
      copy[1] = "You can close this setup window.";
      copy[2] = "Installed";
    }
    text("#heading", copy[0]);
    text("#description", copy[1]);
    text("#primary-action", pendingAction === "open" ? "Opening…" : pendingAction && !active ? "Getting ready…" : copy[2]);
    $("#primary-action").hidden = active;
    $("#primary-action").disabled = finished || !connected || Boolean(pendingAction) || ((state === "error" || state === "prepared") && status?.can_retry === false);
    $("#cancel-action").hidden = !active;
    $("#cancel-action").disabled = !connected || Boolean(pendingAction) || state === "cancelling";
    text("#cancel-action", state === "cancelling" ? "Stopping…" : "Stop setup");

    const showProgress = status && state !== "idle" && state !== "ready";
    $("#progress-area").hidden = !showProgress;
    text("#stage-label", ["error", "cancelled"].includes(state) ? "Setup progress" : stageNames[status?.stage] || (active ? "Setting up" : "Setup progress"));
    text("#progress-percent", percent === null ? "" : `${Math.floor(percent)}%`);
    const progress = $("#download-progress");
    progress.dataset.indeterminate = String(percent === null && active);
    if (percent === null) progress.removeAttribute("aria-valuenow");
    else progress.setAttribute("aria-valuenow", String(Math.floor(percent)));
    const progressDescription = total ? `${bytes(downloaded)} of ${bytes(total)}` : active ? "Preparing downloads" : "";
    text("#progress-detail", progressDescription);
    progress.setAttribute("aria-valuetext", progressDescription || "Setup size pending");
    $("#progress-fill").style.setProperty("--progress", `${percent || 0}%`);
    $("#download-summary").hidden = state !== "idle";
    text("#download-summary", status ? modelBytes > 0 ? `${bytes(modelBytes)} download, including the music models.` : "The music models download as part of setup." : "Getting ready…");
    const suppliedMessage = typeof status?.error === "string" ? status.error
      : !["prepared", "ready", "cancelling"].includes(state) && typeof status?.message === "string" ? status.message : "";
    message("#setup-message", actionNotice || suppliedMessage);
    text("#details-label", state === "idle" ? "What’s included" : "Download details");
    renderAssets(assets);
  }

  async function request(path, method = "GET") {
    const response = await fetch(`/api/${path}`, {
      method,
      credentials: "same-origin",
      headers: { Accept: "application/json", "X-Riff-Installer-Token": token, ...(method === "POST" ? { "Content-Type": "application/json" } : {}) },
      ...(method === "POST" ? { body: "{}" } : {}),
      // A stalled local connection must leave a recoverable interface.
      signal: AbortSignal.timeout(20000),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof result.error === "string" ? result.error : "The installer couldn’t complete that request. Try again.");
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function accept(result) {
    const next = result?.status || result;
    if (!availableStates.has(next?.state)) return false;
    status = next;
    connected = true;
    message("#connection-message", "");
    render();
    return true;
  }

  function schedule() {
    clearTimeout(pollTimer);
    if (finished) return;
    pollTimer = setTimeout(refresh, activeStates.has(status?.state) && !document.hidden ? 750 : 4000);
  }

  async function refresh() {
    if (finished) return;
    if (reading || pendingAction) { schedule(); return; }
    reading = true;
    const epoch = requestEpoch;
    try {
      const result = await request("status");
      if (epoch !== requestEpoch) return;
      if (!accept(result)) throw new Error("The installer returned an incomplete update.");
    } catch (error) {
      if (epoch !== requestEpoch) return;
      connected = false;
      message("#connection-message", error.status === 403 || error.status === 401
        ? "This setup window has expired. Open the Riff installer again."
        : "Connection interrupted. Reconnecting…");
      render();
    } finally {
      reading = false;
      schedule();
    }
  }

  async function perform(action) {
    if (finished || pendingAction || !connected) return;
    requestEpoch += 1;
    pendingAction = action;
    actionNotice = "";
    clearTimeout(pollTimer);
    message("#action-error", "");
    render();
    try {
      const result = await request(action, "POST");
      accept(result);
      if (action === "open" && result.opened === true) {
        finished = result.closing === true;
        actionNotice = finished ? "" : "Riff is open.";
      }
    } catch (error) {
      message("#action-error", error.message);
    } finally {
      pendingAction = null;
      render();
      // Reads also reconcile timed-out actions: setup may already have begun.
      await refresh();
    }
  }

  $("#primary-action").addEventListener("click", () => {
    if (!status || activeStates.has(status.state)) return;
    perform(status.state === "ready" ? "open" : ["error", "cancelled", "prepared"].includes(status.state) ? "retry" : "install");
  });
  $("#cancel-action").addEventListener("click", () => perform("cancel"));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  if (!token || token === "RIFF_INSTALLER_TOKEN") {
    message("#connection-message", "Open the Riff installer to begin setup.");
  } else refresh();
})();
