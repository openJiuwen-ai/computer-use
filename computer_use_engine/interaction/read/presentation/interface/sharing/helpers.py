# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Source-bound scope and cost calculations for compact local declarations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
import json
import re
from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set
from .model import BlockSharing, SharingDraft, SharingPlan, SharingRequest

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.atoms.model import AtomAction, AtomSlot
    from .model import SharedActionGroup, SharedFieldGroup


def canonical(value: object) -> tuple[object, ...]:
    """Compare exact values including type, with stable mapping-key order."""
    if isinstance(value, Mapping):
        return ("mapping", tuple((key, canonical(item)) for key, item in sorted(value.items())))
    if isinstance(value, (tuple, list)):
        return ("sequence", tuple(canonical(item) for item in value))
    return (type(value).__qualname__, value)


def member_scope(request: SharingRequest, ref: str) -> tuple[object, ...]:
    """Keep native windows, menus, blocking and capability evidence separate."""
    interface = request.interface
    atom = request.atoms.atoms[ref]
    node = atom.source_fields
    boundary = ref
    pending = ref
    seen = set()
    while pending not in seen:
        seen.add(pending)
        source = interface.source.native_nodes[pending]
        if source["role"] in ("window", "sheet", "menu", "menuBar"):
            boundary = pending
            break
        parent = interface.source.parents.get(pending)
        if parent is None:
            boundary = pending
            break
        pending = parent
    return (
        interface.source.scopes.get(ref),
        boundary,
        canonical(node.get("capabilities")),
        canonical(node.get("blockedBy", ())),
        canonical(node.get("enabled")),
        canonical(node.get("busy")),
        canonical(node.get("invalid")),
        atom.actions_unavailable,
    )


def declaration_parent(request: SharingRequest, refs: Sequence[str]) -> str | None:
    """Place declarations in a common display group beneath a trusted native owner.

    Original ancestry establishes which sources may share a declaration. Within
    that verified scope, the closest field or region keeps the declaration beside
    the controls it describes. Source slots and executable action owners remain
    attached to their original elements.
    """
    interface = request.interface
    if len(refs) < 2:
        return None
    paths = []
    for ref in refs:
        path: list[str] = []
        seen = {ref}
        parent = interface.source.parents.get(ref)
        while parent is not None and parent not in seen:
            seen.add(parent)
            path.append(parent)
            parent = interface.source.parents.get(parent)
        paths.append(path)
    shared = set(paths[0]).intersection(*paths[1:])
    for ref in paths[0]:
        if ref not in shared or ref not in interface.ref_locations:
            continue
        if any(
            any(item in request.evidence.contested_sources for item in path[: path.index(ref) + 1]) for path in paths
        ):
            continue
        key = interface.ref_locations[ref]
        block = interface.blocks[key]
        if block.primary_ref != ref or block.origin != "native":
            continue
        found = set()
        pending = list(block.children)
        while pending:
            child = interface.blocks[pending.pop()]
            found.update(child.source_refs)
            pending.extend(child.children)
        if set(refs) <= found:
            return _display_parent(request, refs, key)
    return None


def _display_parent(request: SharingRequest, refs: Sequence[str], native_owner: str) -> str:
    """Find the innermost common field or region inside an already proved owner."""
    interface = request.interface
    parents = {child: key for key, block in interface.blocks.items() for child in block.children}
    paths = []
    for ref in refs:
        path = []
        current = interface.ref_locations[ref]
        while current != native_owner:
            path.append(current)
            if current not in parents:
                return native_owner
            current = parents[current]
        paths.append(path)
    common = set(paths[0]).intersection(*paths[1:])
    return next(
        (key for key in paths[0] if key in common and interface.blocks[key].kind in ("field", "region")), native_owner
    )


def string_cost(value: str) -> int:
    """Estimate a safely escaped field without constructing an element line."""
    return len(json.dumps(value, ensure_ascii=False))


def action_saving(refs: Sequence[str], actions: Sequence[AtomAction]) -> int:
    """Include exact member references and declaration overhead in sharing cost."""
    content = (
        sum(string_cost(action.label) + (string_cost(action.name) + 9 if action.ambiguous else 0) for action in actions)
        + max(0, len(actions) - 1) * 2
    )
    individual = len(refs) * (len("Secondary Actions: ") + content + 2)
    declaration = len("Secondary Actions [elements ]: ") + len(reference_set(refs)) + content + 1
    return individual - declaration


