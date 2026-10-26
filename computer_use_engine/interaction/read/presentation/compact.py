"""Task-facing presentation policy. Exact facts remain in snapshot details.

These are rendering choices, never changes to capabilities, source values or
native action catalogues. Unknown controls and meaningful state stay visible.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.formatting import jsonString
from computer_use.interaction.read.presentation.policy import SUPPORTING_ACTIONS, editing

if TYPE_CHECKING:
    from typing import Iterable, Sequence

    from computer_use.interaction.read.presentation.records import FormatField, NodeView, SnapshotFacts
    from computer_use.protocol.records import Record


def display_actions(node: NodeView, actions: Iterable[Record]) -> list[Record]:
    # Supporting actions remain executable through details; the main view
    # emphasizes the primary actions.
    return [action for action in actions if action.get("kind", action["name"]) not in SUPPORTING_ACTIONS]


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

    The exact URL, including its query and fragment, remains in details from
    this same capture.

    Args:
        format_field: Pure formatter for one text field.
        limit: Existing upper bound on returned items for this local page/format operation.
    """
    if value.lower().startswith(("data:", "webdoc:")):
        scheme, _, rest = value.partition(":")
        kind = rest.split(",", 1)[0] if scheme.lower() == "data" else ""
        return format_field(scheme + ":" + kind[:60]) + " [embedded; " + hint + "]"
    if len(value) <= limit:
        return format_field(value)
    return format_field(value[:limit]) + "… [URL shortened; " + hint + "]"


_ENCODED = re.compile(
    r"\A(?P<header>(?:data:[\w!#$&^.+-]+/[\w!#$&^.+-]+(?:;[^;,\s]{1,128})*|/[\w!#$&^.+-]+);base64),"
    r"(?P<data>[A-Za-z0-9+/]{128,}={0,2})\Z",
    re.IGNORECASE,
)


def display_embedded(value: str, format_field: FormatField, *, hint: str = "see details") -> str | None:
    """Preview an explicitly encoded carrier, never guess from long text.

    Chromium may expose an image's data URL as its description with the `data:image` prefix
    removed. Retain the exact field in accounting/details; this function makes no claim that the
    payload was emitted or decoded.

    Args:
        format_field: Pure formatter for one text field.
    """
    match = _ENCODED.fullmatch(value)
    if match is None:
        return None
    return format_field(match["header"]) + f" [embedded data, {len(match['data'])} encoded chars; {hint}]"


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


def observation_warnings(snapshot: SnapshotFacts, *, hint: str = "see details") -> list[str]:
    """Keep actual collection failures in the main view, not provider defaults.

    Args:
        snapshot: Facts captured for this observation; display edits do not mutate native
            evidence.
    """
    aliases = {token: ref for ref, token in snapshot.refs.items()}
    collected = {node["node"] for node in snapshot.facts["nodes"] if node.get("observed", True)}
    warnings, groups = [], {}
    continued = child_continuations(snapshot.roots)
    covered_properties = set()

    def label(value: str) -> str:
        return value if re.fullmatch(r"[\w.:-]+", value) else jsonString(value)

    def detail(value: Mapping[str, Any]) -> str:
        return ": " + jsonString(value["detail"]) if value.get("detail") else ""

    for node in snapshot.facts["nodes"]:
        for key, value in node["fieldStatus"].items():
            if value["state"] in ("error", "timeout", "partial", "stale", "conflict", "unavailable", "redacted"):
                groups.setdefault(label(key) + " (" + value["state"] + ")" + detail(value), []).append(
                    aliases[node["node"]]
                )
    for relation in snapshot.facts["relations"]:
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
            covered_properties.add(relation["property"])
        if (
            relation["state"] in ("error", "timeout", "truncated")
            or relation["certainty"] == "conflicting"
            or relation["state"] not in ("absent", "unsupported")
            and (relation.get("detail") or relation["certainty"] == "unknown")
        ) and not covered:
            groups.setdefault(
                label(relation["property"])
                + " ("
                + relation["state"]
                + ", "
                + relation["certainty"]
                + ")"
                + detail(relation),
                [],
            ).append(aliases[relation["source"]])
        # Missing labels/selected members/content remain discoverable even
        # when the relation query itself completed successfully. Parent/window
        # endpoints outside this observation are scope metadata, not content.
        missing = [aliases[token] for token in relation["targets"] if token not in collected]
        if missing and relation["kind"] not in ("children", "parent", "window"):
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
    for (members, suffix), properties in shared.items():
        message = "; ".join(properties) + suffix if properties[0] is not None else suffix
        warnings.append(members + ": " + message)
    # Shared/cyclic containment and disconnected roots are actionable gaps.
    # Uncollected ancestor/window endpoints alone do not mean missing content;
    # incomplete child branches already carry a local coverage annotation.
    warnings.extend(
        line
        for line in snapshot.annotations
        if (" contains " in line or " has no expanded containment path " in line or line.startswith("Collection:"))
        and line not in {"Collection: " + jsonString("partial native relation: " + prop) for prop in covered_properties}
    )
    return ["Observation notes (" + hint + "):\n" + "\n".join("\t" + line for line in warnings)] if warnings else []


if TYPE_CHECKING:
    from typing import Any, Mapping
