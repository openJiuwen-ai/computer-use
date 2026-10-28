# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Plan captured background content only from a fixed scope and proven modal blockers."""

from __future__ import annotations

from math import ceil
from urllib.parse import quote

from computer_use.interaction.read.presentation.interface.disclosure.policy import estimate_units
from .evidence import OUTLINE_ROLES, recovery_sources
from .model import BackgroundOverview, ClassifiedGroups, GroupDisclosureRequest
from .planning import capture_entries, captured_text_fields


def retained_notices(
    request: GroupDisclosureRequest, refs: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[tuple[str, str, object], ...]]:
    """Preserve source-reported states, collection gaps and nested window identities."""
    native, atoms = request.snapshot.native, request.atoms.atoms
    important, uncertain, nested, state_facts = [], [], [], []
    for ref in refs:
        atom = atoms[ref]
        view = atom.source_fields
        source = native.nodes[request.snapshot.refs[ref]]
        input_target = (
            source.role in ("text_field", "text_area", "combo_box", "search_field", "spin_button")
            or source.states.editable is True
            or source.control_value is not None
            and source.control_value.read_only is False
            or any(view.get("capabilities", {}).get(key) == "available" for key in ("typeText", "replaceText"))
        )
        if (
            any(view.get(key) for key in ("error", "invalid", "modified", "unsaved", "busy"))
            or source.states.busy is True
            or source.states.focused is True
            and input_target
            or source.text is not None
            and source.text.caret_active is True
            or any(
                item.key in ("modified", "unsaved", "invalid", "error") and item.value for item in source.supplements
            )
            or any(relation.kind == "error" and relation.targets for relation in source.relations.values())
        ):
            important.append(ref)
            state_facts.extend(
                (ref, key, view[key]) for key in ("error", "invalid", "modified", "unsaved") if view.get(key)
            )
            state_facts.extend(
                (ref, item.key, item.value)
                for item in source.supplements
                if item.key in ("modified", "unsaved", "invalid", "error") and item.value
            )
        # AppKit cells commonly report focus simultaneously. Preserve every
        # declaration without treating each flag as an active text receiver.
        state_facts.extend(
            (ref, "states." + key, True) for key in ("busy", "focused") if getattr(source.states, key) is True
        )
        if (
            atom.coverage.partial
            or atom.actions_unavailable
            or any(slot.format == "unavailable" for slot in atom.slots)
            or any(
                issue.path[:2] == ("nodes", request.snapshot.refs[ref]) and issue.state not in ("unsupported", "absent")
                for issue in native.issues
            )
        ):
            uncertain.append(ref)
        if source.role in ("window", "sheet", "dialog", "alert"):
            nested.append(ref)
    return tuple(important), tuple(uncertain), tuple(nested), tuple(state_facts)


def candidate(
    classified: ClassifiedGroups, root_key: str, recovery_prefix: int
) -> tuple[BackgroundOverview | None, str]:
    """Fold only a known blocked window while exposing important state and all gaps."""
    request = classified.request
    context = request.window_context
    if (
        context is None
        or not context.scope_confirmed
        or not context.confirmed_blockers
        or context.current
        or context.modal is True
    ):
        return None, "background-requires-fixed-scope-and-confirmed-blocking"
    root = request.interface.blocks[root_key]
    if root.primary_ref is None or not root.children:
        return None, "background-root-has-no-native-child-scope"
    native = request.snapshot.native
    root_token = request.snapshot.refs.get(root.primary_ref)
    if root_token not in native.roots or native.nodes[root_token].role not in ("window", "dialog"):
        return None, "background-root-is-not-its-bound-native-window"
    refs = tuple(ref for key in root.children for ref in classified.block_refs[key])
    recovery = recovery_sources(request, refs)
    if len(refs) < 12 or any(request.snapshot.refs.get(ref) not in native.nodes for ref in recovery):
        return None, "background-too-small-or-source-unavailable"
    atoms = request.atoms.atoms
    entries = capture_entries(classified, refs)
    fields = tuple(field for entry in entries for field in entry.fields)
    important, uncertain, nested, state_facts = retained_notices(request, refs)
    # Retain regional labels and nested window identities without assigning them
    # to another capture or guessing whether an identically named sheet is shared.
    outline = tuple(
        field
        for field in fields
        if field.slot_key in ("name", "description")
        and native.nodes[request.snapshot.refs[field.ref]].role
        in OUTLINE_ROLES | {"window", "sheet", "dialog", "alert"}
    )
    full_units = sum(
        estimate_units(atom.ref + atom.role.label)
        + 4
        + sum(estimate_units(str(slot.value)) + estimate_units(slot.label or "") + 1 for slot in atom.slots)
        + sum(estimate_units(action.label) + 3 for action in atom.actions)
        for atom in (atoms[ref] for ref in refs)
    )
    overview_units = (
        110
        + sum(estimate_units(field.ref + str(field.slot.value)) + 4 for field in outline)
        + sum(estimate_units(ref) + 1 for ref in (*important, *uncertain, *nested))
        + sum(estimate_units(str(slot.value)) + 5 for ref in important for slot in atoms[ref].slots)
    )
    recovery_units = recovery_prefix + 2 * full_units
    saved = full_units - overview_units - ceil(recovery_units * request.policy.recovery_weight)
    if saved < 64 or saved < full_units * 0.2:
        return None, "background-overview-has-insufficient-benefit"
    capture = (native.backend, native.session, native.id)
    key = "background:" + ":".join(quote(value, safe="") for value in (*capture, root.primary_ref))
    return BackgroundOverview(
        key,
        capture,
        root_key,
        root.children,
        refs,
        recovery,
        entries,
        fields,
        outline,
        (),
        (),
        "background",
        sum(len(source.value) for source in captured_text_fields(fields)),
        None,
        full_units,
        overview_units,
        recovery_units,
        saved,
        context.window_ref,
        context.confirmed_blockers,
        context.directory_complete,
        tuple(important),
        tuple(uncertain),
        tuple(nested),
        tuple(state_facts),
    ), "beneficial-confirmed-blocked-background"
