"""Graph schema: a DAG of agent / gate / human nodes with optional bounded rework loops.

Example::

    id: feature-delivery
    inputs:
      feature: {type: string, required: true}
    nodes:
      - id: spec
        agent: architect
        skill: plan-spec
        prompt: "Write a spec for {{ inputs.feature }}"
      - id: implement
        agent: implementer
        depends_on: [spec]
      - id: quality-gate
        type: gate
        depends_on: [implement]
        on_fail: {goto: implement, max_loops: 2}
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from jinja2 import TemplateSyntaxError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from agflow.fsutil import yaml_safe

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
DEFAULT_TIMEOUT_MIN = 30
TIMEOUT_MIN_RANGE = (1, 24 * 60)  # minutes a node may be given; the headless runner clamps to the same bounds


class GraphError(ValueError):
    pass


class InputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["string", "boolean", "number", "integer"] = "string"
    required: bool = False
    default: Any = None
    description: str = ""


class Defaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retries: int = Field(1, ge=0, le=10, description="extra attempts after a failed attempt")
    timeout_min: int = Field(DEFAULT_TIMEOUT_MIN, ge=TIMEOUT_MIN_RANGE[0], le=TIMEOUT_MIN_RANGE[1])
    max_parallel: int = Field(4, ge=1, le=64)


class OnFail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    goto: str
    max_loops: int = Field(2, ge=1, le=10)


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: Literal["agent", "gate", "human"] = "agent"
    title: str = ""
    agent: str | None = None
    skill: str | None = None
    prompt: str = ""
    depends_on: list[str] = Field(default_factory=list)
    when: str | None = None
    retries: int | None = Field(None, ge=0, le=10)
    timeout_min: int | None = Field(None, ge=TIMEOUT_MIN_RANGE[0], le=TIMEOUT_MIN_RANGE[1])
    outputs: list[str] = Field(default_factory=list)
    acceptance: list[str] = Field(default_factory=list)
    require: Literal["all_succeeded", "any_succeeded"] = "all_succeeded"
    on_fail: OnFail | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"node id {v!r} must match {ID_RE.pattern}")
        return v

    @model_validator(mode="after")
    def _by_type(self) -> Node:
        if self.type == "agent" and not self.agent:
            raise ValueError(f"agent node '{self.id}' needs an 'agent'")
        if self.type != "agent" and (self.agent or self.skill):
            raise ValueError(f"{self.type} node '{self.id}' cannot set agent/skill")
        if self.type != "gate" and self.on_fail is not None:
            raise ValueError(f"only gate nodes support on_fail (node '{self.id}')")
        if self.type == "gate" and not self.depends_on:
            raise ValueError(f"gate '{self.id}' must depend on the nodes it checks")
        if self.type == "human" and not self.prompt:
            raise ValueError(f"human node '{self.id}' needs a prompt (the question for the user)")
        return self

    @property
    def label(self) -> str:
        return self.title or self.id


class Graph(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int = 1
    title: str = ""
    description: str = ""
    inputs: dict[str, InputSpec] = Field(default_factory=dict)
    defaults: Defaults = Field(default_factory=Defaults)
    nodes: list[Node]

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"graph id {v!r} must match {ID_RE.pattern}")
        return v

    @model_validator(mode="after")
    def _structure(self) -> Graph:
        ids = [n.id for n in self.nodes]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate node ids: {dupes}")
        if not self.nodes:
            raise ValueError("graph has no nodes")
        known = set(ids)
        for n in self.nodes:
            missing = [d for d in n.depends_on if d not in known]
            if missing:
                raise ValueError(f"node '{n.id}' depends on unknown node(s) {missing}")
            if n.id in n.depends_on:
                raise ValueError(f"node '{n.id}' depends on itself")
            repeated = sorted({d for d in n.depends_on if n.depends_on.count(d) > 1})
            if repeated:
                raise ValueError(f"node '{n.id}' lists the same dependency more than once: {repeated}")
        order = topo_order(self)  # raises on cycles
        for n in self.nodes:
            if n.on_fail:
                if n.on_fail.goto not in known:
                    raise ValueError(f"gate '{n.id}' on_fail.goto '{n.on_fail.goto}' is not a node")
                if n.on_fail.goto not in ancestors(self, n.id):
                    raise ValueError(f"gate '{n.id}' can only rework an upstream node, not '{n.on_fail.goto}'")
        _check_templates(self)
        del order
        return self

    # -- helpers ---------------------------------------------------------------------------
    def node(self, node_id: str) -> Node:
        for n in self.nodes:
            if n.id == node_id:
                return n
        raise KeyError(node_id)

    def retries_for(self, node: Node) -> int:
        return self.defaults.retries if node.retries is None else node.retries

    def timeout_for(self, node: Node) -> int:
        return self.defaults.timeout_min if node.timeout_min is None else node.timeout_min


def topo_order(graph: Graph) -> list[str]:
    indeg = {n.id: len(n.depends_on) for n in graph.nodes}
    children: dict[str, list[str]] = {n.id: [] for n in graph.nodes}
    for n in graph.nodes:
        for d in n.depends_on:
            children[d].append(n.id)
    queue = [n.id for n in graph.nodes if indeg[n.id] == 0]
    order: list[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for c in children[cur]:
            indeg[c] -= 1
            if indeg[c] == 0:
                queue.append(c)
    if len(order) != len(graph.nodes):
        cyclic = sorted(set(indeg) - set(order))
        raise ValueError(f"dependency cycle among nodes {cyclic}")
    return order


def ancestors(graph: Graph, node_id: str) -> set[str]:
    by_id = {n.id: n for n in graph.nodes}
    out: set[str] = set()
    stack = list(by_id[node_id].depends_on)
    while stack:
        cur = stack.pop()
        if cur not in out:
            out.add(cur)
            stack.extend(by_id[cur].depends_on)
    return out


def descendants(graph: Graph, node_id: str) -> set[str]:
    out: set[str] = set()
    frontier = {node_id}
    while frontier:
        nxt = {n.id for n in graph.nodes if set(n.depends_on) & frontier} - out
        out |= nxt
        frontier = nxt
    return out


def _check_templates(graph: Graph) -> None:
    from agflow.graph.templating import env

    e = env()
    for n in graph.nodes:
        try:
            e.from_string(n.prompt)
            for o in n.outputs:
                e.from_string(o)
            if n.when:
                e.compile_expression(n.when)
        except TemplateSyntaxError as exc:
            raise ValueError(f"node '{n.id}': template syntax error: {exc}") from exc


def parse_graph(data: Any, source: str = "<graph>") -> Graph:
    if not isinstance(data, dict):
        raise GraphError(f"{source}: graph must be a YAML mapping")
    try:
        return Graph.model_validate(data)
    except ValidationError as exc:
        msgs = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'graph'}: {e['msg']}" for e in exc.errors())
        raise GraphError(f"{source}: {msgs}") from exc


def load_graph(path: Path) -> Graph:
    try:
        data = yaml_safe().load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # ruamel raises several error types
        raise GraphError(f"{path}: invalid YAML: {exc}") from exc
    return parse_graph(data, str(path))


def parse_graph_text(text: str, source: str = "<graph>") -> Graph:
    try:
        data = yaml_safe().load(text)
    except Exception as exc:
        raise GraphError(f"{source}: invalid YAML: {exc}") from exc
    return parse_graph(data, source)


def coerce_inputs(graph: Graph, given: dict[str, Any] | None) -> dict[str, Any]:
    given = dict(given or {})
    unknown = sorted(set(given) - set(graph.inputs))
    if unknown:
        raise GraphError(f"unknown input(s) {unknown}; graph accepts {sorted(graph.inputs)}")
    out: dict[str, Any] = {}
    for name, spec in graph.inputs.items():
        if name in given and given[name] is not None:
            out[name] = _coerce(name, spec, given[name])
        elif spec.required:
            hint = f" ({spec.description})" if spec.description else ""
            raise GraphError(f"missing required input '{name}'{hint}")
        else:
            out[name] = spec.default
    return out


def _coerce(name: str, spec: InputSpec, value: Any) -> Any:
    try:
        if spec.type == "boolean":
            if isinstance(value, str):
                v = value.strip().lower()
                if v in ("true", "yes", "1", "y", "on"):
                    return True
                if v in ("false", "no", "0", "n", "off", ""):
                    return False
                raise ValueError(value)
            return bool(value)
        if spec.type == "integer":
            return int(value)
        if spec.type == "number":
            return float(value)
        return str(value)
    except (TypeError, ValueError) as exc:
        raise GraphError(f"input '{name}' must be a {spec.type}, got {value!r}") from exc
