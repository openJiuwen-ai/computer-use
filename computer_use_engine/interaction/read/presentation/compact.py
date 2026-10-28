# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Task-facing presentation policy. Exact facts remain in snapshot details.

These are rendering choices, never changes to capabilities, source values or
native action catalogues. Unknown controls and meaningful state stay visible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterable, Sequence

    from computer_use.interaction.read.presentation.actions.control_raises import ControlRaise
    from computer_use.interaction.read.presentation.actions.menu_mechanisms import MenuMechanism
    from computer_use.interaction.read.presentation.containment import PublishedTableColumn
    from computer_use.interaction.read.presentation.records import FormatField, NodeView, SnapshotFacts
    from computer_use.protocol.records import Record
import re
from dataclasses import dataclass

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.containment import containment_notes
from computer_use.interaction.read.presentation.formatting import json_string
from computer_use.interaction.read.presentation.menu_diagnostics import incidental_menu_label_error
from computer_use.interaction.read.presentation.policy import SUPPORTING_ACTIONS, editing


def display_actions(
    node: NodeView,
    actions: Iterable[Record],
    *,
    menu_mechanisms: tuple[MenuMechanism, ...] = (),
    control_raises: tuple[ControlRaise, ...] = (),
) -> list[Record]:
    # Keep context-menu actions executable.
    from computer_use.interaction.read.presentation.actions.control_raises import control_raise_selected
    from computer_use.interaction.read.presentation.actions.menu_mechanisms import menu_action_selected

    return [
        action
        for action in actions
        if action.get("kind", action["name"]) not in SUPPORTING_ACTIONS
        and not menu_action_selected(node, action, menu_mechanisms)
        and not control_raise_selected(node, action, control_raises)
    ]


def display_selection(node: NodeView) -> Mapping[str, Any] | None:
    value = node.get("selectionRange")
    return value if editing(node) or value != dict(location=0, length=0) else None


def unexpanded_menu(node: NodeView) -> bool:
    # Zero-prefix coverage describes an unexpanded menu independently of a
    # retained cursor. Native providers need not reserve a cursor until a
    # nonempty prefix has actually been consumed.
    return (
        node["role"] == "menuBarItem"
        and not node["children"]
        and node.get("childrenStart") == 0
        and node.get("childrenEnd") == 0
        and node.get("childCount", 0) > 0
    )


def display_url(value: str, format_field: FormatField, limit: int = 160, *, hint: str = "see details") -> str:
    """Bound incidental URLs without destroying paths, queries or data bytes.

    The complete URL remains available in details from the same capture.
    Preserve its query, fragment and destination when producing the display.

    Args:
        format_field: Pure formatter for one text field.
        limit: Existing upper bound on returned items for this local page/format operation.
    """
    if value.lower().startswith(("data:", "webdoc:")):
        scheme, _, rest = value.partition(":")
        kind = rest.split(",", 1)[0] if scheme.lower() == "data" else ""
        return format_field(scheme + ":" + kind[:60]) + " [embedded" + ("; " + hint if hint else "") + "]"
    if len(value) <= limit:
        return format_field(value)
    return format_field(value[:limit]) + "… [URL shortened" + ("; " + hint if hint else "") + "]"


