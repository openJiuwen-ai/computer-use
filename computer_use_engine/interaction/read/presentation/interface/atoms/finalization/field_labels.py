# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Account for visual label relations already expressed by a final field grouping."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.atoms.model import AtomFinalizeRequest


def share_field_labels(request: AtomFinalizeRequest) -> AtomFinalizeRequest:
    """Omit repeated relation slots only inside the field containing their exact label.

    Args:
        request: Final layout, original atom fields and the public presentation policy.

    Returns:
        Content with matching visual-label facts represented by the explicit field
        context. Labels outside that field, unresolved labels and explicit subtrees
        keep their relation slots. All original elements and facts remain retained.
    """
    interface = request.interface
    policy = request.branch.request
    if (
        interface is None
        or interface.format_version != "geometric-2"
        or not policy.compact
        or not policy.public_api
        or not policy.share_descriptions
        or request.projection.source.context.get("subtreeRef")
    ):
        return request
    atoms = dict(request.atoms.atoms)
    applicable: dict[str, frozenset[str]] = {}
    pending = [(key, frozenset()) for key in reversed(interface.roots)]
    while pending:
        key, labels = pending.pop()
        block = interface.blocks[key]
        if block.kind == "window":
            labels = frozenset()
        if (
            block.kind == "field"
            and block.label_ref is not None
            and block.label_ref in atoms
            and block.label_ref in interface.ref_locations
        ):
            labels = labels | {block.label_ref}
        for ref in block.source_refs:
            applicable[ref] = labels
        pending.extend((child, labels) for child in reversed(block.children))
    changed = False
    for ref, atom in atoms.items():
        slot = atom.slot("visualLabelRef")
        if slot is None or not isinstance(slot.value, tuple) or len(slot.value) != 1:
            continue
        label = slot.value[0]
        if (
            label == ref
            or label not in applicable.get(ref, ())
            or interface.source.scopes.get(ref) is None
            or interface.source.scopes.get(ref) != interface.source.scopes.get(label)
        ):
            continue
        destinations = tuple(
            replace(item, slot="field-label-context", kind="equivalent")
            if item.slot == "visualLabelRef" and item.target_ref == ref
            else item
            for item in atom.dispositions
        )
        claims = tuple(
            replace(item, kind="equivalent") if item.ref == ref and item.field == "visualLabelRef" else item
            for item in atom.field_claims
        )
        atoms[ref] = replace(
            atom,
            slots=tuple(item for item in atom.slots if item.key != "visualLabelRef"),
            dispositions=destinations,
            field_claims=claims,
        )
        changed = True
    if not changed:
        return request
    document = replace(
        request.atoms,
        atoms=MappingProxyType(atoms),
        dispositions=tuple(item for ref in request.atoms.order for item in atoms[ref].dispositions),
        field_claims=tuple(item for ref in request.atoms.order for item in atoms[ref].field_claims),
    )
    return replace(request, atoms=document)
