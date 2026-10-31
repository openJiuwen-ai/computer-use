# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Expose keyless native capabilities on sources removed from the compact tree."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import json
from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.actions.supporting import action_issues
from computer_use.protocol.observation import Action, ReadIssue

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.model import RenderedPart
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.records import Record


@dataclass(frozen=True)
class NativeCapabilityOccurrence:
    """One source's original position in its native action catalogue."""

    ref: str
    index: int


@dataclass(frozen=True)
class NativeCapabilityGroup:
    """One native declaration retained separately for every listed source.

    Attributes:
        action: Complete keyless declaration, including availability and its target field.
        state: Captured action-catalogue read state.
        issues: Matching catalogue/action issues with source-relative paths.
        refs: Exact source references sharing the declaration.
        scope: Original root reference; declarations stay within this captured branch.
        occurrences: Every original source/ordinal pair, including repeated equal records.
    """

    action: Action
    state: str
    issues: tuple[Mapping[str, object], ...]
    refs: tuple[str, ...]
    scope: str
    occurrences: tuple[NativeCapabilityOccurrence, ...]


def collect_native_capabilities(
    snapshot: SnapshotFacts,
    parts: Sequence[RenderedPart],
) -> tuple[tuple[NativeCapabilityGroup, ...], tuple[tuple[Record, ...], ...]]:
    """Collect keyless declarations whose original nodes have no compact tree line.

    Pruned and folded sources both retain their original reference and catalogue
    ordinal. A parent or child's SDK capability does not cover another source's
    native declaration. Equal records share wording only within the same branch;
    false availability, unknown availability and read failures remain distinct.
    """
    facts = {node["node"]: node for node in snapshot.facts["nodes"]}
    issues: dict[str, list[ReadIssue]] = {}
    for issue in snapshot.native.issues:
        if len(issue.path) >= 3 and issue.path[0] == "nodes" and issue.path[2] == "actions":
            issues.setdefault(issue.path[1], []).append(issue)
    grouped: dict[str, list[str]] = {}
    occurrences: dict[str, list[NativeCapabilityOccurrence]] = {}
    records: dict[str, tuple[Action, str, tuple[Mapping[str, object], ...], str]] = {}
    coverage = []
    for root, part in zip(snapshot.roots, parts, strict=True):
        accounted = []
        projection = part.rendered.get("projection")
        if projection is None or part.uncompressed:
            coverage.append(())
            continue
        displayed = {
            (record["ref"], record["index"]): record
            for record in part.rendered.get("factAccounting", {}).get("compactSupportingActions", ())
        }
        visible = set()
        pending = list(projection["roots"])
        while pending:
            node = pending.pop()
            visible.add(node["ref"])
            pending.extend(node["children"])
        scope = root["ref"]
        for ref in projection["nativeNodes"]:
            if ref in visible:
                continue
            native_id = snapshot.refs.get(ref)
            native = snapshot.native.nodes.get(native_id)
            if native is None:
                continue
            for index, action in enumerate(native.actions or ()):
                if action.key is not None:
                    continue
                state = facts[native_id]["actions"]["state"]
                related = action_issues(issues.get(native_id, ()), index)
                data = asdict(action)
                previous = displayed.get((ref, index))
                if (
                    previous is not None
                    and previous.get("kind") == "display"
                    and previous.get("native") == native_id
                    and previous.get("target") == ref
                    and previous.get("action") == data
                    and previous.get("state") == state
                    and previous.get("issues") == related
                ):
                    continue
                key = json.dumps(
                    (scope, data, state, related), sort_keys=True, ensure_ascii=True, separators=(",", ":")
                )
                records.setdefault(key, (action, state, related, scope))
                grouped.setdefault(key, []).append(ref)
                occurrences.setdefault(key, []).append(NativeCapabilityOccurrence(ref, index))
                accounted.append(
                    dict(
                        ref=ref,
                        native=native_id,
                        index=index,
                        action=data,
                        state=state,
                        issues=related,
                        scope=scope,
                        kind="display",
                        target=ref,
                        representation="native capability declaration",
                    )
                )
        coverage.append(tuple(accounted))
    return (
        tuple(
            NativeCapabilityGroup(
                action, state, issues, tuple(dict.fromkeys(grouped[key])), scope, tuple(occurrences[key])
            )
            for key, (action, state, issues, scope) in records.items()
        ),
        tuple(coverage),
    )


