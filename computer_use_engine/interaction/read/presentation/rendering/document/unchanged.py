# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Current summaries for a proven unchanged compact tree, without its body."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.facts.table import selection_summaries
from computer_use.interaction.read.presentation.accounting.fields import RenderedResult
from computer_use.interaction.read.presentation.compact import observation_warnings
from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.ir import thaw
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedSection
from computer_use.interaction.read.presentation.selection_summary import selection_summary_plan
from computer_use.interaction.read.presentation.summaries import read_summaries

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.preparation import PreparedRoot, PreparedSnapshot


def _root_document(root: PreparedRoot) -> RenderedDocument:
    """Keep the interface's independent declarations without formatting element text."""
    interface, atoms = root.interface, root.atoms
    prefix = (
        "Fields group visually related controls; each number keeps its original target."
        if interface.format_version != "geometric-2"
        and any(block.kind == "field" and block.origin == "visual" for block in interface.blocks.values())
        else ""
    )
    if atoms.shared_index_basis:
        prefix += ("\n" if prefix else "") + "Index is 0-based."
        if any(slot.key in ("rowRange", "columnRange") for atom in atoms.atoms.values() for slot in atom.slots):
            prefix += " Row/Column are 1-based."
    menus = {}
    for ref in interface.target_refs:
        coverage = atoms.atoms[ref].coverage
        if coverage.unexpanded_menu:
            menus.setdefault(coverage.total, []).append(ref)
    suffix = (
        "Menus unexpanded: "
        + "; ".join(
            reference_set(refs) + (f" (0/{total} children)" if total is not None else " (total unknown)")
            for total, refs in menus.items()
        )
        + "."
        if menus
        else ""
    )
    return RenderedDocument((RenderedSection("", (), prefix=prefix, suffix=suffix),))


def unchanged_snapshot(prepared: PreparedSnapshot) -> RenderedResult | None:
    """Render independent current notes after structural comparison proves no change.

    The shortcut applies only when no per-element continuation or recovery hints
    need layout. Other roots use the ordinary terminal renderer, including all
    partial, table, failed, excerpted and grouped-content layouts. This choice
    affects formatting cost only: observation and operation capabilities remain
    available. Full text and captured details always use the complete renderer.
    """
    options = prepared.request
    if not (options.compact and options.public_api and options.share_descriptions):
        return None
    for root in prepared.roots:
        if (
            root.failure is not None
            or root.interface is None
            or root.atoms is None
            or root.collections is not None and root.collections.tables
            or root.disclosure is not None and root.disclosure.excerpts
            or root.groups is not None and root.groups.groups
        ):
            return None
        if any(
            atom.coverage.omissions
            or not atom.coverage.unexpanded_menu and (atom.coverage.partial or atom.coverage.start is not None)
            for atom in root.atoms.atoms.values()
        ):
            return None
    snapshot = prepared.snapshot
    parts, context = [], None
    for root in prepared.roots:
        document = _root_document(root)
        related = snapshot.root_contexts.get(root.root_ref)
        if related is not None:
            if related != context:
                parts.append(
                    "Related elements ("
                    + "; ".join(
                        (kind if kind.isidentifier() else literal(kind)) + " of " + owner for owner, kind in related
                    )
                    + "):"
                )
            parts[-1] = RenderedDocument.join((parts[-1], document.indented()))
        else:
            parts.append(document)
        context = related
    selections = selection_summary_plan(snapshot.facts, snapshot.refs, roots=snapshot.roots)
    parts.extend(selection_summaries(snapshot.facts, snapshot.refs))
    parts.extend(
        read_summaries(
            snapshot.facts,
            snapshot.refs,
            compact=True,
            roots=snapshot.roots,
            selection_plan=selections,
            defer_inactive_selections=True,
            omit_unspecified_document_text=True,
            concise=True,
        )
    )
    parts.extend(
        observation_warnings(
            snapshot,
            displayed_children=frozenset(),
            diagnostic_details=False,
            hint="read the referenced element for native facts",
            concise=True,
            defer_menu_label_errors=True,
        )
    )
    document = RenderedDocument.join(parts, separator="\n\n")
    return RenderedResult({"text": "", "refs": dict(snapshot.refs), "fields": thaw(snapshot.fields)}, document=document)
