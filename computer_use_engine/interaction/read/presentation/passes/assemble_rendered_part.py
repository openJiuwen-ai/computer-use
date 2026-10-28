# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Assemble rendered part."

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.facts.table import selection_summaries
from computer_use.interaction.read.presentation.accounting.fields import RenderedResult
from computer_use.interaction.read.presentation.accounting.identifiers import collect_identifiers
from computer_use.interaction.read.presentation.accounting.native_details import (
    collect_deferred_fields,
    collect_native_details,
)
from computer_use.interaction.read.presentation.accounting.relations import render_relations
from computer_use.interaction.read.presentation.actions.native_capabilities import (
    collect_native_capabilities,
    format_native_capabilities,
)
from computer_use.interaction.read.presentation.actions.supporting import (
    collect_supporting_actions,
    format_supporting_actions,
)
from computer_use.interaction.read.presentation.compact import observation_warnings
from computer_use.interaction.read.presentation.containment import PublishedTableColumn
from computer_use.interaction.read.presentation.details import captured_read_issues
from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.model import PURE, SnapshotParts
from computer_use.interaction.read.presentation.rendering.formatting import field
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedLiteral
from computer_use.interaction.read.presentation.selection_summary import selection_summary_plan
from computer_use.interaction.read.presentation.summaries import read_summaries
from computer_use.pipeline import Contract, Pass

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
    column_coverage: list[PublishedTableColumn] = []
    selections = selection_summary_plan(snapshot.facts, snapshot.refs, roots=snapshot.roots)
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
    parts.extend(read_summaries(snapshot.facts, snapshot.refs, roots=snapshot.roots, selection_plan=selections))
    if snapshot.annotations:
        parts.append("Native facts:\n" + "\n".join("\t" + line for line in snapshot.annotations))
    document = RenderedDocument.join(parts, separator="\n\n")
    native_issues, issue_records = captured_read_issues(snapshot)
    diagnostic_parts: list[str] = []
    content_directories = [
        part.rendered["capturedContentDirectory"]
        for part in rendered_parts
        if part.rendered.get("capturedContentDirectory")
    ]
    content_payloads = [
        part.rendered["capturedContentPayload"]
        for part in rendered_parts
        if part.rendered.get("capturedContentPayload")
    ]
    for part in rendered_parts:
        disclosed = part.rendered.get("disclosedTextFields", ())
        if disclosed:
            diagnostic_parts.append(
                "Captured full text (excerpted in the interface):\n"
                + "\n".join(
                    "\t" + item["ref"] + " " + item["field"] + ": " + literal(item["value"]) for item in disclosed
                )
            )
            part.rendered.setdefault("factAccounting", {})["capturedTextExcerpts"] = [
                dict(
                    item,
                    native=snapshot.refs[item["ref"]],
                    visibility="details",
                    representation="Captured full text",
                    target=item["ref"],
                )
                for item in disclosed
            ]
    if request.compact:
        concise = request.public_api and request.share_descriptions
        actions, coverage = collect_supporting_actions(snapshot, rendered_parts)
        if actions:
            diagnostic_parts.append("\n".join(format_supporting_actions(actions, field)))
        for part, records in zip(rendered_parts, coverage):
            for entry in records:
                entry.update(visibility="details")
            part.rendered.setdefault("factAccounting", {})["compactSupportingActions"] = records
        capabilities, capability_coverage = collect_native_capabilities(snapshot, rendered_parts)
        if capabilities:
            diagnostic_parts.append("\n".join(format_native_capabilities(capabilities, field)))
        for part, records in zip(rendered_parts, capability_coverage):
            for entry in records:
                entry.update(visibility="details")
            part.rendered.setdefault("factAccounting", {})["compactNativeCapabilities"] = records
        primary.extend(selection_summaries(snapshot.facts, snapshot.refs))
        primary.extend(
            read_summaries(
                snapshot.facts,
                snapshot.refs,
                compact=True,
                roots=snapshot.roots,
                selection_plan=selections,
                defer_inactive_selections=concise,
                omit_unspecified_document_text=concise,
                concise=concise,
            )
        )
        declarations = selections.declarations(concise=concise, defer_inactive=concise)
        for part in rendered_parts:
            destinations = []
            for claim in part.rendered.get("fieldCoverage", ()):
                if claim["kind"] != "text-selection-summary":
                    continue
                entry = selections.entries[claim["ref"]]
                deferred = concise and entry.inactive
                # Export detached JSON containers; the source plan remains immutable.
                destinations.append(
                    dict(
                        ref=entry.ref,
                        source=entry.source,
                        property="text.selections",
                        path=("text", "selections"),
                        spans=[dict(span) for span in entry.spans],
                        coverage=dict(entry.coverage),
                        interval=entry.interval,
                        unit=entry.unit,
                        state=entry.read_state,
                        covered_fields=("selectionRange",),
                        representation="Text selection details" if deferred else "Text selection summary",
                        statement=entry.detail_statement() if deferred else declarations[entry.ref],
                        target=entry.ref,
                        visibility="details" if deferred else "public",
                    )
                )
            if destinations:
                part.rendered.setdefault("factAccounting", {})["compactTextSelections"] = destinations
        displayed_children = frozenset(
            ref for part in rendered_parts for ref in part.rendered.get("childCoverageRefs", ())
        )
        columns = []
        for part in rendered_parts:
            for item in part.rendered.get("publishedTableColumns", ()):
                column = PublishedTableColumn.from_record(item)
                if (
                    column.scope[:2] == (snapshot.native.session, snapshot.native.id)
                    and snapshot.refs.get(column.ref) == column.source
                ):
                    columns.append(column)
        warning_options = dict(
            displayed_children=displayed_children,
            hint="read the referenced element for native facts" if request.public_api else "see details",
        )
        detailed_notes = observation_warnings(snapshot, **warning_options)
        compact_warning_options = dict(
            warning_options,
            diagnostic_details=False,
            concise=concise,
            defer_menu_label_errors=request.public_api
            and request.share_descriptions
            and all(not part.uncompressed for part in rendered_parts),
        )
        original_notes = observation_warnings(snapshot, **compact_warning_options)
        full_notes = (
            observation_warnings(
                snapshot,
                **warning_options,
                table_columns=columns,
                covered_columns=column_coverage,
                diagnostic_details=False,
                concise=concise,
                defer_menu_label_errors=compact_warning_options["defer_menu_label_errors"],
            )
            if columns
            else original_notes
        )
        if column_coverage:
            # Incremental delivery can omit the table. Its notes therefore retain
            # the original explicit endpoints, while full delivery uses the grid.
            primary.append(RenderedDocument((RenderedLiteral("\n\n".join(full_notes), "\n\n".join(original_notes)),)))
        else:
            primary.extend(original_notes)
        document = RenderedDocument.join(primary, separator="\n\n")
        parts.extend(detailed_notes)
    if native_issues:
        diagnostic_parts.append(native_issues)
    detail_supplement = RenderedDocument.join((*diagnostic_parts, *parts), separator="\n\n").text
    content_directory = "\n\n".join(content_directories)
    content_payload = "\n\n".join(content_payloads)
    content_catalogue = content_directory + "\n\n" + content_payload if content_directory else ""
    details = (content_catalogue + "\n\n" if content_catalogue else "") + detail_supplement
    # The unchanged native-fact groups are now part of the retained detail text.
    # Mark only identifiers whose exact source/ref supplement was actually built.
    for records in collect_identifiers(snapshot, rendered_parts):
        for entry in records:
            entry.update(
                visibility="details",
                target=entry["ref"],
                native=snapshot.refs[entry["ref"]],
                path=("identifier",),
                representation="Additional native facts",
            )
    deferred_urls = frozenset(
        item["ref"] for part in rendered_parts for item in part.rendered.get("deferredNativeUrls", ())
    )
    url_coverage = (
        collect_native_details(snapshot, rendered_parts, "url", references=deferred_urls) if deferred_urls else ()
    )
    for records in url_coverage:
        for entry in records:
            entry.update(
                visibility="details",
                target=entry["ref"],
                native=snapshot.refs[entry["ref"]],
                path=("url",),
                representation="Additional native facts",
            )
    for records in collect_deferred_fields(snapshot, rendered_parts):
        for entry in records:
            entry.update(
                visibility="details",
                target=entry["ref"],
                native=snapshot.refs[entry["ref"]],
                path=tuple(snapshot.fields[entry["ref"], entry["property"]]["path"]),
                representation="Additional native facts",
            )
    result = RenderedResult(
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
    if content_catalogue:
        result.update(
            capturedContentDirectory=content_directory,
            capturedContentPayload=content_payload,
            detailSupplement=detail_supplement,
        )
    if issue_records:
        result["capturedReadIssues"] = [
            dict(
                record,
                visibility="details",
                representation="Native read issues",
                session=snapshot.native.session,
                observation=snapshot.native.id,
            )
            for record in issue_records
        ]
    if column_coverage:
        result["compactTableContainment"] = [
            dict(column.to_record(), representation="table-column", statement=column.statement())
            for column in column_coverage
        ]
    return result


stage = Pass.define(
    name="assemble_rendered_part",
    function=assemble_rendered_snapshot,
    contract=Contract(
        SnapshotParts,
        dict,
        PURE,
        requires=("accounted root parts",),
        writes=(
            "snapshot text and details",
            "compact action, native capability and identifier coverage",
            "published table containment coverage",
            "full and incremental read-note variants",
            "captured native diagnostic destinations",
        ),
    ),
)
