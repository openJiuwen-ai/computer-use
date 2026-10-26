from __future__ import annotations

from typing import TYPE_CHECKING

"Observation semantics preserved from observation/tree.ts."


if TYPE_CHECKING:
    from typing import Any, Sequence

    from computer_use.interaction.read.presentation.records import NodeView, SemanticNode
_TEXT_ROLES = {"textField", "textArea", "comboBox"}
_NON_VALUE_ROLES = {
    "application",
    "window",
    "sheet",
    "group",
    "splitGroup",
    "column",
    "scrollArea",
    "toolbar",
    "tabGroup",
    "outline",
    "list",
    "button",
    "image",
    "closeButton",
    "zoomButton",
    "minimizeButton",
}
valuePreviewLimit = 1200


def unique(values: Any) -> list[Any]:
    return list(dict.fromkeys(values))


def isWindow(node: NodeView) -> bool:
    return node["role"] in ("window", "sheet")


def isDialog(node: NodeView) -> bool:
    return node["role"] == "sheet" or node.get("subrole") in ("dialog", "systemDialog")


def isHidden(node: NodeView) -> bool:
    # Explicitly hidden is different from an off-screen/occluded modal.
    # Unknown or conflicting native visibility cannot waive modal blocking.
    return node.get("visible") is False and node.get("showing") is False


def isActiveModal(node: NodeView) -> bool:
    return bool(node.get("modal")) and not isHidden(node)


def disclosure(node: NodeView) -> bool | None:
    "Return the observed expanded/disclosing state, or None when it is unknown."
    if node.get("expanded") is True or node.get("expandable") or node["role"] in ("row", "disclosureTriangle"):
        if "expanded" in node:
            return node["expanded"]
        if node.get("expandable") or node.get("disclosing") is True:
            return node.get("disclosing")
    return None


def carriesValue(node: NodeView) -> bool:
    return bool(
        node.get("valueExpected")
        or node["role"]
        in _TEXT_ROLES | {"slider", "checkBox", "switch", "radioButton", "progressIndicator", "incrementor"}
        or (node["role"] == "cell" and node.get("name"))
    )


def hasDisplayValue(node: NodeView) -> bool:
    caps = node.get("capabilities", {})
    return carriesValue(node) or (
        "value" in node
        and (
            node["value"] != ""
            or node["role"] not in _NON_VALUE_ROLES
            or caps.get("typeText") == "available"
            or caps.get("replaceText") == "available"
            or bool(node.get("focused"))
            or "selectionRange" in node
            or bool(node.get("selectedText"))
        )
    )


def hasIncompleteChildren(node: NodeView) -> bool:
    return (
        any(k in node for k in ("partialReason", "nextChildOffset", "childrenStart", "childrenEnd"))
        or bool(node.get("childOmissions"))
        or ("childCount" in node and node.get("childrenRead", len(node["children"])) < node["childCount"])
    )


def hasTextSelection(node: NodeView) -> bool:
    return node["selectionRange"]["length"] > 0 if "selectionRange" in node else bool(node.get("selectedText"))


def selectedTextDescendants(node: NodeView) -> list[Any]:
    "Collect descendant nodes that report a nonempty text selection."
    result = []
    for child in node["children"]:
        if child["role"] in ("application", "window"):
            continue
        result.extend([child] if hasTextSelection(child) else selectedTextDescendants(child))
    return result


def valueLabel(node: NodeView) -> str:
    "Choose the value-field label from document, numeric, and descendant-text facts."
    if node.get("valueKind") == "document":
        return "Document text"

    def hasText(child: NodeView) -> bool:
        return (
            not child.get("valueIsNumber") and "value" in child and child["role"] in _TEXT_ROLES | {"staticText"}
        ) or any(hasText(c) for c in child["children"])

    return (
        "Native value"
        if node.get("valueIsNumber")
        and node["role"] in ("group", "cell", "row", "unknown", "textField")
        and any(hasText(c) for c in node["children"])
        else "Value"
    )


def withInteractions(root: NodeView, inherited: Sequence[str] | None = None) -> SemanticNode:
    """Annotate semantic nodes with their declared and inherited interaction state.

    Args:
        root: Root of the requested semantic view; native bindings remain separate.
    """
    inherited = [] if inherited is None else inherited

    def modals(node: NodeView) -> list[str]:
        """Find modal regions represented in the semantic tree.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
        """
        if isActiveModal(node):
            return [node["ref"]]
        if isWindow(node):
            return []
        return [ref for child in node["children"] for ref in modals(child)]

    own = (
        sorted(unique(root.get("ownedModals", []) + [ref for child in root["children"] for ref in modals(child)]))
        if isWindow(root) or isActiveModal(root)
        else []
    )
    incoming = [] if root["ref"] in inherited or root["role"] == "window" else inherited
    blocked = sorted(unique(incoming + own))
    return {
        **root,
        "blockedBy": blocked,
        **(
            {"capabilities": {k: "unavailable" for k in ("click", "typeText", "replaceText", "focus")}}
            if "capabilities" in root and blocked
            else {}
        ),
        "children": [withInteractions(child, blocked) for child in root["children"]],
    }


def interactionView(root: SemanticNode, includeBackground: bool) -> SemanticNode:
    """Select the visible interaction scope from the annotated semantic tree.

    Args:
        root: Root of the requested semantic view; native bindings remain separate.
    """
    node = root
    if not includeBackground and root.get("blockedBy"):

        def containsModal(child: NodeView) -> bool:
            return child["ref"] in root["blockedBy"] or any(containsModal(c) for c in child["children"])

        children = [child for child in root["children"] if containsModal(child)]
        if children and len(children) != len(root["children"]):
            node = {
                **root,
                "children": children,
                "partialReason": (
                    f"background blocked by "
                    f"{', '.join(root['blockedBy'])}"
                    f"; select this window explicitly to inspect its background"
                ),
            }
            node.pop("childrenRead", None)
            node.pop("childCount", None)
    return {**node, "children": [interactionView(c, includeBackground) for c in node["children"]]}
