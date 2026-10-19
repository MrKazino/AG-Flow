# Research note — what AG-Flow 0.4.0 relies on, and where each fact comes from

Written 2026-10-16 for release 0.4.0. The design of the compatibility check, the updater, the evidence gate and the
manager panel rests on the facts below. Each was taken from a source that can be opened again, not from memory; where a
fact could not be verified, the last column says so. Nothing from the vendor's documents is copied into the repository:
`agflow sync` keeps hashes and short identifiers only.

## Sources

| Short name | What it is | How to open it again |
|---|---|---|
| **docs set** | The documentation that ships with the Antigravity CLI: `agy-customizations/docs/{hooks,plugins,json_configs,mcp_servers,skills,rules}.md` and the built-in skills `plugin`, `ui-plugin-navigation`, `automation` | `~/.gemini/antigravity/builtin/skills/` (the exact folder is found by `agflow sync check`) |
| **web docs** | Antigravity's published documentation, pages `/docs/plugins` and `/docs/sidecars` | `https://antigravity.google/docs/sidecars` |
| **sidecar runtime** | The code of a sidecar that ships with Antigravity (`science`), read to learn what a UI sidecar receives from the app | inside Antigravity's own installation (it is a sidecar that ships with the app, not a file of this repository) |
| **agy** | The installed CLI, version 1.3.3 on the development machine: `agy --version`, `agy --help`, `agy plugin --help`, and the change log embedded in the binary | run the commands; the change log is text inside the executable |
| **PyPI** | The JSON API of the Python Package Index | `https://pypi.org/pypi/agflow/json` |
| **machine logs** | The Antigravity CLI log and conversation transcripts on the development machine (used for H1) | `~/.gemini/antigravity-cli/log/` |

## Facts

