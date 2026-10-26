"""Request-local indexes of unified observation facts for presentation accounting."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from computer_use.protocol.observation import ElementValue, FieldPath, NativeNode, NativeObservation, ReadIssue


def data(value: Any) -> Any:
    """Return a JSON-safe semantic value without native wrappers or object copies."""
    if isinstance(value, ElementValue):
        return {"element": str(value.node)}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, Enum):
        return value.value
    if type(value) is float and not math.isfinite(value):
        return {"number": "NaN" if math.isnan(value) else "Infinity" if value > 0 else "-Infinity"}
    if is_dataclass(value):
        return {item.name: data(result) for item in fields(value) if (result := getattr(value, item.name)) is not None}
    if isinstance(value, Mapping):
        return {str(key): data(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [data(item) for item in value]
    return value


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


def node_fields(node: NativeNode) -> dict[str, tuple[tuple[str | int, ...], Any]]:
    """Index domain values alongside their exact paths in the immutable graph."""
    result = {}

    def add(path: tuple[str | int, ...], value: Any) -> None:
        result[field_name(path)] = (path, data(value))

    for item in fields(node):
        name, value = item.name, getattr(node, item.name)
        if name in ("relations", "actions") or value is None:
            continue
        if name == "supplements":
            for index, supplement in enumerate(value):
                add((name, index), supplement)
        elif name == "geometry":
            for key, geometry in value.items():
                add((name, key), geometry)
        elif is_dataclass(value):
            for part in fields(value):
                if (content := getattr(value, part.name)) is not None:
                    add((name, part.name), content)
        elif not isinstance(value, (Mapping, tuple)) or value:
            add((name,), value)
    return result


def node_values(node: NativeNode) -> dict[str, Any]:
    """Expose indexed semantic values while preserving duplicate supplements."""
    return {name: value for name, (_, value) in node_fields(node).items()}


class _IssueIndex:
    """Resolve restrictions by exact field prefix without rescanning the capture."""

    def __init__(self, issues: Iterable[ReadIssue]) -> None:
        self.paths: dict[FieldPath, list[tuple[int, ReadIssue]]] = {}
        self.nodes: dict[str, list[ReadIssue]] = {}
        for order, issue in enumerate(issues):
            self.paths.setdefault(issue.path, []).append((order, issue))
            if len(issue.path) >= 2 and issue.path[0] == "nodes":
                self.nodes.setdefault(issue.path[1], []).append(issue)

    def applicable(self, path: FieldPath) -> list[ReadIssue]:
        """Return exact and ancestor restrictions in their original capture order.

        Collection-wide gaps describe missing membership. They do not invalidate
        facts already acquired from a particular node.
        """
        if len(path) < 2:
            return [issue for _, issue in self.paths.get(path, ())]
        matching = [item for length in range(2, len(path) + 1) for item in self.paths.get(path[:length], ())]
        matching.sort(key=lambda item: item[0])
        return [issue for _, issue in matching]


def status_record(issues: list[ReadIssue]) -> dict[str, Any] | None:
    """Retain every applicable restriction, with a compact state for view consumers."""
    if not issues:
        return None
    priority = {
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
    primary = max(issues, key=lambda issue: priority.get(issue.state, 0))
    result = {"state": primary.state, "issues": [data(issue) for issue in issues]}
    if primary.detail:
        result["detail"] = primary.detail
    captured = next(
        (
            issue.continuation
            for issue in reversed(issues)
            if issue.continuation is not None
            and issue.continuation.kind == "text"
            and issue.continuation.consistency == "captured"
        ),
        None,
    )
    if captured is not None:
        result.update(
            textRef=captured.token, unit=captured.unit, totalUnits=captured.total, delivered=data(captured.delivered)
        )
    return result


def observation_index(observation: NativeObservation) -> dict[str, Any]:
    """Build derived indexes using only public paths and relationship meanings.

    The immutable observation stays authoritative. These indexes serve existing
    display accounting and contain no platform-property interpretation.
    """
    rows, edges, unread = [], [], []
    issue_index = _IssueIndex(observation.issues)
    for token, node in observation.nodes.items():
        indexed = node_fields(node)
        values = {name: value for name, (_, value) in indexed.items()}
        paths = {name: path for name, (path, _) in indexed.items()}
        base = ("nodes", token)
        statuses = {}
        for name, path in paths.items():
            matching = issue_index.applicable((*base, *path))
            if (status := status_record(matching)) is not None:
                statuses[name] = status
        for issue in issue_index.nodes.get(token, ()):
            if len(issue.path) > 2 and issue.path[2] == "relations":
                continue
            path = issue.path[2:]
            name = field_name(path) or "node"
            paths.setdefault(name, path)
            if name not in statuses:
                statuses[name] = status_record(issue_index.applicable(issue.path))
        action_issues = issue_index.applicable((*base, "actions"))
        if node.actions is None:
            actions = dict(state=status_record(action_issues)["state"] if action_issues else "not_read", items=[])
            if action_issues:
                actions["issues"] = [data(issue) for issue in action_issues]
        else:
            actions = dict(
                state="complete" if not action_issues else status_record(action_issues)["state"],
                items=[data(action) for action in node.actions],
            )
            if action_issues:
                actions["issues"] = [data(issue) for issue in action_issues]
        observed = bool(values or node.actions is not None or node.relations)
        if not observed:
            unread.append(token)
        rows.append(
            dict(
                node=token,
                values=values,
                fieldPaths=paths,
                fieldStatus=statuses,
                requestedFields=list(dict.fromkeys((*values, *statuses))),
                actions=actions,
                observed=observed,
            )
        )
        for key, relation in node.relations.items():
            issues = issue_index.applicable((*base, "relations", key))
            state = status_record(issues)
            edge = dict(
                source=token,
                property=key,
                recordKey=key,
                kind=relation.kind,
                targets=list(relation.targets),
                state=state["state"] if state else "complete",
                certainty="unknown" if relation.evidence == "candidate" else "confirmed",
            )
            if relation.enumeration != "enumerated":
                edge.update(enumeration=relation.enumeration, state=edge["state"] if state else "partial")
            if relation.view is not None:
                edge["view"] = relation.view
            if relation.positions is not None:
                edge["positions"] = list(relation.positions)
                if relation.positions and relation.positions == tuple(
                    range(relation.positions[0], relation.positions[0] + len(relation.positions))
                ):
                    edge["offset"] = relation.positions[0]
            if relation.offset is not None:
                edge["offset"] = relation.offset
            if relation.total is not None:
                edge["total"] = relation.total
            if issues:
                edge["issues"] = [data(issue) for issue in issues]
                if state.get("detail"):
                    edge["detail"] = state["detail"]
                continuation = next(
                    (issue.continuation for issue in reversed(issues) if issue.continuation is not None), None
                )
                if continuation is not None:
                    edge.update(cursor=continuation.token, consistency=continuation.consistency)
            edges.append(edge)
    return dict(
        kind="nodes",
        session=observation.session,
        platform=observation.backend,
        observed=dict(
            id=observation.id,
            startedAt=observation.interval_ns[0] // 1_000_000,
            completedAt=observation.interval_ns[1] // 1_000_000,
        ),
        roots=list(observation.roots),
        nodes=rows,
        relations=edges,
        unread=unread,
        stopReasons=[
            (field_name(issue.path) + ": " if issue.path else "") + (issue.detail or issue.state)
            for issue in observation.issues
            if len(issue.path) < 2 or issue.path[0] != "nodes"
        ],
        coordinateSpaces=data(observation.coordinate_spaces),
        issues=[data(issue) for issue in observation.issues],
    )
