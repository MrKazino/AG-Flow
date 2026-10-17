"""Sandboxed Jinja2 environment for node prompts, outputs and `when` conditions."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment


class _Environment(SandboxedEnvironment):
    def getattr(self, obj: Any, attribute: str) -> Any:
        """``inputs.items`` is the input called ``items``, not ``dict.items``.

        Jinja prefers a Python attribute to a mapping key, so a key named like a dict method (``items``, ``keys``,
        ``values``, ``get``, ``copy``) rendered as ``<built-in method ...>``. A template author reading ``a.b`` on a
        mapping means the entry ``b``; the methods stay reachable whenever there is no such entry.
        """
        if isinstance(obj, Mapping) and attribute in obj:
            return obj[attribute]
        return super().getattr(obj, attribute)


@lru_cache(maxsize=1)
def env() -> SandboxedEnvironment:
    return _Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


def render(template: str, ctx: dict[str, Any]) -> str:
    return env().from_string(template).render(**ctx)


def evaluate(expression: str, ctx: dict[str, Any]) -> Any:
    return env().compile_expression(expression, undefined_to_none=False)(**ctx)
