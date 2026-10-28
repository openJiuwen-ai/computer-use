# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read-refresh evidence from unified observation facts.

Loading, exposed content, and collection coverage remain independent. These
queries inspect captured facts and never activate or send input to a window.
"""

from __future__ import annotations

from math import isfinite
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from computer_use.interaction.read.facts.snapshot import SemanticSnapshot
    from computer_use.protocol.observation import Geometry, NativeNode, NativeObservation
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts


def _rect(geometry: Geometry | None) -> tuple[float, float, float, float] | None:
    if geometry is None or geometry.valid is False:
        return None
    parts = (geometry.x, geometry.y, geometry.width, geometry.height)
    if any(type(value) not in (int, float) or not isfinite(value) for value in parts):
        return None
    return parts if min(parts[2:]) > 0 else None


def _content(node: NativeNode) -> bool:
    """Whether a node reports content beyond an anonymous layout container."""
    return (
        node.role not in ("group", "split_group", "scroll_area", "unknown")
        or any((node.name, node.description, node.help, node.placeholder, node.url))
        or node.control_value is not None
        and node.control_value.value not in (None, "")
        or node.numeric_value is not None
        and node.numeric_value.value is not None
        or node.text is not None
        and bool(node.text.content)
        or bool(node.actions)
    )


def window_content_unexposed(snapshot: SemanticSnapshot | None) -> bool:
    """Identify a positively observed window shell with no exposed body content.

    Args:
        snapshot: Unified facts and collection coverage from the last read.

    Returns:
        Whether known window geometry or complete chrome-only containment supports
        a shell diagnosis. Missing roles, geometry, or children leave it unknown.
    """
    if snapshot is None:
        return False
    observation = snapshot.native
    roots = set(observation.roots)
    client = next(
        (
            node.geometry.get("client")
            for key, node in observation.nodes.items()
            if key in roots and _rect(node.geometry.get("client")) is not None
        ),
        None,
    )
    if client is None:
        return _window_shell(observation)
    if snapshot.facts["stopReasons"] or snapshot.facts["unread"]:
        return False
    x, y, w, h = _rect(client)
    for key, node in observation.nodes.items():
        if key in roots or node.role == "window" or node.role_detail == "title_bar":
            continue
        if not _content(node):
            continue
        bounds = node.geometry.get("bounds")
        rect = _rect(bounds)
        if rect is None or bounds.space != client.space:
            return False
        bx, by, bw, bh = rect
        if min(x + w, bx + bw) > max(x, bx) and min(y + h, by + bh) > max(y, by):
            return False
    return True


def _window_shell(observation: NativeObservation) -> bool:
    """Recognize complete window chrome and anonymous layout by public semantics."""
    if len(observation.roots) != 1 or any(issue.path in ((), ("nodes",)) for issue in observation.issues):
        return False
    root = observation.roots[0]
    nodes = observation.nodes
    if nodes[root].role not in ("window", "sheet"):
        return False
    if nodes[root].control_value is not None and nodes[root].control_value.value not in (None, ""):
        return False
    # Relation coverage and label availability use different state rules. Index
    # their exact source paths once before inspecting every node and relation.
    limited_relations: set[tuple[str | int, ...]] = set()
    unavailable_labels: set[str | int] = set()
    label_fields = {"name", "description", "help", "placeholder", "url", "control_value", "text"}
    for issue in observation.issues:
        path = issue.path
        if len(path) >= 4 and path[0] == "nodes" and path[2] == "relations" and issue.state != "absent":
            limited_relations.add(path[:4])
        if (
            len(path) > 2
            and path[0] == "nodes"
            and path[2] in label_fields
            and issue.state not in ("absent", "unsupported")
        ):
            unavailable_labels.add(path[1])
    children, titles, external = {}, set(), set()
    for key, node in nodes.items():
        for record_key, relation in node.relations.items():
            prefix = ("nodes", key, "relations", record_key)
            limited = prefix in limited_relations
            if relation.kind in ("parent", "window"):
                external.update(relation.targets)
            matches_key = (
                True
                if relation.kind == "labelled_by" and key == root and not limited and relation.evidence != "candidate"
                else False
            )
            if matches_key:
                titles.update(relation.targets)
            if relation.kind == "children":
                if key in children or limited or relation.evidence == "candidate":
                    return False
                if (
                    relation.enumeration != "enumerated"
                    or relation.positions is not None
                    and relation.positions != tuple(range(len(relation.targets)))
                ):
                    return False
                if relation.total is not None and relation.total != len(relation.targets):
                    return False
                children[key] = relation.targets
    expanded = set(children)
    if (
        set(nodes) - expanded - external
        or root not in expanded
        or any(child not in expanded for targets in children.values() for child in targets)
    ):
        return False
    pending, visited = [root], set()
    while pending:
        current = pending.pop()
        if current in visited:
            return False
        visited.add(current)
        pending.extend(children[current])
    if visited | titles != expanded:
        return False
    chrome = {"close", "minimize", "zoom", "fullscreen"}
    layout = {"group", "split_group", "scroll_area"}
    support = {"show_menu", "scroll", "scroll_into_view"}
    for key in expanded:
        node = nodes[key]
        if key == root:
            continue
        if node.role == "button" and node.role_detail in chrome and key in children[root]:
            continue
        title = key in titles and node.role == "static_text" and not children[key]
        if not title and (node.role not in layout or _content_without_actions(node)):
            return False
        if node.states.editable is True or any(
            action.available is True and action.kind in ("set_value", "replace_selection", "insert_text")
            for action in node.actions or ()
        ):
            return False
        if node.actions is None or any(action.kind not in support for action in node.actions):
            return False
        if key in unavailable_labels:
            return False
    return True


def _content_without_actions(node: NativeNode) -> bool:
    return (
        any((node.name, node.description, node.help, node.placeholder, node.url))
        or node.control_value is not None
        and node.control_value.value not in (None, "")
        or node.numeric_value is not None
        and node.numeric_value.value is not None
        or node.text is not None
        and bool(node.text.content)
    )


def busy_nodes(snapshot: SemanticSnapshot | None) -> tuple[str, ...]:
    if snapshot is None:
        return ()
    return tuple(key for key, node in snapshot.native.nodes.items() if node.states.busy is True)


def refreshable(snapshot: SemanticSnapshot | None) -> bool:
    """Avoid replaying collection failures as evidence of application loading."""
    if snapshot is None:
        return False
    if snapshot.native.backend != "windows":
        return True
    return not snapshot.facts["unread"] and not any(
        issue.state == "timeout" or issue.state == "error" and "relations" in issue.path
        for issue in snapshot.native.issues
    )


def initialization_requested(directory: DirectoryFacts, launch: Mapping[str, Any] | None) -> bool:
    """Return explicit startup evidence that permits the initialization allowance."""
    return (
        directory.get("provider", {}).get("status") == "requestReturned"
        or launch is not None
        and launch["dispatch"] == "dispatched"
    )
