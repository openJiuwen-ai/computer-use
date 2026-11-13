# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Elide a repeated heading title only after its original Name was delivered."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument, DisplayTree
from computer_use.interaction.read.presentation.headers import (
    InterfaceHeader,
    MemberHeading,
    interface_header,
    member_heading,
)

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree


def _body_after(document: DisplayDocument, position: int) -> DisplayTree | None:
    """Find the first tree within this exact typed window section."""
    for part in document.parts[position + 1:]:
        if isinstance(part, DisplayTree):
            return part
        if isinstance(part, DisplayText) and part.kind in ("interface_header", "member_heading", "captured_metadata"):
            break
    return None


def _name_delivered(window: MemberHeading, tree: DisplayTree, rendered: RenderedTree) -> bool:
    """Require the original window Name and its actual same-scope public emission."""
    target = window.target
    prepared = tree.prepared
    if not window.in_scope or not window.expanded or not window.title:
        return False
    if target is None or tree.details or not prepared.request.compact:
        return False
    if not prepared.request.public_api or not prepared.request.share_descriptions:
        return False
    if len(tree.scope) != 5:
        return False
    binding = tuple(target.get(key) for key in ("session", "instance", "root_window", "surface", "node"))
    if tree.scope != binding:
        return False
    for identity in binding[:4]:
        identity_type = type(identity)
        if identity_type is not str or not identity:
            return False
    if prepared.snapshot.session != binding[0]:
        return False
    proof = rendered.handle.title_evidence()
    if proof is None:
        return False
    _, token, name = proof
    return binding[4] in (None, token) and name == window.title


def full_header_text(document: DisplayDocument, position: int, snapshots: Mapping[int, RenderedTree]) -> str | None:
    """Return a shorter full heading when its following root already names the window.

    This is a terminal formatting choice. Captured headings, detail documents and
    incremental headings retain their complete original titles.

    Args:
        document: Frozen public document whose typed sections establish window scope.
        position: Index of the current interface or member heading.
        snapshots: Already-rendered snapshots with their actual field coverage.

    Returns:
        A full-view heading with its repeated title omitted, or None when the
        original heading must be used.
    """
    part = document.parts[position]
    if not isinstance(part, DisplayText) or part.kind not in ("interface_header", "member_heading"):
        return None
    header = next(
        (
            item.values[0]
            for item in reversed(document.parts[: position + 1])
            if isinstance(item, DisplayText) and item.kind in ("interface_header", "captured_metadata")
        ),
        None,
    )
    if not isinstance(header, InterfaceHeader):
        return None
    if part.kind == "interface_header":
        if len(header.scoped) != 1:
            return None
        window = header.scoped[0]
    else:
        window = part.values[0]
        if not isinstance(window, MemberHeading) or not any(
            candidate.ref == window.ref
            and candidate.native_window == window.native_window
            and candidate.title == window.title
            and candidate.target == window.target
            for candidate in header.windows
        ):
            return None
    tree = _body_after(document, position)
    rendered = snapshots.get(id(tree.prepared)) if tree is not None else None
    if tree is None or rendered is None or not _name_delivered(window, tree, rendered):
        return None
    return (
        interface_header(header, name_in_body=True)
        if part.kind == "interface_header"
        else member_heading(window, name_in_body=True)
    )