def field_saving(refs: Sequence[str], slot: AtomSlot) -> int:
    """Compare repeated scalar slots with a single explicit source declaration."""
    label = slot.label or slot.key
    content = string_cost(slot.value) if type(slot.value) is str else string_cost(str(slot.value))
    local_label_cost = len(slot.label) + 2 if slot.label is not None else 0
    individual = len(refs) * (local_label_cost + content + 2)
    declaration = len(label) + len(" [elements ]: ") + len(reference_set(refs)) + content + 1
    return individual - declaration


def empty_value_profitable(refs: Sequence[str], slot: AtomSlot) -> bool:
    """Include numeric reference spelling in the benefit gate for a two-character value.

    Empty Value slots cost about three tokens each; sparse, long reference lists
    can cost more despite using fewer characters. This conservative lexical
    estimate counts numeric chunks and punctuation without a tokenizer dependency.
    """
    reference_cost = sum(
        (len(piece) + 2) // 3 if piece.isdecimal() else 1 for piece in re.findall(r"\d+|[^\d]", reference_set(refs))
    )
    local_cost = len(refs) * (3 if slot.label is not None else 1)
    return local_cost > reference_cost + 10


def empty_value_groups(
    request: SharingRequest, members: Sequence[tuple[str, AtomSlot]]
) -> tuple[tuple[tuple[str, AtomSlot], ...], ...]:
    """Partition empty values into profitable declarations beneath trusted native parents.

    A conflicted outer owner stops ancestor traversal. Independent complete
    subregions can still share their own fields without crossing that boundary.
    Larger valid groups are considered first; each original slot is consumed once.
    """
    candidates: dict[str, list[tuple[str, AtomSlot]]] = {}
    interface = request.interface
    for ref, slot in members:
        parent = interface.source.parents.get(ref)
        seen = {ref}
        while parent is not None and parent not in seen:
            if parent in request.evidence.contested_sources:
                break
            seen.add(parent)
            key = interface.ref_locations.get(parent)
            if key is not None:
                block = interface.blocks[key]
                if block.primary_ref == parent and block.origin == "native":
                    candidates.setdefault(key, []).append((ref, slot))
            parent = interface.source.parents.get(parent)
    selected: list[tuple[tuple[str, AtomSlot], ...]] = []
    consumed: set[str] = set()
    for candidate in sorted(candidates.values(), key=len, reverse=True):
        remaining = tuple((ref, slot) for ref, slot in candidate if ref not in consumed)
        refs = tuple(ref for ref, _ in remaining)
        if len(refs) < 2 or declaration_parent(request, refs) is None:
            continue
        slot = remaining[0][1]
        if field_saving(refs, slot) > 0 and empty_value_profitable(refs, slot):
            selected.append(remaining)
            consumed.update(refs)
    return tuple(selected)


def append_groups(
    draft: SharingDraft,
    *,
    actions: Mapping[str, Sequence[SharedActionGroup]] = {},
    fields: Mapping[str, Sequence[SharedFieldGroup]] = {},
) -> SharingDraft:
    """Publish new declarations and their exact per-source exclusions immutably."""
    blocks = dict(draft.plan.by_block)
    omitted_slots = {ref: set(values) for ref, values in draft.plan.omitted_slots.items()}
    omitted_actions = {ref: set(values) for ref, values in draft.plan.omitted_actions.items()}
    for key in dict.fromkeys((*actions, *fields)):
        previous = blocks.get(key, BlockSharing())
        action_groups, field_groups = tuple(actions.get(key, ())), tuple(fields.get(key, ()))
        blocks[key] = BlockSharing((*previous.actions, *action_groups), (*previous.fields, *field_groups))
        for group in action_groups:
            for ref in group.refs:
                omitted_actions.setdefault(ref, set()).update(
                    action.catalogue_index for action in draft.request.atoms.atoms[ref].actions
                )
        for group in field_groups:
            for ref in group.refs:
                omitted_slots.setdefault(ref, set()).add(group.slot_key)
    return replace(
        draft,
        plan=SharingPlan(
            MappingProxyType(blocks),
            MappingProxyType({ref: frozenset(values) for ref, values in omitted_slots.items()}),
            MappingProxyType({ref: frozenset(values) for ref, values in omitted_actions.items()}),
        ),
    )
