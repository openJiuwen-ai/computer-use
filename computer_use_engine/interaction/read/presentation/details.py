# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Captured-detail documents and exact native read-issue records."""

from __future__ import annotations

from dataclasses import replace
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from computer_use.json import dumps
from computer_use.interaction.read.presentation.content import DisplayText, text
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.protocol.observation.codec import encode_read_issue

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import SnapshotFacts
    from computer_use.protocol.observation import NativeObservation


DETAILS_HINT = "Details: state.read_details(element=N); omit element for all."


def _record_json(value: object) -> str:
    """Keep diagnostic JSON on one physical line through document indentation."""
    return dumps(value).replace("\x85", "\\u0085").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def captured_details_document(document: DisplayDocument) -> DisplayDocument:
    """Select complete captured supplements while retaining window and source boundaries.

    Typed detail hints disappear from this view. Application strings and genuine
    continuation commands remain unchanged; creating the document performs no rendering.
    """

    def retained(value: object) -> object:
        if isinstance(value, DisplayText):
            if value.kind == "details_hint":
                return text("")
            if value.kind in ("interface_header", "image_header"):
                return replace(value, kind="captured_metadata")
            return replace(value, values=tuple(retained(item) for item in value.values))
        if isinstance(value, tuple):
            return tuple(retained(item) for item in value)
        return value

    return replace(
        document,
        parts=tuple(
            replace(part, details=True) if isinstance(part, DisplayTree) else retained(part) for part in document.parts
        ),
    )


def captured_read_issues(snapshot: SnapshotFacts) -> tuple[str, list[dict[str, object]]]:
    """Preserve every ordered issue with its raw path and exact source-reference alias.

    Values use the observation codec's semantic tags, including conflict alternatives
    and captured continuation records. Repeated issues retain separate ordinals.
    """
    return format_read_issues(snapshot.native, {token: ref for ref, token in snapshot.refs.items()})


def format_read_issues(
    native: NativeObservation,
    aliases: Mapping[str, str],
    *,
    ordinals: Iterable[int] | None = None,
) -> tuple[str, list[dict[str, object]]]:
    """Format selected native issues with their original capture-wide ordinals.

    Args:
        native: Immutable observation that owns the issue sequence.
        aliases: Original native-token to display-number mapping.
        ordinals: Exact issue positions to include, or None for the complete sequence.

    Returns:
        The diagnostic text and detached records, retaining every encoded issue field.
    """
    records: list[dict[str, object]] = []
    for index in range(len(native.issues)) if ordinals is None else ordinals:
        issue = native.issues[index]
        ref = aliases.get(issue.path[1]) if len(issue.path) >= 2 and issue.path[0] == "nodes" else None
        records.append(dict(index=index, ref=ref, issue=encode_read_issue(issue)))
    if not records:
        return "", []
    identity = dict(session=native.session, observation=native.id)
    return (
        "Native read issues "
        + _record_json(identity)
        + ":\n"
        + "\n".join("\t" + _record_json(record) for record in records)
    ), records
