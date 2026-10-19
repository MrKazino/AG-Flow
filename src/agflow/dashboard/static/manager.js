/* AG-Flow manager pages: workflows, proposed improvements, sync & updates, workspace switch.
   Loaded after app.js, which exposes its helpers as window.AgApp. Same rule as app.js: the DOM is built with h() from
   text nodes, never from HTML strings. */
(function () {
  "use strict";
  const A = window.AgApp;
  if (!A) return;
  const { h, api, act, badge, table, toast, openDrawer, closeDrawer, route, pages, titles } = A;

  titles.workflows = "Workflows";
  titles.sync = "Sync & updates";

  const when = (t) => (t ? new Date(t * 1000).toLocaleString() : "never");
  const rate = (runs) => (runs.success_rate === null || runs.success_rate === undefined
    ? "no finished runs" : `${Math.round(runs.success_rate * 100)}% of ${runs.succeeded + runs.failed}`);
  const statusClass = { ok: "ok", drift: "warn", breaking: "err", unknown: "skipped" };
  const FIX_LINK = {
    "workflow.agent.missing": ["#/agents", "Agents"], "workflow.agent.disabled": ["#/agents", "Agents"],
    "workflow.skill.missing": ["#/skills", "Skills"], "workflow.skill.disabled": ["#/skills", "Skills"],
    "workflow.mcp.orchestrator": ["#/mcp", "MCP servers"],
  };

  function diffView(text) {
    const pre = h("pre", { class: "content diff" });
    for (const line of String(text || "").split("\n")) {
      const kind = line.startsWith("+++") || line.startsWith("---") ? "meta" : line.startsWith("@@") ? "hunk"
        : line.startsWith("+") ? "add" : line.startsWith("-") ? "del" : "";
      pre.appendChild(h("span", { class: "dl " + kind }, line + "\n"));
    }
    return pre;
  }

  const here = (id) => "#/workflows/" + encodeURIComponent(id);

  // ---------- workflows ------------------------------------------------------------------------------------
  pages.workflows = async (arg) => (arg ? workflowDetail(arg) : workflowList());

  async function workflowList() {
    const rows = await api("/workflows");
    return h("div", { class: "card" },
      h("p", { class: "muted" }, "Each workflow with what it needs, whether that is installed, and how its runs went. "
        + "Open one for its steps, files, lessons and proposed improvements."),
      table([
        { label: "Workflow", render: r => [h("strong", {}, r.id), h("div", { class: "small muted" }, r.title || "")] },
        { label: "Source", render: r => h("span", { class: "chip" }, r.source) },
        { label: "Steps", render: r => r.nodes },
        { label: "Needs", render: r => `${r.agents.length} agents · ${r.skills.length} skills` },
        { label: "Health", render: r => (r.error ? badge("invalid", "err")
          : [r.problems.error ? badge(`${r.problems.error} error${r.problems.error === 1 ? "" : "s"}`, "err") : null,
            r.problems.warning ? badge(`${r.problems.warning} warning${r.problems.warning === 1 ? "" : "s"}`, "warn") : null,
            !r.problems.error && !r.problems.warning ? badge("ready", "ok") : null]) },
        { label: "Runs", render: r => (r.runs.recorded ? rate(r.runs) : "none yet") },
        { label: "Waiting", render: r => [r.active ? badge(`${r.active} running`, "info") : null,
          r.proposals_pending ? badge(`${r.proposals_pending} proposal${r.proposals_pending === 1 ? "" : "s"}`, "warn") : null] },
      ], rows, r => { location.hash = here(r.id); }));
  }

  async function workflowDetail(id) {
    const d = await api("/workflows/" + encodeURIComponent(id));
    if (d.error && !d.nodes) {
      return h("div", { class: "card" }, h("div", { class: "notice err" }, d.error), h("p", {}, h("a", { href: "#/workflows" }, "← All workflows")));
    }
    const lookFor = h("button", { type: "button", onclick: async () => {
      const r = await act(() => api(`/workflows/${encodeURIComponent(id)}/propose`, { method: "POST" }));
      toast(r.proposal ? "A proposal is ready below." : "Nothing recurs often enough to propose a change yet.");
      route();
    } }, "Look for improvements");
    const head = h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", { style: "margin:0" }, d.title || d.id), h("span", { class: "chip" }, d.source),
        d.active_runs.length ? badge(`${d.active_runs.length} running`, "info") : null, h("span", { class: "spacer" }),
        h("a", { class: "btn ghost", href: `#/graphs/${encodeURIComponent(id)}` }, "Open the graph"), lookFor),
      d.description ? h("p", { class: "muted" }, d.description) : null,
      h("p", { class: "small muted" }, h("code", {}, d.path)),
      h("p", { class: "small" }, h("a", { href: "#/workflows" }, "← All workflows")));

    const problemsCard = d.problems.length ? h("div", { class: "card" }, h("h2", {}, "What stands in the way"),
      h("ul", { class: "plain" }, d.problems.map(p => h("li", { class: "row" }, badge(p.level), h("span", {}, p.message),
        FIX_LINK[p.code] ? h("a", { href: FIX_LINK[p.code][0] }, FIX_LINK[p.code][1] + " →") : null)))) : null;

    const state = (it) => (!it.found ? badge("not installed", "err") : it.enabled ? badge("ready", "ok") : badge("disabled", "warn"));
    const needRows = [
      ...Object.entries(d.needs.agents).map(([name, it]) => ({ kind: "agent", name, it, used: it.nodes.join(", "), page: "agents" })),
      ...Object.entries(d.needs.skills).map(([name, it]) => ({ kind: "skill", name, it, used: it.nodes.join(", "), page: "skills" })),
      ...Object.entries(d.needs.mcp).map(([name, it]) => ({ kind: "MCP server", name, it, used: "driving the workflow from chat", page: "mcp" })),
    ];
    const needs = h("div", { class: "card" }, h("h2", {}, "What it needs"), table([
      { label: "Kind", render: r => r.kind },
      { label: "Name", render: r => h("strong", {}, r.name) },
      { label: "Status", render: r => state(r.it) },
      { label: "Used by", cls: "desc", render: r => r.used },
      { label: "Where", render: r => (r.it.scope ? h("span", { class: "chip" }, r.it.scope) : null) },
      { label: "", render: r => h("a", { href: "#/" + r.page }, "Manage →") },
    ], needRows));

    const steps = h("div", { class: "card" }, h("h2", {}, "Steps"), table([
      { label: "Step", render: n => h("strong", {}, n.id) },
      { label: "Type", render: n => n.type },
      { label: "Agent", render: n => n.agent || "" },
      { label: "Skill", render: n => n.skill || "" },
      { label: "Evidence", render: n => (n.evidence ? badge(n.evidence, n.evidence === "required" ? "ok" : n.evidence === "advisory" ? "info" : "skipped") : "") },
      { label: "Time limit", render: n => (n.timeout_min ? `${n.timeout_min} min` : "") },
      { label: "Retries", render: n => (n.retries === null ? "" : n.retries) },
    ], d.nodes));

    const files = d.needs.files.length ? h("div", { class: "card" }, h("h2", {}, "Files it produces or checks"), table([
      { label: "Step", render: f => f.node },
      { label: "", render: f => badge(f.kind, "skipped") },
      { label: "Path", cls: "desc", render: f => h("code", {}, f.path) },
      { label: "Now", render: f => (f.templated ? h("span", { class: "muted small" }, "per run") : f.exists ? badge("exists", "ok") : badge("not yet", "skipped")) },
    ], d.needs.files)) : null;

    const runs = h("div", { class: "card" }, h("h2", {}, "Runs"),
      d.runs.recorded ? h("p", {}, `${d.runs.recorded} finished run(s) remembered; ${rate(d.runs)} succeeded.`)
        : h("p", { class: "muted" }, "No finished runs remembered yet: they are recorded automatically when a run ends."),
      d.runs.recorded ? table([
        { label: "Run", render: r => h("a", { href: `#/runs/${encodeURIComponent(r.run)}` }, r.run) },
        { label: "Status", render: r => badge(r.status) },
        { label: "Steps retried", render: r => r.retried || "" },
        { label: "Finished", render: r => when(r.at) },
      ], d.runs.recent) : null);

    const lessons = h("div", { class: "card" }, h("h2", {}, "What earlier runs taught it"),
      d.lessons.length ? table([
        { label: "Runs", render: p => `${p.runs}×` },
        { label: "What", render: p => h("code", {}, p.kind) },
        { label: "Step", render: p => p.node || "(whole run)" },
        { label: "Detail", cls: "desc", render: p => p.detail },
      ], d.lessons) : h("p", { class: "muted" }, "Nothing has gone wrong twice yet."));

    const improvements = h("div", { class: "card" }, h("h2", {}, "Proposed improvements"),
      h("p", { class: "muted small" }, "Changes are only proposed when the same problem shows up in at least two runs. "
        + "Nothing is applied until you say so; every change is backed up and can be undone."),
      d.proposals.length ? d.proposals.map(p => proposalCard(p, id)) : h("p", { class: "muted" }, "None right now."));

    return h("div", {}, head, problemsCard, needs, steps, files, runs, lessons, improvements);
  }

  function proposalCard(p, graphId) {
    const pending = p.status === "pending";
    const chosen = new Set(p.changes.map(c => c.id));
    const applyBtn = h("button", { type: "button" });
    const refresh = () => {
      applyBtn.disabled = !chosen.size;
      applyBtn.textContent = chosen.size === p.changes.length ? "Apply all changes" : `Apply ${chosen.size} selected`;
    };
    const list = h("ul", { class: "plain" }, p.changes.map(c => h("li", { style: "margin-bottom:6px" },
      pending ? h("label", { class: "row" }, h("input", { type: "checkbox", checked: true, "aria-label": c.text, onchange: e => {
        if (e.target.checked) chosen.add(c.id); else chosen.delete(c.id);
        refresh();
      } }), h("span", {}, c.text)) : h("span", {}, c.text),
      h("div", { class: "small muted" }, c.reason))));
    const reason = h("input", { placeholder: "why not (optional)", "aria-label": "Reason for declining", style: "min-width:200px" });
    applyBtn.addEventListener("click", async () => {
      const wording = p.creates_copy ? "\n\nThis writes a workspace copy of a bundled workflow; later AG-Flow updates to the bundled one will not reach this workspace until the copy is removed." : "";
      if (!confirm(`Apply ${chosen.size} change(s) to ${graphId}?\n\nA backup is kept and you can undo it.${wording}`)) return;
      await act(() => api(`/proposals/${encodeURIComponent(p.id)}/apply`, { method: "POST", body: { only: [...chosen] } }), "Applied");
      route();
    });
    refresh();
    const buttons = pending ? h("div", { class: "row", style: "margin-top:10px" }, applyBtn, h("span", { class: "spacer" }), reason,
      h("button", { class: "ghost", type: "button", onclick: async () => {
        await act(() => api(`/proposals/${encodeURIComponent(p.id)}/reject`, { method: "POST", body: { reason: reason.value.trim() } }), "Declined: it will not be proposed again for 30 days");
        route();
      } }, "Decline"))
      : p.status.startsWith("applied") ? h("div", { class: "row", style: "margin-top:10px" },
        h("button", { class: "ghost", type: "button", onclick: async () => {
          if (!confirm("Undo this change? The workflow file goes back to how it was.")) return;
          try {
            await api(`/workflows/${encodeURIComponent(graphId)}/undo`, { method: "POST", body: { force: false } });
          } catch (e) {
            if (!/edited after/.test(e.message) || !confirm(e.message + "\n\nUndo anyway and lose those edits?")) { toast(e.message, true); return; }
            await act(() => api(`/workflows/${encodeURIComponent(graphId)}/undo`, { method: "POST", body: { force: true } }));
          }
          toast("Undone");
          route();
        } }, "Undo")) : null;
    return h("div", { class: "proposal" },
      h("div", { class: "row" }, h("strong", {}, `Proposal ${p.id}`), badge(p.status, pending ? "warn" : p.status.startsWith("applied") ? "ok" : "skipped"),
        h("span", { class: "muted small" }, when(p.created_at)),
        p.creates_copy && pending ? h("span", { class: "chip", title: p.target }, "writes a workspace copy") : null),
      list,
      h("details", {}, h("summary", { class: "small" }, "Show the exact change"), diffView(p.diff)),
      buttons);
  }

  // ---------- sync & updates -------------------------------------------------------------------------------
  pages.sync = async () => {
    const hl = await api("/health");
    const s = hl.sync;
    const sync = h("div", { class: "card" }, h("h2", {}, "Antigravity compatibility"),
      s ? h("div", { class: "row" }, badge(s.status, statusClass[s.status] || "skipped"),
        h("span", {}, `Antigravity ${s.agy_version || "not found"}; AG-Flow was verified against ${s.verified_agy_version || "unknown"}`),
        h("span", { class: "muted small" }, `checked ${s.checked_at || ""}`))
        : h("p", { class: "muted" }, "Not checked yet."),
      s && s.findings.length ? h("ul", { class: "plain", style: "margin-top:10px" }, s.findings.map(f => h("li", { style: "margin-bottom:12px" },
        h("div", { class: "row" }, badge(f.level, f.level === "breaking" ? "err" : f.level === "drift" ? "warn" : "skipped"),
          f.component ? h("span", { class: "chip" }, f.component) : null, h("span", {}, f.message)),
        f.action ? h("div", { class: "small muted", style: "margin-left:8px" }, f.action) : null))) : null,
      h("div", { class: "row", style: "margin-top:12px" },
        h("button", { type: "button", onclick: async () => {
          const r = await act(() => api("/sync/check", { method: "POST" }), "Checked");
          toast(`Antigravity compatibility: ${r.status}`);
          route();
        } }, "Check now"),
        h("button", { class: "ghost", type: "button", onclick: () => verifyDrawer() }, "Verify and accept…")),
      h("p", { class: "small muted" }, "Checking only reads Antigravity's version and the names its documentation defines. "
        + "Accepting records this Antigravity as verified after AG-Flow's own doctor and hook probe pass; it changes nothing else."));

    const u = hl.update;
    const update = h("div", { class: "card" }, h("h2", {}, "AG-Flow updates"),
      h("div", { class: "row" }, h("span", {}, `You have ${u.current}.`),
        u.available ? badge(`${u.latest} available`, "warn") : u.latest ? badge("up to date", "ok") : badge("not checked", "skipped"),
        h("span", { class: "muted small" }, `channel ${u.channel} · background checks ${u.auto_check ? "on" : "off"} · last checked ${when(u.checked_at)}`)),
      u.error ? h("div", { class: "notice err", style: "margin-top:8px" }, "The last check failed: " + u.error) : null,
      hl.latest && hl.latest.notes ? h("details", { style: "margin-top:8px" }, h("summary", { class: "small" }, `Release notes for ${hl.latest.version}`),
        h("pre", { class: "content" }, hl.latest.notes)) : null,
      h("div", { class: "row", style: "margin-top:12px" },
        h("button", { type: "button", onclick: async () => {
          const r = await act(() => api("/updates/check", { method: "POST", body: { force: true } }));
          toast(r.status === "error" ? "Could not check: " + r.error : r.status === "available" ? `AG-Flow ${r.latest.version} is available` : "Up to date", r.status === "error");
          route();
        } }, "Check now"),
        h("button", { class: "ghost", type: "button", onclick: () => planDrawer() }, "Show the plan…")),
      h("p", { class: "small muted" }, "Applying an update is done from a terminal on purpose (it replaces the program this page runs in): ",
        h("code", {}, "agflow update apply"), ". It asks first, backs everything up, keeps the files you edited and undoes itself if anything fails."));

    const sidecars = h("div", { class: "card" }, h("h2", {}, "Inside Antigravity"),
      h("ul", { class: "plain" }, hl.sidecars.map(sc => h("li", { style: "margin-bottom:10px" },
        h("div", { class: "row" }, h("strong", {}, sc.label), h("span", { class: "chip" }, sc.id),
          sc.ready ? badge("on", "ok") : !sc.installed ? badge("not installed", "skipped") : !sc.valid ? badge("invalid", "err") : badge("switched off", "warn")),
        sc.problems.map(p => h("div", { class: "small", style: "color:var(--err)" }, p)),
        sc.steps.length ? h("ol", { class: "steps small" }, sc.steps.map(t => h("li", {}, t))) : null))),
      h("p", { class: "small muted" }, "Daily check last ran: ", when(hl.maintenance.last_run),
        hl.maintenance.stale ? " (the scheduled check is off or has not run lately)" : ""),
      h("p", { class: "small muted" }, "AG-Flow never switches these on for you. To open this manager from a chat, the agent can share: ", h("code", {}, "[AG-Flow Manager](sidecar://agflow/manager/)")));
    return h("div", {}, sync, update, sidecars);
  };

  async function verifyDrawer() {
    const r = await act(() => api("/sync/apply", { method: "POST", body: { dry_run: true } }));
    const body = h("div", {},
      h("div", { class: "row" }, badge(r.report.status, statusClass[r.report.status] || "skipped")),
      r.problems.length ? h("div", { class: "notice err", style: "margin:10px 0" }, r.problems.map(p => h("div", {}, p))) : null,
      h("ul", {}, r.notes.map(n => h("li", {}, n))),
      r.would_accept ? h("div", { class: "drawer-foot row" }, h("span", { class: "foot-hint muted small" }, "Doctor and the hook probe passed."),
        h("button", { class: "ghost", type: "button", onclick: closeDrawer }, "Not now"),
        h("button", { type: "button", onclick: async () => {
          await act(() => api("/sync/apply", { method: "POST", body: { dry_run: false } }), "Accepted: this Antigravity is now the verified one");
          closeDrawer();
          route();
        } }, "Accept this Antigravity")) : null);
    openDrawer("Verify against this Antigravity", body);
  }

  async function planDrawer() {
    const r = await act(() => api("/updates/plan"));
    if (!r.plan) { openDrawer("Update plan", h("div", { class: "notice err" }, r.error || "No plan available.")); return; }
    const p = r.plan;
    openDrawer(`AG-Flow ${p.current} → ${p.target}`, h("div", {},
      h("p", {}, `${p.bump} update · ${p.install_mode} install`),
      p.blocked.length ? h("div", { class: "notice err", style: "margin-bottom:10px" }, p.blocked.map(b => h("div", {}, "Blocked: " + b))) : null,
      p.warnings.length ? h("ul", {}, p.warnings.map(w => h("li", {}, w))) : null,
      p.ok ? h("ol", { class: "steps" }, p.steps.map(s => h("li", {}, s))) : null,
      p.notes ? h("details", {}, h("summary", { class: "small" }, "Release notes"), h("pre", { class: "content" }, p.notes)) : null,
      p.ok ? h("p", {}, "To go ahead, run this in a terminal: ", h("code", {}, "agflow update apply")) : null));
  }

  // ---------- top bar: workspace switch and health chip -----------------------------------------------------
  const chip = h("a", { id: "health-chip", class: "chip warn", href: "#/sync", hidden: true });
  async function refreshHealth() {
    try {
      const hl = await api("/health");
      chip.hidden = !hl.notes.length;
      chip.textContent = hl.notes.length ? hl.notes[0] : "";
    } catch (_) { chip.hidden = true; }
  }

  async function workspaceDrawer() {
    const d = await act(() => api("/workspaces"));
    const input = h("input", { value: d.current, "aria-label": "Workspace folder", style: "width:100%" });
    const use = async (path) => {
      const r = await act(() => api("/workspace", { method: "POST", body: { path } }), "Workspace changed");
      document.getElementById("workspace").textContent = r.workspace;
      closeDrawer();
      refreshHealth();
      route();
    };
    openDrawer("Which project?", h("div", {},
      d.sidecar ? h("p", { class: "muted small" }, "Antigravity starts this panel without telling it which project is open, so choose one here.") : null,
      h("ul", { class: "plain" }, d.known.map(p => h("li", { style: "margin-bottom:6px" },
        h("button", { class: p === d.current ? "" : "ghost", type: "button", onclick: () => use(p) }, p)))),
      h("h3", {}, "Another folder"), input,
      h("div", { class: "row", style: "margin-top:8px" }, h("button", { type: "button", onclick: () => use(input.value.trim()) }, "Use this folder"))));
  }

  const wsBox = document.getElementById("workspace");
  if (wsBox) {
    wsBox.setAttribute("role", "button");
    wsBox.tabIndex = 0;
    wsBox.title = "Click to switch project";
    wsBox.addEventListener("click", workspaceDrawer);
    wsBox.addEventListener("keydown", e => { if (e.key === "Enter") workspaceDrawer(); });
    wsBox.parentNode.appendChild(chip);
  }
  refreshHealth();
})();
