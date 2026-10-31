# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Index a projection without changing its native or displayed source fields."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft, InterfaceRequest
from computer_use.interaction.read.presentation.ir import freeze


def apply(request: InterfaceRequest) -> InterfaceDraft:
    """Detach the projected forest and replace child dictionaries with exact references."""
    projection = request.projection
    nodes: dict[str, Any] = {}
    parents: dict[str, str] = {}
    order: list[str] = []
    roots = tuple(node["ref"] for node in projection["roots"])
    pending = [(node, None) for node in reversed(projection["roots"])]
    while pending:
        node, parent = pending.pop()
        ref = node["ref"]
        if not isinstance(ref, str) or ref in nodes:
            raise ValueError("Interface sources require unique real references")
        if ref not in projection.source.display_nodes:
            raise ValueError("Interface source is outside the captured projection")
        children = node["children"]
        nodes[ref] = freeze(
            {
                **{key: value for key, value in node.items() if key not in ("children", "native")},
                "children": tuple(child["ref"] for child in children),
            }
        )
        if parent is not None:
            parents[ref] = parent
        order.append(ref)
        pending.extend((child, ref) for child in reversed(children))
    return InterfaceDraft(
        projection.source,
        MappingProxyType(nodes),
        MappingProxyType(parents),
        tuple(order),
        roots,
        projection.provenance,
        freeze(projection.get("foldedInto", {})),
        request.enabled,
        relations=request.relations,
        atoms=request.atoms,
    )
