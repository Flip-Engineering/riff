/* Recorded origins, shared with agents through the read-only lineage API. */
(() => {
  const panel = $("#detail-lineage"), content = $("#lineage-content");
  let selected = null, revision = 0;
  function link(node) {
    return node.missing ? esc(node.title) : `<a href="/?recording=${encodeURIComponent(node.id)}#studio" target="_blank" rel="noopener" aria-label="${esc(`Open ${node.title} in a new tab`)}">${esc(node.title)}</a>`;
  }
  const value = (change, side) => !change[`${side}_recorded`] ? "Not recorded"
    : change[side] === null ? "Default / unset" : change.field === "max_seconds" ? `${change[side]} seconds` : typeof change[side] === "object"
      ? JSON.stringify(change[side], null, 2) : String(change[side]);
  async function load() {
    if (!panel.open || !selected) return;
    const current = ++revision, id = selected;
    content.textContent = "Loading related takes…";
    try {
      const graph = await api(`/api/tracks/${id}/lineage`);
      if (current !== revision || id !== selected) return;
      const nodes = new Map(graph.nodes.map(node => [node.id, node]));
      content.innerHTML = (graph.has_cycle ? '<p class="control-hint">These recorded links contain a cycle. Original relationships are shown unchanged.</p>' : "")
        + (graph.edges.length ? "" : '<p class="control-hint">No parent or variations have been recorded for this take.</p>')
        + `<ul class="lineage-takes">${graph.nodes.map(node => {
          const origins = [...new Map(graph.edges.filter(edge => edge.child === node.id).map(edge => [edge.parent, edge])).values()];
          const relationship = edge => graph.edges.some(other => other.child === edge.child && other.parent === edge.parent && other.kind !== edge.kind)
            ? "Variation and performance from" : edge.kind === "performance" ? "Performance from" : "Variation of";
          return `<li${node.id === id ? ' aria-current="true"' : ""}><p>${link(node)}${node.id === id ? " (this take)" : ""}${node.archived ? " (archived)" : ""}</p>${origins.map(edge =>
            `<div class="lineage-origin"><p>${relationship(edge)} ${link(nodes.get(edge.parent))}</p>${edge.changes === null
              ? '<p class="control-hint">Source recipe unavailable; changes cannot be compared.</p>'
              : edge.changes.length ? `<details><summary>Changed inputs (${edge.changes.length})</summary><dl>${edge.changes.map(change =>
                `<div><dt>${esc(change.label)}</dt><dd><span>From</span><pre>${esc(value(change, "before"))}</pre><span>To</span><pre>${esc(value(change, "after"))}</pre></dd></div>`).join("")}</dl></details>`
                : '<p class="control-hint">The compared recorded inputs are unchanged.</p>'}</div>`).join("")}</li>`;
        }).join("")}</ul><p class="control-hint">Links show recorded origins, not inferred musical similarity. Open a take in a new tab to keep your current draft and notes.</p>`;
    } catch (error) { if (current === revision) content.textContent = error.message; }
  }
  panel.addEventListener("toggle", load);
  window.RiffLineage = { details(track) { revision++; selected = track.id; content.replaceChildren(); void load(); } };
})();
