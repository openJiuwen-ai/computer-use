# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Source-based protection and hierarchy queries shared by the group planning stages."""

from __future__ import annotations

from collections.abc import Mapping

from computer_use.interaction.read.presentation.policy import SUPPORTING_ACTIONS
from .model import GroupDisclosureRequest

PASSIVE_ROLES = frozenset(
    (
        "static_text",
        "heading",
        "paragraph",
        "group",
        "section",
        "list",
        "list_item",
        "row",
        "cell",
        "column",
        "document",
    )
)
RECORD_ROLES = frozenset(("row", "list_item"))
OUTLINE_ROLES = frozenset(("heading", "section", "group", "list", "document"))


def source_protections(request: GroupDisclosureRequest, ref: str) -> tuple[str, ...]:
    """Return explicit reasons a source must remain directly represented in full text."""
    snapshot, interface = request.snapshot, request.interface
    token = snapshot.refs.get(ref)
    node = snapshot.native.nodes.get(token)
    if node is None:
        return ("missing-native-source",)
    reasons: list[str] = []
    atom = request.atoms.atoms.get(ref)
    view = interface.source.native_nodes.get(ref, {})
    if node.role not in PASSIVE_ROLES:
        reasons.append("independent-or-unknown-role")
    if node.actions is None:
        reasons.append("unknown-actions")
    elif any(
        action.available is not False
        and (action.key is not None or action.kind not in ("focus", "set_value", "replace_selection"))
        and action.kind not in SUPPORTING_ACTIONS
        and not any(
            item.get("ref") == ref and item.get("native") == token and item.get("index") == index
            for item in (*request.atoms.deferred_menu_actions, *request.atoms.deferred_control_raises)
        )
        for index, action in enumerate(node.actions)
    ):
        reasons.append("independent-actions")
    if any(
        view.get("capabilities", {}).get(key) == "available" for key in ("click", "typeText", "replaceText", "focus")
    ):
        reasons.append("independent-input")
    states = node.states
    if (
        any(
            getattr(states, name) is True
            for name in (
                "focused",
                "focusable",
                "selected",
                "selectable",
                "editable",
                "protected",
                "busy",
                "modal",
                "has_popup",
                "virtualized",
            )
        )
        or states.enabled is False
        or states.checked is not None
        or states.expanded is not None
        and node.role in ("row", "list_item")
        or states.visible is False
        or states.showing is False
    ):
        reasons.append("active-or-important-state")
    text = node.text
    if text is not None and (
        text.read_only is False
        or text.caret_active is True
        or bool(text.selected_text)
        or any(
            span.collapsed is not True or span.start != span.end or bool(span.text) for span in text.selections or ()
        )
    ):
        reasons.append("input-or-selection")
    if node.role == "document" and (text is None or text.read_only is not True):
        reasons.append("unconfirmed-readonly-document")
    if (
        node.control_value is not None
        and node.control_value.read_only is False
        or node.numeric_value is not None
        and node.numeric_value.read_only is False
    ):
        reasons.append("writable-value")
    # Provider namespaces hold original diagnostics retained in the existing
    # global detail supplement. Portable semantic additions remain protective.
    if any(
        not item.key.startswith("provider.") and not (item.key == "disclosing" and item.value is False)
        for item in node.supplements
    ):
        reasons.append("additional-native-meaning")
    if any(
        issue.path[:2] == ("nodes", token) and issue.state not in ("absent", "unsupported")
        for issue in snapshot.native.issues
    ):
        reasons.append("native-uncertainty")
    if view.get("error") or view.get("invalid") or view.get("blockedBy") or view.get("modified") or view.get("unsaved"):
        reasons.append("error-or-blocked-state")
    for relation in node.relations.values():
        if relation.kind not in ("children", "parent", "window") and relation.targets:
            reasons.append("independent-semantic-relation")
        if (relation.kind == "children" or relation.targets or relation.total not in (None, 0)) and (
            relation.evidence == "candidate"
            or relation.enumeration != "enumerated"
            or relation.offset not in (None, 0)
            or relation.total is not None
            and relation.total != len(relation.targets)
        ):
            reasons.append("incomplete-relation")
    if atom is not None:
        if atom.actions or atom.actions_unavailable:
            reasons.append("displayed-or-unknown-actions")
        if atom.states:
            reasons.append("displayed-state")
        if atom.coverage.partial or atom.coverage.unexpanded_menu:
            reasons.append("incomplete-child-coverage")
        if any(
            slot.protection or slot.format in ("unavailable", "selection", "span") or slot.preview
            for slot in atom.slots
        ):
            reasons.append("protected-display-field")
        if any(str(fact.key.source) not in snapshot.refs for slot in atom.slots for fact in slot.facts):
            reasons.append("unresolved-field-source")
    return tuple(dict.fromkeys(reasons))


def recovery_sources(request: GroupDisclosureRequest, projected_refs: tuple[str, ...]) -> tuple[str, ...]:
    """Include folded originals and every field contributor without changing their aliases."""
    wanted = set(projected_refs)
    wanted.update(ref for ref, owner in request.interface.folded_refs.items() if owner in wanted)
    for ref in projected_refs:
        atom = request.atoms.atoms[ref]
        wanted.update(str(fact.key.source) for slot in atom.slots for fact in slot.facts)
    ordered = tuple(ref for ref in request.interface.source.order if ref in wanted)
    return (*ordered, *(ref for ref in request.snapshot.refs if ref in wanted and ref not in ordered))


def block_members(request: GroupDisclosureRequest) -> Mapping[str, tuple[str, ...]]:
    """Collect each reading block's real sources in its existing child order."""
    result: dict[str, tuple[str, ...]] = {}

    def visit(key: str) -> tuple[str, ...]:
        block = request.interface.blocks[key]
        refs = (*block.source_refs, *(ref for child in block.children for ref in visit(child)))
        result[key] = tuple(dict.fromkeys(refs))
        return result[key]

    for root in request.interface.roots:
        visit(root)
    return result


def permits_descendants(request: GroupDisclosureRequest, key: str, protections: Mapping[str, tuple[str, ...]]) -> bool:
    """Keep labels and inner controls attached to independently meaningful owners."""
    block = request.interface.blocks[key]
    for ref in block.source_refs:
        node = request.snapshot.native.nodes.get(request.snapshot.refs.get(ref))
        if node is not None and node.role in ("window", "web_area", "scroll_area", "table", "list"):
            if node.states.modal is not True and node.states.editable is not True:
                continue
        # A captured parent's collection gap remains visible on the parent. Its
        # complete passive children can still be summarized as captured content.
        reasons = set(protections.get(ref, ())) - {"incomplete-relation", "incomplete-child-coverage"}
        if reasons:
            return False
    return True
