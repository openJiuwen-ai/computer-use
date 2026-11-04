# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"Full text rendering, with separate source-aware projection and line layout."

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

import regex
from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.accounting.fields import RenderedFields, RenderedResult, SourceEmission
from computer_use.interaction.read.presentation.actions.layout import action_fields
from computer_use.interaction.read.presentation.compact import (
    display_actions,
    display_embedded,
    display_selection,
    display_url,
    unexpanded_menu,
)
from computer_use.interaction.read.presentation.compression.descriptions import (
    description_declaration,
    shared_description,
)
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.compression.policy import secondary_actions
from computer_use.interaction.read.presentation.compression.projection import projectTree
from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.formatting import json_string, number_string
from computer_use.interaction.read.presentation.interface.atoms.semantics import (
    CONVENTIONAL,
    STANDARD_CLICK,
    select_role,
    select_states,
)
from computer_use.interaction.read.presentation.ir import FactKey
from computer_use.interaction.read.presentation.rendering.layout.child_coverage import capture_child_hint
from computer_use.interaction.read.presentation.rendering.layout.empty_values import (
    compact_empty_values,
)
from computer_use.interaction.read.presentation.rendering.layout.model import (
    ChildCoverageHint,
    LayoutFrame,
    UnexpandedMenuSummary,
)
from computer_use.interaction.read.presentation.rendering.layout.pipeline import LayoutPipeline
from computer_use.interaction.read.presentation.rendering.multiline_values import compact_multiline_value
from computer_use.interaction.read.presentation.rendering.native_urls import (
    EmittedCaption,
    NativeUrlPlan,
    emitted_caption,
    select_deferred_native_urls,
)
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedNode, RenderedSection
from computer_use.interaction.read.presentation.tree import (
    carries_value,
    has_display_value,
    has_text_selection,
    is_active_modal,
    is_dialog,
    is_window,
    selected_text_descendants,
    unique,
    value_label,
    VALUE_PREVIEW_LIMIT,
)
from computer_use.protocol.primitives import trim, utf16_length

# Compatibility for historical callers.
__all__ = [
    "CONVENTIONAL",
    "STANDARD_CLICK",
    "bounded",
    "compact_empty_values",
    "field",
    "partial",
    "preview",
    "quoted",
    "render_projection",
    "render_projection_lines",
    "render_tree",
    "rendered_actions",
    "role",
    "shortened",
]

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any, Iterable, Sequence

    from computer_use.interaction.read.presentation.actions.control_raises import ControlRaise
    from computer_use.interaction.read.presentation.actions.menu_mechanisms import MenuMechanism
    from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import NodeView, RenderedTree, SemanticNode
    from computer_use.interaction.read.presentation.rendering.native_descriptions import (
        NativeDescriptionField,
        NativeRoleAlias,
    )
    from computer_use.interaction.read.presentation.rendering.native_urls import NativeUrlSource
    from computer_use.interaction.read.presentation.selection_summary import SelectionSummaryPlan
    from computer_use.protocol.records import Record

# Compatibility for historical callers.


@dataclass(frozen=True)
class _NativeUrlSlot:
    """A generated field slot used to reassemble one node after peer comparison."""

    line: int
    base: str
    fields: tuple[str, ...]
    slot: int
    separator: str
    state_at: int
    state_flags: str


def shortened(value: str, limit: float) -> Record:
    """Build a head/tail text preview at grapheme boundaries.

    Args:
        value: Original string to preview.
        limit: Preview threshold; short strings are measured in UTF-16 units before
            the grapheme-based head/tail split.

    Returns:
        Head text, tail text, and the count omitted between them.
    """
    if utf16_length(value) <= limit:
        return dict(head=value, tail="", omitted=0)
    head_limit, tail_limit = math.floor(limit / 4), limit - math.floor(limit / 4)
    head_value, tail_value, count_value, tail_index = [], [], 0, 0
    if re.search("[\\ud800-\\udfff]", value):
        value = value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")
    for match in regex.finditer("\\X", value):
        segment = match[0]
        if count_value < head_limit:
            head_value.append(segment)
        else:
            if tail_index < len(tail_value):
                tail_value[tail_index] = segment
            else:
                tail_value.append(segment)
            tail_index = (tail_index + 1) % tail_limit
        count_value += 1
    if count_value <= limit:
        return dict(head=value, tail="", omitted=0)
    return dict(
        head="".join(head_value),
        tail="".join(tail_value[tail_index:] + tail_value[:tail_index]),
        omitted=count_value - limit,
    )


def quoted(text: str) -> str:
    if text is None:
        return "null"
    return re.sub("[\\x7f-\\x9f\\u2028\\u2029]", lambda m: f"\\u{ord(m[0]):04x}", json_string(text))


def field(text: str) -> str:
    return (
        quoted(text)
        if (
            not text
            or text != trim(text)
            or re.search('[,">\\\\\\x00-\\x1f\\x7f-\\x9f\\u2028\\u2029]', text)
            or re.match("[<+~|]", text)
            or re.search("\\(partial:|\\(showing ", text)
        )
        else text
    )


def short_unexpanded_menu_summary(menus: Mapping[int, Sequence[str]]) -> str | None:
    """Name the exact unopened menu sources; their direct counts stay in details."""
    return "Menus unexpanded: " + reference_set(ref for refs in menus.values() for ref in refs) + "."


def bounded(text: str, limit: int) -> str:
    part = shortened(text, limit)
    return f"{part['head']}\n[… {part['omitted']} characters omitted …]\n{part['tail']}" if part["omitted"] else text


def preview(text: str, name: str, limit: float) -> str:
    part = shortened(text, limit)
    return (
        (f"{name} preview: {field(part['head'])} … [{part['omitted']} characters omitted] … {field(part['tail'])}")
        if part["omitted"]
        else f"{name}: {field(text)}"
    )


def role(node: NodeView) -> str:
    """Format the observed role description with dialog, popover, row, and container labels."""
    selected = select_role(node)
    return field(selected.label) if selected.source_description else selected.label


