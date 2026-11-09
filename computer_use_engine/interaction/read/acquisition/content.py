# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Positive captured-content evidence, independent of read coverage and clocks."""

from __future__ import annotations

from math import isnan
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.protocol.observation import NativeNode, NativeObservation, Relation

_CONTENT_PATHS = (
    *((key,) for key in ("role", "role_detail", "name", "description", "help", "placeholder", "url")),
    ("control_value", "value"),
    ("numeric_value", "value"),
    ("text", "content"),
    *(
        ("states", key)
        for key in ("checked", "selected", "expanded", "window_state", "enabled", "modal", "visible", "showing")
    ),
    *(("structure", key) for key in ("row_count", "column_count", "set_size", "selected_count")),
    ("structure", "scroll_x", "position"),
    ("structure", "scroll_y", "position"),
)
_LAYOUT_ROLES = {"group", "split_group", "scroll_area", "unknown"}
_MEANINGFUL_PATHS = (
    ("name",),
    ("description",),
    ("placeholder",),
    ("text", "content"),
    ("control_value", "value"),
    ("numeric_value", "value"),
)


def same_content_scope(before: SemanticSnapshot | None, after: SemanticSnapshot | None) -> bool:
    """Compare native graph identities; callers retain their own exact window binding."""
    if before is None or after is None:
        return False
    left, right = before.native, after.native
    if not left.roots or (left.backend, left.session, left.roots) != (right.backend, right.session, right.roots):
        return False
    return (
        before.collection is None or after.collection is None or before.collection.surface == after.collection.surface
    )


def _value(node: NativeNode, path: tuple[str, ...]):
    value = node
    for key in path:
        value = getattr(value, key, None)
        if value is None:
            break
    return value


def _known(observation: NativeObservation, key: str, path: tuple[str, ...]) -> bool:
    return _value(observation.nodes[key], path) is not None and not observation.issue_index.applicable(
        ("nodes", key, *path)
    )


def _different(left, right) -> bool:
    comparable_nan = isinstance(left, float) and isinstance(right, float)
    if comparable_nan and isnan(left) and isnan(right):
        return False
    return type(left) is not type(right) or left != right


def _complete_children(observation: NativeObservation, key: str, name: str, relation: Relation) -> bool:
    return (
        relation.kind == "children"
        and relation.evidence != "candidate"
        and relation.enumeration == "enumerated"
        and relation.offset in (None, 0)
        and relation.positions in (None, tuple(range(len(relation.targets))))
        and relation.total in (None, len(relation.targets))
        and not observation.issue_index.applicable(("nodes", key, "relations", name))
        and not any(
            issue.path in ((), ("nodes",)) and issue.state not in ("absent", "unsupported")
            for issue in observation.issues
        )
    )


def _meaningful(observation: NativeObservation, key: str) -> bool:
    node = observation.nodes[key]
    if _known(observation, key, ("role",)) and node.role not in _LAYOUT_ROLES:
        return True
    return any(
        _known(observation, key, path) and _value(node, path) not in (None, "") for path in _MEANINGFUL_PATHS
    )


def has_content_progress(before: SemanticSnapshot | None, after: SemanticSnapshot | None) -> bool:
    """Return only a proved same-scope content change, never coverage inequality.

    Missing fields/nodes, anonymous containers, collection issues, focus changes,
    capture-local text sources and native diagnostic supplements are not proof.
    A newly observed control proves addition only behind a previously complete
    parent children enumeration, rather than a formerly unexpanded graph.
    """
    if not same_content_scope(before, after):
        return False
    left, right = before.native, after.native
    for key in left.nodes.keys() & right.nodes.keys():
        for path in _CONTENT_PATHS:
            if (
                _known(left, key, path)
                and _known(right, key, path)
                and _different(_value(left.nodes[key], path), _value(right.nodes[key], path))
            ):
                return True
        for name, old_relation in left.nodes[key].relations.items():
            if not _complete_children(left, key, name, old_relation):
                continue
            relation = right.nodes[key].relations.get(name)
            if relation is None:
                continue
            if (
                relation.kind != "children"
                or relation.evidence == "candidate"
                or relation.view != old_relation.view
            ):
                continue
            for child in relation.targets:
                previously_unknown = child not in left.nodes and child not in old_relation.targets
                if previously_unknown and child in right.nodes and _meaningful(right, child):
                    return True
            if _complete_children(right, key, name, relation) and any(
                child in left.nodes and child not in relation.targets and _meaningful(left, child)
                for child in old_relation.targets
            ):
                return True
    return False


def prefer_earlier_budget_capture(before: SemanticSnapshot | None, after: SemanticSnapshot | None) -> bool:
    """Retain one earlier capture when a later budget-limited read loses coverage."""
    if not same_content_scope(before, after) or has_content_progress(before, after):
        return False
    left, right = before.native, after.native
    if right.interval_ns[0] < left.interval_ns[0] or not any(
        issue.state in ("partial", "timeout", "not_read") and "budget" in (issue.detail or "").lower()
        for issue in right.issues
    ):
        return False
    if left.nodes.keys() - right.nodes.keys():
        return True
    for key in left.nodes.keys() & right.nodes.keys():
        if any(_known(left, key, path) and not _known(right, key, path) for path in _CONTENT_PATHS):
            return True
    return False
