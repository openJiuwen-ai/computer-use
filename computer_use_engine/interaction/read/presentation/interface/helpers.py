# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Pure structural predicates shared by interface stages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft

CONTROL_ROLES = frozenset(
    (
        "button",
        "menuButton",
        "link",
        "checkBox",
        "radioButton",
        "switch",
        "popUpButton",
        "comboBox",
        "slider",
        "incrementor",
        "closeButton",
        "minimizeButton",
        "zoomButton",
    )
)
FIELD_ROLES = frozenset(("textField", "textArea", "comboBox"))
REGION_ROLES = frozenset(("toolbar", "tabGroup", "tabPanel", "scrollArea", "splitGroup", "webArea", "document"))
COLLECTION_ROLES = frozenset(("list", "outline"))
TEXT_ROLES = frozenset(("staticText", "heading", "image"))


def element_key(ref: str) -> str:
    """Return a layout-only key without interpreting the real element identifier."""
    return "element:" + ref


def synthetic_key(kind: str, parent: str, members: Sequence[str]) -> str:
    """Derive a stable local grouping key from identities rather than screen positions."""
    payload = repr((kind, parent, tuple(members))).encode("utf-8", errors="surrogatepass")
    return kind + ":" + sha256(payload).hexdigest()[:24]


def revise(draft: InterfaceDraft, blocks: Mapping[str, InterfaceBlock], *decisions: str) -> InterfaceDraft:
    """Publish a detached immutable block map for the next local stage."""
    return replace(draft, blocks=MappingProxyType(dict(blocks)), decisions=(*draft.decisions, *decisions))


def children_refs(node: Mapping[str, Any]) -> tuple[str, ...]:
    """Read the already-indexed immutable direct-child sequence."""
    return tuple(node.get("children", ()))


def single_line(node: Mapping[str, Any]) -> bool:
    """Reject inline layout when any displayed textual field requires its own lines."""
    for field in (
        "name",
        "description",
        "value",
        "help",
        "placeholder",
        "selectedText",
        "fullDescription",
        "valueDescription",
        "rangeDescription",
    ):
        value = node.get(field)
        if isinstance(value, str) and any(character in value for character in ("\n", "\r", "\t", "|")):
            return False
    return True


def same_scope(draft: InterfaceDraft, refs: Sequence[str]) -> bool:
    """Keep local groupings inside the same original window and blocking scope."""
    return (
        bool(refs)
        and len({(draft.evidence[ref].scope, tuple(draft.projected_nodes[ref].get("blockedBy", ()))) for ref in refs})
        == 1
    )


def prefix_signature(draft: InterfaceDraft, ref: str) -> tuple[object, ...]:
    """Compare the actual typed role and ordered states that a header will replace.

    The public path already has atomic content, including states derived from
    native supplements and the captured input focus. Structural-only callers
    retain the conservative source-field comparison until atoms are available.
    """
    if draft.atoms is not None:
        atom = draft.atoms.atoms[ref]
        return ("atom", atom.role.label, atom.role.source_description, tuple(state.label for state in atom.states))
    node = draft.projected_nodes[ref]
    fields = (
        "role",
        "subrole",
        "roleDescription",
        "enabled",
        "focused",
        "focusable",
        "selected",
        "selectable",
        "pressable",
        "editable",
        "settable",
        "required",
        "invalid",
        "busy",
        "modal",
        "expandable",
        "expanded",
        "toggleState",
        "valueType",
        "valueKind",
        "capabilities",
        "blockedBy",
    )
    return ("source", *(node.get(name) for name in fields))
