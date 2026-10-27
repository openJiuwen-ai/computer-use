from __future__ import annotations

import math
import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import regex
from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.accounting.fields import RenderedFields, RenderedResult
from computer_use.interaction.read.presentation.actions.layout import action_fields
from computer_use.interaction.read.presentation.compact import (
    display_actions,
    display_embedded,
    display_selection,
    display_url,
    unexpanded_menu,
)
from computer_use.interaction.read.presentation.compression.policy import secondary_actions
from computer_use.interaction.read.presentation.compression.projection import projectTree
from computer_use.interaction.read.presentation.formatting import json_string, number_string

# Compatibility for historical callers.
from computer_use.interaction.read.presentation.rendering.layout.empty_values import (
    compact_empty_values as compact_empty_values,
)
from computer_use.interaction.read.presentation.rendering.layout.model import LayoutFrame
from computer_use.interaction.read.presentation.rendering.layout.pipeline import LayoutPipeline
from computer_use.interaction.read.presentation.rendering.output import RenderedDocument, RenderedNode, RenderedSection
from computer_use.interaction.read.presentation.tree import (
    VALUE_PREVIEW_LIMIT,
    carries_value,
    disclosure,
    has_display_value,
    has_text_selection,
    is_active_modal,
    is_dialog,
    is_hidden,
    is_window,
    selected_text_descendants,
    unique,
    value_label,
)
from computer_use.protocol.primitives import trim, utf16_length

"Full text rendering, with separate source-aware projection and line layout."


if TYPE_CHECKING:
    from typing import Any, Sequence

    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.records import NodeView, RenderedTree, SemanticNode
    from computer_use.protocol.records import Record


CONVENTIONAL = {
    "button",
    "popUpButton",
    "menuButton",
    "checkBox",
    "radioButton",
    "switch",
    "link",
    "closeButton",
    "zoomButton",
    "minimizeButton",
    "textField",
    "textArea",
    "comboBox",
}
STANDARD_CLICK = CONVENTIONAL | {"menuItem", "menuBarItem"}


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
    "Format the observed role description with dialog, popover, row, and container labels."
    if is_dialog(node) and node["role"] != "sheet":
        return "dialog"
    if node.get("subrole") == "popover":
        return "popover"
    description = node.get("roleDescription")
    if node["role"] == "row" and (not description or description in ("row", "outline row", "外框行")):
        return "row"
    if node["role"] == "group" and (not description or description in ("group", "组")):
        return "container"
    return field(description) if description else re.sub("([a-z])([A-Z])", "\\1 \\2", node["role"]).lower()


def partial(node: NodeView) -> None | str:
    "Describe incomplete child collection using its observed counts and stop reason."
    read = node.get("childrenRead", len(node.get("native", node)["children"]))
    reason = node.get("partialReason")
    matches_reason = (
        True
        if reason is None and node["role"] != "application" and "childCount" in node and read < node["childCount"]
        else False
    )
    if matches_reason:
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


def rendered_actions(node: NodeView, compact: bool = False) -> list[Any]:
    "Format secondary-action labels and include native names when labels are ambiguous."
    result = []
    actions = secondary_actions(node)
    for action in display_actions(node, actions) if compact else actions:
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


