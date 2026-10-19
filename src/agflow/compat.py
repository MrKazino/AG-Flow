"""Is AG-Flow still compatible with the Antigravity that is installed?

:func:`check` compares what :mod:`agflow.antigravity_probe` sees against a baseline: the names and layouts AG-Flow was
verified against, plus a list of **requirements** (names AG-Flow's own hooks, manifests and configs depend on). Four
answers, each naming the AG-Flow component affected:

* ``breaking`` - something AG-Flow depends on is no longer in Antigravity's docs;
* ``drift``    - Antigravity changed in a way AG-Flow was not verified against (names added or removed, a new major
  version); nothing AG-Flow depends on is gone, but it should be re-verified (``agflow sync apply``);
* ``ok``       - the same vocabulary AG-Flow was verified against (docs may be reworded);
* ``unknown``  - nothing to compare (Antigravity is not installed, or its docs are not where they were): never an alarm.

The shipped baseline is data (``data/compat_baseline.json``). When the user re-verifies a newer Antigravity, the result
is stored as an *accepted* baseline in AG-Flow's state folder, so an acknowledged change stops raising the same alarm.
Nothing here modifies Antigravity or the user's configuration.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agflow import antigravity_probe as ap
from agflow import fsutil, paths, versions

SCHEMA = 1
BASELINE_FILE = Path(__file__).resolve().parent / "data" / "compat_baseline.json"
ACCEPTED_NAME = "compat-accepted.json"
LAST_NAME = "compat-last.json"

STRUCTURAL = ("fields", "table_keys", "values", "layouts", "limits", "tree_files")  # a change here is drift
INFORMATIONAL = ("json_keys", "code_terms")  # example or prose changes: noted only
SHOWN = 8  # names listed per finding

LEVELS = ("info", "unknown", "drift", "breaking")  # ascending severity
EXIT_CODE = {"ok": 0, "unknown": 0, "drift": 1, "breaking": 2}
_FINDING_KEYS = ("level", "code", "message", "component", "action")

UPDATE_HINT = "Update AG-Flow (`agflow update check`), then run `agflow sync apply`."
REVERIFY_HINT = "Run `agflow sync apply` to re-verify AG-Flow against this Antigravity and accept it."

Diff = dict[str, tuple[list[str], list[str]]]


@dataclass(frozen=True)
class Requirement:
    """A name AG-Flow depends on: ``value`` must appear among the ``kind`` markers of ``doc``."""

    doc: str
    kind: str
    value: str
    component: str
    why: str = ""
    action: str = ""


@dataclass(frozen=True)
class Finding:
    level: str  # info | unknown | drift | breaking
    code: str
    message: str
    component: str = ""
    action: str = ""

    def to_json(self) -> dict[str, str]:
        return {k: getattr(self, k) for k in _FINDING_KEYS}


@dataclass
class Report:
    status: str = "unknown"
    findings: list[Finding] = field(default_factory=list)
    agy_version: str | None = None
    verified_agy_version: str | None = None
    baseline: str = "none"  # shipped | accepted | none
    checked_at: str = ""

    @property
    def exit_code(self) -> int:
        return EXIT_CODE.get(self.status, 0)

    def at_level(self, level: str) -> list[Finding]:
        return [f for f in self.findings if f.level == level]

    def to_json(self) -> dict[str, Any]:
        return {"status": self.status, "agy_version": self.agy_version,
                "verified_agy_version": self.verified_agy_version, "baseline": self.baseline,
                "checked_at": self.checked_at, "findings": [f.to_json() for f in self.findings]}

    @classmethod
    def from_json(cls, data: Any) -> Report:
        """Rebuild a stored report; anything malformed becomes an empty ``unknown`` report, never an error."""
        if not isinstance(data, dict):
            return cls()
        raw = data.get("findings")
        findings = [Finding(**{k: str(item.get(k, "")) for k in _FINDING_KEYS})
                    for item in raw if isinstance(item, dict) and item.get("level") in LEVELS] \
            if isinstance(raw, list) else []
        status = data.get("status")
        return cls(status=status if status in EXIT_CODE else "unknown", findings=findings,
                   agy_version=_opt(data.get("agy_version")),
                   verified_agy_version=_opt(data.get("verified_agy_version")),
                   baseline=_opt(data.get("baseline")) or "none", checked_at=_opt(data.get("checked_at")) or "")

    def lines(self) -> list[str]:
        """Plain-text summary for the terminal."""
        head = {"ok": "AG-Flow is compatible with the installed Antigravity.",
                "drift": "Antigravity changed since AG-Flow was last verified against it.",
                "breaking": "Antigravity no longer documents something AG-Flow depends on.",
                "unknown": "AG-Flow could not verify Antigravity (nothing to compare)."}[self.status]
        out = [head]
        if self.agy_version or self.verified_agy_version:
            out.append(f"  Antigravity {self.agy_version or 'not found'}; verified against "
                       f"{self.verified_agy_version or 'unknown'} ({self.baseline} baseline).")
        for finding in sorted(self.findings, key=lambda f: -LEVELS.index(f.level)):
            where = f"{finding.component}: " if finding.component else ""
            out.append(f"  [{finding.level}] {where}{finding.message}")
            if finding.action:
                out.append(f"      -> {finding.action}")
        return out


def _opt(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _strs(value: Any) -> list[str]:
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z"


# --- baselines -------------------------------------------------------------------------------------------------------


def _read(path: Path) -> Any:
    try:
        return fsutil.read_json(path, default=None)
    except (OSError, ValueError):
        return None


def shipped() -> dict[str, Any]:
    """The baseline that ships with this AG-Flow (empty when absent or unreadable)."""
    data = _read(BASELINE_FILE)
    return data if isinstance(data, dict) and data.get("schema") == SCHEMA else {}


def accepted_path(state_dir: Path | None = None) -> Path:
    return (state_dir or paths.global_state_dir()) / ACCEPTED_NAME


def last_path(state_dir: Path | None = None) -> Path:
    return (state_dir or paths.global_state_dir()) / LAST_NAME


def parse_requirements(baseline: dict[str, Any]) -> tuple[list[Requirement], list[str]]:
    """The valid requirements, and a description of each malformed entry (so a packaging mistake is visible)."""
    items = baseline.get("requires")
    if not isinstance(items, list):
        return [], ["requires is missing"] if baseline else []
    good: list[Requirement] = []
    problems: list[str] = []
    for index, raw in enumerate(items):
        ok = isinstance(raw, dict) and raw.get("kind") in ap.MARKER_KINDS and all(
            isinstance(raw.get(k), str) and raw[k] for k in ("doc", "value", "component"))
        if ok:
            good.append(Requirement(raw["doc"], raw["kind"], raw["value"], raw["component"],
                                    str(raw.get("why", "")), str(raw.get("action", ""))))
        else:
            problems.append(f"requires[{index}] is malformed")
    return good, problems


def effective(state_dir: Path | None = None) -> tuple[dict[str, Any], str]:
    """The baseline to check against: the shipped requirements, with the user's accepted snapshot (if any) as the
    verified state. Returns ``(baseline, "shipped" | "accepted" | "none")``."""
    base = shipped()
    if not base:
        return {}, "none"
    saved = _read(accepted_path(state_dir))
    if isinstance(saved, dict) and saved.get("schema") == SCHEMA and isinstance(saved.get("snapshot"), dict):
        merged = dict(base)
        merged["verified"] = saved["snapshot"]
        merged["tested_antigravity"] = sorted({*_strs(base.get("tested_antigravity")),
                                               *_strs(saved.get("tested_antigravity"))})
        return merged, "accepted"
    return base, "shipped"


# --- the comparison --------------------------------------------------------------------------------------------------


def marker_diff(old: dict[str, list[str]], new: dict[str, list[str]], kinds: tuple[str, ...]) -> Diff:
    """``kind -> (added, removed)`` for the kinds that changed."""
    out: Diff = {}
    for kind in kinds:
        before, after = set(old.get(kind, [])), set(new.get(kind, []))
        if before != after:
            out[kind] = (sorted(after - before), sorted(before - after))
    return out


def _names(items: list[str]) -> str:
    shown = ", ".join(items[:SHOWN])
    return shown + (f" (+{len(items) - SHOWN} more)" if len(items) > SHOWN else "")


def _describe(diff: Diff) -> str:
    parts = []
    for kind, (added, removed) in diff.items():
        label = kind.replace("_", " ")
        if added:
            parts.append(f"new {label}: {_names(added)}")
        if removed:
            parts.append(f"gone {label}: {_names(removed)}")
    return "; ".join(parts)


def check(snap: ap.Snapshot, baseline: dict[str, Any], *, source: str = "shipped", now: str | None = None) -> Report:
    """Compare ``snap`` with ``baseline``. Pure: no file is read or written, nothing is run."""
    findings: list[Finding] = []
    verified = ap.Snapshot.from_json(baseline.get("verified"))
    requires, problems = parse_requirements(baseline)
    report = Report(agy_version=snap.agy_version, verified_agy_version=verified.agy_version,
                    baseline=source if baseline else "none", checked_at=now or _now())

    if not baseline:
        findings.append(Finding("unknown", "baseline-missing",
                                "AG-Flow's compatibility baseline is missing or unreadable.", "agflow",
                                "Reinstall AG-Flow."))
    findings += [Finding("unknown", "baseline-malformed", f"the compatibility baseline is malformed ({p}).",
                         "agflow", "Reinstall AG-Flow.") for p in problems]
    if not snap.has_docs:
        findings.append(Finding("unknown", "docs-missing", "Antigravity's shipped documentation was not found, so "
                                "what AG-Flow depends on could not be verified.", "antigravity"))
    if snap.agy_path is None and snap.agy_version is None:
        findings.append(Finding("unknown", "agy-missing", "the Antigravity CLI (`agy`) was not found.",
                                "antigravity"))

    # 1. what AG-Flow depends on must still be there
    for req in requires:
        if req.doc not in snap.markers:
            continue  # reported once, by docs-missing or doc-gone
        if req.value not in snap.markers[req.doc].get(req.kind, []):
            why = f" ({req.why})" if req.why else ""
            findings.append(Finding(
                "breaking", "requirement-missing",
                f"`{req.value}` is no longer in {req.doc} ({req.kind.replace('_', ' ')}){why}.",
                req.component, req.action or UPDATE_HINT))

    # 2. what changed since AG-Flow was verified
    if verified.has_docs and snap.has_docs:
        findings += _doc_findings(verified, snap)

    # 3. versions
    findings += _version_findings(snap, verified, _strs(baseline.get("tested_antigravity")))

    levels = {f.level for f in findings}
    report.findings = findings
    report.status = ("breaking" if "breaking" in levels else "drift" if "drift" in levels else
                     "unknown" if not snap.has_docs or not baseline else "ok")
    return report


def _doc_findings(verified: ap.Snapshot, snap: ap.Snapshot) -> list[Finding]:
    out: list[Finding] = []
    for doc in sorted(set(verified.doc_hashes) | set(snap.doc_hashes)):
        before, after = verified.doc_hashes.get(doc), snap.doc_hashes.get(doc)
        if before == after:
            continue
        if before is None:
            out.append(Finding("drift", "doc-new", f"{doc} is new in this Antigravity.", "antigravity", REVERIFY_HINT))
        elif after is None:
            out.append(Finding("drift", "doc-gone", f"{doc} is no longer shipped.", "antigravity", REVERIFY_HINT))
        else:
            old, new = verified.markers.get(doc, {}), snap.markers.get(doc, {})
            structural = marker_diff(old, new, STRUCTURAL)
            prose = marker_diff(old, new, INFORMATIONAL)
            if structural:
                out.append(Finding("drift", "markers-changed", f"{doc} changed: {_describe(structural)}.",
                                   "antigravity", REVERIFY_HINT))
            elif prose:
                out.append(Finding("info", "docs-mention-changed",
                                   f"{doc} mentions different names: {_describe(prose)}."))
            else:
                out.append(Finding("info", "doc-reworded",
                                   f"{doc} was reworded; the names it defines are unchanged."))
    return out


def _bump(old: str, new: str) -> str | None:
    """How ``new`` differs from ``old`` (see :func:`versions.bump_kind`), or None when either is not a version."""
    before, after = versions.parse(old), versions.parse(new)
    return versions.bump_kind(before, after) if before and after else None


def _version_findings(snap: ap.Snapshot, verified: ap.Snapshot, tested: list[str]) -> list[Finding]:
    out: list[Finding] = []
    if snap.agy_version and verified.agy_version and snap.agy_version not in {verified.agy_version, *tested}:
        kind = _bump(verified.agy_version, snap.agy_version)
        if kind == "major":
            out.append(Finding("drift", "agy-major", f"Antigravity moved from {verified.agy_version} to "
                               f"{snap.agy_version} (a new major version).", "antigravity", REVERIFY_HINT))
        elif kind in ("minor", "patch", "pre"):
            out.append(Finding("info", "agy-version", f"Antigravity {snap.agy_version} (AG-Flow was verified "
                               f"against {verified.agy_version}); the documented names are unchanged."))
        elif kind == "downgrade":
            out.append(Finding("info", "agy-older", f"Antigravity {snap.agy_version} is older than the "
                               f"{verified.agy_version} AG-Flow was verified against."))
    for label, now_v, then_v in (("Antigravity SDK", snap.sdk_version, verified.sdk_version),
                                 ("Gemini CLI", snap.gemini_cli_version, verified.gemini_cli_version)):
        if now_v and then_v and now_v != then_v and _bump(then_v, now_v) in ("major", "minor"):
            out.append(Finding("info", "tool-version", f"{label} moved from {then_v} to {now_v}."))
    return out


# --- stored results --------------------------------------------------------------------------------------------------


def accept(snap: ap.Snapshot, state_dir: Path | None = None, *, now: str | None = None) -> Path:
    """Record this snapshot as verified on this machine, so the same change stops being reported."""
    from agflow import __version__

    path = accepted_path(state_dir)
    fsutil.write_json(path, {"schema": SCHEMA, "accepted_at": now or _now(), "agflow": __version__,
                             "snapshot": snap.to_json(),
                             "tested_antigravity": [snap.agy_version] if snap.agy_version else []})
    return path


def forget_accepted(state_dir: Path | None = None) -> bool:
    """Go back to the shipped baseline. Returns whether there was an accepted one to remove."""
    path = accepted_path(state_dir)
    if path.exists():
        path.unlink()
        return True
    return False


def run(state_dir: Path | None = None, *, gemini_dir: Path | None = None, save: bool = True,
        snapshot: ap.Snapshot | None = None) -> Report:
    """Probe, compare with the effective baseline, and (by default) remember the result for banners and ``doctor``."""
    snap = snapshot if snapshot is not None else ap.probe(gemini_dir)
    baseline, source = effective(state_dir)
    report = check(snap, baseline, source=source)
    if save:
        save_last(report, state_dir)
    return report


def save_last(report: Report, state_dir: Path | None = None) -> None:
    with contextlib.suppress(OSError):  # a cache; never a reason to fail a check
        fsutil.write_json(last_path(state_dir), report.to_json())


def load_last(state_dir: Path | None = None, *, max_age_seconds: float | None = None) -> Report | None:
    """The last saved result, or None when there is none, it is unreadable, or it is older than ``max_age_seconds``."""
    path = last_path(state_dir)
    data = _read(path)
    if not isinstance(data, dict):
        return None
    if max_age_seconds is not None:
        try:
            if time.time() - path.stat().st_mtime > max_age_seconds:
                return None
        except OSError:
            return None
    return Report.from_json(data)


# --- re-verifying against a newer Antigravity -----------------------------------------------------------------------


@dataclass
class ApplyResult:
    report: Report
    problems: list[str] = field(default_factory=list)  # errors in AG-Flow's own installation (doctor + hook probe)
    accepted: bool = False
    would_accept: bool = False  # a dry run that reached the point of accepting
    path: Path | None = None
    notes: list[str] = field(default_factory=list)


def installation_problems(workspace: Path | None = None) -> list[str]:
    """Errors in AG-Flow's own installed plugin and hooks (``doctor`` plus the live hook probe). Problems in the
    user's other skills, agents or plugins are not AG-Flow's to judge here and are left out."""
    from agflow.inventory import Inventory
    from agflow.models import Level
    from agflow.validate import validate_inventory

    inventory = Inventory.discover(workspace, include_global=True)
    return [f"{i.code}: {i.message}" for i in validate_inventory(inventory, probe_hooks=True)
            if i.level == Level.ERROR and (i.source == f"plugin:{paths.PLUGIN_NAME}" or i.code == "hooks.broken")]


def apply(workspace: Path | None = None, *, state_dir: Path | None = None, snapshot: ap.Snapshot | None = None,
          dry_run: bool = False, verify: Callable[[Path | None], list[str]] | None = None) -> ApplyResult:
    """Re-verify AG-Flow against the installed Antigravity and, only when that holds, accept it as the new baseline.

    Refuses when something AG-Flow depends on is gone (accepting would hide a real break) and when AG-Flow's own
    installation has errors (the hook probe runs the installed hooks for real). Never edits Antigravity or any
    configuration; the only thing written is the accepted baseline."""
    snap = snapshot if snapshot is not None else ap.probe()
    baseline, source = effective(state_dir)
    report = check(snap, baseline, source=source)
    result = ApplyResult(report)
    if report.status == "breaking":
        result.notes.append("Not accepted: something AG-Flow depends on is gone from this Antigravity. Updating "
                            "AG-Flow is the fix (`agflow update check`).")
        return result
    if report.status == "unknown":
        result.notes.append("Nothing to verify against (Antigravity or its docs were not found); nothing changed.")
        return result
    if not report.findings:
        result.notes.append("Already verified: nothing has changed since AG-Flow was last checked.")
        return result
    result.problems = (verify or installation_problems)(workspace)
    if result.problems:
        result.notes.append("Not accepted: fix AG-Flow's own installation first (`agflow doctor --fix`), then run "
                            "this again.")
        return result
    if dry_run:
        result.would_accept = True
        result.notes.append("Would accept this Antigravity as verified (dry run; nothing written).")
        return result
    result.path = accept(snap, state_dir)
    result.accepted = True
    save_last(check(snap, effective(state_dir)[0], source="accepted"), state_dir)
    result.notes.append(f"Accepted: AG-Flow's installed hooks and plugin pass doctor and the hook probe with "
                        f"Antigravity {snap.agy_version or '(version unknown)'}.")
    return result


def dumps(report: Report) -> str:
    return json.dumps(report.to_json(), indent=2, ensure_ascii=False)
