/* Minimal layered DAG renderer (no dependencies).
 * layout = {nodes: [{id, type, title, agent, skill, when, layer}], edges: [{from, to, kind}]}
 * statuses = optional {nodeId: "succeeded" | "running" | ...}
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const W = 176, H = 58, GX = 92, GY = 22, PAD = 24;

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (parent) parent.appendChild(e);
    return e;
  }

  function truncate(s, n) { s = s || ""; return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  function render(container, layout, statuses) {
    container.replaceChildren();
    if (!layout || !layout.nodes || !layout.nodes.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "No nodes";
      container.appendChild(empty);
      return;
    }
    const layers = {};
    layout.nodes.forEach(n => (layers[n.layer] = layers[n.layer] || []).push(n));
    const maxRows = Math.max(...Object.values(layers).map(l => l.length));
    const nLayers = Math.max(...layout.nodes.map(n => n.layer)) + 1;
    const hasRework = layout.edges.some(e => e.kind === "rework");
    const width = PAD * 2 + nLayers * W + (nLayers - 1) * GX;
    const height = PAD * 2 + maxRows * H + (maxRows - 1) * GY + (hasRework ? 46 : 0);
    const pos = {};
    Object.entries(layers).forEach(([layer, nodes]) => {
      const colH = nodes.length * H + (nodes.length - 1) * GY;
      const top = PAD + ((maxRows * H + (maxRows - 1) * GY) - colH) / 2;
      nodes.forEach((n, i) => { pos[n.id] = { x: PAD + layer * (W + GX), y: top + i * (H + GY) }; });
    });

    const svg = el("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
      "aria-label": `Graph ${layout.id || ""} with ${layout.nodes.length} nodes` });
    const defs = el("defs", {}, svg);
    for (const [id, cls] of [["arrow", "var(--neutral)"], ["arrow-rework", "var(--warn)"]]) {
      const m = el("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" }, defs);
      el("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: cls }, m);
    }

    const byId = Object.fromEntries(layout.nodes.map(n => [n.id, n]));
    layout.edges.forEach(e => {
      const a = pos[e.from], b = pos[e.to];
      if (!a || !b) return;
      if (e.kind === "rework") {
        // down from the gate, along a channel under all nodes, up the gap left of the target, into its left side
        const bottom = height - PAD / 2 - 10, cx = b.x - 22, ty = b.y + H * 0.75;
        const d = `M ${a.x + W / 2} ${a.y + H} L ${a.x + W / 2} ${bottom} L ${cx} ${bottom} L ${cx} ${ty} L ${b.x - 2} ${ty}`;
        el("path", { d, class: "gedge rework", "marker-end": "url(#arrow-rework)" }, svg);
        const t = el("text", { x: (a.x + W / 2 + cx) / 2, y: bottom - 5, "text-anchor": "middle", class: "gedge-label" }, svg);
        t.textContent = `rework ×${e.max_loops}`;
      } else {
        const x0 = a.x + W, y0 = a.y + H / 2, x1 = b.x - 2, y1 = b.y + H / 2, mx = (x0 + x1) / 2;
        el("path", { d: `M ${x0} ${y0} C ${mx} ${y0}, ${mx} ${y1}, ${x1} ${y1}`, class: "gedge", "marker-end": "url(#arrow)" }, svg);
        const target = byId[e.to];
        if (target && target.when) {
          const t = el("text", { x: mx, y: (y0 + y1) / 2 - 5, "text-anchor": "middle", class: "gedge-label" }, svg);
          t.textContent = "if " + truncate(target.when, 16);
        }
      }
    });

    layout.nodes.forEach(n => {
      const p = pos[n.id];
      const status = statuses && statuses[n.id];
      const g = el("g", { class: `gnode ${status || ""}`, transform: `translate(${p.x},${p.y})`, tabindex: 0 }, svg);
      const title = el("title", {}, g);
      title.textContent = `${n.id} (${n.type}${n.agent ? ": " + n.agent : ""})${status ? " — " + status : ""}`;
      if (n.type === "gate") {
        el("polygon", { points: `14,0 ${W - 14},0 ${W},${H / 2} ${W - 14},${H} 14,${H} 0,${H / 2}` }, g);
      } else if (n.type === "human") {
        el("polygon", { points: `12,0 ${W},0 ${W - 12},${H} 0,${H}` }, g);
      } else {
        el("rect", { width: W, height: H, rx: 10 }, g);
      }
      const t1 = el("text", { x: W / 2, y: 23, "text-anchor": "middle", "font-weight": 600 }, g);
      t1.textContent = truncate(n.title || n.id, 24);
      const sub = n.type === "agent" ? (n.agent + (n.skill ? " · /" + n.skill : "")) : n.type;
      const t2 = el("text", { x: W / 2, y: 41, "text-anchor": "middle", class: "sub" }, g);
      t2.textContent = truncate(sub, 28);
    });
    container.appendChild(svg);
  }

  window.AgGraph = { render };
})();