| # | Fact | Source | Consequence in 0.4.0 | Verified how |
|---|---|---|---|---|
| 1 | Antigravity's **Settings screen is not an extension point.** A plugin adds UI through a **sidecar** whose web panel opens in the side pane, opened from chat by a link of the form `sidecar://<plugin>/<folder>/<path>`; a plugin can also appear under Customizations | docs set (`plugin`, `ui-plugin-navigation`), web docs | "The manager inside Antigravity" is a side-pane panel and the plugin's own page; AG-Flow does not claim to add a section to Settings | Read in the docs set and the web docs. Not seen in a live window. |
| 2 | `sidecar.json` fields: `command`, `args`, `env`, `restart_policy` (`always` / `on-failure` / `never`), `has_web_ui`, `display_name`, `description`, `ui_config.views[]` with `entrypoint: SIDECAR_UI_ENTRYPOINT_AUX_PANE`, `path`, `title` | docs set, web docs, the sidecars shipped with Antigravity | `plugin/sidecars/manager/sidecar.json`; doctor validates the same fields | Read; two real `sidecar.json` files compared |
| 3 | A plugin's sidecars live at `~/.gemini/config/plugins/<plugin>/sidecars/<name>/`, with the id `<plugin>/<name>`; the sidecar's working directory is its own folder; whether one runs is `sidecars["<id>"].enabled` in `~/.gemini/config/config.json` | docs set, web docs | The installer wires the files; the panel cannot learn the open project from its working directory and starts on the workspace with the most recent run | Read |
| 4 | **Sidecars are off by default and an agent must not enable one**; the user does, from the Automations/Customizations pages or in `config.json` | docs set (`automation`), web docs | AG-Flow writes definitions and never writes `enabled`; `agflow panel status` prints the steps (a test pins that nothing writes Antigravity's config) | Read |
| 5 | A UI sidecar listens on `127.0.0.1:$ANTIGRAVITY_SIDECAR_WEB_PORT`; the token is in `ANTIGRAVITY_SIDECAR_UI_TOKEN` (the runtime also reads `_AUTH_TOKEN` and `_WEB_TOKEN`); the pane sends it as the header `x-sidecar-token`, and also on the first address; other variables: `ANTIGRAVITY_PROJECT_ID`, `_CONVERSATION_ID`, `_APP_DATA_DIR` | sidecar runtime | `agflow dashboard --sidecar`: loopback only, refuses to start without the token, every `/api/*` call needs it | Read in the vendor runtime; reproduced by tests and in a browser. **Not documented as an API, and not exercised in a live Antigravity window.** |
| 6 | `builtin: schedule` is a sidecar whose `args[0]` is a cron expression (optionally prefixed with `CRON_TZ=`) followed by any command and its arguments | web docs (`/docs/sidecars`), docs set (`automation`) | `plugin/sidecars/maintenance/sidecar.json` runs `agflow maintenance run` daily; it uses no quota because it starts no conversation | Read in both |
| 7 | `plugin.json` documents seven fields: `name`, `displayName`, `description`, `logo`, `suggestedPrompts` (at most three are shown), `version`, `disabled`; **other fields are dropped without a message** | docs set (`plugin`), web docs (`/docs/plugins`) | The manifest keeps only those; doctor reports others as `plugin.manifest.field` | Read in both |
| 8 | `agy plugin` has `install`, `list`, `enable`, `disable`, `uninstall`; installing from a GitHub URL is documented. **`agy plugin update` and custom marketplaces are not documented**; plugin management calls exist only as undocumented language-server calls | agy (`--help`), web docs | AG-Flow updates its own plugin with `agflow install` (which it controls) and does not depend on an undocumented call | `agy plugin --help` read on 1.3.3 |
| 9 | **PyPI has no project named `agflow`** (the JSON API answers 404 on 2026-10-16), so anyone could register the name | PyPI | Updates come only from the GitHub Releases of `MrKazino/AG-Flow`; `pip install agflow` appears nowhere | Request made on the date above. A name can be taken at any time; the design does not depend on it staying free. |
| 10 | The hook, plugin and sidecar contracts changed in most 1.x releases (empty hook decisions in 1.0.16, hooks breaking file-editing tools in 1.1.7, matchers on non-tool steps in 1.1.9, hook ordering, plugin enable/disable fixes) | agy (change log in the binary) | Drift is routine, so AG-Flow checks for it instead of assuming (`agflow sync`) | Read on 1.3.3 |
| 11 | A hook that fails is an error for the agent, not a log line; the CLI answers an unrecognised pre-tool decision by blocking | agy (strings in the binary), machine logs | Every hook command fails open (H1); the hook probe runs them the way Antigravity does | Read; the first entry in the machine log is the H1 evidence |
| 12 | Baseline cost on the development machine before 0.4.0: `agflow version` 548–560 ms, `import agflow.mcp_server` 1.4 s, dashboard import 1.0 s, idle hook 187–193 ms, `doctor` 2.9–3.2 s | measured with `scripts/bench.py` | Targets and the table in the README | Measured; one machine |

## What is still unverified

* **No live Antigravity window was available.** Facts 1–6 rest on the documents and the vendor's runtime. The first real
  check is switching the panel on (`agflow panel status` says how) and opening `sidecar://agflow/manager/`.
* **No GitHub release of AG-Flow exists yet**, so the update path was exercised against a local fake of the GitHub API and
  with real wheels in a throwaway environment and a sandboxed home folder, not against github.com.
* **The documents were available for agy 1.3.3 only.** `compat_baseline.json` therefore lists 1.3.3 as the tested version;
  other versions get `drift` or `breaking` findings until `agflow sync apply` (or a new release) accepts them.
* **The effect on hallucination is not measured.** The evidence gate, the verifier and the grounding protocol make an
  unsupported claim visible and harder to pass on; no live model was run to measure how often one occurs.

## Re-verifying

```bash
agflow sync check                                   # the installed Antigravity against the baseline
python scripts/make_compat_baseline.py --dry-run    # which requirements hold for the installed Antigravity
python scripts/smoke_release.py                     # real wheels: update, rollback, a broken release refused
```
