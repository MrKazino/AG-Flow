/* agflow dashboard — vanilla JS, no build step, no third-party code. */
(function () {
  "use strict";

  // ---------- token & API --------------------------------------------------------------
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  if (params.get("token")) {
    try { sessionStorage.setItem("agflow-token", params.get("token")); } catch (_) { /* storage blocked */ }
    window.__agflowToken = params.get("token");
    history.replaceState(null, "", "#/overview");
  }
  const token = () => {
    try { return sessionStorage.getItem("agflow-token") || window.__agflowToken || ""; }
    catch (_) { return window.__agflowToken || ""; }
  };

  // Reads started for the current page are cancelled when the user navigates on (see route()). Writes are never tied
  // to the page: aborting a POST the user already triggered would leave them unsure whether it happened.
  let routeSignal = null;

  async function api(path, opts = {}) {
    const method = opts.method || "GET";
    const res = await fetch("/api" + path, {
      method,
      headers: { "Content-Type": "application/json", "X-Agflow-Token": token() },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      signal: method === "GET" ? routeSignal : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `${res.status} ${res.statusText}`);
    return data;
  }

  // ---------- DOM helpers ---------------------------------------------------------------
  function h(tag, props, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") e.className = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      e.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return e;
  }
  const badge = (text, cls) => h("span", { class: `badge ${cls || text}` }, text);
  const $view = document.getElementById("view");

  function toast(msg, isErr) {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.className = "toast" + (isErr ? " err" : "");
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (t.hidden = true), isErr ? 6000 : 3000);
  }

  async function act(fn, okMsg) {
    try {
      const r = await fn();
      if (okMsg) toast(okMsg);
      return r;
    } catch (e) {
      toast(e.message, true);
      throw e;
    }
  }

  function openDrawer(title, body) {
    document.getElementById("drawer-title").textContent = title;
    const b = document.getElementById("drawer-body");
    b.replaceChildren(body);
    const d = document.getElementById("drawer");
    d.hidden = false;
    document.getElementById("drawer-close").focus();
  }
  function closeDrawer() { document.getElementById("drawer").hidden = true; }
  document.getElementById("drawer-close").addEventListener("click", closeDrawer);
  document.getElementById("drawer").addEventListener("click", e => { if (e.target.id === "drawer") closeDrawer(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

  function table(columns, rows, onRow) {
    if (!rows.length) return h("div", { class: "empty" }, "Nothing here yet.");
    return h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, columns.map(c => h("th", { scope: "col" }, c.label)))),
      h("tbody", {}, rows.map(r => {
        const tr = h("tr", { class: onRow ? "clickable" : "" }, columns.map(c => h("td", { class: c.cls || "" }, c.render(r))));
        if (onRow) {
          tr.tabIndex = 0;
          tr.addEventListener("click", e => { if (!e.target.closest("button")) onRow(r); });
          tr.addEventListener("keydown", e => { if (e.key === "Enter") onRow(r); });
        }
        return tr;
      }))));
  }

  function toggleSwitch(on, label, onChange) {
    const b = h("button", { class: "switch", role: "switch", "aria-checked": String(on), "aria-label": label, type: "button" });
    b.addEventListener("click", async e => {
      e.stopPropagation();
      b.disabled = true;
      try { await onChange(!on); } finally { b.disabled = false; }
    });
    return b;
  }

  async function showFile(item) {
    const f = await act(() => api("/file?path=" + encodeURIComponent(item.path)));
    openDrawer(`${item.kind}: ${item.name}`, h("div", {},
      h("p", { class: "muted small" }, f.path, ` · ${f.chars} chars`),
      h("pre", { class: "content" }, f.content)));
  }

  // ---------- pages -------------------------------------------------------------------------
  const pages = {};

  pages.overview = async () => {
    const o = await api("/overview");
    const tiles = [["skill", "Skills", "skills"], ["agent", "Agents", "agents"], ["rule", "Rules", "rules"],
      ["mcp", "MCP servers", "mcp"], ["hook", "Hooks", "hooks"], ["graph", "Graphs", "graphs"], ["plugin", "Plugins", "plugins"]];
    const doc = o.doctor;
    const installRow = (scope) => {
      const i = o.install[scope];
      if (!i) return null;
      return h("tr", {},
        h("td", {}, scope),
        h("td", {}, i.installed ? badge("installed", "ok") : badge("not installed", "skipped")),
        h("td", { class: "desc" }, i.target, i.profile ? ` (profile: ${i.profile})` : ""),
        h("td", {}, i.installed
          ? h("button", { class: "ghost small", onclick: async () => { await act(() => api("/uninstall", { method: "POST", body: { scope } }), "Uninstalled"); route(); } }, "Uninstall")
          : h("button", { class: "small", onclick: async () => {
            try {
              await api("/install", { method: "POST", body: { scope } });
            } catch (e) {
              if (!/already installed/.test(e.message)) { toast(e.message, true); return; }
              if (!confirm(e.message + "\n\nInstall a second copy anyway?")) return;
              await act(() => api("/install", { method: "POST", body: { scope, allow_duplicate: true } }));
            }
            toast("Installed");
            route();
          } }, "Install")));
    };
    const hh = o.hook_health;
    const guardAlarm = !hh ? null : h("div", { class: "notice err", style: "margin-bottom:16px" },
      h("strong", {}, "Safety guard unavailable. "),
      `agflow's hooks could not understand ${hh.total} call(s) from Antigravity in the last ${hh.window_days} days `
      + `(${Object.entries(hh.counts).map(([k, n]) => `${k} ×${n}`).join(", ")}; last ${new Date(hh.last_seen * 1000).toLocaleString()}). `
      + "Those calls were NOT checked by the guard. Antigravity may have changed its hook payload — see ",
      h("a", { href: "#/doctor" }, "Doctor → hooks.guard_blind"), " for how to fix it.");
    return h("div", {},
      guardAlarm,
      h("div", { class: "grid tiles" }, tiles.map(([k, label, page]) => h("a", { class: "tile", href: `#/${page}` },
        h("div", { class: "label" }, label),
        h("div", { class: "value" }, o.counts[k].enabled),
        h("div", { class: "sub" }, o.counts[k].disabled ? `${o.counts[k].disabled} disabled` : " ")))),
      h("div", { class: "grid two", style: "margin-top:16px" },
        h("div", { class: "card" }, h("h2", {}, "Health"),
          h("div", { class: "row" }, badge(`${doc.error} errors`, doc.error ? "err" : "ok"), badge(`${doc.warning} warnings`, doc.warning ? "warn" : "ok"), badge(`${doc.info} info`, "info")),
          h("p", { class: "muted" }, doc.error ? "Errors make Antigravity ignore files silently. Open Doctor to fix them." : "No blocking problems found."),
          h("a", { href: "#/doctor" }, "Open Doctor →")),
        h("div", { class: "card" }, h("h2", {}, "agflow plugin"),
          h("table", {}, h("tbody", {}, installRow("global"), installRow("workspace"))))),
      h("div", { class: "card", style: "margin-top:16px" }, h("h2", {}, "Active runs"),
        table([{ label: "Run", render: r => h("a", { href: `#/runs/${r.run_id}` }, r.run_id) },
          { label: "Graph", render: r => r.graph_id },
          { label: "Progress", render: r => `${r.counts.succeeded + r.counts.skipped}/${Object.values(r.counts).reduce((a, b) => a + b, 0)} done` },
          { label: "Status", render: r => badge(r.status) }], o.active_runs)));
  };

  pages.doctor = async () => {
    const [d, plan] = await Promise.all([api("/doctor"), api("/fix/plan")]);
    const fixById = Object.fromEntries(plan.fixes.map(f => [f.id, f]));
    const manualById = Object.fromEntries(plan.manual.map(m => [m.id, m]));
    let filter = "all";
    const box = h("div", {});
    const fixCell = i => fixById[i.id]
      ? h("button", { class: `small ${fixById[i.id].tier === "safe" ? "" : "ghost"}`, title: fixById[i.id].title, onclick: () => openFixDrawer(plan, [i.id]) },
        fixById[i.id].tier === "safe" ? "Fix" : "Fix…")
      : manualById[i.id] ? h("button", { class: "ghost small", onclick: () => openFixDrawer(plan, [i.id]) }, "How to fix") : null;
    const draw = () => box.replaceChildren(table([
      { label: "Level", render: i => badge(i.level) },
      { label: "Code", render: i => h("code", {}, i.code) },
      { label: "Message", cls: "desc", render: i => [i.message, i.path ? h("div", { class: "small" }, h("code", {}, i.path)) : null] },
      { label: "Source", render: i => i.source ? h("span", { class: "chip" }, i.source) : null },
      { label: "", render: fixCell },
    ], d.issues.filter(i => filter === "all" || i.level === filter)));
    const sel = h("select", { "aria-label": "Filter by level", onchange: e => { filter = e.target.value; draw(); } },
      ["all", "error", "warning", "info"].map(v => h("option", { value: v }, v)));
    draw();
    const nSafe = plan.fixes.filter(f => f.tier === "safe").length;
    const nReview = plan.fixes.length - nSafe;
    const last = plan.last_batch;
    return h("div", {},
      plan.fixes.length || last ? h("div", { class: "card fix-banner" },
        h("div", { class: "row" },
          h("div", { style: "flex:1;min-width:240px" },
            h("h2", { style: "margin:0 0 4px" }, plan.fixes.length ? `${plan.fixes.length} issue${plan.fixes.length === 1 ? "" : "s"} can be fixed automatically` : "No automatic fixes left"),
            h("div", { class: "muted small" }, plan.fixes.length
              ? `${nSafe} safe · ${nReview} need your choice · ${plan.manual.length} with step-by-step guidance. You see every change before it happens; everything is backed up and can be undone.`
              : "The remaining issues need a manual step — use “How to fix” on a row.")),
          last ? h("button", { class: "ghost", onclick: () => undoFix(last) }, "Undo last fix") : null,
          plan.fixes.length ? h("button", { onclick: () => openFixDrawer(plan) }, "Fix issues…") : null)) : null,
      h("div", { class: "card" },
        h("div", { class: "row" }, badge(`${d.counts.error} errors`, d.counts.error ? "err" : "ok"), badge(`${d.counts.warning} warnings`, d.counts.warning ? "warn" : "ok"),
          h("span", { class: "spacer" }), sel,
          h("button", { class: "ghost small", onclick: async () => {
            const r = await act(() => api("/migrate", { method: "POST", body: { dry_run: true } }));
            openDrawer("Migration preview", h("div", {}, r.actions.length ? h("pre", { class: "content" }, r.actions.join("\n")) : h("p", {}, "Nothing to migrate."),
              r.conflicts.length ? h("pre", { class: "content" }, r.conflicts.join("\n")) : null,
              r.actions.length ? h("button", { onclick: async () => { await act(() => api("/migrate", { method: "POST", body: { dry_run: false } }), "Migrated"); closeDrawer(); route(); } }, "Apply migration") : null));
          } }, "Migrate workflows…")),
        box));
  };

  // Undo the last fix batch: the banner on the doctor page and the result drawer both come here. `last` (the batch
  // shown in the banner) asks for confirmation first; the drawer's button acts on fixes the user has just applied.
  async function undoFix(last) {
    if (last) {
      const when = new Date(last.time * 1000).toLocaleString();
      if (!confirm(`Undo the fix batch from ${when}?\n\n${last.fixes.join("\n")}\n\nEvery file it changed is restored from the backup.`)) return;
    }
    const r = await act(() => api("/fix/undo", { method: "POST" }));
    toast(`Reverted ${r.fixes.length} fix(es)`);
    closeDrawer();
    route();
  }

  // Fix drawer: safe fixes preselected, choices unselected (Skip), manual guidance last.
  function openFixDrawer(plan, onlyIds) {
    const only = onlyIds ? new Set(onlyIds) : null;
    const fixes = plan.fixes.filter(f => !only || only.has(f.id));
    const manual = plan.manual.filter(m => !only || only.has(m.id));
    const choice = {};  // fix id -> option id | undefined (skip)
    const applyBtn = h("button", { type: "button" });
    const refresh = () => {
      const n = Object.values(choice).filter(v => v !== undefined).length;
      applyBtn.textContent = n ? `Apply ${n} selected fix${n === 1 ? "" : "es"}` : "Select fixes to apply";
      applyBtn.disabled = !n;
    };
    const changes = list => h("ul", { class: "changes" }, list.map(c => h("li", {}, c)));
    const head = f => h("div", { class: "fix-head" }, badge(f.level), h("strong", {}, f.title),
      h("div", { class: "small muted" }, h("code", {}, f.code), f.path ? [" · ", h("code", {}, f.path)] : null));

    const safe = fixes.filter(f => f.tier === "safe").map(f => {
      choice[f.id] = f.default;
      const cb = h("input", { type: "checkbox", checked: true, "aria-label": `Apply: ${f.title}`,
        onchange: e => { choice[f.id] = e.target.checked ? f.default : undefined; refresh(); } });
      return h("label", { class: "fix-item" }, h("div", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap" }, cb,
        h("div", { style: "min-width:0" }, head(f), changes(f.options[0].changes))));
    });
    const review = fixes.filter(f => f.tier === "review").map(f => {
      // opened from one row: that click was the choice, so start on the recommended option (still needs Apply)
      const start = only && only.size === 1 ? f.default : "";
      choice[f.id] = start || undefined;
      const name = "opt-" + f.id;
      const radio = (value, label, extra) => h("label", { class: "fix-option" },
        h("input", { type: "radio", name, value, checked: value === start, onchange: () => { choice[f.id] = value || undefined; refresh(); } }),
        h("div", { style: "min-width:0" }, h("div", {}, label), extra));
      return h("div", { class: "fix-item" }, head(f),
        f.note ? h("p", { class: "small", style: "margin:6px 0" }, f.note) : null,
        h("fieldset", {}, h("legend", { class: "small muted" }, "Choose"),
          radio("", "Skip — leave it as it is"),
          f.options.map(o => radio(o.id, [o.label, o.id === f.default && f.options.length > 1 ? h("span", { class: "badge ok", style: "margin-left:6px" }, "recommended") : null], changes(o.changes)))));
    });
    const man = manual.map(m => h("details", { class: "fix-item", open: !!only },
      h("summary", {}, badge(m.level), " ", m.title, m.source ? h("span", { class: "chip", style: "margin-left:6px" }, m.source) : null),
      h("ol", { class: "steps" }, m.steps.map(s => h("li", {}, s)))));

    const section = (title, sub, items) => items.length ? h("section", { class: "fix-section" }, h("h3", {}, title, h("span", { class: "muted small" }, ` (${items.length})`)),
      h("p", { class: "muted small" }, sub), items) : null;
    const body = h("div", {},
      section("Safe fixes", "Mechanical repairs that don't change how anything behaves. Preselected — untick any you don't want.", safe),
      section("Needs your choice", "More than one valid outcome, so nothing happens until you pick an option.", review),
      section("Manual", "agflow can't fix these safely (for example, files owned by another plugin's publisher). Exact steps:", man),
      fixes.length ? h("div", { class: "drawer-foot row" },
        h("span", { class: "muted small foot-hint" }, "Everything is backed up first; “Undo last fix” restores it."),
        h("button", { class: "ghost", type: "button", onclick: closeDrawer }, "Cancel"), applyBtn) : null);
    applyBtn.addEventListener("click", async () => {
      const selections = Object.fromEntries(Object.entries(choice).filter(([, v]) => v !== undefined));
      applyBtn.disabled = true;
      applyBtn.textContent = "Applying…";
      try {
        const r = await act(() => api("/fix/apply", { method: "POST", body: { selections } }));
        showFixResult(r);
      } catch (_) { refresh(); }
    });
    refresh();
    const title = only && fixes.length + manual.length === 1 ? (fixes[0] ? "Fix issue" : "How to fix") : "Fix issues";
    openDrawer(title, body);
  }

  function showFixResult(r) {
    const line = (cls, label, text, extra) => h("li", {}, badge(label, cls), " ", text, extra);
    const b = r.before, a = r.after;
    const body = h("div", {},
      h("div", { class: "row", style: "margin-bottom:12px" },
        badge(`errors ${b.error} → ${a.error}`, a.error ? "err" : "ok"), badge(`warnings ${b.warning} → ${a.warning}`, a.warning ? "warn" : "ok")),
      h("ul", { class: "result-list" },
        r.applied.map(x => line("ok", "fixed", x.title, h("div", { class: "small muted" }, x.option))),
        r.failed.map(x => line("err", "failed", x.title, h("div", { class: "small" }, x.error))),
        r.skipped.map(x => line("skipped", "skipped", x.id, h("div", { class: "small muted" }, x.reason))),
        r.unresolved.map(x => line("warn", "check", x.title, h("div", { class: "small muted" }, "still reported after the fix — see Doctor")))),
      r.backup_dir ? h("p", { class: "small muted" }, "Backup: ", h("code", {}, r.backup_dir)) : null,
      h("div", { class: "drawer-foot row" }, h("span", { style: "flex:1" }),
        r.batch ? h("button", { class: "ghost", type: "button", onclick: () => undoFix() }, "Undo these fixes") : null,
        h("button", { type: "button", onclick: () => { closeDrawer(); route(); } }, "Done")));
    openDrawer(r.failed.length ? "Fixes applied with problems" : "Fixes applied", body);
    route();
  }

  // The table every component page shares: name, scope, the page's own columns, a description and the on/off switch.
  // options: { cols: extra columns placed after Scope, describe: item -> description text }
  function componentTable(kind, items, options = {}) {
    return table([
      { label: "Name", render: i => h("strong", {}, i.name) },
      { label: "Scope", render: i => h("span", { class: "chip" }, i.scope) },
      ...(options.cols || []),
      { label: "Description", cls: "desc", render: options.describe || (i => i.description) },
      { label: "On", render: i => i.scope === "legacy" ? badge("legacy", "warn") : toggleSwitch(i.enabled, `Enable ${i.name}`, async on => {
        await act(() => api(`/items/${kind}/${encodeURIComponent(i.name)}/toggle`, { method: "POST", body: { enabled: on } }), `${on ? "Enabled" : "Disabled"} ${i.name}`);
        route();
      }) },
    ], items, i => showFile(i));
  }

  // A page that is one card with an introduction and a component table. options: cols, describe (see above), intro.
  function componentPage(kind, options = {}) {
    return async () => {
      const items = await api("/items?kind=" + kind);
      return h("div", { class: "card" },
        h("p", { class: "muted" }, options.intro || "Disabling moves an item to agflow's parking area (reversible). Workspace items override plugins, which override global ones."),
        componentTable(kind, items, options));
    };
  }

  pages.skills = componentPage("skill");
  pages.agents = componentPage("agent", { cols: [
    { label: "Model", render: i => i.meta.model || "inherit" },
    { label: "Role", render: i => [i.meta.mainAgent ? badge("main", "info") : null, i.meta.subagent ? badge("subagent", "skipped") : null] },
  ] });

  pages.rules = async () => {
    const [g, rules] = await Promise.all([api("/gemini-md"), api("/items?kind=rule")]);
    const ta = h("textarea", { class: "code", spellcheck: "false", "aria-label": "Global GEMINI.md" });
    ta.value = g.content;
    const meter = h("div", { class: "meter" }, h("span", {}));
    const count = h("span", { class: "muted small" });
    const update = () => {
      const n = ta.value.length, pct = Math.min(100, (n / g.limit) * 100);
      meter.className = "meter" + (n > g.limit ? " err" : pct > 90 ? " warn" : "");
      meter.firstChild.style.width = pct + "%";
      count.textContent = `${n.toLocaleString()} / ${g.limit.toLocaleString()} chars`;
    };
    ta.addEventListener("input", update);
    update();
    return h("div", {},
      h("div", { class: "card" }, h("h2", {}, "Global GEMINI.md"),
        h("p", { class: "muted small" }, g.path, " — always loaded; keep it short. The agflow block between the markers is managed by ", h("code", {}, "agflow install"), "."),
        g.content.length > g.limit ? h("div", { class: "notice err", style: "margin-bottom:10px" },
          `${g.content.length.toLocaleString()} characters — over the ${g.limit.toLocaleString()} limit, so Antigravity may not load the end of it. `,
          h("a", { href: "#/doctor" }, "Doctor → Fix issues"), " can move the largest sections into on-demand skills (reversible).") : null,
        ta, h("div", { class: "row", style: "margin-top:10px" }, h("div", { style: "flex:1" }, meter), count,
          h("button", { onclick: async () => { await act(() => api("/gemini-md", { method: "PUT", body: { content: ta.value } }), "Saved GEMINI.md (backup kept)"); } }, "Save"))),
      h("div", { class: "card" }, h("h2", {}, "Rules"),
        componentTable("rule", rules, {
          cols: [{ label: "Trigger", render: i => badge(i.meta.trigger || "missing", i.meta.trigger ? "info" : "err") }],
          describe: i => i.description || (i.meta.globs ? `globs: ${i.meta.globs}` : ""),
        })));
  };

  pages.mcp = async () => {
    const d = await api("/mcp");
    const name = h("input", { placeholder: "name", "aria-label": "Server name", list: "mcp-catalog" });
    const cmd = h("input", { placeholder: "command (stdio) e.g. npx", "aria-label": "Command" });
    const args = h("input", { placeholder: "args, space-separated", "aria-label": "Arguments", style: "min-width:220px" });
    const url = h("input", { placeholder: "or URL (http)", "aria-label": "URL" });
    const scope = h("select", { "aria-label": "Scope" }, ["global", "workspace"].map(s => h("option", { value: s }, s)));
    return h("div", {},
      h("div", { class: "card" }, h("h2", {}, "Servers"),
        table([
          { label: "Name", render: s => h("strong", {}, s.name) },
          { label: "Scope", render: s => h("span", { class: "chip" }, s.scope) },
          { label: "Launch", cls: "desc", render: s => h("code", {}, s.description) },
          { label: "Env", render: s => s.missing_env.length ? badge("missing " + s.missing_env.join(", "), "warn") : badge("ok", "ok") },
          { label: "On", render: s => s.editable ? toggleSwitch(s.enabled, `Enable ${s.name}`, async on => {
            await act(() => api(`/mcp/${s.scope}/${encodeURIComponent(s.name)}/toggle`, { method: "POST", body: { enabled: on } }));
            route();
          }) : badge(s.scope.startsWith("plugin") ? "plugin" : s.scope, "skipped") },
          { label: "", render: s => s.editable ? h("button", { class: "danger small", onclick: async () => {
            if (!confirm(`Remove MCP server ${s.name}?`)) return;
            await act(() => api(`/mcp/${s.scope}/${encodeURIComponent(s.name)}`, { method: "DELETE" }), "Removed");
            route();
          } }, "Remove") : null },
        ], d.servers)),
      h("div", { class: "card" }, h("h2", {}, "Add a server"),
        h("p", { class: "muted small" }, "Pick a catalog name (github, playwright, context7, …) and leave the rest empty, or give a command/URL. Put secrets in env vars and reference them as ${VAR}."),
        h("datalist", { id: "mcp-catalog" }, Object.keys(d.catalog).map(k => h("option", { value: k }))),
        h("div", { class: "row" }, name, cmd, args, url, scope,
          h("button", { onclick: async () => {
            await act(() => api("/mcp", { method: "POST", body: { name: name.value.trim(), command: cmd.value.trim(), args: args.value.trim() ? args.value.trim().split(/\s+/) : [], url: url.value.trim(), scope: scope.value } }), "Added");
            route();
          } }, "Add"))));
  };

  pages.hooks = async () => {
    const items = await api("/items?kind=hook");
    return h("div", { class: "card" },
      h("p", { class: "muted" }, "agflow's hooks: PreToolUse guard (deny/ask on destructive commands and secret files), PostToolUse run ledger, Stop continuation while a graph has ready nodes. Tune with .agents/agflow/guard.yaml or disable with AGFLOW_GUARD=off."),
      table([
        { label: "Event", render: i => h("span", {}, badge(i.meta.event, "info"),
          i.meta.inert ? h("span", { title: "Antigravity ignores this entry (wrong layout for its event): run agflow fix" }, " ", badge("ignored", "warn")) : null) },
        { label: "Matcher", render: i => h("code", {}, i.meta.matcher || "*") },
        { label: "Command", cls: "desc", render: i => h("code", {}, i.description) },
        { label: "Scope", render: i => h("span", { class: "chip" }, i.scope) },
      ], items));
  };

  pages.plugins = async () => {
    const items = await api("/items?kind=plugin");
    return h("div", { class: "card" }, table([
      { label: "Plugin", render: i => h("strong", {}, i.name) },
      { label: "Version", render: i => i.meta.version || "" },
      { label: "Scope", render: i => h("span", { class: "chip" }, i.scope) },
      { label: "Description", cls: "desc", render: i => i.description },
    ], items, i => showFile(i)));
  };

  pages.profiles = async () => {
    const p = await api("/profiles");
    return h("div", { class: "grid two" }, Object.entries(p).map(([name, spec]) => h("div", { class: "card" },
      h("div", { class: "row" }, h("h2", { style: "margin:0" }, name), h("span", { class: "spacer" }),
        h("button", { class: "small", onclick: async () => {
          if (!confirm(`Apply profile "${name}" to this workspace? Items outside it are disabled (reversible).`)) return;
          const r = await act(() => api(`/profiles/${name}/apply`, { method: "POST" }), `Applied ${name}`);
          openDrawer(`Profile ${name} applied`, h("pre", { class: "content" }, r.actions.join("\n") || "No changes."));
        } }, "Apply")),
      h("p", { class: "muted" }, spec.description || ""),
      spec.error ? h("div", { class: "notice err" }, spec.error) : [
        h("div", { class: "small muted" }, `Skills (${spec.skills.length})`), h("div", { class: "chips" }, spec.skills.map(s => h("span", { class: "chip" }, s))),
        h("div", { class: "small muted", style: "margin-top:8px" }, `Agents (${spec.agents.length})`), h("div", { class: "chips" }, spec.agents.map(s => h("span", { class: "chip" }, s))),
      ])));
  };

  const legend = () => h("div", { class: "legend" },
    ["pending", "running", "succeeded", "failed", "skipped"].map(s => badge(s)),
    h("span", {}, "▭ agent · ⬡ gate · ▱ human · dashed = rework loop"));

  pages.graphs = async (arg) => {
    const list = await api("/graphs");
    const current = arg || (list[0] && list[0].id);
    const box = h("div", { class: "graph-box" });
    const ta = h("textarea", { class: "code", spellcheck: "false", "aria-label": "Graph YAML", style: "min-height:360px" });
    const msg = h("div", {});
    let detail = null;
    if (current) {
      detail = await api("/graphs/" + encodeURIComponent(current));
      ta.value = detail.yaml;
      AgGraph.render(box, detail.layout);
    }
    // Typing triggers a debounced validation; a slow answer for older text must not replace the result for newer text.
    const validation = AgLatest.latest();
    const validate = async () => {
      const token = validation.begin();
      const r = await api("/graphs/validate", { method: "POST", body: { yaml: ta.value } });
      if (!validation.isCurrent(token)) return r;
      if (r.ok) { AgGraph.render(box, r.layout); msg.replaceChildren(h("div", { class: "notice ok" }, `Valid · ${r.layout.nodes.length} nodes`)); }
      else msg.replaceChildren(h("div", { class: "notice err" }, r.error));
      return r;
    };
    let timer;
    ta.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(validate, 500); });
    const save = async () => {
      const r = await validate();
      if (!r.ok) return;
      await act(() => api("/graphs/" + encodeURIComponent(r.layout.id), { method: "PUT", body: { yaml: ta.value } }), `Saved to workspace as ${r.layout.id}.yaml`);
    };
    return h("div", { class: "split" },
      h("div", { class: "card" }, h("h2", {}, "Graphs"),
        h("div", { class: "list" }, list.map(g => h("button", { class: g.id === current ? "active" : "", type: "button", onclick: () => (location.hash = "#/graphs/" + g.id) },
          g.id, g.error ? " ⚠" : "", h("div", { class: "src" }, g.source))))),
      h("div", {},
        h("div", { class: "card" },
          h("div", { class: "row" }, h("h2", { style: "margin:0" }, detail ? (detail.layout.title || detail.layout.id) : "No graph"), h("span", { class: "spacer" }),
            detail ? h("span", { class: "muted small" }, detail.editable ? "workspace graph" : "read-only source · saving creates a workspace copy") : null),
          detail ? h("p", { class: "muted" }, detail.layout.description) : null,
          box, legend(),
          detail ? h("p", { class: "small muted" }, "Run it from Antigravity: ", h("code", {}, `/graph-run ${detail.layout.id}`), " or headless: ",
            h("code", {}, `agflow graph run ${detail.layout.id} --backend sdk` + Object.entries(detail.layout.inputs).filter(([, v]) => v.required).map(([k]) => ` -i ${k}=…`).join(""))) : null),
        h("div", { class: "card" }, h("div", { class: "row" }, h("h2", { style: "margin:0" }, "YAML"), h("span", { class: "spacer" }),
          h("button", { class: "ghost small", onclick: validate }, "Validate"), h("button", { class: "small", onclick: save }, "Save to workspace")),
          msg, h("div", { style: "margin-top:8px" }, ta))));
  };

  // Live run updates over SSE. EventSource cannot send headers, and the dashboard token must never be put in a URL
  // (history, proxy and server logs), so each connection first trades the token (header) for a one-time ticket.
  // A used ticket cannot be replayed and EventSource gives up after a 401, so every reconnect asks for a new one.
  function liveStream(runId, onData) {
    let source = null, closed = false, delay = 1000;
    const retry = () => { if (!closed) setTimeout(connect, delay = Math.min(delay * 2, 15000)); };
    async function connect() {
      if (closed) return;
      try {
        const t = await api("/stream-ticket", { method: "POST", body: { run_id: runId } });
        if (closed) return;
        source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/stream?ticket=${encodeURIComponent(t.ticket)}`);
        source.onopen = () => { delay = 1000; };
        source.onmessage = (ev) => { try { onData(JSON.parse(ev.data)); } catch (_) { /* ignore partial */ } };
        source.onerror = () => { source.close(); source = null; retry(); };
      } catch (_) { retry(); }
    }
    connect();
    return { close() { closed = true; if (source) source.close(); } };
  }

  let stream = null;
  pages.runs = async (arg, ctx) => {
    if (arg) return runDetail(arg, ctx);
    const runs = await api("/runs");
    return h("div", { class: "card" }, table([
      { label: "Run", render: r => h("a", { href: `#/runs/${r.run_id}` }, r.run_id) },
      { label: "Graph", render: r => r.graph_id },
      { label: "Status", render: r => badge(r.status) },
      { label: "Nodes", render: r => h("div", { class: "chips" }, Object.entries(r.counts).filter(([, n]) => n).map(([k, n]) => badge(`${n} ${k}`, k))) },
      { label: "Started", render: r => new Date(r.created_at * 1000).toLocaleString() },
    ], runs, r => (location.hash = `#/runs/${r.run_id}`)));
  };

  async function runDetail(runId, ctx) {
    const box = h("div", { class: "graph-box" });
    const head = h("div", { class: "row" });
    const nodes = h("div", {});
    const ledger = h("pre", { class: "content", style: "max-height:320px" });
    const human = h("div", {});
    const draw = (d) => {
      const statuses = Object.fromEntries(d.nodes.map(n => [n.id, n.status]));
      AgGraph.render(box, d.layout, statuses);
      head.replaceChildren(h("h2", { style: "margin:0" }, d.run_id), badge(d.status), h("span", { class: "spacer" }),
        d.status === "running" ? h("button", { class: "danger small", onclick: async () => {
          if (!confirm("Abort this run?")) return;
          await act(() => api(`/runs/${runId}/abort`, { method: "POST", body: { reason: "aborted from dashboard" } }), "Aborted");
        } }, "Abort") : null);
      human.replaceChildren(...d.waiting_on_human.map(nid => h("div", { class: "notice", style: "margin-top:12px" },
        h("strong", {}, `Approval needed: ${nid}`), " ",
        h("button", { class: "small", onclick: () => act(() => api(`/runs/${runId}/approve`, { method: "POST", body: { node: nid, approved: true, note: "approved in dashboard" } }), "Approved") }, "Approve"), " ",
        h("button", { class: "danger small", onclick: () => act(() => api(`/runs/${runId}/approve`, { method: "POST", body: { node: nid, approved: false, note: "rejected in dashboard" } }), "Rejected") }, "Reject"))));
      nodes.replaceChildren(table([
        { label: "Node", render: n => h("strong", {}, n.id) },
        { label: "Agent", render: n => n.agent || n.type },
        { label: "Status", render: n => badge(n.status) },
        { label: "Attempts", render: n => n.attempts },
        { label: "Summary", cls: "desc", render: n => n.summary || "" },
      ], d.nodes));
      ledger.textContent = d.ledger.slice(-80).map(e => `${new Date(e.ts * 1000).toLocaleTimeString()}  ${e.event.padEnd(16)} ${e.node || e.gate || ""} ${e.status || ""} ${(e.summary || e.detail || e.error || "").toString().slice(0, 140)}`).join("\n");
    };
    draw(await api(`/runs/${runId}`));
    if (!ctx.current()) return h("div", {});  // the user already moved on: don't open a stream for a page nobody sees
    if (stream) stream.close();
    stream = liveStream(runId, draw);
    return h("div", {},
      h("div", { class: "card" }, head, human, h("div", { style: "margin-top:12px" }, box), legend()),
      h("div", { class: "card" }, h("h2", {}, "Nodes"), nodes),
      h("div", { class: "card" }, h("h2", {}, "Ledger"), ledger));
  }

  // ---------- router ----------------------------------------------------------------------------
  const titles = { overview: "Overview", doctor: "Doctor", skills: "Skills", agents: "Agents", rules: "Rules & GEMINI.md",
    mcp: "MCP servers", hooks: "Hooks", plugins: "Plugins", profiles: "Profiles", graphs: "Graphs", runs: "Runs" };

  // Navigating from A to B before A's data arrives must not let A's late response replace B's view. Each navigation
  // takes a token and cancels the previous page's in-flight reads; whatever still arrives for an old token is dropped.
  const routeGuard = AgLatest.latest();
  let routeAbort = null;

  async function route() {
    const token = routeGuard.begin();
    if (routeAbort) routeAbort.abort();
    routeAbort = new AbortController();
    routeSignal = routeAbort.signal;
    const ctx = { current: () => routeGuard.isCurrent(token) };
    const [, page = "overview", arg] = (location.hash.match(/^#\/([^/]+)(?:\/(.+))?/) || []);
    const name = pages[page] ? page : "overview";
    if (stream && !(name === "runs" && arg)) { stream.close(); stream = null; }
    document.getElementById("title").textContent = titles[name];
    document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.page === name));
    $view.replaceChildren(h("div", { class: "empty" }, "Loading…"));
    try {
      const view = await pages[name](arg && decodeURIComponent(arg), ctx);
      if (!ctx.current()) return;
      $view.replaceChildren(view);
    } catch (e) {
      if (!ctx.current() || e.name === "AbortError") return;  // superseded or cancelled by a newer navigation
      $view.replaceChildren(h("div", { class: "notice err" }, e.message.includes("token")
        ? "Missing or invalid token. Open the exact URL printed by `agflow dashboard`." : e.message));
    }
  }

  // ---------- theme & boot ----------------------------------------------------------------------
  const root = document.documentElement;
  try { const t = localStorage.getItem("agflow-theme"); if (t) root.dataset.theme = t; } catch (_) { /* ignore */ }
  document.getElementById("theme").addEventListener("click", () => {
    const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    root.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("agflow-theme", root.dataset.theme); } catch (_) { /* ignore */ }
  });

  window.addEventListener("hashchange", route);
  api("/overview").then(o => {
    document.getElementById("workspace").textContent = o.workspace;
    document.getElementById("version").textContent = "v" + o.version;
  }).catch(() => {});
  route();
})();
