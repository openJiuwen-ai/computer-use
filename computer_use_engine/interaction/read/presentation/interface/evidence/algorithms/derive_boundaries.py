# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain native scopes, independent operations and incomplete local content."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.helpers import REGION_ROLES
from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, NodeEvidence


def apply(draft: InterfaceDraft) -> InterfaceDraft:
    """Classify each source using observed facts, without inferring input from geometry."""
    evidence = {}
    for ref, node in draft.projected_nodes.items():
        native = draft.source.native_nodes[ref]
        read = native.get("childrenRead", len(native.get("children", ())))
        total = native.get("childCount", read)
        incomplete = (
            node.get("partialReason") is not None
            or native.get("partialReason") is not None
            or isinstance(read, int)
            and isinstance(total, int)
            and read < total
        )
        independent = (
            bool(node.get("actions"))
            or node.get("pressable") is True
            or node.get("selectable") is True
            or node.get("editable") is True
            or any(value == "available" for value in node.get("capabilities", {}).values())
            or node.get("focused") is True
            or node.get("selected") is True
        )
        boundary = (
            node["role"]
            in REGION_ROLES
            | {"window", "sheet", "table", "list", "outline", "row", "cell", "column", "menu", "menuBar"}
            or node.get("modal") is True
            or node.get("subrole") in ("dialog", "systemDialog", "popover")
        )
        hidden = node.get("visible") is False or node.get("showing") is False or node.get("offscreen") is True
        evidence[ref] = NodeEvidence(draft.source.scopes[ref], independent, boundary, incomplete, hidden)
    return replace(draft, evidence=MappingProxyType(evidence))
