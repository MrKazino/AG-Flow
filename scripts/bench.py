"""Measure the two performance claims behind F3 and F27: hook latency vs run count, doctor runtime on a big plugin.

    uv run python scripts/bench.py [--runs 1,50,500] [--running 1] [--skills 200] [--only hooks|doctor]

* hooks: a workspace with N historical runs, ``--running`` of them still running (the realistic shape: most runs
  are finished). Reports the in-process PostToolUse handler and the real ``agflow hook`` subprocess wall time.
* doctor: ``agflow doctor`` on a synthetic plugin (skills with local references, agents, always-on rules).

Everything happens under a temporary HOME and workspace; nothing touches the real ~/.gemini. Output is JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path


def _median_ms(fn: Callable[[], object], repeat: int) -> float:
    samples = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(samples), 1)


def bench_hooks(base: Path, counts: list[int], running: int) -> dict[str, dict[str, float]]:
    from agflow import hooks
    from agflow.graph import engine
    from agflow.graph.catalog import resolve
    from agflow.graph.schema import coerce_inputs
    from agflow.graph.store import RunStore

    ws = base / "hook-ws"
    (ws / ".git").mkdir(parents=True)
    graph, _ = resolve("bugfix", ws)
    store = RunStore(ws)
    payload = {"cwd": str(ws), "tool_name": "run_command", "tool_input": {"CommandLine": "ls"}}
    out: dict[str, dict[str, float]] = {}
    created = 0
    for target in counts:
        while created < target:
            state = engine.new_run(graph, coerce_inputs(graph, {"bug": str(created)}), str(ws), now=1_000.0 + created)
            if created >= running:
                state.status = "succeeded"  # a finished run: the common case
            store._write(state)
            created += 1

        def run_subprocess() -> None:
            subprocess.run([sys.executable, "-m", "agflow", "hook", "post-tool-use"], input=json.dumps(payload),
                           capture_output=True, text=True, check=True)

        hooks.post_tool_use(payload)  # warm: the first call after an upgrade indexes what it has not seen
        out[str(target)] = {
            "post_tool_use_inprocess_ms": _median_ms(lambda: hooks.post_tool_use(payload), repeat=7),
            "post_tool_use_subprocess_ms": _median_ms(run_subprocess, repeat=3),
        }
    return out


def bench_doctor(base: Path, skills: int) -> dict[str, float]:
    from agflow import paths
    from agflow.inventory import Inventory
    from agflow.validate import validate_inventory

    plugin = base / "big-plugin"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "big-plugin", "version": "1.0.0", "description": "synthetic"}), encoding="utf-8")
    for i in range(skills):
        d = plugin / "skills" / f"skill-{i:04d}"
        (d / "references").mkdir(parents=True)
        refs = [f"references/ref-{j}.md" for j in range(5)]
        for r in refs:
            (d / r).write_text(f"# {r}\n", encoding="utf-8")
        body = "\n".join(f"See [ref]({r}) and `{r}`." for r in refs)
        (d / "SKILL.md").write_text(
            f"---\nname: skill-{i:04d}\ndescription: Synthetic skill number {i} used to measure doctor runtime. "
            f"Use when benchmarking.\n---\n# T\n{body}\n", encoding="utf-8")
    (plugin / "agents").mkdir()
    for i in range(max(1, skills // 4)):
        (plugin / "agents" / f"agent-{i:03d}.md").write_text(
            f"---\nname: agent-{i:03d}\ndescription: Synthetic agent {i} for the doctor benchmark.\nsubagent: true\n"
            "model: pro\ncommandExecutionPolicy: auto\ntools:\n  - view_file\n  - run_command\n---\n# Agent\nWork.\n",
            encoding="utf-8")
    (plugin / "rules").mkdir()
    for i in range(max(1, skills // 2)):
        text = "---\ntrigger: always_on\ndescription: synthetic rule\n---\n" + ("rule text " * 40) + "\n"
        (plugin / "rules" / f"rule-{i:03d}.md").write_text(text, encoding="utf-8")

    def run() -> None:
        inv = Inventory.discover(None, include_global=False, include_plugins=False, include_legacy=False,
                                 extra_layouts=[paths.plugin_layout(plugin)])
        validate_inventory(inv)

    return {"items": skills + max(1, skills // 4) + max(1, skills // 2), "doctor_ms": _median_ms(run, repeat=3)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", default="1,50,500", help="comma-separated run counts (default 1,50,500)")
    ap.add_argument("--running", type=int, default=1, help="how many runs stay 'running' (default 1)")
    ap.add_argument("--skills", type=int, default=200, help="skills in the synthetic plugin (default 200)")
    ap.add_argument("--only", choices=("hooks", "doctor"), help="run just one of the benchmarks")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="agflow-bench-") as tmp:
        base = Path(tmp)
        home = base / "home"
        home.mkdir()
        os.environ["HOME"] = os.environ["USERPROFILE"] = str(home)
        result: dict[str, object] = {}
        if args.only in (None, "hooks"):
            result["hooks"] = bench_hooks(base, [int(x) for x in args.runs.split(",")], args.running)
        if args.only in (None, "doctor"):
            result["doctor"] = bench_doctor(base, args.skills)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