_ENCODED = re.compile(
    r"\A(?P<header>(?:data:[\w!#$&^.+-]+/[\w!#$&^.+-]+(?:;[^;,\s]{1,128})*|/[\w!#$&^.+-]+);base64),"
    r"(?P<data>[A-Za-z0-9+/]{128,}={0,2})\Z",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class EmbeddedContent:
    """Explicit carrier syntax and payload size selected independently of text rendering."""

    header: str
    encoded_characters: int


def embedded_content(value: str) -> EmbeddedContent | None:
    """Recognize the exact encoded carrier syntax accepted by compact presentation."""
    match = _ENCODED.fullmatch(value)
    return EmbeddedContent(match["header"], len(match["data"])) if match is not None else None


def display_embedded(value: str, format_field: FormatField, *, hint: str = "see details") -> str | None:
    """Preview an explicitly encoded carrier, never guess from long text.

    Chromium may expose an image's data URL as its description with the `data:image` prefix
    removed. Retain the exact field in accounting/details; this function makes no claim that the
    payload was emitted or decoded.

    Args:
        format_field: Pure formatter for one text field.
    """
    content = embedded_content(value)
    if content is None:
        return None
    return (
        format_field(content.header)
        + f" [embedded data, {content.encoded_characters} encoded chars"
        + ("; " + hint if hint else "")
        + "]"
    )


def child_continuations(roots: Sequence[NodeView]) -> set[str]:
    """References with a child-page entry or an explicit unexpanded-menu read entry.

    Args:
        roots: Ordered roots in this observed or projected view.
    """
    refs, pending = set(), list(roots)
    while pending:
        node = pending.pop()
        if "nextChildOffset" in node or unexpanded_menu(node):
            refs.add(node["ref"])
        pending.extend(node["children"])
    return refs


def _covered_collection_notes(snapshot: SnapshotFacts, covered: set[tuple[str, str]]) -> set[str]:
    """Match collection summaries to their fully explained native relation queries.

    Semantic relation names may differ from native selectors. Query metadata and
    the original collection issue establish that both messages describe the same
    gap; every unfinished query for that selector must already have a child hint
    or a precise local relation note describing the standard unexpanded query.
    """
    metadata = snapshot.collection
    if metadata is None:
        return set()
    by_selector: dict[str, list[bool]] = {}
    for relation in snapshot.facts["relations"]:
        if relation["state"] in ("complete", "absent", "unsupported"):
            continue
        key = (relation["source"], relation["recordKey"])
        selector = metadata.relation_fields.get(key)
        if selector is None:
            # This unlocated gap might contribute to the aggregate summary.
            return set()
        by_selector.setdefault(selector, []).append(key in covered)
    explained = {
        "partial native relation: " + selector for selector, matches in by_selector.items() if matches and all(matches)
    }
    return {
        "Collection: " + json_string("nodes: " + issue.detail)
        for issue in snapshot.native.issues
        if issue.path == ("nodes",)
        and issue.state == "partial"
        and issue.detail in explained
        and issue.continuation is None
        and not issue.alternatives
    }


def _property_list(properties: list[str]) -> str:
    """Shorten adjacent paths with the same prefix without dropping a property."""
    parts: list[str] = []
    index = 0
    while index < len(properties):
        prefix, separator, leaf = properties[index].rpartition(".")
        leaves, end = [leaf], index + 1
        while separator and end < len(properties):
            following_prefix, _, following_leaf = properties[end].rpartition(".")
            if following_prefix != prefix:
                break
            leaves.append(following_leaf)
            end += 1
        if len(leaves) > 1:
            packed = prefix + ".{" + ", ".join(leaves) + "}"
            original = "; ".join(properties[index:end])
            parts.append(packed if len(packed) < len(original) else original)
        else:
            parts.append(properties[index])
        index = end
    return "; ".join(parts)


def _warning_lines(shared: Mapping[tuple[str, str], list[str | None]]) -> list[str]:
    """Share exact diagnostic details only within an identical reference set.

    Status and certainty stay attached to their own properties. Provider error
    messages remain opaque, including every error code and failed query step.
    """
    groups: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    order: list[tuple[str, str] | str] = []
    for (members, suffix), properties in shared.items():
        names = _property_list(properties) if properties[0] is not None else None
        original = members + ": " + (names + suffix if names is not None else suffix)
        match = re.fullmatch(r' \(([^)]*)\): (".*")', suffix) if names is not None else None
        if match is None:
            order.append(original)
            continue
        key = (members, match[2])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((match[1], names, original))
    lines: list[str] = []
    for item in order:
        if isinstance(item, str):
            lines.append(item)
            continue
        records = groups[item]
        combined = (
            item[0] + ": " + item[1] + "; " + "; ".join(status + " [" + names + "]" for status, names, _ in records)
        )
        originals = [original for _, _, original in records]
        lines.extend([combined] if len(records) > 1 and len(combined) < len("\n".join(originals)) else originals)
    return lines


def observation_warnings(
    snapshot: SnapshotFacts,
    *,
    hint: str = "see details",
    displayed_children: frozenset[str] = frozenset(),
    table_columns: Sequence[PublishedTableColumn] = (),
    covered_columns: list[PublishedTableColumn] | None = None,
    diagnostic_details: bool = True,
    defer_menu_label_errors: bool = False,
    concise: bool = False,
) -> list[str]:
    """Keep actual collection failures in the main view, not provider defaults.

    Args:
        snapshot: Facts captured for this observation; display edits do not mutate native
            evidence.
        displayed_children: Source references whose zero-expansion child hints were
            actually emitted by this snapshot's formatter, individually or in a shared group.
        table_columns: Complete column relations represented by the delivered full grid.
        covered_columns: Receives the exact column edges shortened in these notes.
        diagnostic_details: Include opaque provider messages; False retains field states,
            uncertainty and continuation routes while complete messages remain in captured pages.
        defer_menu_label_errors: Keep incidental inverse-label errors of completely
            named menu items in their captured details instead of the default view.
        concise: Group missing relation targets by property and source in the
            default public whole-view output; the Skill supplies read syntax.
    """
    aliases = {token: ref for ref, token in snapshot.refs.items()}
    collected = {node["node"] for node in snapshot.facts["nodes"] if node.get("observed", True)}
    warnings, groups = [], {}
    missing_targets: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    continued = child_continuations(snapshot.roots) | displayed_children
    covered_relations = set()

    def label(value: str) -> str:
        return value if re.fullmatch(r"[\w.:-]+", value) else json_string(value)

    def detail(value: Mapping[str, Any]) -> str:
        message = value.get("detail")
        if diagnostic_details:
            return ": " + json_string(message) if message else ""
        coordinate_note = "Live coordinate page; values and native objects may change between reads."
        if isinstance(message, str) and (message == coordinate_note or message.endswith(" " + coordinate_note)):
            return ": " + json_string(coordinate_note)
        if value.get("consistency") == "live":
            return ': "Live continuation; contents may change between reads."'
        return ""

    for node in snapshot.facts["nodes"]:
        for key, value in node["fieldStatus"].items():
            states = (
                (value["state"],)
                if diagnostic_details
                else tuple(dict.fromkeys((value["state"], *(issue["state"] for issue in value.get("issues", ())))))
            )
            for state in states:
                if state in ("error", "timeout", "partial", "stale", "conflict", "unavailable", "redacted"):
                    groups.setdefault(label(key) + " (" + state + ")" + detail(value), []).append(aliases[node["node"]])
    for relation in snapshot.facts["relations"]:
        if not diagnostic_details and defer_menu_label_errors and incidental_menu_label_error(snapshot, relation):
            continue
        # Native coverage and a read entry are already stated next to the
        # source or in the unexpanded-menu summary. Do not repeat them.
        # Provider errors, conflicts and other details remain visible.
        covered = (
            relation["kind"] == "children"
            and aliases[relation["source"]] in continued
            and relation["state"] == "partial"
            and relation["certainty"] == "confirmed"
            and relation.get("detail")
            in (
                None,
                "native relation has an unread continuation",
                "native relation not expanded; read its source to continue",
            )
        )
        if covered:
            covered_relations.add((relation["source"], relation["recordKey"]))
        if (
            relation["state"] in ("error", "timeout", "truncated")
            or not diagnostic_details
            and relation["state"] in ("partial", "not_read", "stale", "redacted", "unavailable")
            or relation["certainty"] == "conflicting"
            or relation["state"] not in ("absent", "unsupported")
            and (relation.get("detail") or relation["certainty"] == "unknown")
        ) and not covered:
            coverage = ""
            if not diagnostic_details and relation["state"] not in ("complete", "absent", "unsupported"):
                raw = snapshot.native.nodes[relation["source"]].relations.get(relation["recordKey"])
                if raw is not None and raw.total is not None:
                    coverage = "; " + str(len(raw.targets)) + "/" + str(raw.total) + " targets read"
                    if raw.offset is not None:
                        coverage += ", offset " + str(raw.offset)
                if relation.get("cursor") is not None:
                    coverage += "; continuation available"
            groups.setdefault(
                label(relation["property"])
                + " ("
                + relation["state"]
                + ", "
                + relation["certainty"]
                + ")"
                + detail(relation)
                + coverage,
                [],
            ).append(aliases[relation["source"]])
            if (
                relation["state"] == "partial"
                and relation["certainty"] == "confirmed"
                and relation.get("detail") == "native relation not expanded; read its source to continue"
            ):
                # This exact source/property/state/read route is now explicit in
                # Read notes. Its typed collection summary adds no further gap.
                covered_relations.add((relation["source"], relation["recordKey"]))
        # Missing labels/selected members/content remain discoverable even
        # when the relation query itself completed successfully. Parent/window
        # endpoints outside this observation are scope metadata, not content.
        missing = [aliases[token] for token in relation["targets"] if token not in collected]
        if missing and relation["kind"] not in ("children", "parent", "window"):
            if concise and not diagnostic_details:
                missing_targets.setdefault(label(relation["property"]), []).append(
                    (aliases[relation["source"]], tuple(missing))
                )
            else:
                groups.setdefault(
                    label(relation["property"])
                    + " -> "
                    + ", ".join(missing)
                    + " (not collected; inspect with app.element(ref).read())",
                    [],
                ).append(aliases[relation["source"]])
    # A collection budget often expires for the same set of nodes across many
    # fields. Share only exactly equal reference sets and diagnostic suffixes;
    # preserve each affected property and never turn unread fields into values.
    shared = {}
    for message, refs in groups.items():
        members = reference_set(refs)
        match = re.fullmatch(r"([\w.:-]+)( \([^)]*\)(?:: .*)?)", message)
        key = (members, match[2]) if match else (members, message)
        shared.setdefault(key, []).append(match[1] if match else None)
    warnings.extend(_warning_lines(shared))
    if missing_targets:
        warnings.extend(
            property
            + " targets not collected: "
            + "; ".join(source + "→" + ", ".join(targets) for source, targets in entries)
            + "."
            for property, entries in missing_targets.items()
        )
    # Shared/cyclic containment and disconnected roots are actionable gaps.
    # Uncollected ancestor/window endpoints alone do not mean missing content;
    # incomplete child branches already carry a local coverage annotation.
    covered_notes = _covered_collection_notes(snapshot, covered_relations)
    warnings.extend(
        containment_notes(
            [
                line
                for line in snapshot.annotations
                if (
                    " contains " in line
                    or " has no expanded containment path " in line
                    or diagnostic_details
                    and line.startswith("Collection:")
                )
                and line not in covered_notes
            ],
            columns=table_columns,
            covered=covered_columns,
        )
    )
    if not diagnostic_details:
        collection = dict.fromkeys(
            (issue.path, issue.state)
            for issue in snapshot.native.issues
            if (not issue.path or issue.path[0] != "nodes" or len(issue.path) < 2)
            and issue.state not in ("absent", "unsupported")
            and ("Collection: " + json_string("nodes: " + issue.detail) if issue.detail else "") not in covered_notes
        )
        warnings.extend(
            "Collection: " + label(".".join(map(str, path)) or "observation") + " (" + state + ")"
            for path, state in collection
        )
    return ["Read notes:\n" + "\n".join("\t" + line for line in warnings)] if warnings else []


if TYPE_CHECKING:
    from typing import Any, Mapping
