# Hook payload fixtures

Each `*.json` file is one hook stdin payload plus what agflow must do with it. `tests/test_hooks_contract.py` runs
every file in this folder, so adding a file adds a test.

```json
{
  "_provenance": "vendor-doc | captured | synthetic",
  "_source": "where it came from",
  "event": "pre-tool-use | post-tool-use | stop",
  "payload": { "...": "the JSON Antigravity sends on stdin; use <WS> for the workspace path" },
  "expect": {
    "problems": [],          // what agflow's shape check must report ([] = a shape agflow understands)
    "decision": "deny"       // pre-tool-use only: "deny" | "ask" | null (no opinion)
  }
}
```

## The files

| File | Provenance | What it pins |
|---|---|---|
| `vendor_pre_tool_use_dangerous.json` | vendor-doc | the documented layout carrying `rm -rf /` is denied |
| `vendor_pre_tool_use_ask.json` | vendor-doc | the documented layout carrying a force-push asks |
| `vendor_pre_tool_use_harmless.json` | vendor-doc | the documented layout carrying `npm test` raises no alarm and gives no opinion |
| `vendor_post_tool_use.json` | vendor-doc | the documented post-tool payload is understood |
| `vendor_stop.json` | vendor-doc | the documented stop payload is understood |
| `synthetic_legacy_flat_keys.json` | synthetic | the older flat key names still work |
| `synthetic_drift_unknown_shape.json` | synthetic | a layout agflow does not know raises `unknown_shape` |
| `synthetic_drift_command_key_renamed.json` | synthetic | a renamed command argument raises `guard_blind:command` |
| `synthetic_drift_path_key_renamed.json` | synthetic | a renamed path argument raises `guard_blind:path` |

`tests/test_sync_audits.py` (S3) fails for a fixture that is missing from this table.

## Provenance — read this before trusting a fixture

* **`vendor-doc`** — taken from Antigravity's own documentation of the hook contract that ships inside the product
  (`~/.gemini/antigravity*/builtin/skills/agy-customizations/docs/hooks.md`). Values are the documentation's
  examples (paths and ids replaced by placeholders). This is the best contract evidence available without access to a
  running Antigravity, but it is **documentation, not a recording**.
* **`captured`** — a payload recorded from a real Antigravity session. **None exist yet.** To add one: set
  `AGFLOW_HOOK_DEBUG=1`, run Antigravity, and copy a line of `~/.gemini/config/agflow/hook-debug.jsonl`
  (its `payload` field) into a new file here with `"_provenance": "captured"`. Remove anything private first.
* **`synthetic`** — written by hand to exercise a case (an older guess at the shape, or a deliberate drift such as a
  renamed key). They prove the alarm fires; they say nothing about what Antigravity really sends.
