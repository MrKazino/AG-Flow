"""Sandboxed Jinja2 environment for node prompts, outputs and `when` conditions."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment


@lru_cache(maxsize=1)
def env() -> SandboxedEnvironment:
    return SandboxedEnvironment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


def render(template: str, ctx: dict[str, Any]) -> str:
    return env().from_string(template).render(**ctx)


def evaluate(expression: str, ctx: dict[str, Any]) -> Any:
    return env().compile_expression(expression, undefined_to_none=False)(**ctx)