def partial(node: NodeView) -> None | str:
    "Describe incomplete child collection using its observed counts and stop reason."
    read = node.get("childrenRead", len(node.get("native", node)["children"]))
    reason = node.get("partialReason")
    if reason is None and node["role"] != "application" and "childCount" in node and read < node["childCount"]:
        reason = "children not fully read"
    if reason is None:
        return None
    if "childrenRead" in node and "childCount" in node:
        return (
            f"showing "
            f"{number_string(node['childrenRead'])}"
            f" of "
            f"{number_string(node['childCount'])}"
            f" items; partial: "
            f"{field(reason)}"
        )
    if "childCount" in node:
        return f"showing {number_string(read)} of {number_string(node['childCount'])} items; partial: {field(reason)}"
    return f"partial: {field(reason)}; total unknown"


def rendered_actions(
    node: NodeView,
    compact: bool = False,
    *,
    menu_mechanisms: tuple[MenuMechanism, ...] = (),
    control_raises: tuple[ControlRaise, ...] = (),
) -> list[Any]:
    """Format secondary-action labels and include native names when labels are ambiguous."""
    result = []
    actions = secondary_actions(node)
    for action in (
        display_actions(node, actions, menu_mechanisms=menu_mechanisms, control_raises=control_raises)
        if compact
        else actions
    ):
        label = action.get("description") or action["name"]
        ambiguous = any(
            other["name"] != action["name"] and (other["name"] == label or other.get("description") == label)
            for other in node.get("actions", [])
        )
        result.append(f"{field(label)} [name: {quoted(action['name'])}]" if ambiguous else field(label))
    return unique(result)


def render_tree(root: SemanticNode, context: Record | None = None) -> RenderedTree:
    return render_projection(root, context, projectTree(root, context))


def render_projection(root: SemanticNode, context: Record | None, projection: ProjectionResult) -> RenderedTree:
    draft = LayoutPipeline().run(render_projection_lines(root, context, projection))
    return draft.finalize(draft.lines, list(draft.records))


