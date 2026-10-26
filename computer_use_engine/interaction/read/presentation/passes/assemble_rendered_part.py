from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.facts.table import selection_summaries
from computer_use.interaction.read.presentation.accounting.fields import RenderedResult
from computer_use.interaction.read.presentation.accounting.relations import render_relations
from computer_use.interaction.read.presentation.compact import observation_warnings
from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.model import PURE, SnapshotParts
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument
from computer_use.interaction.read.presentation.summaries import read_summaries
from computer_use.pipeline import Contract, Pass

"Assemble rendered part."


if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.records import RenderedTree
    from computer_use.pipeline.core import Control


def assemble_rendered_snapshot(value: SnapshotParts, control: Control) -> RenderedTree:
    """Combine ordered root presentations into one snapshot rendering.

    Add root-relationship context, native fact/relation supplements, and selection/read
    summaries. Return compact or full text with complete details, source projections, and
    continuation metadata for the same captured snapshot.
    """
    request, rendered_parts = value.request, value.parts
    snapshot = request.snapshot
    parts: list[str | RenderedDocument] = []
    projections, context = [], None
    supplements, relation_supplements, relation_sources = {}, {}, []
    for root, part in zip(snapshot.roots, rendered_parts):
        rendered = part.rendered
        for label, items in part.supplements.items():
            supplements.setdefault(label, []).extend(items)
        for relation, items in part.relation_supplements.items():
            relation_supplements.setdefault(relation, []).extend(items)
        relation_sources.extend(part.relation_sources)
        document = getattr(rendered, "document", None) or RenderedDocument((rendered["text"],))
        related = snapshot.root_contexts.get(root["ref"])
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
        projections.append(rendered)
    primary = parts.copy()
    if supplements:
        parts.append(
            "Additional native facts (grouped by element reference):\n"
            + "\n".join("\t" + reference_set(items) + " " + label for label, items in supplements.items())
        )
    if relation_supplements:
        parts.append(
            (
                'Native relations (source -> ordered targets; total="target-count" means the '
                "length of that target "
                "array):\n"
            )
            + "\n".join("\t" + line for line in render_relations(relation_supplements))
        )
    parts.extend(selection_summaries(snapshot.facts, snapshot.refs))
    parts.extend(read_summaries(snapshot.facts, snapshot.refs))
    if snapshot.annotations:
        parts.append("Native facts:\n" + "\n".join("\t" + line for line in snapshot.annotations))
    document = RenderedDocument.join(parts, separator="\n\n")
    details = document.text
    if request.compact:
        primary.extend(selection_summaries(snapshot.facts, snapshot.refs))
        primary.extend(read_summaries(snapshot.facts, snapshot.refs, compact=True))
        primary.extend(
            observation_warnings(
                snapshot, hint="read the referenced element for native facts" if request.public_api else "see details"
            )
        )
        document = RenderedDocument.join(primary, separator="\n\n")
    return RenderedResult(
        dict(
            text=document.text,
            details=details,
            refs=dict(snapshot.refs),
            projections=projections,
            fields=dict(snapshot.fields),
            textRefs=dict(snapshot.text_refs),
            relationSources=relation_sources,
            inlineChildRefs=[ref for part in projections for ref in part.get("inlineChildRefs", ())],
        ),
        document=document,
    )


stage = Pass.define(
    name="assemble_rendered_part",
    function=assemble_rendered_snapshot,
    contract=Contract(
        SnapshotParts,
        dict,
        PURE,
        requires=("accounted root parts",),
        writes=("snapshot text and details",),
    ),
)