def _capability_record(group: NativeCapabilityGroup, format_field: Callable[[str], str]) -> str:
    """Keep the established complete public wording for one native declaration."""
    action = group.action
    availability = (
        "availability unknown" if action.available is None else "available" if action.available else "unavailable"
    )
    fields = [format_field(action.kind) + " " + availability]
    for label, value in (("Label", action.label), ("Description", action.description)):
        if value is not None:
            fields.append(label + ": " + format_field(value))
    if action.target is not None:
        fields.append("Target: " + json.dumps(action.target, ensure_ascii=False, separators=(",", ":")))
    if action.shortcuts:
        fields.append(
            "Shortcuts: "
            + json.dumps([asdict(shortcut) for shortcut in action.shortcuts], ensure_ascii=False, separators=(",", ":"))
        )
    if group.state != "complete":
        fields.append("catalogue " + group.state)
    if group.issues:
        fields.append("Issues: " + json.dumps(group.issues, ensure_ascii=False, separators=(",", ":")))
    return "; ".join(fields)


def _legacy_scope(groups: Sequence[NativeCapabilityGroup], records: Sequence[str]) -> tuple[str, ...]:
    """Format the existing per-record reference sets without changing their membership."""
    return tuple(
        "  [elements " + reference_set(group.refs) + "]: " + record
        for group, record in zip(groups, records, strict=True)
    )


def _bundle_scope(groups: Sequence[NativeCapabilityGroup], records: Sequence[str]) -> tuple[str, ...] | None:
    """Transpose complete equal per-source sequences when their reference shells shrink.

    The occurrence indexes determine native order and remain solely in the source
    ledger. Sharing a shell preserves the prior public record order, fields and original
    target; repeated equal native occurrences remain distinct in the ledger.
    Different read evidence, record order, multiplicity or source scope keeps bundles
    separate. Sources outside a selected bundle keep their existing record lines.
    """
    if len(groups) < 2 or len({group.scope for group in groups}) != 1:
        return None
    identities = [
        json.dumps(
            (asdict(group.action), group.state, group.issues), sort_keys=True, ensure_ascii=True, separators=(",", ":")
        )
        for group in groups
    ]
    by_ref: dict[str, list[tuple[int, int]]] = {}
    for group_index, group in enumerate(groups):
        if {item.ref for item in group.occurrences} != set(group.refs):
            return None
        for item in group.occurrences:
            by_ref.setdefault(item.ref, []).append((item.index, group_index))
    bundles: dict[tuple[str, ...], tuple[list[str], tuple[int, ...]]] = {}
    for ref, occurrences in by_ref.items():
        ordered = sorted(occurrences)
        if len({index for index, _ in ordered}) != len(ordered):
            return None
        native_sequence = tuple(index for _, index in ordered)
        identity = tuple(identities[index] for index in native_sequence)
        # Eligibility retains native order and multiplicity; public record order
        # remains the existing formatter's first-seen declaration order.
        public_sequence = tuple(sorted(set(native_sequence)))
        refs, _ = bundles.setdefault(identity, ([], public_sequence))
        refs.append(ref)
    candidate: list[str] = []
    bundled: set[str] = set()
    for refs, sequence in bundles.values():
        if len(refs) < 2 or len(sequence) < 2:
            continue
        candidate.append("  [elements " + reference_set(refs) + "]:")
        candidate.extend("    " + records[index] for index in sequence)
        bundled.update(refs)
    if not bundled:
        return None
    for group, record in zip(groups, records, strict=True):
        remaining = tuple(ref for ref in group.refs if ref not in bundled)
        if remaining:
            candidate.append("  [elements " + reference_set(remaining) + "]: " + record)
    original = _legacy_scope(groups, records)
    return tuple(candidate) if len("\n".join(candidate)) < len("\n".join(original)) else None


def format_native_capabilities(
    groups: Sequence[NativeCapabilityGroup],
    format_field: Callable[[str], str],
) -> tuple[str, ...]:
    """Render native declarations with one ref shell for equal ordered bundles.

    Only complete equal per-source sequences within the same scope share a shell.
    Single-source or single-record sequences retain the prior format; a transposition
    also retains that format when the resulting scope text would be longer. Records
    keep their full wording, availability, target, shortcuts and read limitations.
    """
    if not groups:
        return ()
    scopes: dict[str, list[NativeCapabilityGroup]] = {}
    for group in groups:
        scopes.setdefault(group.scope, []).append(group)
    lines = ["Native capabilities (reported; no performAction name):"]
    for local in scopes.values():
        records = tuple(_capability_record(group, format_field) for group in local)
        lines.extend(_bundle_scope(local, records) or _legacy_scope(local, records))
    return tuple(lines)
