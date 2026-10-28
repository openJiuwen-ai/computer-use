# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Read already-proved visual rows without deriving native help or input targets."""

from __future__ import annotations

from collections.abc import Mapping

from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument
from computer_use.interaction.read.presentation.interface.helpers import CONTROL_ROLES, FIELD_ROLES
from computer_use.interaction.read.presentation.interface.model import InterfaceDocument


def explanation_rows(interface: InterfaceDocument, atoms: AtomDocument) -> Mapping[str, tuple[str, ...]]:
    """Locate descriptive images beside controls in their innermost visual row.

    Args:
        interface: Validated final block forest with every original member retained.
        atoms: Immutable captured descriptions, roles, actions and capabilities.

    Returns:
        Image references mapped to all other members of the same selected row.
        The association describes display proximity; it selects no help recipient
        or executable target, including when several controls share the row.
    """
    rows: dict[str, list[str]] = {}
    locations: dict[str, str] = {}
    pending = [(key, None) for key in reversed(interface.roots)]
    while pending:
        key, row = pending.pop()
        block = interface.blocks[key]
        if block.origin == "visual" and block.layout == "visual-row":
            row = key
            rows.setdefault(row, [])
        if row is not None:
            for ref in block.source_refs:
                rows[row].append(ref)
                locations[ref] = row
        pending.extend((child, row) for child in reversed(block.children))
    result: dict[str, tuple[str, ...]] = {}
    for ref, key in locations.items():
        node = interface.projected_nodes[ref]
        slot = atoms.atoms[ref].slot("description")
        if node["role"] != "image" or slot is None or not isinstance(slot.value, str) or not slot.value.strip():
            continue
        neighbours = tuple(member for member in rows[key] if member != ref)
        if any(interface.projected_nodes[member]["role"] in CONTROL_ROLES | FIELD_ROLES for member in neighbours):
            result[ref] = neighbours
    return result
