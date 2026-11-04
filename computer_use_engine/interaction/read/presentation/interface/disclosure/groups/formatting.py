# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Terminal overview text and leading, exactly recoverable captured-content catalogues."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.fallback import literal
from .model import BackgroundOverview, GroupDisclosurePlan, GroupOverview

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import CapturedSnapshot


class CapturedDocument(Protocol):
    """A frozen text view rendered by the normal terminal formatting boundary."""

    @property
    def text(self) -> str: ...


def format_group_overview(group: GroupOverview) -> tuple[str, ...]:
    """Render a selected passive group with exact samples and the genuine global detail route."""
    if isinstance(group, BackgroundOverview):
        lines = [
            f"Blocked background overview: window {group.window_ref}; "
            + "blocked by window "
            + ", ".join(map(str, group.blockers))
            + "; "
            + f"{len(group.projected_refs)} captured elements. Controls remain unavailable in this background scope."
        ]
        if group.outline:
            lines.append(
                "  Captured regions: "
                + "; ".join(f"{field.ref} {literal(field.slot.value)}" for field in group.outline)
            )
        if group.nested_windows:
            lines.append(
                "  Nested window elements: "
                + reference_set(group.nested_windows)
                + "; separate original references, not identified with another window capture."
            )
        if group.uncertain_refs:
            lines.append(
                "  Incomplete or unknown captured facts: elements "
                + reference_set(group.uncertain_refs)
                + "; inspect captured details or reread that element."
            )
        if not group.directory_complete:
            lines.append("  Window directory incomplete; unknown window relationships remain unresolved.")
        states: dict[tuple[str, str], list[str]] = {}
        for ref, key, value in group.state_facts:
            states.setdefault((key, literal(value)), []).append(ref)
        lines.extend(
            "  Reported background state [elements " + reference_set(refs) + f"]: {key} = {value}"
            for (key, value), refs in states.items()
        )
        lines.append(
            f"  Full captured background: state.read_details(); section {group.section_key} "
            "(global details; follow returned cursor)."
        )
        return tuple(lines)
    count = (
        f"{group.captured_records} captured records"
        if group.kind == "records"
        else f"{len(group.projected_refs)} captured objects"
    )
    lines = [
        f"Content overview [{group.section_key}]: {count}; "
        f"{group.captured_characters} characters across captured fields; "
        f"elements {reference_set(group.projected_refs)}."
    ]
    if group.outline:
        lines.append("  Outline: " + "; ".join(f"{field.ref} {literal(field.slot.value)}" for field in group.outline))
    for label, parts in (("First excerpt", group.head), ("Last excerpt", group.tail)):
        for part in parts:
            lines.append(f"  {label} [element {part.source.ref}]: {literal(part.text)}")
    lines.append(
        f"  Full captured content: state.read_details(); section {group.section_key} "
        "(global details; follow returned cursor)."
    )
    return tuple(lines)


def format_group_directory(plan: GroupDisclosurePlan) -> str:
    """List precise content sections separately from their possibly long captured payloads."""
    if not plan.groups:
        return ""
    lines = ["Captured content directory (same observation; complete hidden fields):"]
    for group in plan.groups:
        lines.append(
            f"  {group.section_key}: elements {reference_set(group.projected_refs)}; "
            f"{group.captured_characters} characters across captured fields"
        )
    return "\n".join(lines)


def format_group_payload(
    plan: GroupDisclosurePlan,
    snapshot: CapturedSnapshot,
    background_content: Mapping[str, tuple[str, ...]] | None = None,
) -> str:
    """Restore hidden fields with exact sources ahead of ordinary native diagnostic supplements.

    The catalogue retains each original object, its display parent, complete slot
    values and exact contributing facts. Native properties and relation records
    remain in the existing supplement rather than being duplicated per group.
    """
    if not plan.groups:
        return ""
    native = snapshot.native
    capture = (native.backend, native.session, native.id)
    if any(group.capture != capture for group in plan.groups):
        raise ValueError("Group detail catalogue cannot use a different observation")
    lines: list[str] = []
    for group in plan.groups:
        lines.append(f"Captured content section {group.section_key}:")
        lines.append("  Source: " + literal(dict(backend=capture[0], session=capture[1], observation=capture[2])))
        if isinstance(group, BackgroundOverview):
            content = (background_content or {}).get(group.section_key)
            if not content:
                raise ValueError("Background detail payload lacks the complete original presentation")
            lines.append("  Captured presentation (blocked background; original targets and actions):")
            lines.extend(content)
        for entry in group.entries:
            token = snapshot.refs.get(entry.ref)
            if token not in native.nodes:
                raise ValueError("Group detail catalogue lost its original native source")
            lines.append(
                "  Object: " + literal(dict(ref=entry.ref, parent=entry.parent, role=entry.role, native=token))
            )
            for field in entry.fields:
                # Equal original facts use one value with their exact owners;
                # composite or heterogeneous originals retain separate values.
                equal, other = [], []
                for fact in field.slot.facts:
                    identity = dict(ref=str(fact.key.source), field=fact.key.attribute)
                    if type(fact.value) is type(field.slot.value) and fact.value == field.slot.value:
                        equal.append(identity)
                    else:
                        other.append(dict(**identity, value=fact.value))
                record: dict[str, object] = dict(key=field.slot_key, value=field.slot.value)
                if equal and equal != [dict(ref=entry.ref, field=field.slot_key)]:
                    record["sameSources"] = equal
                if other:
                    record["sourceValues"] = other
                native_differences = [
                    dict(ref=source.ref, field=source.native_field, value=source.value)
                    for source in field.sources
                    if type(source.value) is not type(source.normalized) or source.value != source.normalized
                ]
                if native_differences:
                    record["nativeValues"] = native_differences
                lines.append("    Field: " + literal(record))
        lines.append(f"End captured content section {group.section_key}.")
    return "\n".join(lines)


def format_group_details(plan: GroupDisclosurePlan, snapshot: CapturedSnapshot) -> str:
    """Compose a single capture's directory and payload at the final text boundary."""
    directory, payload = format_group_directory(plan), format_group_payload(plan, snapshot)
    return directory + ("\n" if directory and payload else "") + payload


@dataclass(frozen=True)
class GroupDetailsDocument:
    """Place recoverable content before technical supplements in existing local pagination."""

    plan: GroupDisclosurePlan
    snapshot: CapturedSnapshot
    supplement: CapturedDocument | None = None

    @property
    def text(self) -> str:
        """Materialize the captured catalogue only at the normal text-output boundary."""
        prefix = format_group_details(self.plan, self.snapshot)
        if self.supplement is None:
            return prefix
        return prefix + ("\n\n" if prefix else "") + self.supplement.text