def render_projection_lines(root: SemanticNode, context: Record, projection: ProjectionResult) -> LayoutFrame:
    """Render an already-compressed forest without recapturing or recompressing.

    Args:
        root: Root of the requested semantic view; native bindings remain separate.
        projection: Source-preserving presentation result and its provenance ledger.
    """
    context = {} if context is None else context
    compact = bool(context.get("compact"))
    detail_hint = "read this element for native facts" if context.get("publicApi") else "see details"
    coverage = RenderedFields(projection) if context.get("recordFields") else None

    def record(node: NodeView, *fields: str, kind: str = "display", target: str | None = None) -> None:
        if coverage is not None:
            for field_name in fields:
                coverage.record(node, field_name, kind=kind, target=target)

    focused_ref = context.get("focusedRef")
    roots = projection["roots"]
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

    def visit(
        node: NodeView,
        depth: int,
        parent_blocked: Sequence[str] | None = None,
        shared_help: str | None = None,
        in_menu: bool = False,
        parent_ref: str | None = None,
    ) -> None:
        """Visit.

        Args:
            node: Source/projected node in this exact view; its native identity is unchanged.
            depth: Current relation/traversal depth within the existing bound.
        """
        nonlocal selected_descendants
        parent_blocked = [] if parent_blocked is None else parent_blocked
        in_menu = in_menu or node["role"] in ("menu", "menuBar", "menuItem", "menuBarItem")
        indent, states, omissions = "\t" * depth, [], []
        line = f"{node['ref']} {role(node)}"
        if is_dialog(node) and node["role"] != "sheet" or node.get("subrole") == "popover":
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
        if node.get("enabled") is False:
            states.append("reported disabled" if deferred_click else "disabled")
            record(node, "enabled")
        if node.get("selected"):
            states.append("selected")
            record(node, "selected")
        if node.get("toggleState") in ("on", "off", "mixed"):
            states.append(node["toggleState"])
            record(node, "toggleState")
        if node.get("busy") is True:
            states.append("busy")
            record(node, "busy")
        if node.get("headingLevel"):
            states.append("heading level " + str(node["headingLevel"]))
            record(node, "headingLevel")
        if node.get("landmarkType"):
            states.append(node["landmarkType"] + " landmark")
            record(node, "landmarkType")
        if caps is not None:
            matches_caps = (
                True
                if (
                    caps["click"] == "available"
                    and not node.get("selectable")
                    and (not compact or node["role"] not in STANDARD_CLICK)
                )
                else False
            )
            if matches_caps:
                states.append("clickable")
            matches_node = (
                True
                if (
                    node.get("selectable")
                    and node.get("enabled") is not False
                    and not node.get("selected")
                    and caps["click"] != "unavailable"
                )
                else False
            )
            if matches_node:
                states.append("selectable")
            if caps["typeText"] == "available":
                states.append("text-input")
            elif caps["replaceText"] == "available":
                states.append("replace-only")
            if caps["replaceText"] != caps["typeText"] and caps["replaceText"] != "available":
                states.append("replace " + caps["replaceText"])
            matches_conventional = (
                True
                if (
                    caps["focus"] == "available"
                    and caps["typeText"] != "available"
                    and node["role"] not in CONVENTIONAL
                    and not node.get("selectable")
                    and (not compact or node["role"] not in ("group", "webArea"))
                )
                else False
            )
            if matches_conventional:
                states.append("focusable")
        else:
            if node.get("selectable"):
                if not node.get("selected"):
                    states.append("selectable")
            elif node.get("pressable") and node["role"] not in CONVENTIONAL:
                states.append("clickable")
            matches_conventional_condition = (
                True
                if (
                    node.get("focusable")
                    and node["role"] not in CONVENTIONAL
                    and not node.get("selectable")
                    and (not compact or node["role"] not in ("group", "webArea"))
                )
                else False
            )
            if matches_conventional_condition:
                states.append("focusable")
        if node.get("submenu"):
            states.append("submenu")
        expanded = disclosure(node)
        if expanded is not None:
            states.append("expanded" if expanded else "collapsed")
            record(node, "expanded" if "expanded" in node else "disclosing")
        if node["ref"] == focused_ref:
            states.append("focused")
        elif node.get("focused"):
            states.append("reports focus")
            record(node, "focused")
        if node.get("modal"):
            states.append("modal")
            record(node, "modal")
        elif node.get("modal") is False and is_dialog(node):
            states.append("non-modal")
            record(node, "modal")
        if is_hidden(node):
            states.append("hidden")
            record(node, "visible")
            record(node, "showing")
        if (
            node["ref"] == focused_ref
            and not has_text_selection(node)
            and node["role"] not in ("application", "window")
        ):
            selected_descendants = selected_text_descendants(node)
            if selected_descendants:
                states.append("contains selected text")
        state_flags = " (" + ", ".join(states) + ")" if states else ""
        line += state_flags

        def display(value: str, name: str, limit: float = math.inf, attribute: str | None = None) -> str:
            """Display.

            Args:
                limit: Existing upper bound on returned items for this local page/format
                    operation.
                attribute: Native/display field name recorded by the provenance ledger.
            """
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

        if node.get("name"):
            line += " " + display(node["name"], "Name", attribute="name")
        fields = []
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
            fields.append("Index (0-based): " + str(node["index"]))
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
            fields.append("Description: " + display(node["description"], "Description", attribute="description"))
        if node.get("fullDescription"):
            fields.append(
                "Full description: " + display(node["fullDescription"], "Full description", attribute="fullDescription")
            )
        if node.get("localizedLandmarkType"):
            fields.append(
                "Landmark: " + display(node["localizedLandmarkType"], "Landmark", attribute="localizedLandmarkType")
            )
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
        value_parts = shortened(node.get("value", ""), VALUE_PREVIEW_LIMIT)
        embedded_value = (
            display_embedded(value, field, hint=detail_hint) if compact and isinstance(value, str) else None
        )
        block_value = (
            show_value
            and "value" in node
            and embedded_value is None
            and (any(c in node["value"] for c in "\r\n") or value_parts["omitted"] > 0)
        )
        if show_value and not block_value:
            natural_text = (
                compact
                and node["role"] == "staticText"
                and not node.get("name")
                and not node.get("description")
                and value is not None
            )
            fields.append(
                ("" if natural_text else value_label(node) + ": ")
                + ("<unavailable>" if value is None else embedded_value or field(value))
            )
            if embedded_value is None:
                record(node, "value")
        if node.get("labelTargets"):
            fields.append("Labels for: " + ", ".join(node["labelTargets"]))
        if node.get("incompleteLabelRelation"):
            fields.append("Label relation: partly outside this observation or unavailable")
        selection_range = display_selection(node) if compact else node.get("selectionRange")
        if selection_range is not None:
            fields.append(
                (f"Selection: {number_string(selection_range['location'])}:{number_string(selection_range['length'])}")
            )
            record(node, "selectionRange")
        if "shortcut" in node:
            fields.append("Shortcut: " + display(node["shortcut"], "Shortcut"))
        if "menuMark" in node:
            fields.append("Mark: " + (display(node["menuMark"], "Mark") if node["menuMark"] else "<none>"))
        # The public readText continuation reads Value, not SelectedText. An
        # acquired selection cannot be shortened without another valid entry.
        selected_parts = shortened(node.get("selectedText", ""), math.inf if coverage is not None else 200)
        if node.get("selectedText") and not selected_parts["omitted"]:
            fields.append("Selected text: " + field(node["selectedText"]))
            record(node, "selectedText")
        fields.extend(
            action_fields(node, part_actions.get(node["ref"], []), lambda source: rendered_actions(source, compact))
        )
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
        if node.get("url") and node["url"] not in (
            node.get("name"),
            node.get("description"),
            node.get("value"),
            node.get("help"),
        ):
            fields.append(
                "URL: "
                + (
                    display_url(node["url"], field, hint=detail_hint)
                    if compact
                    else display(node["url"], "URL", attribute="url")
                )
            )
        matches_node_condition = (
            True
            if (
                node.get("identifier")
                and not node.get("name")
                and not node.get("description")
                and not (node["role"] == "staticText" and node.get("value"))
            )
            else False
        )
        if matches_node_condition:
            identifier = node["identifier"]
            if compact and utf16_length(identifier) > 120:
                # The full identifier stays unaccounted here, so native fact
                # accounting retains it verbatim in this capture's details.
                fields.append(
                    "ID: " + field(identifier[:24] + "…" + identifier[-80:]) + " [ID shortened; " + detail_hint + "]"
                )
            else:
                fields.append("ID: " + display(identifier, "ID", attribute="identifier"))
        if node.get("blockedBy") and ",".join(node["blockedBy"]) != ",".join(parent_blocked):
            fields.append("Blocked by: " + ", ".join(node["blockedBy"]))
        if fields:
            line += (", " if node.get("name") else " ") + ", ".join(fields)
        line_start = len(lines_value)
        hints: list[str] = []

        def hint(text: str) -> None:
            lines_value.append(text)
            hints.append(text)

        lines_value.append(indent + line)
        refs_value.append(node["ref"])
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
                        "continue using the returned cursor"
                        if context.get("publicApi")
                        else (f'app.element({quoted(node["ref"])}).read({{"offset": {node["nextChildOffset"]}}})')
                    )
                    inline_children.add(node["ref"])
                elif end == node["childCount"]:
                    child_coverage += "; end of direct children"
                else:
                    child_coverage += "; partial (" + detail_hint + ")"
                hint(f"{indent}\tChildren: {child_coverage}")
            else:
                hint(indent + "\tChildren: " + partial_info)
        matches_compact = (
            True
            if (
                compact
                and not partial_info
                and not deferred_menu
                and "childrenStart" in node
                and "childrenEnd" in node
                and node["childrenEnd"] == node.get("childCount")
            )
            else False
        )
        if matches_compact:
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
        else:
            matches_partial_info = (
                True
                if partial_info and "childrenStart" not in node and node["role"] != "application" and not deferred_menu
                else False
            )
            if matches_partial_info:
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
                    f"; use (await app.element({node['ref']})).readText()"
                    if context.get("publicApi")
                    else f"; use element({quoted(node['ref'])}).readText()"
                )
                if "readableTextRefs" not in context or node["ref"] in context["readableTextRefs"]
                else "; full captured value unavailable"
            )
            text_block(value_label(node), value_parts, continuation)
            record(node, "value", kind="continuation" if value_parts["omitted"] else "display")
        for attribute, label in (
            ("documentText", "Document text"),
            ("controlValue", "Control value"),
            ("rangeValue", "Range value"),
        ):
            if attribute not in node:
                continue
            if node[attribute] == node.get("value") and {attribute, "value"} <= set(
                node.get("_completeTextValues", ())
            ):
                if coverage is not None:
                    coverage.equivalent_text(node, attribute)
                continue
            part = shortened(node[attribute], VALUE_PREVIEW_LIMIT)
            if any(c in node[attribute] for c in "\r\n") or part["omitted"]:
                text_block(label, part, "; " + detail_hint)
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
            + role(node)
            + ((", " + preview(label, "Name", 120) if focus_name["omitted"] else " " + field(label)) if label else "")
        )
        if show_value:
            focus += ", " + preview(value, value_label(node), 200) if value is not None else ", Value: <unavailable>"
        if selection_range is not None:
            focus += (
                f", Selection: {number_string(selection_range['location'])}:{number_string(selection_range['length'])}"
            )
        if node.get("selectedText"):
            focus += ", " + preview(node["selectedText"], "Selected text", 200)
        focus_labels[node["ref"]] = focus
        summary_name = label or node.get("value") or node.get("identifier") or ""
        short = shortened(summary_name, 80)
        summary_labels[node["ref"]] = (
            node["ref"]
            + " "
            + role(node)
            + (" " + field(short["head"] + ("…" if short["omitted"] else "") + short["tail"]) if summary_name else "")
            + (" (preview; see tree)" if short["omitted"] else "")
        )
        if node.get("selected"):
            (selected_menus if in_menu else selected_value).append(node)
        if node.get("focused"):
            reported_focus.append(node["ref"])
        labels_value[node["ref"]] = node.get("name") or node.get("description") or node.get("value") or node["role"]
        has_caption_context = (
            True
            if (
                node["role"] in ("group", "cell")
                and not node.get("name")
                and not node.get("description")
                and len(node["children"]) > 1
                and any(child["role"] in ("staticText", "heading") for child in node["children"])
            )
            else False
        )
        if has_caption_context:
            context_refs[node["ref"]] = unique(
                context_refs.get(node["ref"], []) + [child["ref"] for child in node["children"]]
            )
        for target in node.get("labelTargets", []):
            context_refs.setdefault(target, []).append(node["ref"])
        state_at = len(f"{node['ref']} {role(node)}")
        display_nodes.append(
            RenderedNode(
                node["ref"],
                parent_ref,
                depth,
                "\n".join(lines_value[line_start:]),
                indent + line[:state_at] + line[slice(state_at + len(state_flags), None)],
                tuple(hints),
                partial=bool(partial_info and not deferred_menu),
            )
        )
        for child in node["children"]:
            visit(child, depth + 1, node.get("blockedBy"), child_help, in_menu, node["ref"])

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
    tree_start = len(lines_value)
    for node in roots:
        visit(node, 0)
    tree_end = len(lines_value)
    if collapsed_menus:
        lines_value.append(
            "Menu contents not expanded: "
            + "; ".join(
                reference_set(items) + " (0/" + str(count) + " children)" for count, items in collapsed_menus.items()
            )
            + ". Use app.element(ref).read() to inspect a menu."
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
            if range_ is not None:
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

        def _iter_source(_source_items: Iterator[Any]) -> Iterator[Any]:
            for source, owner in _source_items:
                if (
                    source != focused_ref
                    and projection["nativeNodes"].get(source, {}).get("focused")
                    and owner in focus_labels
                ):
                    yield "inside " + owner

        internal = list(_iter_source(iter(projection["foldedInto"].items())))
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
        if context.get("recordStages"):
            result["layoutStages"] = layout_stages
        if coverage is not None:
            result["fieldCoverage"] = coverage.entries
        return (
            RenderedResult(result, coverage.source_emissions, coverage.source_input, document=document)
            if coverage is not None
            else RenderedResult(result, document=document)
        )

    return LayoutFrame(lines_value, roots, context, field, finalize=finalize)
