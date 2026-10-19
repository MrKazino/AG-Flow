"""Hide credentials before data is written somewhere meant for logs, reports or screens.

A graph run can be started with inputs such as ``-i api_key=...``. The engine needs the real value (it renders
``{{ inputs.api_key }}`` into the subagent prompt, and ``state.json`` keeps it for that reason), but the append-only
ledger, ``report.md`` and ``report.json`` are read by people and tools, copied into tickets and committed by mistake.
Those derived artifacts go through this module first.

Two independent signals, both deliberately conservative (a false positive only blanks a displayed value):

* the **key name** looks like a secret (``api_key``, ``GITHUB_TOKEN``, ``password``, ``Authorization`` ...): the
  whole value is replaced, whatever it is;
* the **value** looks like a well-known credential (``sk-...``, ``ghp_...``, ``AIza...``, ``Bearer ...``, a PEM
  private-key header ...): just that part of the text is replaced, under any key.

Stdlib only; it is imported by the hook fast path's neighbours and must stay cheap.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "***REDACTED***"

# `AUTH` also matches author/authority, which are not secrets; "Authorization" is.
SECRET_KEY_RE = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH(?!OR(?!IZ)))", re.IGNORECASE)

_SECRET_VALUE_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),  # sk-... style API keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),  # Google API keys
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key ids
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def redact_text(text: str) -> str:
    """``text`` with every well-known credential shape replaced."""
    for pattern in _SECRET_VALUE_RES:
        text = pattern.sub(REDACTED, text)
    return text


def redact_mapping(data: Any, _key: str | None = None) -> Any:
    """A deep copy of ``data`` that is safe to write to a log or report. The argument is never modified."""
    if _key is not None and SECRET_KEY_RE.search(_key) and data not in (None, "", False, 0):
        return REDACTED
    if isinstance(data, dict):
        return {k: redact_mapping(v, str(k)) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact_mapping(v) for v in data]
    if isinstance(data, str):
        return redact_text(data)
    return data