def render_projection_lines(
    root: SemanticNode,
    context: Record,
    projection: ProjectionResult,
    *,
    selection_summaries: SelectionSummaryPlan | None = None,
    defer_identifiers: bool = False,
    native_url_sources: Mapping[str, NativeUrlSource] | None = None,
    native_description_fields: Mapping[FactKey, NativeDescriptionField] | None = None,
    native_role_aliases: Mapping[str, NativeRoleAlias] | None = None,
    menu_mechanisms: tuple[MenuMechanism, ...] = (),
    control_raises: tuple[ControlRaise, ...] = (),
    compact_multiline_values: bool = False,
    concise_menu_summary: bool = False,
    disclosure_plan: DisclosurePlan | None = None,
) -> LayoutFrame:
    """Render an already-compressed forest without recapturing or recompressing.

    Args:
        root: Root of the requested semantic view; native bindings remain separate.
        projection: Source-preserving presentation result and its provenance ledger.
        selection_summaries: Exact statements the enclosing snapshot assembly will publish.
            Without this delivery promise, node selection ranges remain inline.
        defer_identifiers: The enclosing snapshot assembly retains independent identifier
            fields in captured details. Caption and summary fallbacks remain unchanged.
        native_url_sources: Same-capture URL evidence supplied only when snapshot assembly
            retains exact native-field details. Standalone rendering keeps URL fields.
        native_description_fields: Complete original fields whose diagnostic-only output
            is retained by enclosing snapshot details. Standalone formatting keeps it inline.
        native_role_aliases: Exact generic role spellings backed by complete original
            descriptions and their same-capture native-detail destinations.
        menu_mechanisms: Exact standard menu action occurrences retained by the enclosing
            snapshot's supporting-action details. Standalone formatting keeps these labels.
        compact_multiline_values: Whether complete, unpreviewed whole-view values may use
            a shorter JSON scalar instead of their paragraph block.
        control_raises: Exact standard atomic-control Raise occurrences retained by the
            enclosing snapshot's action details. Window and viewport Raise stays inline.
        concise_menu_summary: Keep unopened menu references in whole-view output while
            retaining their direct native child counts in the captured details.
        disclosure_plan: Exact passive-field excerpts selected before final text formatting.
    """
    context = {} if context is None else context
    compact = bool(context.get("compact"))
    menu_mechanisms = menu_mechanisms if compact and not context.get("subtreeRef") else ()
    control_raises = control_raises if compact and not context.get("subtreeRef") else ()
    detail_hint = ""  # One captured-details entry is emitted by the document footer.
    coverage = RenderedFields(projection) if context.get("recordFields") else None

    def record(node: NodeView, *fields: str, kind: str = "display", target: str | None = None) -> None:
        if coverage is not None:
            for field_name in fields:
                coverage.record(node, field_name, kind=kind, target=target)

    deferred_native_fields: dict[FactKey, Record] = {}
    disclosed_fields: dict[tuple[str, str], Record] = {}

    def defer_fields(facts: Iterable[FactKey]) -> bool:
        selected = tuple(facts)
        if (
            not compact
            or context.get("subtreeRef")
            or not native_description_fields
            or not selected
            or any(fact not in native_description_fields for fact in selected)
        ):
            return False
        for fact in selected:
            deferred_native_fields[fact] = native_description_fields[fact].destination()
        return True

    focused_ref = context.get("focusedRef")
    roots = projection["roots"]
    index_values: set[int] = set()
    has_table_coordinates = False
    if compact and not context.get("subtreeRef"):
        pending: list[NodeView] = list(roots)
        while pending:
            node = pending.pop()
            if "index" in node:
                index_values.add(node["index"])
            has_table_coordinates |= any(node.get(key, {}).get("length", 0) > 0 for key in ("rowRange", "columnRange"))
            pending.extend(node["children"])
    index_basis = "Index is 0-based."
    if has_table_coordinates:
        index_basis += " Row/Column are 1-based."
    # Distinct values remain distinct even when identical leaves share a line.
    # Keep the convention with this section so full and diff remain self-contained.
    index_count = len(index_values)
    share_index_basis = index_count >= 3 and index_count * len(" (0-based)") > len(index_basis) + 1
    part_actions = {}
    if context.get("nativeFacts"):
        # Folding a caption changes presentation, never its action target.
        # Keep that source next to the action instead of requiring the model
        # to reconstruct the native graph. Build once for the entire tree.
        for ref, owner in projection["foldedInto"].items():
            source = projection["nativeNodes"][ref]
            part_actions.setdefault(owner, []).append(source)
    lines_value, refs_value, labels_value, focus_labels, summary_labels, reported_focus = [], [], {}, {}, {}, []
    selected_value, selected_menus, context_refs, selected_descendants = [], [], {}, []
    collapsed_menus = {}
    inline_children = set()
    display_nodes: list[RenderedNode] = []
    child_hints: list[ChildCoverageHint] = []
    node_line_ranges: dict[str, tuple[int, int]] = {}
    node_prefixes: dict[str, str] = {}
    node_action_lines: dict[str, tuple[str, str]] = {}
    captions: dict[str, list[EmittedCaption]] = {}
    visible_nodes: dict[str, NodeView] = {}
    visible_parents: dict[str, str | None] = {}
    # Keep producer-owned field slots; omission never searches or edits application text.
    url_slots: dict[str, _NativeUrlSlot] = {}

    def visit(
        node: NodeView,
        depth: int,
        parent_blocked: Sequence[str] | None = None,
        shared_help: str | None = None,
        in_menu: bool = False,
        parent_ref: str | None = None,
        layout: tuple[tuple[str, str], ...] = (),
    ) -> None:
        """Visit.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
            depth: Current relation/traversal depth within the existing bound.
        """
        nonlocal selected_descendants
        visible_nodes[node["ref"]], visible_parents[node["ref"]] = node, parent_ref
        caption_emission_start = len(coverage.source_emissions) if coverage is not None else 0
        parent_blocked = [] if parent_blocked is None else parent_blocked
        in_menu = in_menu or node["role"] in ("menu", "menuBar", "menuItem", "menuBarItem")
        indent, states, omissions = "\t" * (depth + len(layout)), [], []
        role_label = role(node)
        alias = native_role_aliases.get(node["ref"]) if native_role_aliases else None
        aliased = (
            alias is not None
            and not is_dialog(node)
            and node.get("subrole") != "popover"
            and node["role"] == alias.role
            and node.get("subrole") == alias.subrole
            and node.get("roleDescription") == alias.description.value
            and defer_fields((alias.fact,))
        )
        if aliased and alias is not None:
            role_label = alias.label
        line = f"{node['ref']} {role_label}"
        show_value = (
            carries_value(node["native"])
            or (node["ref"] == focused_ref and "value" in node)
            or (
                has_display_value(node["native"])
                and node.get("value") != node.get("name")
                and node.get("value") != node.get("description")
            )
        )
        binary = node["role"] in ("checkBox", "switch", "radioButton")
        value = node.get("value")
        if binary and (node.get("valueIsNumber") or value in ("true", "false")):
            value = {"0": "off", "1": "on", "true": "on", "false": "off"}.get(value, value)
        if aliased and alias is not None:
            record(node, "role")
            if alias.subrole is not None:
                record(node, "subrole")
            if coverage is not None:
                # Role fields are immutable source facts, outside text-caption
                # provenance. Record their actual alias destinations explicitly.
                coverage.source_emissions.extend(
                    SourceEmission(FactKey(node["ref"], attribute), node[attribute], node["ref"], attribute, "display")
                    for attribute in (("role", "subrole") if alias.subrole is not None else ("role",))
                )
        elif is_dialog(node) and node["role"] != "sheet" or node.get("subrole") == "popover":
            record(node, "subrole")
        elif node.get("roleDescription"):
            record(node, "roleDescription")
        else:
            record(node, "role")
        caps = node.get("capabilities")
        deferred_click = (
            node.get("enabled") is False
            and caps is not None
            and caps.get("click") == "unknown"
            and not node.get("blockedBy")
        )
        selected_states = select_states(node, compact=compact, focused_ref=focused_ref)
        states = [label for label, _ in selected_states.labels]
        # These are the original formatter's recorded state fields. Capability
        # decisions remain structured evidence without changing legacy accounting.
        recorded_state_fields = {
            "enabled",
            "selected",
            "toggleState",
            "busy",
            "headingLevel",
            "landmarkType",
            "expanded",
            "disclosing",
            "focused",
            "modal",
            "visible",
            "showing",
        }
        for _, attributes in selected_states.labels:
            for attribute in attributes:
                if attribute in recorded_state_fields:
                    record(node, attribute)
        for attribute in selected_states.equivalent_fields:
            record(node, attribute, kind="equivalent")
        if (
            node["ref"] == focused_ref
            and not has_text_selection(node)
            and node["role"] not in ("application", "window")
        ):
            selected_descendants = selected_text_descendants(node)
        state_flags = " (" + ", ".join(states) + ")" if states else ""
        node_prefixes[node["ref"]] = role_label + state_flags
        line += state_flags

        def display(value: str, name: str, limit: float = math.inf, attribute: str | None = None) -> str:
            """Display.

            Args:
                limit: Existing upper bound on returned items for this local page/format
                    operation.
                attribute: Native/display field name recorded by the provenance ledger.
            """
            excerpt = disclosure_plan.get(node["ref"], attribute) if disclosure_plan is not None and attribute else None
            if excerpt is not None and excerpt.source.value == value:
                record(node, attribute, kind="continuation")
                disclosed_fields[(node["ref"], attribute)] = {
                    "ref": node["ref"],
                    "field": excerpt.source.native_field,
                    "attribute": attribute,
                    "value": excerpt.source.value,
                    "ranges": [(fragment.start, fragment.end) for fragment in excerpt.fragments],
                }
                parts = " … ".join(field(fragment.text) for fragment in excerpt.fragments)
                return parts + f" [excerpt: {excerpt.shown}/{excerpt.total} chars; read_details(element={node['ref']})]"
            if compact and (encoded := display_embedded(value, field, hint=detail_hint)) is not None:
                # No full-value claim: accounting keeps the exact field under
                # its source reference in details, including folded captions.
                return encoded
            part = shortened(value, limit)
            if part["omitted"]:
                omissions.append(f"Omitted: {part['omitted']} characters ({name})")
            elif attribute is not None:
                record(node, attribute)
            return field(part["head"] + part["tail"])

        def caption(attribute: str, text: str) -> str:
            if native_url_sources is not None and coverage is not None:
                captions.setdefault(node["ref"], []).append(
                    emitted_caption(
                        projection, node, attribute, text, coverage.source_emissions[caption_emission_start:]
                    )
                )
            return text

        if node.get("name"):
            line += " " + caption("name", display(node["name"], "Name", attribute="name"))
        fields = []
        if node.get("spatialPosition"):
            fields.append("Position: " + node["spatialPosition"])
        if node.get("visualLabelRef"):
            fields.append("Visual label: element " + node["visualLabelRef"])
        # Table coordinates are native index ranges, not spreadsheet addresses.
        # Display positive spans using human row/column numbers; retain empty
        # ranges explicitly rather than inventing a position or merged cell.
        for key, label in (("rowRange", "Row"), ("columnRange", "Column")):
            if key in node:
                span = node[key]
                record(node, key)
                if span["length"] == 0:
                    fields.append(f"{label} range (0-based): {span['location']}:0")
                else:
                    start, end = span["location"] + 1, span["location"] + span["length"]
                    fields.append(
                        label
                        + ("s" if start != end else "")
                        + ": "
                        + str(start)
                        + ("–" + str(end) if start != end else "")
                    )
        for key, label in (
            ("rowCount", "Table rows"),
            ("columnCount", "Table columns"),
            ("selectedChildCount", "Selected children"),
        ):
            if key in node:
                fields.append(label + ": " + str(node[key]))
                record(node, key)
        if "index" in node:
            fields.append(("Index: " if share_index_basis else "Index (0-based): ") + str(node["index"]))
            record(node, "index")
        if deferred_click:
            fields.append("Click: unknown (rechecked on click)")
        if node.get("editorOwnerRef"):
            fields.append("Editor for: " + node["editorOwnerRef"])
        if "disclosureLevel" in node and node["disclosureLevel"] != node.get("outlineDepth"):
            fields.append("Outline level: " + number_string(node["disclosureLevel"]))
            record(node, "disclosureLevel")
        if node.get("disclosedBy"):
            fields.append("Parent row: " + node["disclosedBy"])
        if node.get("incompleteDisclosureRelation"):
            fields.append("Parent row: outside this observation or unavailable")
        if node.get("placeholder"):
            fields.append("Placeholder: " + display(node["placeholder"], "Placeholder", attribute="placeholder"))
        if "expanded" in node and "disclosing" in node and node["expanded"] != node["disclosing"]:
            fields.append(
                "Disclosure state: "
                + ("expanded" if node["disclosing"] else "collapsed")
                + " (native reports disagree)"
            )
            record(node, "disclosing")
        if caps is not None and node.get("enabled") is not False and not node.get("blockedBy"):
            if caps["click"] != "available" and (
                node["role"] in (STANDARD_CLICK if compact else CONVENTIONAL) or node.get("selectable")
            ):
                fields.append("Click: " + caps["click"])
            if (
                node["role"] in ("textField", "textArea", "comboBox")
                and caps["typeText"] != "available"
                and caps["replaceText"] != "available"
            ):
                fields.append("Input: " + caps["typeText"])
        if node["ref"] == focused_ref and node.get("focused") is False:
            fields.append("Focus flag: false")
            record(node, "focused")
        if node.get("description") and node["description"] != node.get("name"):
            fields.append(
                "Description: "
                + caption("description", display(node["description"], "Description", attribute="description"))
            )
        if node.get("fullDescription"):
            fields.append(
                "Full description: " + display(node["fullDescription"], "Full description", attribute="fullDescription")
            )
        for attribute, label in (("valueDescription", "Value description"), ("rangeDescription", "Range description")):
            if (
                attribute in node
                and not shared_description(node, attribute)
                and not defer_fields((FactKey(node["ref"], attribute),))
            ):
                fields.append(label + ": " + display(node[attribute], label, attribute=attribute))
        if node.get("localizedLandmarkType"):
            fields.append(
                "Landmark: " + display(node["localizedLandmarkType"], "Landmark", attribute="localizedLandmarkType")
            )
        value_parts = shortened(node.get("value", ""), VALUE_PREVIEW_LIMIT)
        value_excerpt = disclosure_plan.get(node["ref"], "value") if disclosure_plan is not None else None
        if value_excerpt is not None and value_excerpt.source.value != value:
            value_excerpt = None
        embedded_value = (
            display_embedded(value, field, hint=detail_hint) if compact and isinstance(value, str) else None
        )
        block_value = (
            show_value
            and "value" in node
            and value_excerpt is None
            and embedded_value is None
            and (any(c in node["value"] for c in "\r\n") or value_parts["omitted"] > 0)
        )
        compact_value = None
        if (
            block_value
            and compact_multiline_values
            and compact
            and context.get("publicApi")
            and context.get("shareDescriptions")
            and not context.get("subtreeRef")
            and not value_parts["omitted"]
        ):
            source = projection.provenance.get((node["ref"], "value"))
            evidence = projection.source.context.get("compressionEvidence")
            complete_value = (
                isinstance(evidence, CompressionEvidence)
                and source is not None
                and bool(source.parts)
                and all(
                    part.facts and all((fact.source, fact.attribute) in evidence.complete_text for fact in part.facts)
                    for part in source.parts
                )
            )
            if complete_value:
                natural_text = node["role"] == "staticText" and not node.get("name") and not node.get("description")
                # Later fields have not been assembled yet. The two-byte separator
                # bounds their cost; a lone Value can cost one byte less.
                prefix = ", " + ("" if natural_text else value_label(node) + ": ")
                compact_value = compact_multiline_value(
                    value,
                    label=value_label(node),
                    indent=indent,
                    inline_prefix=prefix,
                    quote=quoted,
                    format_field=field,
                )
                block_value = compact_value is None
        if show_value and not block_value:
            natural_text = (
                compact
                and node["role"] == "staticText"
                and not node.get("name")
                and not node.get("description")
                and value is not None
            )
            value_text = (
                "<unavailable>"
                if value is None
                else display(value, "Value", attribute="value")
                if value_excerpt is not None
                else compact_value or embedded_value or field(value)
            )
            fields.append(("" if natural_text else value_label(node) + ": ") + value_text)
            if embedded_value is None and value_excerpt is None:
                record(node, "value")
            caption("value", value_text)
        if node.get("labelTargets"):
            fields.append("Labels for: " + ", ".join(node["labelTargets"]))
        if node.get("incompleteLabelRelation"):
            fields.append("Label relation: partly outside this observation or unavailable")
        selection_range = display_selection(node) if compact else node.get("selectionRange")
        summarized = False
        if selection_range is not None:
            summarized = (
                compact and selection_summaries is not None and selection_summaries.covers(node["ref"], selection_range)
            )
            if not summarized:
                fields.append(
                    f"Selection: {number_string(selection_range['location'])}:{number_string(selection_range['length'])}"
                )
            record(node, "selectionRange", kind="text-selection-summary" if summarized else "display")
        if "shortcut" in node:
            fields.append("Shortcut: " + display(node["shortcut"], "Shortcut"))
        if "menuMark" in node:
            fields.append("Mark: " + (display(node["menuMark"], "Mark") if node["menuMark"] else "<none>"))
        # The public readText continuation reads Value, not SelectedText. An
        # acquired selection cannot be shortened without another valid entry.
        selectedText = node.get("selectedText")
        selected_parts = shortened(
            selectedText if selectedText is not None else "", math.inf if coverage is not None else 200
        )
        if selectedText is not None and not selected_parts["omitted"]:
            fields.append("Selected text: " + field(selectedText))
            record(node, "selectedText")
        action_start = len(fields)
        own_action_fields = action_fields(
            node,
            part_actions.get(node["ref"], []),
            lambda source: rendered_actions(
                source, compact, menu_mechanisms=menu_mechanisms, control_raises=control_raises
            ),
        )
        fields.extend(own_action_fields)
        if node.get("actionsUnavailable"):
            fields.append("Secondary Actions: <unavailable>")
        if node.get("help") and node["help"] not in (
            shared_help,
            node.get("name"),
            node.get("description"),
            node.get("value"),
        ):
            fields.append("Help: " + display(node["help"], "Help", attribute="help"))
        child_help = (
            node["children"][0].get("help")
            if len(node["children"]) >= 3
            and node["children"][0].get("help")
            and all(child.get("help") == node["children"][0]["help"] for child in node["children"])
            else None
        )
        if child_help:
            fields.append("Children Help: " + display(child_help, "Children Help"))
            if not compact or display_embedded(child_help, field, hint=detail_hint) is None:
                for child in node["children"]:
                    record(child, "help", kind="equivalent", target=node["ref"])
        url_slot = None
        if node.get("url") and node["url"] not in (
            node.get("name"),
            node.get("description"),
            node.get("value"),
            node.get("help"),
        ):
            url_slot = len(fields)
            fields.append(
                "URL: "
                + (
                    display_url(node["url"], field, hint=detail_hint)
                    if compact
                    else display(node["url"], "URL", attribute="url")
                )
            )
        if "documentUrl" in node:
            fields.append("Document: " + field(node["documentUrl"]))
            record(node, "documentUrl")
        if "documentReports" in node:
            fields.append("Document reports (unresolved): " + literal(node["documentReports"]))
            record(node, "documentReports")
        if node.get("identifier") and not defer_identifiers:
            # Standalone formatters have no captured-detail delivery contract.
            fields.append("ID: " + field(node["identifier"]))
            record(node, "identifier")
        if node.get("blockedBy") and ",".join(node["blockedBy"]) != ",".join(parent_blocked):
            fields.append("Blocked by: " + ", ".join(node["blockedBy"]))
        field_base = line
        field_separator = ", " if node.get("name") else " "
        if fields:
            line += field_separator + ", ".join(fields)
        if len(own_action_fields) == 1 and not part_actions.get(node["ref"]):
            without = fields[:action_start] + fields[action_start + 1 :]
            node_action_lines[node["ref"]] = (
                line,
                field_base + (field_separator + ", ".join(without) if without else ""),
            )
        line_start = len(lines_value)
        if compact and native_url_sources is not None and coverage is not None and url_slot is not None:
            url_slots[node["ref"]] = _NativeUrlSlot(
                line_start,
                field_base,
                tuple(fields),
                url_slot,
                field_separator,
                len(f"{node['ref']} {role_label}"),
                state_flags,
            )
        hints: list[str] = []

        def hint(text: str) -> None:
            lines_value.append(text)
            hints.append(text)

        lines_value.append(indent + line)
        refs_value.append(node["ref"])
        for group in node.get("sharedDescriptions", ()):
            if defer_fields(group.facts):
                continue
            hint(indent + "\t" + description_declaration(group))
            for fact in group.facts:
                record(projection["nativeNodes"][fact.source], fact.attribute, kind="shared", target=group.scope)
        for group in node.get("sourceRoles", ()):
            if defer_fields(group.facts):
                continue
            label = "Native role description" if group.attribute == "roleDescription" else "Native role"
            hint(
                indent
                + "\t"
                + label
                + " [elements "
                + reference_set(fact.source for fact in group.facts)
                + "]: "
                + quoted(group.value)
            )
            for fact in group.facts:
                record(projection["nativeNodes"][fact.source], fact.attribute, kind="shared", target=group.scope)
        partial_info = partial(node)
        deferred_menu = compact and unexpanded_menu(node)
        if deferred_menu:
            collapsed_menus.setdefault(node["childCount"], []).append(node["ref"])
        if partial_info and not deferred_menu:
            if compact and "childrenStart" in node:
                start = node.get("childrenStart", 0)
                end = node.get("childrenEnd", start)
                child_coverage = f"{end - start}/{node['childCount']} read" + (f", offset {start}" if start else "")
                if "nextChildOffset" in node:
                    child_coverage += "; partial; " + (
                        "cursor below"
                        if context.get("publicApi")
                        else (f'app.element({quoted(node["ref"])}).read({{"offset": {node["nextChildOffset"]}}})')
                    )
                    inline_children.add(node["ref"])
                elif end == node["childCount"]:
                    child_coverage += "; end of direct children"
                else:
                    child_coverage += "; partial; read()" if context.get("publicApi") else "; partial"
                hint(f"{indent}\tChildren: {child_coverage}")
                if context.get("publicApi"):
                    coverage_hint = capture_child_hint(node, projection.source, len(lines_value) - 1, hints[-1])
                    if coverage_hint is not None:
                        child_hints.append(coverage_hint)
            else:
                hint(indent + "\tChildren: " + partial_info)
        if (
            compact
            and not partial_info
            and not deferred_menu
            and "childrenStart" in node
            and "childrenEnd" in node
            and node["childrenEnd"] == node.get("childCount")
        ):
            hint(
                (f"{indent}\tChildren: offsets {node['childrenStart']}–{node['childrenEnd']}; end of direct children.")
            )
        if "childrenStart" in node and not deferred_menu and not compact:
            end = (
                ""
                if "childrenEnd" not in node
                else "; inspected through offset " + number_string(node["childrenEnd"] - 1)
                if node["childrenEnd"] > node["childrenStart"]
                else "; no direct children inspected"
            )
            complete = (
                "; end of direct children"
                if "childrenEnd" in node and node["childrenEnd"] == node.get("childCount")
                else ""
            )
            hint(
                (
                    f"{indent}"
                    f"\tChildren: native children offset "
                    f"{number_string(node['childrenStart'])}"
                    f"{end}"
                    f"{complete}"
                    f"; live native order, not a frozen list."
                )
            )
        for omission in node.get("childOmissions", []):
            hint(indent + "\tOmitted: " + omission)
        if "nextChildOffset" in node and not deferred_menu and not compact:
            hint(
                f"{indent}\tContinue: use the returned cursor"
                if context.get("publicApi")
                else (
                    f"{indent}"
                    f"\tContinue: element("
                    f"{quoted(node['ref'])}"
                    f').read({{"offset": '
                    f"{number_string(node['nextChildOffset'])}"
                    f"}})"
                )
            )
        elif partial_info and "childrenStart" not in node and node["role"] != "application" and not deferred_menu:
            hint(
                f"{indent}\tInspect subtree: (await app.element({node['ref']})).read()"
                if context.get("publicApi")
                else f"{indent}\tInspect subtree: element({quoted(node['ref'])}).read()"
            )
        for omission in omissions:
            hint(indent + "\t" + omission)

        def text_block(name: str, part: Record, continuation: str = "") -> None:
            "Text Block."
            lines_value.append(indent + "\t" + name + ":")

            def body(value: str) -> None:
                # Display provider paragraph separators without changing the
                # captured source, UTF-16 offsets or readText pagination.
                lines_value.extend(indent + "\t| " + field(p) for p in re.split("\\r\\n|\\r|\\n", value))

            body(part["head"])
            if part["omitted"]:
                hint(f"{indent}\tOmitted: {part['omitted']} characters ({name}){continuation}")
                body(part["tail"])

        if block_value:
            continuation = (
                (
                    f"; use (await app.element({node['ref']})).read_text()"
                    if context.get("publicApi")
                    else f"; use element({quoted(node['ref'])}).read_text()"
                )
                if "readableTextRefs" not in context or node["ref"] in context["readableTextRefs"]
                else "; full captured value unavailable"
            )
            value_start = len(lines_value)
            text_block(value_label(node), value_parts, continuation)
            record(node, "value", kind="continuation" if value_parts["omitted"] else "display")
            caption("value", "\n".join(text[len(indent) + 1 :] for text in lines_value[value_start:]))
        for attribute, label in (
            ("documentText", "Document text"),
            ("controlValue", "Control value"),
            ("rangeValue", "Range value"),
        ):
            if attribute not in node:
                continue
            if (
                attribute in node.get("_numericValueAliases", ())
                and node[attribute] == node.get("value")
                and show_value
                and not value_parts["omitted"]
                and embedded_value is None
            ):
                record(node, attribute, kind="equivalent")
                continue
            if node[attribute] == node.get("value") and {attribute, "value"} <= set(
                node.get("_completeTextValues", ())
            ):
                if coverage is not None:
                    coverage.equivalent_text(node, attribute)
                continue
            part = shortened(node[attribute], VALUE_PREVIEW_LIMIT)
            if any(c in node[attribute] for c in "\r\n") or part["omitted"]:
                text_block(label, part)
            else:
                lines_value.append(indent + "\t" + label + ": " + field(node[attribute]))
            record(node, attribute, kind="continuation" if part["omitted"] else "display")
        if selected_parts["omitted"]:
            text_block("Selected text", selected_parts)
        if coverage is not None:
            for attribute in ("description", "help", "url"):
                coverage.equivalent_text(node, attribute)
        label = node.get("name") or node.get("description") or ""
        focus_name = shortened(label, 120)
        focus = (
            node["ref"]
            + " "
            + role_label
            + ((", " + preview(label, "Name", 120) if focus_name["omitted"] else " " + field(label)) if label else "")
        )
        if show_value:
            focus += ", " + preview(value, value_label(node), 200) if value is not None else ", Value: <unavailable>"
        if selection_range is not None and not summarized:
            focus += (
                f", Selection: {number_string(selection_range['location'])}:{number_string(selection_range['length'])}"
            )
        if selectedText is not None:
            focus += ", " + preview(selectedText, "Selected text", 200)
        focus_labels[node["ref"]] = focus
        summary_name = label or node.get("value") or node.get("identifier") or ""
        short = shortened(summary_name, 80)
        summary_labels[node["ref"]] = (
            node["ref"]
            + " "
            + role_label
            + (" " + field(short["head"] + ("…" if short["omitted"] else "") + short["tail"]) if summary_name else "")
            + (" (preview; see tree)" if short["omitted"] else "")
        )
        if node.get("selected"):
            (selected_menus if in_menu else selected_value).append(node)
        if node.get("focused"):
            reported_focus.append(node["ref"])
        labels_value[node["ref"]] = node.get("name") or node.get("description") or node.get("value") or node["role"]
        if (
            node["role"] in ("group", "cell")
            and not node.get("name")
            and not node.get("description")
            and len(node["children"]) > 1
            and any(child["role"] in ("staticText", "heading") for child in node["children"])
        ):
            context_refs[node["ref"]] = unique(
                context_refs.get(node["ref"], []) + [child["ref"] for child in node["children"]]
            )
        for target in node.get("labelTargets", []):
            context_refs.setdefault(target, []).append(node["ref"])
        state_at = len(f"{node['ref']} {role_label}")
        display_nodes.append(
            RenderedNode(
                node["ref"],
                parent_ref,
                depth,
                "\n".join(lines_value[line_start:]),
                indent + line[:state_at] + line[state_at + len(state_flags) :],
                tuple(hints),
                partial=bool(partial_info and not deferred_menu),
                layout=layout,
            )
        )
        node_line_ranges[node["ref"]] = (line_start, len(lines_value))
        groups = {ref: group for group in node.get("layoutGroups", ()) for ref in group["members"]}
        for child in node["children"]:
            child_layout = layout
            if child["ref"] in groups:
                group = groups[child["ref"]]
                heading = "\t" * (depth + 1 + len(layout)) + "Visual row:"
                key = node["ref"] + ":row:" + group["members"][0]
                child_layout = (*layout, (key, heading))
                if child["ref"] == group["members"][0]:
                    lines_value.append(heading)
            visit(child, depth + 1, node.get("blockedBy"), child_help, in_menu, node["ref"], child_layout)

    if "app" in context:
        windows = [node for node in roots if is_window(node)]
        header_value, title_omission = f"App: {field(context['app'])}.", None
        if len(windows) == 1 and windows[0].get("name"):
            part = shortened(windows[0]["name"], 500)
            header_value = f"Window: {quoted(part['head'] + part['tail'])}, {header_value}"
            if part["omitted"]:
                title_omission = f"Window title omitted: {part['omitted']} characters"
        lines_value.extend(
            [
                header_value,
                (f"Tree: full (subtree {context['subtreeRef']} only; ancestors and other window contents not expanded)")
                if context.get("subtreeRef")
                else "Tree: full (native menu query)"
                if context.get("menuQuery")
                else "Tree: full (current UI only)",
            ]
        )
        interactions = []

        def contexts(node: NodeView, window: NodeView | None = None) -> None:
            """Contexts.

            Args:
                node: Source/projected node in this exact view; its native identity is
                    unchanged.
            """
            owner = node["ref"] if is_window(node) else window
            if is_active_modal(node):
                interactions.append(
                    node["ref"] + " modal" + (f" in window {window}" if window else " (owner in window relations)")
                )
            for child in node["children"]:
                contexts(child, owner)

        for node in roots:
            contexts(node)
        if interactions:
            lines_value.append("Interaction: " + "; ".join(interactions))
        if title_omission:
            lines_value.append(title_omission)
    if share_index_basis:
        lines_value.append(index_basis)
    tree_start = len(lines_value)
    for node in roots:
        visit(node, 0)
    url_plan = (
        select_deferred_native_urls(
            native_url_sources,
            visible_nodes,
            visible_parents,
            {ref: tuple(items) for ref, items in captions.items()},
            tuple(url_slots),
        )
        if native_url_sources
        else NativeUrlPlan()
    )
    deferred_urls = url_plan.destinations
    if deferred_urls:
        from dataclasses import replace

        record_indexes = {record.ref: index for index, record in enumerate(display_nodes)}
        for destination in deferred_urls:
            ref = destination["ref"]
            node_action_lines.pop(ref, None)
            item = url_slots[ref]
            retained = item.fields[: item.slot] + item.fields[item.slot + 1 :]
            line = item.base + (item.separator + ", ".join(retained) if retained else "")
            old = display_nodes[record_indexes[ref]]
            indent = lines_value[item.line][: len(lines_value[item.line]) - len(lines_value[item.line].lstrip("\t"))]
            lines_value[item.line] = indent + line
            display_nodes[record_indexes[ref]] = replace(
                old,
                text="\n".join((lines_value[item.line], *old.text.split("\n")[1:])),
                content=indent + line[: item.state_at] + line[item.state_at + len(item.state_flags) :],
            )
    tree_end = len(lines_value)
    unexpanded_menus: tuple[UnexpandedMenuSummary, ...] = ()
    if collapsed_menus:
        short_summary = short_unexpanded_menu_summary(collapsed_menus) if concise_menu_summary else None
        lines_value.append(
            short_summary
            if short_summary is not None
            else "Menus unexpanded: "
            + "; ".join(
                reference_set(items) + " (0/" + str(count) + " children)" for count, items in collapsed_menus.items()
            )
            + "."
        )
        unexpanded_menus = (
            UnexpandedMenuSummary(
                tuple((ref, count) for count, items in collapsed_menus.items() for ref in items), lines_value[-1]
            ),
        )
        inline_children.update(ref for items in collapsed_menus.values() for ref in items)
    if "app" in context:
        if selected_value:

            def summary(node: NodeView) -> str:
                """Summary.

                Args:
                    node: Source/projected node in this exact view; its native identity is
                        unchanged.
                """
                parts = [summary_labels[node["ref"]]]

                def has_label(child: NodeView) -> bool:
                    return bool(
                        child.get("name")
                        or child.get("description")
                        or (
                            child.get("value")
                            and child["role"] in ("staticText", "heading", "textField", "textArea", "comboBox", "cell")
                        )
                    )

                def label_children(child: NodeView) -> list[NodeView]:
                    return [member for member in child["children"] if member["role"] != "row"]

                tail = node
                while not has_label(tail) and len(label_children(tail)) == 1:
                    tail = label_children(tail)[0]
                if tail is not node and has_label(tail):
                    parts.append("label: " + summary_labels[tail["ref"]])
                if not has_label(tail) and len(label_children(tail)) > 1:
                    labelled = []

                    def collect(child: NodeView) -> None:
                        """Collect.

                        Args:
                            child: Source/projected node in this exact view; its native identity
                                is unchanged.
                        """
                        if has_label(child):
                            labelled.append(child)
                        else:
                            for member in label_children(child):
                                collect(member)

                    for child in label_children(tail):
                        collect(child)
                    if labelled:
                        parts.append(
                            "contents: "
                            + "; ".join(summary_labels[c["ref"]] for c in labelled[:3])
                            + (f"; {len(labelled) - 3} more labelled contents (in tree)" if len(labelled) > 3 else "")
                        )
                return "; ".join(parts)

            lines_value.append(
                "Selected: "
                + "; ".join(summary(n) for n in selected_value[:8])
                + (
                    f"; {len(selected_value) - 8} more selected items (marked in tree)"
                    if len(selected_value) > 8
                    else ""
                )
            )
        if selected_menus:
            lines_value.append(
                "Selected menus: "
                + "; ".join(summary_labels[n["ref"]] for n in selected_menus[:8])
                + (f"; {len(selected_menus) - 8} more (marked in tree)" if len(selected_menus) > 8 else "")
            )
        if root["role"] == "application" and partial(root):
            lines_value.append("Tree partial: " + partial(root))
        if focused_ref is not None and focused_ref in focus_labels:
            summary = "\nReported focus: " + summary_labels[focused_ref]
            range_ = projection["nativeNodes"].get(focused_ref, {}).get("selectionRange")
            summarized_focus = (
                compact
                and range_ is not None
                and selection_summaries is not None
                and selection_summaries.covers(focused_ref, range_)
            )
            if range_ is not None and not summarized_focus:
                summary += (
                    f"; text selection: empty at UTF-16 offset {number_string(range_['location'])}"
                    if range_["length"] == 0
                    else (
                        f"; text selection: "
                        f"{number_string(range_['length'])}"
                        f" UTF-16 units at offset "
                        f"{number_string(range_['location'])}"
                    )
                )
            selections = [focus_labels[node["ref"]] for node in selected_descendants if node["ref"] in focus_labels]
            if selections:
                summary += ". Text selection in descendants (not exact focus): " + "; ".join(selections[:3])
                if len(selections) > 3:
                    summary += f"; {len(selections) - 3} more selected text controls"
            lines_value.append(summary)
        elif focused_ref is not None and projection["foldedInto"].get(focused_ref, "") in focus_labels:
            lines_value.append("\nReported focus: inside " + projection["foldedInto"][focused_ref])
        elif focused_ref is not None:
            ancestor = next((id for id in context.get("focusAncestorRefs", []) if id in focus_labels), None)
            lines_value.append(
                (f"\nReported focus: inside {focus_labels[ancestor]} (native child is not in the expanded tree).")
                if ancestor
                else "\nReported focus: outside the expanded tree."
            )
        elif reported_focus:
            lines_value.append("\nReported focus: unavailable.")
        internal = [
            "inside " + owner
            for source, owner in projection["foldedInto"].items()
            if source != focused_ref
            and projection["nativeNodes"].get(source, {}).get("focused")
            and owner in focus_labels
        ]
        others = unique([ref for ref in reported_focus if ref != focused_ref] + internal)
        if others:
            lines_value.append(
                "Focus flags: "
                + ", ".join(others[:8])
                + (f"; {len(others) - 8} more" if len(others) > 8 else "")
                + " (node reports; not confirmed input focus)."
            )

    def finalize(laid_out: list[str], layout_stages: list[Record]) -> RenderedTree:
        from dataclasses import replace

        text = "\n".join(laid_out)
        document = RenderedDocument(
            (
                RenderedSection(
                    text,
                    tuple(replace(node, related=tuple(context_refs.get(node.ref, ()))) for node in display_nodes),
                    "\n".join(lines_value[:tree_start]),
                    "\n".join(lines_value[tree_end:]),
                ),
            )
        )
        result = dict(
            text=text,
            refs=refs_value,
            labels=labels_value,
            roots=roots,
            contextRefs=context_refs,
            projection=projection,
        )
        if compact:
            result["inlineChildRefs"] = sorted(inline_children)
            result["childCoverageRefs"] = tuple(hint.ref for hint in child_hints)
        if context.get("recordStages"):
            result["layoutStages"] = layout_stages
        if coverage is not None:
            result["fieldCoverage"] = coverage.entries
        if deferred_native_fields:
            result["deferredNativeFields"] = tuple(deferred_native_fields.values())
        if disclosed_fields:
            result["disclosedTextFields"] = tuple(disclosed_fields.values())
        if menu_mechanisms:
            result["deferredMenuActions"] = tuple(asdict(item) for item in menu_mechanisms)
        if control_raises:
            result["deferredControlRaises"] = tuple(asdict(item) for item in control_raises)
        if deferred_urls:
            result["deferredNativeUrls"] = deferred_urls
            result["deferredNativeUrlPeers"] = url_plan.peer_groups
        return (
            RenderedResult(result, coverage.source_emissions, coverage.source_input, document=document)
            if coverage is not None
            else RenderedResult(result, document=document)
        )

    return LayoutFrame(
        lines_value,
        roots,
        context,
        field,
        finalize=finalize,
        child_hints=tuple(child_hints),
        rendered_nodes=tuple(display_nodes),
        node_line_ranges=node_line_ranges,
        node_prefixes=node_prefixes,
        unexpanded_menus=unexpanded_menus,
        node_action_lines=node_action_lines,
    )
