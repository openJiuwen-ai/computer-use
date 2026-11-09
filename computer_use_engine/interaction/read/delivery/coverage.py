# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.projection import prepare_tree
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.protocol.primitives import frozen

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.presentation.records import NodeView
    from computer_use.protocol.records import Record


def tree_collection_issues(snapshot):
    """Return explicit whole-tree collection gaps from this exact native capture.

    Optional field support and unrelated referenced parent objects are not tree
    collection failures. A returned root also cannot turn a native nodes/roots
    partial or timeout record into a complete observation.
    """
    return tuple(
        issue for issue in snapshot.native.issues
        if issue.path in ((), ("nodes",), ("roots",)) and issue.state not in ("absent", "unsupported")
    )


def document_coverage(document: DisplayDocument) -> Mapping[str, Any]:
    """Retain collection evidence from the same captured facts as the delivered tree.

    Complete child collections are deliberately not inferred from formatted
    child counts: normalization omits those counts for complete collections.
    Related roots and partial/unread children remain absence, not proven removal.
    """
    parents, complete = {}, set()
    for tree in document.parts:
        if not isinstance(tree, DisplayTree):
            continue
        snapshot = tree.prepared.snapshot
        pending = [(root, None) for root in snapshot.roots]
        while pending:
            node, parent = pending.pop()
            parents[node["ref"]] = parent
            pending.extend((child, node["ref"]) for child in node["children"])
        aliases = {native: ref for ref, native in snapshot.refs.items()}
        children = {}
        for relation in snapshot.facts["relations"]:
            if relation["kind"] == "children" and relation["certainty"] == "confirmed":
                children.setdefault(relation["source"], []).append(relation)
        unread = set(snapshot.facts.get("unread", ()))
        for source, relations in children.items():
            if len(relations) != 1:
                continue
            relation = relations[0]
            targets = relation["targets"]
            complete_range = (
                relation["state"] == "complete"
                and relation.get("offset", 0) == 0
                and relation.get("total", len(targets)) == len(targets)
            )
            if complete_range and not unread.intersection(targets) and source in aliases:
                complete.add(aliases[source])
    return frozen({"parents": frozen(parents), "complete": tuple(sorted(complete))})


def observation_coverage(observation: Record, roots: Sequence[NodeView] | None = None) -> Mapping[str, Any]:
    """Build structural coverage evidence for comparing observation baselines.

    Args:
        roots: Ordered roots in this observed or projected view.
    """
    parents, complete, displayed = {}, [], {}

    def remember(node: NodeView) -> None:
        displayed[node["ref"]] = node
        for child in node["children"]:
            remember(child)

    if observation["root"] is not None:
        for root in prepare_tree(observation["root"], observation["context"]) if roots is None else roots:
            remember(root)

    def visit(node: NodeView, parent: str | None) -> None:
        """Collect the snapshot's represented nodes and relation coverage.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
        """
        parents[node["ref"]] = parent
        view = displayed.get(node["ref"])
        matches_node = (
            True
            if (
                "childCount" in node
                and node.get("childrenRead") == node["childCount"]
                and "partialReason" not in node
                and not node.get("childOmissions")
                and node.get("childrenStart", 0) == 0
                and (view is None or "partialReason" not in view)
            )
            else False
        )
        if matches_node:
            complete.append(node["ref"])
        for child in node["children"]:
            visit(child, node["ref"])

    if observation["root"] is not None:
        visit(observation["root"], None)
    return frozen(dict(parents=frozen(parents), complete=tuple(complete)))


def confirms_removal(previous: Record | None, current: Record | None, ref: str) -> bool:
    """Check whether current coverage proves a previously observed node was removed.

    Args:
        previous: Previously delivered state used only for diff/baseline comparison.
        current: Currently confirmed target or catalogue; absence is not a guessed fallback.
        ref: Display reference from the observation, not an operating-system identifier.
    """
    if previous is None or current is None or ref in current["parents"]:
        return False
    complete = set(current["complete"])
    child = ref
    while child is not None:
        parent = previous["parents"].get(child)
        if parent is None:
            return False
        if parent in current["parents"]:
            return parent in complete
        child = parent
    return False


if TYPE_CHECKING:
    from typing import Any, Mapping
