# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public declarations for supporting actions omitted from compact node lines."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
import json
from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.compression.policy import secondary_actions
from computer_use.interaction.read.presentation.policy import SUPPORTING_ACTIONS
from computer_use.interaction.read.presentation.actions.menu_mechanisms import capture_menu_mechanisms
from computer_use.interaction.read.presentation.actions.control_raises import capture_control_raises
from computer_use.protocol.observation import Action, ReadIssue

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.model import RenderedPart
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.records import Record


_FRIENDLY = {"show_menu": "Show menu", "scroll_into_view": "Scroll into view"}


@dataclass(frozen=True)
class SupportingActionGroup:
    """One exact action record declared independently on each listed source.

    Attributes:
        action: Complete original operation, including its callable key and availability.
        state: Native action-catalogue read state shared by these sources.
        issues: Matching catalogue/action issues with source-relative paths.
        refs: Public source references; grouping does not redirect their operations.
    """

    action: Action
    state: str
    issues: tuple[Mapping[str, object], ...]
    refs: tuple[str, ...]


def action_issues(issues: Sequence[ReadIssue], index: int) -> tuple[dict[str, object], ...]:
    """Retain one action's read issues with paths relative to its source catalogue."""
    result = []
    for issue in issues:
        relative = issue.path[3:]
        if relative and relative[0] != index:
            continue
        # The action index is retained in coverage. Relative paths let equal
        # records share wording even at different positions in their catalogues.
        path = ("action", *relative[1:]) if relative else ("catalogue",)
        result.append({**asdict(issue), "path": path})
    return tuple(result)


def collect_supporting_actions(
    snapshot: SnapshotFacts,
    parts: Sequence[RenderedPart],
) -> tuple[tuple[SupportingActionGroup, ...], tuple[tuple[Record, ...], ...]]:
    """Find original supporting operations absent from compact secondary-action fields.

    Source keys and native action ordinals remain distinct in the coverage ledger.
    Equal records can share a declaration across references; another element's
    action never satisfies this element's coverage. Existing same-source primary
    actions and uncompressed fallback records keep their original presentation.
    """
    facts = {node["node"]: node for node in snapshot.facts["nodes"]}
    issues: dict[str, list[ReadIssue]] = {}
    for issue in snapshot.native.issues:
        if len(issue.path) >= 3 and issue.path[0] == "nodes" and issue.path[2] == "actions":
            issues.setdefault(issue.path[1], []).append(issue)
    grouped: dict[str, list[str]] = {}
    records: dict[str, tuple[Action, str, tuple[Mapping[str, object], ...]]] = {}
    coverage = []
    for part in parts:
        accounted = []
        projection = part.rendered.get("projection")
        selected_menu = (
            {
                item
                for item in capture_menu_mechanisms(snapshot, projection)
                if asdict(item) in part.rendered.get("deferredMenuActions", ())
            }
            if projection is not None and not part.uncompressed
            else set()
        )
        selected_raises = (
            {
                item
                for item in capture_control_raises(snapshot, projection)
                if any(
                    type(marker.get("index")) is int and marker == asdict(item)
                    for marker in part.rendered.get("deferredControlRaises", ())
                )
            }
            if projection is not None and not part.uncompressed
            else set()
        )
        for ref, node in projection["nativeNodes"].items() if projection is not None and not part.uncompressed else ():
            native_id = snapshot.refs.get(ref)
            native = snapshot.native.nodes.get(native_id)
            if native is None:
                continue
            secondary = secondary_actions(node)
            for index, action in enumerate(native.actions or ()):
                deferred_menu = any(
                    item.ref == ref and item.native == native_id and item.index == index for item in selected_menu
                )
                deferred_raise = any(
                    item.ref == ref and item.native == native_id and item.index == index for item in selected_raises
                )
                if action.kind not in SUPPORTING_ACTIONS and not deferred_menu and not deferred_raise:
                    continue
                # A keyed record missing from secondaryActions is already the
                # same source's primary action. Never compare different owners.
                if action.key is not None and not any(
                    value["name"] == action.key and value.get("kind", value["name"]) == action.kind
                    for value in secondary
                ):
                    continue
                state = facts[native_id]["actions"]["state"]
                related = action_issues(issues.get(native_id, ()), index)
                key = json.dumps(
                    (asdict(action), state, related), sort_keys=True, ensure_ascii=True, separators=(",", ":")
                )
                records.setdefault(key, (action, state, related))
                grouped.setdefault(key, []).append(ref)
                accounted.append(
                    dict(
                        ref=ref,
                        native=native_id,
                        index=index,
                        action=asdict(action),
                        state=state,
                        issues=related,
                        kind="display",
                        target=ref,
                        representation="supporting action declaration",
                    )
                )
        coverage.append(tuple(accounted))
    return (
        tuple(SupportingActionGroup(*records[key], tuple(dict.fromkeys(refs))) for key, refs in grouped.items()),
        tuple(coverage),
    )


def format_supporting_actions(
    groups: Sequence[SupportingActionGroup],
    format_field: Callable[[str], str],
) -> tuple[str, ...]:
    """Format source action labels with their callable selector and read state.

    The original description, label, or key supplies the title. A different key
    stays explicit because each owner's other actions may make the description
    ambiguous. Distinct fields and uncertain read/availability states remain
    visible; equal strings from this same action share their occurrence.
    """
    lines = []
    for group in groups:
        action = group.action
        title = next(
            (value for value in (action.description, action.label, action.key) if value),
            _FRIENDLY.get(action.kind, action.kind),
        )
        fields = [format_field(title)]
        represented = {title}
        if action.key is not None:
            if action.key != title:
                fields[0] += " [name: " + json.dumps(action.key, ensure_ascii=False) + "]"
            represented.add(action.key)
        else:
            fields[0] += " (no callable name)"
        for label, value in (("Description", action.description), ("Label", action.label)):
            if value is not None and value not in represented:
                fields.append(label + ": " + format_field(value))
                represented.add(value)
        fields.append(
            "availability unknown" if action.available is None else "available" if action.available else "unavailable"
        )
        if action.target is not None:
            fields.append("Target: " + json.dumps(action.target, ensure_ascii=False, separators=(",", ":")))
        if action.shortcuts:
            fields.append(
                "Shortcuts: "
                + json.dumps(
                    [asdict(shortcut) for shortcut in action.shortcuts], ensure_ascii=False, separators=(",", ":")
                )
            )
        if group.state != "complete":
            fields.append("catalogue " + group.state)
        if group.issues:
            fields.append("Issues: " + json.dumps(group.issues, ensure_ascii=False, separators=(",", ":")))
        lines.append("Supporting actions [elements " + reference_set(group.refs) + "]: " + "; ".join(fields))
    return tuple(lines)
