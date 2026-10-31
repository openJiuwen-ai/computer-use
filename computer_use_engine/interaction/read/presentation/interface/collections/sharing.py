# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exact structural prefix sharing and short passive cell-chain selection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace

from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument, AtomFact, AtomPlan, AtomState
from computer_use.interaction.read.presentation.interface.collections.model import (
    CollectionMemberPrefix,
    CollectionPrefixPlan,
    CollectionRequest,
    TablePlan,
)


def _facts(items: Iterable[AtomFact]) -> tuple[tuple[str, type, object], ...]:
    """Compare the same evidence values without conflating their original owners."""
    return tuple((item.key.attribute, type(item.value), item.value) for item in items)


def _role_signature(atom: AtomPlan) -> tuple[object, ...]:
    """Compare role values and evidence fields while retaining distinct source IDs."""
    return (atom.role.label, atom.role.source_description, _facts(atom.role.facts))


def shared_prefix(
    atoms: AtomDocument, refs: tuple[str, ...], *, structural_slots: tuple[str, ...] = ()
) -> CollectionPrefixPlan | None:
    """Intersect exact structured header fields while preserving per-member state.

    Only the caller's proven structural fields can move into the header. Values,
    labels, actions and selection ranges stay on their original member. Common
    states retain the first member's order and every matching source occurrence.
    """
    if len(refs) < 2 or len(set(refs)) != len(refs) or any(ref not in atoms.atoms for ref in refs):
        return None
    members = tuple(atoms.atoms[ref] for ref in refs)
    role_key = _role_signature(members[0])
    if any(_role_signature(member) != role_key for member in members[1:]):
        return None
    role = replace(members[0].role, facts=tuple(fact for member in members for fact in member.role.facts))
    indices: list[list[int]] = [[] for _ in members]
    states = []
    for first_index, first in enumerate(members[0].states):
        matching = [first_index]
        for member_index, member in enumerate(members[1:], 1):
            index = next(
                (
                    index
                    for index, state in enumerate(member.states)
                    if index not in indices[member_index]
                    and state.label == first.label
                    and _facts(state.facts) == _facts(first.facts)
                ),
                None,
            )
            if index is None:
                break
            matching.append(index)
        if len(matching) != len(members):
            continue
        states.append(
            AtomState(
                first.label,
                tuple(fact for member, index in zip(members, matching) for fact in member.states[index].facts),
            )
        )
        for selected, index in zip(indices, matching):
            selected.append(index)
    slots = []
    for key in structural_slots:
        candidates = tuple(member.slot(key) for member in members)
        if any(
            slot is None
            or slot.preview is not None
            or slot.protection is not None
            or slot.format not in ("span", "number")
            for slot in candidates
        ):
            continue
        first = candidates[0]
        if any(
            (slot.label, slot.format, slot.placement, type(slot.value), slot.value, _facts(slot.facts))
            != (first.label, first.format, first.placement, type(first.value), first.value, _facts(first.facts))
            for slot in candidates[1:]
        ):
            continue
        slots.append(replace(first, facts=tuple(fact for slot in candidates for fact in slot.facts), provenance=None))
    keys = tuple(slot.key for slot in slots)
    return CollectionPrefixPlan(
        refs,
        role,
        tuple(states),
        tuple(slots),
        tuple(CollectionMemberPrefix(ref, tuple(selected), keys) for ref, selected in zip(refs, indices)),
    )


def passive_cell_chain(request: CollectionRequest, ref: str, descendants: tuple[str, ...]) -> tuple[str, ...]:
    """Select a whole short single-child chain, retaining every original member.

    Only passive leaf-like roles with complete known action coverage participate.
    The plan changes line layout, so no values, states, or source roles are elided.
    """
    if not 1 <= len(descendants) <= 3:
        return ()
    atoms = request.atoms.atoms
    previous = ref
    for child in descendants:
        atom = atoms[child]
        native = request.native.nodes.get(request.refs.get(child))
        fields = request.interface.projected_nodes[child]
        capabilities = fields.get("capabilities")
        capabilities = capabilities if isinstance(capabilities, Mapping) else {}
        if (
            atoms[previous].children != (child,)
            or native is None
            or native.role not in ("static_text", "image")
            or native.actions != ()
            or atom.actions
            or atom.actions_unavailable
            or atom.coverage.partial
            or any(value == "available" for value in capabilities.values())
            or any(isinstance(slot.value, str) and ("\n" in slot.value or "\r" in slot.value) for slot in atom.slots)
        ):
            return ()
        previous = child
    return (ref, *descendants) if not atoms[previous].children else ()


def share_prefixes(plan: TablePlan, request: CollectionRequest) -> TablePlan:
    """Select common row/cell header fields and retain native column names."""
    indices = tuple(sorted({cell.column for row in plan.rows for cell in row.cells}))
    typed_prefixes = []
    labels = []
    for index in indices:
        members = [next((cell for cell in row.cells if cell.column == index), None) for row in plan.rows]
        refs = tuple(cell.ref for cell in members if cell is not None)
        if len(refs) != len(plan.rows) or any(
            cell.row_span != 1 or cell.column_span != 1 for cell in members if cell is not None
        ):
            typed_prefixes.append(None)
        else:
            typed_prefixes.append(shared_prefix(request.atoms, refs, structural_slots=("columnRange",)))
        names = []
        for column in plan.columns:
            if column.index != index:
                continue
            native = request.native.nodes.get(request.refs.get(column.ref))
            path = ("nodes", request.refs.get(column.ref), "name")
            unavailable = any(
                issue.path[: len(path)] == path or path[: len(issue.path)] == issue.path
                for issue in request.native.issues
            )
            if native is not None and native.name and not unavailable:
                names.append(native.name)
        labels.append(names[0] if len(names) == 1 else None)
    return replace(
        plan,
        column_indices=indices,
        column_labels=tuple(labels),
        row_prefix=shared_prefix(
            request.atoms, tuple(row.ref for row in plan.rows), structural_slots=("disclosureLevel",)
        ),
        column_prefixes=tuple(typed_prefixes),
    )
