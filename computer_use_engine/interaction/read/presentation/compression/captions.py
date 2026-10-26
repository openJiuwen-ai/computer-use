from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import content, protectedContent, sameBlocking
from computer_use.interaction.read.presentation.tree import disclosure, isDialog, valuePreviewLimit
from computer_use.protocol.primitives import utf16Length

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ProjectionEditor, ProjectionView, ViewNode


def passiveLabel(
    state: ProjectionView,
    edit: ProjectionEditor,
    node: ViewNode,
    target: ViewNode,
    linked: bool = False,
) -> bool:
    return (
        passive_caption(state, edit, node, target)
        and ("capabilities" not in target or linked or state.memberOf(node, target))
        and (not node.get("labelTargets") or linked)
    )


def passive_caption(state: ProjectionView, edit: ProjectionEditor, node: ViewNode, target: ViewNode) -> bool:
    """A complete caption with no independent interaction or semantic boundary.

    Ownership is checked by the caller: an explicit label relation, a declared control member,
    or an exactly repeated field on the immediate parent.

    Args:
        state: State owned by this operation or session; local IR does not replace that owner.
        edit: Local presentation editor; cannot alter native identity or capabilities.
        node: Source/projected node in this exact view; its native identity is unchanged.
        target: Source/projected node in this exact view; its native identity is unchanged.
    """
    return (
        node["role"] in ("staticText", "heading")
        and len(content(node)) > 0
        and not state.keep(node)
        and node["ref"] != state.context.get("focusedRef")
        and not any(node.get(k) for k in ("focused", "pressable", "selectable", "focusable", "selected"))
        and node.get("enabled") is not False
        and disclosure(node) is None
        and not node.get("expandable")
        and not node.get("submenu")
        and not node.get("valueExpected")
        and not protectedContent(node)
        and not node.get("incompleteLabelRelation")
        and "available" not in node.get("capabilities", {}).values()
        and not any(node.get(k) for k in ("preserveGroup", "subrole", "help", "url", "modal"))
        and "control" not in node
        and not isDialog(node)
        and utf16Length(node.get("value", "")) <= valuePreviewLimit
        and sameBlocking(node, target)
    )
