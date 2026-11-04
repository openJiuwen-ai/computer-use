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
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedSection

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree


def _body_after(document: DisplayDocument, position: int) -> DisplayTree | None:
    """Find the first tree within this exact typed window section."""
    for part in document.parts[position + 1 :]:
        if isinstance(part, DisplayTree):
            return part
        if isinstance(part, DisplayText) and part.kind in ("interface_header", "member_heading", "captured_metadata"):
            break
    return None


def _name_delivered(window: MemberHeading, tree: DisplayTree, rendered: RenderedTree) -> bool:
    """Require the original window Name and its actual same-scope public emission."""
    target = window.target
    prepared = tree.prepared
    if (
        not window.in_scope
        or not window.expanded
        or not window.title
        or target is None
        or tree.details
        or not prepared.request.compact
        or not prepared.request.public_api
        or not prepared.request.share_descriptions
        or len(tree.scope) != 5
        or len(prepared.roots) != 1
    ):
        return False
    binding = tuple(target.get(key) for key in ("session", "instance", "root_window", "surface", "node"))
    if (
        tree.scope != binding
        or any(type(value) is not str or not value for value in binding[:4])
        or prepared.snapshot.native.session != binding[0]
    ):
        return False
    root = prepared.roots[0]
    if root.failure is not None or root.projection is None:
        return False
    ref = root.root_ref
    snapshot = prepared.snapshot
    token = snapshot.refs.get(ref)
    native = snapshot.native.nodes.get(token)
    if (
        native is None
        or native.role not in ("window", "dialog", "sheet")
        or snapshot.native.roots != (token,)
        or binding[4] not in (None, token)
        or type(native.name) is not str
        or native.name != window.title
    ):
        return False
    for issue in snapshot.native.issues:
        path = tuple(issue.path)
        if len(path) < 2 or path[0] != "nodes":
            # A missing collection member does not invalidate an acquired Name.
            if path != ("nodes",) or issue.state != "partial":
                return False
        elif path[1] == token and (len(path) == 2 or path[2] in ("role", "name")):
            return False
    projected = next((node for node in prepared.nodes if node.ref == ref and node.parent is None), None)
    source = rendered.get("fields", {}).get((ref, "name"))
    if (
        projected is None
        or projected.fields.get("name") != native.name
        or source is None
        or source.get("node") != token
        or tuple(source.get("path", ())) != ("name",)
        or source.get("representation") != "field"
        or type(source.get("normalized")) is not str
        or source["normalized"] != native.name
    ):
        return False
    claims = [
        claim
        for projection in rendered.get("projections", ())
        for claim in projection.get("fieldCoverage", ())
        if claim.get("ref") == ref
        and claim.get("field") == "name"
        and claim.get("target") == ref
        and claim.get("kind") in ("display", "equivalent")
        and type(claim.get("value")) is str
        and claim["value"] == native.name
    ]
    output = getattr(rendered, "document", None)
    if not claims or not isinstance(output, RenderedDocument):
        return False
    return any(
        node.ref == ref and node.parent is None
        for section in output.parts
        if isinstance(section, RenderedSection)
        for node in section.nodes
    )


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
