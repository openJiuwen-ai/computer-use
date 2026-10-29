# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.projection import prepare_tree
from computer_use.protocol.primitives import frozen

if TYPE_CHECKING:
    from typing import Sequence

    from computer_use.interaction.read.presentation.records import NodeView
    from computer_use.protocol.records import Record


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
