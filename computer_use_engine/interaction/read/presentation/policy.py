"""Evidence used only to organize a fact-backed display, never to route input.

Supporting actions and provider-wide metadata are still emitted by native fact
accounting. They need not turn every layout wrapper into a separate UI control.
Unknown fields, incomplete observations and independent actions stay boundaries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

    from computer_use.interaction.read.presentation.records import NodeView
    from computer_use.protocol.records import Record
SUPPORTING_ACTIONS = frozenset(("show_menu", "scroll_into_view"))
_BOOLEAN_METADATA = frozenset(
    (
        "focusWritable",
        "valueWritable",
        "textWritable",
        "editable",
        "readOnly",
        "textReadOnly",
        "sensitive",
        "protected",
        "multiLine",
    )
)


def known_metadata(field: str | None, value: Any) -> bool:
    """Recognized metadata may share an annotation without forcing a wrapper.

    Keep this typed and narrow: being present in the field-name dictionary is not proof that an
    unfamiliar value is safe to move away from its node.
    """
    if field == "editable" and value is True:
        # An explicit editor declaration is meaningful even when this driver
        # lacks a currently available input route.
        return False
    if field in _BOOLEAN_METADATA:
        return type(value) is bool
    if field == "orientation":
        return value in ("horizontal", "vertical")
    return False


def independent_actions(node: NodeView) -> bool:
    return any(action.get("kind", action["name"]) not in SUPPORTING_ACTIONS for action in node.get("actions", ()))


def editing(node: NodeView) -> bool:
    return node["role"] in ("textField", "textArea", "comboBox", "document") or any(
        node.get("capabilities", {}).get(key) == "available" for key in ("typeText", "replaceText")
    )


def display_copy(node: NodeView) -> Record:
    """Move incidental defaults to exact, grouped native facts, not oblivion.

    Args:
        node: Source/projected node in this exact view; its native identity is unchanged.
    """
    result = {**node, "_factBacked": True}
    if (
        not editing(node)
        and not node.get("focused")
        and not node.get("selectedText")
        and node.get("selectionRange") == dict(location=0, length=0)
    ):
        result.pop("selectionRange")
    if (
        node.get("disclosureLevel") == 0
        and node["role"] not in ("outline", "row", "disclosureTriangle")
        and not node.get("disclosedBy")
        and not node.get("expanded")
        and not node.get("disclosing")
    ):
        result.pop("disclosureLevel")
    return result
