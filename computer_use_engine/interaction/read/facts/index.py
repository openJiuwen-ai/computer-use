# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Request-local indexes of unified observation facts for presentation accounting."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from functools import cache
from types import MappingProxyType
from typing import Any

from computer_use.interaction.read.facts.snapshot import _FactIndex
from computer_use.protocol.observation import (
    ElementValue,
    FieldPath,
    NativeNode,
    NativeObservation,
    ReadIssue,
    Supplement,
)

_DATA_ATOMS = frozenset((str, bool, int, bytes, type(None)))
_ISSUE_PRIORITY = {
    "conflict": 8,
    "error": 7,
    "timeout": 6,
    "unavailable": 5,
    "redacted": 5,
    "stale": 4,
    "partial": 3,
    "not_read": 2,
    "unsupported": 1,
    "absent": 0,
}


@cache
def _record_names(record_type: type) -> tuple[str, ...]:
    return tuple(item.name for item in fields(record_type))


def data(value: Any) -> Any:
    """Return a JSON-safe semantic value without native wrappers or object copies."""
    kind = type(value)
    if kind in _DATA_ATOMS:
        return value
    if kind is float:
        if math.isfinite(value):
            return value
        return MappingProxyType({"number": "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"})
    if kind is dict or kind is MappingProxyType:
        return MappingProxyType({str(key): data(item) for key, item in value.items()})
    if kind is tuple and all(type(item) in _DATA_ATOMS for item in value):
        return value
    if kind is tuple or kind is list:
        return tuple(data(item) for item in value)
    if isinstance(value, ElementValue):
        return MappingProxyType({"element": str(value.node)})
    if isinstance(value, datetime):
        return MappingProxyType({"datetime": value.isoformat()})
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        result = {}
        for name in _record_names(kind):
            content = getattr(value, name)
            if content is not None:
                result[name] = data(content)
        return MappingProxyType(result)
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): data(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(data(item) for item in value)
    return value


class _ReadIssueValue(Mapping):
    """Complete immutable JSON-semantic view over one captured native issue.

    Body consumers usually need only path/state. Explicit details still read
    every original field using the same semantic encoder as other source data.
    """

    __slots__ = ("_issue",)

    def __init__(self, issue):
        object.__setattr__(self, "_issue", issue)

    def __setattr__(self, name, value):
        raise AttributeError("captured issue values are immutable")

    def __delattr__(self, name):
        raise AttributeError("captured issue values are immutable")

    def __iter__(self):
        yield "path"
        yield "state"
        if self._issue.detail is not None:
            yield "detail"
        if self._issue.continuation is not None:
            yield "continuation"
        yield "alternatives"

    def __len__(self):
        return 3 + (self._issue.detail is not None) + (self._issue.continuation is not None)

    def __getitem__(self, key):
        if key not in ("path", "state", "detail", "continuation", "alternatives"):
            raise KeyError(key)
        value = getattr(self._issue, key)
        if value is None and key in ("detail", "continuation"):
            raise KeyError(key)
        return value if key in ("path", "state", "detail") else data(value)


def field_name(path: tuple[str | int, ...]) -> str:
    """Encode a semantic path without confusing mapping keys and separators."""
    import json

    result = ""
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        elif part.isidentifier():
            result += ("." if result else "") + part
        else:
            result += "[" + json.dumps(part, ensure_ascii=False) + "]"
    return result


def node_fields(
    node: NativeNode,
    *,
    name_for: Callable[[FieldPath], str] = field_name,
    supplement_data: Callable[[Supplement], Any] = data,
) -> dict[str, tuple[FieldPath, Any]]:
    """Index domain values alongside their exact paths in the immutable graph."""
    result = {}

    def add(path: tuple[str | int, ...], value: Any) -> None:
        result[name_for(path)] = (path, data(value))

    for item in fields(node):
        name, value = item.name, getattr(node, item.name)
        if name in ("relations", "actions") or value is None:
            continue
        if name == "supplements":
            for index, supplement in enumerate(value):
                path = (name, index)
                result[name_for(path)] = (path, supplement_data(supplement))
        elif name == "geometry":
            for key, geometry in value.items():
                add((name, key), geometry)
        elif is_dataclass(value):
            for part in fields(value):
                content = getattr(value, part.name)
                if content is not None:
                    add((name, part.name), content)
        elif not isinstance(value, (Mapping, tuple)) or value:
            add((name,), value)
    return result


def node_values(node: NativeNode) -> dict[str, Any]:
    """Expose indexed semantic values while preserving duplicate supplements."""
    return {name: value for name, (_, value) in node_fields(node).items()}


def status_record(
    issues: Sequence[ReadIssue], encoded_issues: Mapping[int, Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """Retain every applicable restriction, with a compact state for view consumers."""
    if not issues:
        return None
    primary = issues[0] if len(issues) == 1 else max(issues, key=lambda issue: _ISSUE_PRIORITY.get(issue.state, 0))
    result = {"state": primary.state, "issues": tuple(encoded_issues[id(issue)] for issue in issues)}
    if primary.detail:
        result["detail"] = primary.detail

    for issue in reversed(issues):
        captured = issue.continuation
        if captured is not None and captured.kind == "text" and captured.consistency == "captured":
            result.update(
                textRef=captured.token,
                unit=captured.unit,
                totalUnits=captured.total,
                delivered=data(captured.delivered),
            )
            break
    return MappingProxyType(result)


def observation_index(observation: NativeObservation) -> _FactIndex:
    """Build derived indexes using only public paths and relationship meanings.

    The immutable observation stays authoritative. These indexes serve existing
    display accounting and contain no platform-property interpretation.
    """
    rows, edges, unread = [], [], []
    issue_index = observation.issue_index
    name_for = cache(field_name)
    scalar_supplements = {}

    def supplement_data(supplement: Supplement) -> Mapping[str, Any]:
        value_type = type(supplement.value)
        if value_type not in _DATA_ATOMS:
            return data(supplement)
        # Provider diagnostics often report the same scalar on every element.
        # Share only the encoded value. Each node still owns its field path.
        key = (supplement.key, value_type, supplement.value, supplement.label)
        encoded = scalar_supplements.get(key)
        if encoded is None:
            scalar_supplements[key] = encoded = data(supplement)
        return encoded

    # A source restriction can apply to several fields and also appears in the
    # complete issue list. Preserve that shared source identity when deriving
    # facts, instead of copying its nested metadata at every occurrence.
    encoded_issues = {id(issue): _ReadIssueValue(issue) for issue in observation.issues}
    for token, node in observation.nodes.items():
        indexed = node_fields(node, name_for=name_for, supplement_data=supplement_data)
        values = {name: value for name, (_, value) in indexed.items()}
        paths = {name: path for name, (path, _) in indexed.items()}
        base = ("nodes", token)
        statuses = {}
        for name, path in paths.items():
            matching = issue_index.applicable((*base, *path))
            status = status_record(matching, encoded_issues)
            if status is not None:
                statuses[name] = status
        constrained_relations = set()
        for issue in issue_index.for_node(token):
            if len(issue.path) > 2 and issue.path[2] == "relations":
                if len(issue.path) > 4:
                    constrained_relations.add(issue.path[3])
                continue
            path = issue.path[2:]
            name = name_for(path) or "node"
            paths.setdefault(name, path)
            if name not in statuses:
                statuses[name] = status_record(issue_index.applicable(issue.path), encoded_issues)
        action_issues = issue_index.applicable((*base, "actions"))
        if node.actions is None:
            actions = dict(
                state=status_record(action_issues, encoded_issues)["state"] if action_issues else "not_read", items=()
            )
            if action_issues:
                actions["issues"] = tuple(encoded_issues[id(issue)] for issue in action_issues)
        else:
            actions = dict(
                state="complete" if not action_issues else status_record(action_issues, encoded_issues)["state"],
                items=tuple(data(action) for action in node.actions),
            )
            if action_issues:
                actions["issues"] = tuple(encoded_issues[id(issue)] for issue in action_issues)
        observed = bool(values or node.actions is not None or node.relations)
        if not observed:
            unread.append(token)
        rows.append(
            MappingProxyType(
                dict(
                    node=token,
                    values=MappingProxyType(values),
                    fieldPaths=MappingProxyType(paths),
                    fieldStatus=MappingProxyType(statuses),
                    requestedFields=tuple(dict.fromkeys((*values, *statuses))),
                    actions=MappingProxyType(actions),
                    observed=observed,
                )
            )
        )
        for key, relation in node.relations.items():
            path = (*base, "relations", key)
            issues = (
                [issue for issue in issue_index.overlapping(path) if len(issue.path) >= len(base)]
                if key in constrained_relations else issue_index.applicable(path)
            )
            state = status_record(issues, encoded_issues)
            relation_state = state["state"] if state else "complete"
            if relation_state in ("absent", "unsupported") and not any(
                len(issue.path) <= len(path) and issue.state in ("absent", "unsupported") for issue in issues
            ):
                # A missing target slot or count restricts coverage without
                # invalidating the relationship or discarding acquired targets.
                relation_state = "partial"
            edge = dict(
                source=token,
                property=key,
                recordKey=key,
                kind=relation.kind,
                targets=tuple(relation.targets),
                state=relation_state,
                certainty="unknown" if relation.evidence == "candidate" else "confirmed",
            )
            if relation.enumeration != "enumerated":
                edge.update(enumeration=relation.enumeration, state=edge["state"] if state else "partial")
            if relation.view is not None:
                edge["view"] = relation.view
            if relation.positions is not None:
                edge["positions"] = tuple(relation.positions)
                if relation.positions and relation.positions == tuple(
                    range(relation.positions[0], relation.positions[0] + len(relation.positions))
                ):
                    edge["offset"] = relation.positions[0]
            if relation.offset is not None:
                edge["offset"] = relation.offset
            if relation.total is not None:
                edge["total"] = relation.total
            if relation.inspected_count is not None:
                edge["inspectedCount"] = relation.inspected_count
                edge["unavailablePositions"] = relation.unavailable_positions
            if issues:
                edge["issues"] = tuple(encoded_issues[id(issue)] for issue in issues)
                if state.get("detail"):
                    edge["detail"] = state["detail"]
                continuation = next(
                    (issue.continuation for issue in reversed(issues) if issue.continuation is not None), None
                )
                if continuation is not None:
                    edge.update(cursor=continuation.token, consistency=continuation.consistency)
            edges.append(MappingProxyType(edge))
    return _FactIndex(
        observation,
        dict(
            kind="nodes",
            session=observation.session,
            platform=observation.backend,
            observed=MappingProxyType(
                dict(
                    id=observation.id,
                    startedAt=observation.interval_ns[0] // 1_000_000,
                    completedAt=observation.interval_ns[1] // 1_000_000,
                )
            ),
            roots=tuple(observation.roots),
            nodes=tuple(rows),
            relations=tuple(edges),
            unread=tuple(unread),
            stopReasons=tuple(
                (field_name(issue.path) + ": " if issue.path else "") + (issue.detail or issue.state)
                for issue in observation.issues
                if len(issue.path) < 2 or issue.path[0] != "nodes"
            ),
            coordinateSpaces=data(observation.coordinate_spaces),
            issues=tuple(encoded_issues[id(issue)] for issue in observation.issues),
        ),
    )
