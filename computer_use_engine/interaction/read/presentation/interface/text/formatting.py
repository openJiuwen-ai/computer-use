# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Serialize typed fields and selected blocks without inspecting older rendered text."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import re
from typing import TYPE_CHECKING
from types import MappingProxyType

from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.presentation.fallback import literal
from computer_use.interaction.read.presentation.interface.atoms.model import AtomPlan, AtomSlot
from computer_use.interaction.read.presentation.interface.text.model import FormattedInterface, InterfaceTextRequest
from computer_use.interaction.read.presentation.interface.text.shared_content import slice_statement
from computer_use.interaction.read.presentation.interface.text.geometric import write_geometric_block
from computer_use.interaction.read.presentation.interface.text.columns import compact_grid, floating_row
from computer_use.interaction.read.presentation.interface.text.grouped_content import write_grouped_children
from computer_use.interaction.read.presentation.interface.text.readable import (
    note_lines,
    notes_only,
    packed_atom,
    source_slot_label,
)
from computer_use.interaction.read.presentation.rendering.formatting import field as scalar_field, quoted
from computer_use.interaction.read.presentation.rendering.output import RenderedNode

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.composition.content.model import FieldPresentation
    from computer_use.interaction.read.presentation.interface.collections.model import CollectionPrefixPlan, TablePlan


def field(value: str) -> str:
    """Quote interface delimiters as application content rather than layout syntax."""
    return quoted(value) if any(character in value for character in "|;{}") else scalar_field(value)


def slot_value(slot: AtomSlot) -> str:
    """Serialize one value using the type and interpretation selected by atomic planning."""
    value = slot.value
    if slot.format == "preview":
        preview = slot.preview
        if preview is None:
            raise ValueError("A prepared preview lacks its captured text ranges")
        suffix = (
            f" [embedded data, {preview.encoded_characters} encoded chars]"
            if preview.kind == "embedded"
            else " [embedded]"
            if preview.kind == "embedded-url"
            else "… [URL shortened]"
        )
        return field(preview.head) + suffix
    if slot.format == "unavailable":
        return "<unavailable>"
    if slot.format == "span" and isinstance(value, Mapping):
        start, count = value["location"], value["length"]
        return (
            f"{start}:0 (0-based)" if count == 0 else str(start + 1) if count == 1 else f"{start + 1}–{start + count}"
        )
    if slot.format == "selection" and isinstance(value, Mapping):
        return f"{value['location']}:{value['length']}"
    if slot.format == "refs":
        return ", ".join(str(ref) for ref in value)
    if slot.format == "toggle":
        if value is True or value == "true" or value == "1":
            return "on"
        if value is False or value == "false" or value == "0":
            return "off"
    if slot.format in ("literal", "number") or not isinstance(value, str):
        return literal(value)
    return field(value)


def _role(atom: AtomPlan, *, states: bool = True) -> str:
    role = field(atom.role.label)
    if states and atom.states:
        role += " (" + ", ".join(state.label for state in atom.states) + ")"
    return role


class InterfaceWriter:
    """Write a prepared block graph while retaining complete per-element diff records.

    Every traversal and layout choice comes from InterfaceDocument. Fields remain
    independently addressable in the retained atoms even when they share a line.
    """

    def __init__(self, request: InterfaceTextRequest) -> None:
        self.request = request
        self.document = request.interface
        self.atoms = request.atoms.atoms
        self.lines: list[str] = []
        self.nodes: dict[str, RenderedNode] = {}
        self.menus: list[str] = []
        self.declarations: list[tuple[str, str, int]] = []
        self.group_sections: list[str] = []
        self.background_content: dict[str, tuple[str, ...]] = {}

    def value(self, ref: str, slot: AtomSlot, *, shared: bool = False) -> str:
        """Render a preselected excerpt or the complete typed field value."""
        sliced = (
            self.request.text_slices.plan_by(ref, slot.key) if shared and self.request.text_slices is not None else None
        )
        if sliced is not None:
            return slice_statement(sliced)
        excerpt = self.request.disclosure.get(ref, slot.key) if self.request.disclosure else None
        if excerpt is not None:
            if excerpt.source.value != slot.value:
                raise ValueError("Prepared excerpt no longer matches its atomic source field")
            return (
                " … ".join(field(fragment.text) for fragment in excerpt.fragments)
                + f" [excerpt: {excerpt.shown}/{excerpt.total} chars; read_details(element={ref})]"
            )
        return slot_value(slot)

    def atom_lines(
        self,
        ref: str,
        *,
        short: bool = False,
        states: bool = True,
        shared: bool = False,
        slots: tuple[AtomSlot, ...] | None = None,
        collection_prefix: CollectionPrefixPlan | None = None,
    ) -> tuple[str, ...]:
        """Serialize one complete real target; shared headers affect only explicit prefix slots."""
        atom = self.atoms[ref]
        style = self.request.readable.nodes.get(ref) if self.request.readable is not None else None
        prefix = ref if short else ref + " " + _role(atom, states=states)
        member = (
            next((item for item in collection_prefix.members if item.ref == ref), None)
            if collection_prefix is not None
            else None
        )
        if member is not None:
            remaining = [state.label for index, state in enumerate(atom.states) if index not in member.state_indices]
            prefix = ref + (" (" + ", ".join(remaining) + ")" if remaining else "")
        if style is not None and style.glyph is not None:
            prefix = ref + " " + style.glyph + prefix[len(ref) :]
        captions, inline, blocks = [], [], []
        sharing = self.request.sharing if shared else None
        omitted_slots = sharing.omitted_slots.get(ref, ()) if sharing is not None else ()
        omitted_actions = sharing.omitted_actions.get(ref, ()) if sharing is not None else ()
        for slot in atom.slots if slots is None else slots:
            if style is not None and (slot.key in style.note_slots or slot.key == style.toggle_slot):
                continue
            if slot.key in omitted_slots or member is not None and slot.key in member.slot_keys:
                continue
            value = self.value(ref, slot, shared=shared)
            label = "Index" if slot.key == "index" and self.request.atoms.shared_index_basis else slot.label
            label = source_slot_label(self, ref, slot.key, label)
            if (
                shared
                and label is None
                and self.request.text_slices is not None
                and self.request.text_slices.plan_by(ref, slot.key) is not None
            ):
                label = "Text"
            if shared and self.request.text_slices is not None and (ref, slot.key) in self.request.text_slices.anchors:
                label = slot.key.capitalize()
            text = (label + ": " if label is not None else "") + value
            if style is not None and slot.key in style.block_slots and slot.placement != "block":
                blocks.append(text)
            elif slot.placement == "caption":
                captions.append(text)
            elif slot.placement == "block":
                excerpt = self.request.disclosure.get(ref, slot.key) if self.request.disclosure else None
                sliced = (
                    shared
                    and self.request.text_slices is not None
                    and self.request.text_slices.plan_by(ref, slot.key) is not None
                )
                if isinstance(slot.value, str) and slot.preview is None and excerpt is None and not sliced:
                    blocks.append((label or "Value") + ":")
                    blocks.extend("| " + field(paragraph) for paragraph in re.split(r"\r\n|\r|\n", slot.value))
                else:
                    blocks.append(text)
            else:
                inline.append(text)
        if captions:
            prefix += " " + " ".join(captions)
        if atom.actions:
            groups: dict[str, list[str]] = {}
            for action in atom.actions:
                if style is not None and action.catalogue_index in style.note_actions:
                    continue
                if action.catalogue_index in omitted_actions:
                    continue
                spelling = field(action.label)
                if action.ambiguous:
                    spelling += " [name: " + quoted(action.name) + "]"
                groups.setdefault(action.source_ref, []).append(spelling)
            for owner, actions in groups.items():
                label = "Secondary Actions" if owner == ref else f"Secondary Actions [element {owner}]"
                inline.append(label + ": " + ", ".join(actions))
        if atom.actions_unavailable:
            inline.append("Secondary Actions: <unavailable>")
        if inline:
            prefix += (", " if captions else " ") + ", ".join(inline)
        for declaration in atom.declarations:
            owners = reference_set(dict.fromkeys(fact.key.source for fact in declaration.facts))
            label = {
                "valueDescription": "Value description",
                "rangeDescription": "Range description",
                "roleDescription": "Native role description",
                "role": "Native role",
                "description": "Description",
            }.get(declaration.attribute, declaration.attribute)
            blocks.append(label + " [elements " + owners + "]: " + literal(declaration.value))
        if not shared:
            blocks.extend("Native: " + line for line in note_lines(self, ref, include_owner=False))
        return (prefix, *("\t" + item for item in blocks))

    def coverage_lines(self, atom: AtomPlan) -> tuple[str, ...]:
        """Format preserved collection coverage, keeping actual continuation routes explicit."""
        coverage = atom.coverage
        if coverage.unexpanded_menu:
            if atom.ref not in self.menus:
                self.menus.append(atom.ref)
            return ()
        result = []
        if coverage.start is not None and coverage.total is not None:
            read = coverage.end - coverage.start if coverage.end is not None else coverage.read
            count = (str(read) if read is not None else "?") + "/" + str(coverage.total) + " read"
            if coverage.start:
                count += f", offset {coverage.start}"
            if coverage.next_offset is not None:
                count += "; partial; cursor below"
            elif coverage.end == coverage.total:
                count += "; end of direct children"
            else:
                count += "; partial; read()"
            result.append("Children: " + count)
        elif coverage.partial:
            count = (
                f"{coverage.read}/{coverage.total} read"
                if coverage.read is not None and coverage.total is not None
                else f"total {coverage.total}"
                if coverage.total is not None
                else "total unknown"
            )
            if coverage.reason:
                count += "; partial: " + field(coverage.reason)
            if coverage.next_offset is not None:
                count += "; cursor below"
            result.append("Children: " + count)
            if coverage.next_offset is None:
                result.append(f"Inspect subtree: (await app.element({atom.ref})).read()")
        result.extend("Omitted: " + field(item) for item in coverage.omissions)
        return tuple(result)

    def remember(
        self, ref: str, depth: int, context: tuple[tuple[str, str], ...], *, slots: tuple[AtomSlot, ...] | None = None
    ) -> None:
        """Keep unshared typed fields as exact per-target text for incremental delivery."""
        if ref in self.nodes:
            raise ValueError("A target appeared twice in the prepared interface traversal")
        atom = self.atoms[ref]
        indent = "\t" * depth
        own = tuple(indent + line for line in self.atom_lines(ref, slots=slots))
        coverage_hints = tuple(indent + "\t" + line for line in self.coverage_lines(atom))
        excerpts = self.request.disclosure.excerpts if self.request.disclosure is not None else {}
        recovery_hints = tuple(
            indent
            + "\t"
            + f"Omitted: {excerpt.total - excerpt.shown} of "
            + f"{excerpt.total} characters from {attribute}; read_details(element={ref})"
            for (owner, attribute), excerpt in excerpts.items()
            if owner == ref
        )
        if self.request.groups is not None:
            recovery_hints += tuple(
                indent
                + "\t"
                + "Omitted: captured content for elements "
                + reference_set(group.projected_refs)
                + f"; state.read_details(); section {group.section_key}"
                for group in self.request.groups.groups
                if group.projected_refs[0] == ref
            )
        hints = (*coverage_hints, *recovery_hints)
        related = tuple(self.document.projected_nodes[ref].get("labelTargets", ()))
        self.nodes[ref] = RenderedNode(
            ref,
            atom.parent,
            depth,
            "\n".join((*own, *coverage_hints)),
            indent + self.atom_lines(ref, states=False, slots=slots)[0],
            hints,
            related,
            partial=atom.coverage.partial and not atom.coverage.unexpanded_menu,
            layout=context,
        )

    def write_atom(self, ref: str, depth: int, context: tuple[tuple[str, str], ...], *, short: bool = False) -> None:
        self.remember(ref, depth, context)
        if notes_only(self, ref):
            return
        indent = "\t" * depth
        self.lines.extend(indent + line for line in self.atom_lines(ref, shared=True, short=short))
        if self.request.coverage is None or ref not in self.request.coverage.omitted_refs:
            self.lines.extend(indent + "\t" + line for line in self.coverage_lines(self.atoms[ref]))

    def write_field(self, plan: FieldPresentation, depth: int, context: tuple[tuple[str, str], ...]) -> None:
        """Write already-composed input slots and the selected original-source member modes."""
        inline_unit = self.document.blocks[plan.block_key].layout == "control-unit"
        if plan.heading_ref is not None and not inline_unit:
            heading = self.write_field_heading(plan.heading_ref, depth, context)
            context = (*context, (plan.block_key, heading))
            depth += 1
        self.remember(plan.target_ref, depth, context, slots=plan.target_slots)
        target_line = len(self.lines)
        self.lines.extend(
            "\t" * depth + line for line in self.atom_lines(plan.target_ref, shared=True, slots=plan.target_slots)
        )
        self.lines.extend("\t" * (depth + 1) + line for line in self.coverage_lines(self.atoms[plan.target_ref]))
        heading = "\t" * depth + self.atom_lines(plan.target_ref, slots=plan.target_slots)[0]
        child_context = (*context, (plan.block_key, heading))
        for member in plan.members:
            if member.ref == plan.heading_ref and not inline_unit:
                continue
            self.remember(member.ref, depth + 1, child_context)
            if member.mode == "complete":
                if notes_only(self, member.ref):
                    continue
                lines = ["\t" * (depth + 1) + line for line in self.atom_lines(member.ref, shared=True)]
                lines.extend("\t" * (depth + 2) + line for line in self.coverage_lines(self.atoms[member.ref]))
                if inline_unit:
                    self.lines[target_line] += " [" + lines[0].lstrip("\t") + "]"
                    self.lines.extend(lines[1:])
                else:
                    self.lines.extend(lines)
            elif member.mode == "caption-reference":
                role = field(self.atoms[member.ref].role.label)
                self.lines[target_line] += f" [{role}: {member.ref}]"
            elif member.mode == "action-source":
                groups: dict[str, list[str]] = {}
                style = self.request.readable.nodes.get(member.ref) if self.request.readable is not None else None
                for action in self.atoms[member.ref].actions:
                    if style is not None and action.catalogue_index in style.note_actions:
                        continue
                    label = field(action.label)
                    if action.ambiguous:
                        label += " [name: " + quoted(action.name) + "]"
                    groups.setdefault(action.source_ref, []).append(label)
                for source, labels in groups.items():
                    self.lines.append(
                        "\t" * (depth + 1) + f"Secondary Actions [element {source}]: " + ", ".join(labels)
                    )

    def write_field_heading(self, ref: str, depth: int, context: tuple[tuple[str, str], ...]) -> str:
        """Print a real label as a field heading with its complete original facts."""
        self.remember(ref, depth, context)
        indent = "\t" * depth
        lines = self.atom_lines(ref, shared=True)
        heading = indent + "Field: " + lines[0]
        self.lines.append(heading)
        self.lines.extend(indent + line for line in lines[1:])
        self.lines.extend(indent + "\t" + line for line in self.coverage_lines(self.atoms[ref]))
        return heading

    def write_table(self, table: TablePlan, depth: int, context: tuple[tuple[str, str], ...]) -> None:
        """Serialize a proven native collection, retaining real rows, cells and descendants."""
        self.write_atom(table.ref, depth, context)
        indent = "\t" * (depth + 1)
        deferred = {(item.ref, item.slot.key) for item in table.detail_fields}

        def visible_slots(ref: str) -> tuple[AtomSlot, ...]:
            return tuple(slot for slot in self.atoms[ref].slots if (ref, slot.key) not in deferred)

        def header(prefix: CollectionPrefixPlan | None) -> str:
            if prefix is None:
                return ""
            text = field(prefix.role.label)
            if prefix.states:
                text += " (" + ", ".join(state.label for state in prefix.states) + ")"
            if prefix.slots:
                text += ", " + ", ".join(
                    (slot.label + ": " if slot.label else "") + slot_value(slot) for slot in prefix.slots
                )
            return " [" + text + "]"

        headings = ["Rows" + header(table.row_prefix)]
        for index, prefix, label in zip(table.column_indices, table.column_prefixes, table.column_labels):
            caption = (
                field(label)
                if label is not None
                else ("Cells" if table.layout == "outline" else "Column " + str(index + 1))
            )
            headings.append(caption + header(prefix))
        separator = " > " if table.layout == "outline" else " | "
        heading = indent + ("Outline: " if table.layout == "outline" else "Records: ") + separator.join(headings)
        self.lines.append(heading)
        if deferred:
            self.lines.append(indent + "Structure details (indices and cell extents): observation.read_details()")
        row_context = (*context, ("table:" + table.ref, heading))
        for row in table.rows:
            if table.layout == "rows":
                self.write_atom(row.ref, depth + 2, row_context)
                self.write_declarations(self.document.ref_locations[row.ref], depth + 2)
                for cell in row.cells:
                    self.write(self.document.ref_locations[cell.ref], depth + 3, row_context)
                continue
            self.remember(row.ref, depth + 2, row_context)
            row_text, row_tail = packed_atom(
                self.atom_lines(row.ref, shared=True, slots=visible_slots(row.ref), collection_prefix=table.row_prefix),
                row.ref,
                depth + 2,
                readable=self.request.readable is not None,
            )
            cells = [row_text]
            trailing = list(row_tail)
            for cell in row.cells:

                def cell_branch(ref: str, child_depth: int) -> str:
                    self.remember(ref, child_depth, row_context)
                    column_prefix = table.column_prefixes[table.column_indices.index(cell.column)]
                    own, tail = packed_atom(
                        self.atom_lines(
                            ref,
                            shared=True,
                            slots=visible_slots(ref),
                            collection_prefix=column_prefix if ref == cell.ref else None,
                        ),
                        ref,
                        depth + 2,
                        readable=self.request.readable is not None,
                    )
                    trailing.extend(tail)
                    children = self.atoms[ref].children
                    if not children:
                        return own
                    members = [cell_branch(child, child_depth + 1) for child in children]
                    return own + " > " + (members[0] if len(members) == 1 else "{" + "; ".join(members) + "}")

                cells.append(cell_branch(cell.ref, depth + 2))
            self.lines.append(indent + "\t" + separator.join(cells))
            self.lines.extend(trailing)
            pending = [self.document.ref_locations[row.ref]]
            while pending:
                member_key = pending.pop()
                self.write_declarations(member_key, depth + 2)
                pending.extend(reversed(self.document.blocks[member_key].children))
        for column in table.columns:
            if column.ref not in self.nodes:
                self.write(self.document.ref_locations[column.ref], depth + 1, row_context)
        for ref in table.remaining_refs:
            if ref not in self.nodes:
                self.write(self.document.ref_locations[ref], depth + 1, context)

    def write(
        self, key: str, depth: int = 0, context: tuple[tuple[str, str], ...] = (), *, inherited_prefix: bool = False
    ) -> None:
        """Write a block and its preselected, source-explicit shared declarations."""
        start = len(self.lines)
        self._write(key, depth, context, inherited_prefix=inherited_prefix)
        self.write_declarations(key, depth, at=start + 1)

    def write_declarations(self, key: str, depth: int, *, at: int | None = None) -> None:
        """Publish one real block's shared content beneath its displayed owner.

        Packed layouts call this for each member whose ordinary block traversal
        they consume, preserving the original declaration scopes and source refs.
        """
        plan = self.request.sharing
        shared = plan.by_block.get(key) if plan is not None else None
        groups = self.request.coverage.by_block.get(key, ()) if self.request.coverage is not None else ()
        declarations = []
        indent = "\t" * (depth + 1)
        for index, group in enumerate(shared.actions if shared else ()):
            labels = [
                field(action.label) + (" [name: " + quoted(action.name) + "]" if action.ambiguous else "")
                for action in group.actions
            ]
            declarations.append(
                indent + "Secondary Actions [elements " + reference_set(group.refs) + "]: " + ", ".join(labels)
            )
            self.declarations.append((key, "actions", index))
        for index, group in enumerate(shared.fields if shared else ()):
            heading = self.request.readable.headings.get(key) if self.request.readable is not None else None
            if heading is None or heading.shared_index != index:
                declarations.append(
                    indent
                    + (group.slot.label or group.slot.key)
                    + " [elements "
                    + reference_set(group.refs)
                    + "]: "
                    + slot_value(group.slot)
                )
            self.declarations.append((key, "fields", index))
        for index, group in enumerate(groups):
            declarations.append(
                indent
                + "Children [elements "
                + reference_set(group.refs)
                + f"]: 0/{group.total} read; partial: "
                + field(group.reason)
                + "; inspect each with element(ref).read()"
            )
            self.declarations.append((key, "coverage", index))
        position = len(self.lines) if at is None else at
        self.lines[position:position] = declarations

    def validate_declarations(self) -> None:
        """Require every planned shared destination to occur exactly once in the full view."""
        expected = []
        if self.request.sharing is not None:
            for key, groups in self.request.sharing.by_block.items():
                expected.extend((key, "actions", index) for index in range(len(groups.actions)))
                expected.extend((key, "fields", index) for index in range(len(groups.fields)))
        if self.request.coverage is not None:
            for key, groups in self.request.coverage.by_block.items():
                expected.extend((key, "coverage", index) for index in range(len(groups)))
        if Counter(self.declarations) != Counter(expected):
            raise ValueError("Serialized interface omitted or duplicated a shared declaration")
        if Counter(self.group_sections) != Counter(
            group.section_key for group in (self.request.groups.groups if self.request.groups is not None else ())
        ):
            raise ValueError("Serialized interface omitted or duplicated a content overview")
        expected_backgrounds = {
            group.section_key
            for group in (self.request.groups.groups if self.request.groups is not None else ())
            if group.kind == "background"
        }
        if set(self.background_content) != expected_backgrounds:
            raise ValueError("Serialized background overview lost its complete captured presentation")

    def _write(
        self, key: str, depth: int = 0, context: tuple[tuple[str, str], ...] = (), *, inherited_prefix: bool = False
    ) -> None:
        """Serialize the exact chosen block kind; no grouping is inferred from rendered fields."""
        block = self.document.blocks[key]
        if self.request.groups is not None and any(group.parent_key == key for group in self.request.groups.groups):
            write_grouped_children(self, block, depth, context)
            return
        indent = "\t" * depth
        fields = self.request.fields.fields if self.request.fields is not None else {}
        if key in fields:
            self.write_field(fields[key], depth, context)
            return
        tables = self.request.collections.tables if self.request.collections is not None else {}
        if block.primary_ref in tables:
            self.write_table(tables[block.primary_ref], depth, context)
            return
        if write_geometric_block(self, block, depth, context):
            return
        if block.layout == "compact-list":
            if block.shared_prefix_ref is None:
                raise ValueError("Prepared empty control group lacks its role prefix")
            refs = []
            for child in block.children:
                ref = self.document.blocks[child].primary_ref
                if ref is None:
                    raise ValueError("Prepared empty control group contains a synthetic member")
                self.remember(ref, depth + 1, context)
                refs.append(ref)
            self.lines.append(
                indent + "Elements [" + _role(self.atoms[block.shared_prefix_ref]) + "]: " + reference_set(refs)
            )
            return
        if block.layout == "record-blocks":
            prefix = (
                " [" + _role(self.atoms[block.shared_prefix_ref]) + "]" if block.shared_prefix_ref is not None else ""
            )
            card_rows = {
                row.key for plan in self.document.geometric_patterns if plan.kind == "cards" for row in plan.rows
            }
            caption = "Items" if block.children and set(block.children) <= card_rows else "Records (visual grouping)"
            heading = indent + caption + prefix + ":"
            self.lines.append(heading)
            record_context = (*context, (key, heading))
            for record in block.children:
                self.write(record, depth + 1, record_context, inherited_prefix=block.shared_prefix_ref is not None)
            return
        if block.layout in ("chain", "record-path"):
            cells = []
            member_blocks = []
            member_bodies = []
            current, member_depth = block, depth
            while True:
                if current.primary_ref is None:
                    raise ValueError("A visual chain has no original target")
                self.remember(current.primary_ref, member_depth, context)
                content = self.atom_lines(current.primary_ref, shared=True)
                cells.append(content[0])
                member_bodies.extend(content[1:])
                member_blocks.append(current.key)
                if current.layout not in ("chain", "record-path"):
                    break
                current = self.document.blocks[current.children[0]]
                member_depth += 1
            self.lines.append(indent + " > ".join(cells))
            self.lines.extend(indent + line for line in member_bodies)
            for member_key in member_blocks[1:]:
                self.write_declarations(member_key, depth)
            subtree_start = len(self.lines)
            for child in current.children:
                self.write(child, member_depth + 1, context)
            # The chain already expresses its internal levels through `>`.
            # Keep standalone records at canonical depth for diff, while the
            # full-view subtree starts one display level below the chain line.
            packed_indent = "\t" * (member_depth - depth)
            if packed_indent:
                self.lines[subtree_start:] = [line.removeprefix(packed_indent) for line in self.lines[subtree_start:]]
            return
        if block.layout == "rows":
            if block.shared_prefix_ref is None:
                raise ValueError("Prepared row group has no shared prefix source")
            heading = indent + "Elements [" + _role(self.atoms[block.shared_prefix_ref]) + "]:"
            self.lines.append(heading)
            child_context = (*context, (key, heading))
            for child in block.children:
                self.write(child, depth + 1, child_context, inherited_prefix=True)
            return
        if block.layout == "records":
            headings = [
                (_role(self.atoms[ref]) if ref is not None else label)
                for label, ref in zip(block.columns, block.column_prefix_refs)
            ]
            heading = indent + ("Visual records: " if block.origin == "visual" else "Records: ") + " | ".join(headings)
            self.lines.append(heading)
            matrix = []
            irregular = {}
            row_annotations: dict[str, list[str]] = {}
            for row_key in block.children:
                row = self.document.blocks[row_key]
                row_context = (*context, (row_key, heading))
                cells = [""] * len(block.columns)
                ordered_cells = []
                trailing = []
                slots = row.column_slots or tuple(range(len(row.children)))
                for index, child in zip(slots, row.children):
                    member = self.document.blocks[child]
                    ref = member.primary_ref
                    if ref is None or member.children:
                        raise ValueError("A prepared record column has no independent atomic cell")
                    self.remember(ref, depth + 1, row_context)
                    text, tail = packed_atom(
                        self.atom_lines(
                            ref, short=index is not None and block.column_prefix_refs[index] is not None, shared=True
                        ),
                        ref,
                        depth + 1,
                        readable=self.request.readable is not None,
                    )
                    trailing.extend(tail)
                    ordered_cells.append((index, text))
                    if index is not None:
                        cells[index] = text
                if None in slots:
                    irregular[row_key] = floating_row(ordered_cells, len(block.columns))
                else:
                    matrix.append(cells)
                row_annotations[row_key] = trailing
            rendered = (
                compact_grid(matrix, block.column_alignments)
                if block.column_alignments
                else tuple(" | ".join(cells) for cells in matrix)
            )
            ordinary = iter(rendered)
            for row_key in block.children:
                line = irregular[row_key] if row_key in irregular else next(ordinary)
                self.lines.append(indent + "\t" + line)
                self.lines.extend(row_annotations[row_key])
                self.write_declarations(row_key, depth + 1)
                for child in self.document.blocks[row_key].children:
                    self.write_declarations(child, depth + 1)
                    ref = self.document.blocks[child].primary_ref
                    if self.request.coverage is None or ref not in self.request.coverage.omitted_refs:
                        self.lines.extend(indent + "\t\t" + value for value in self.coverage_lines(self.atoms[ref]))
            return
        if block.layout == "inline":
            prefix = _role(self.atoms[block.shared_prefix_ref]) if block.shared_prefix_ref else None
            heading = indent + ("Elements [" + prefix + "]:" if prefix else "Visual row:")
            if not inherited_prefix:
                self.lines.append(heading)
            child_context = context if inherited_prefix else (*context, (key, heading))
            child_depth = depth if inherited_prefix else depth + 1
            cells = []
            trailing = []
            for child in block.children:
                member = self.document.blocks[child]
                if member.primary_ref is None or member.children:
                    raise ValueError("A prepared inline member is not atomic")
                ref = member.primary_ref
                self.remember(ref, child_depth, child_context)
                head, tail = packed_atom(
                    self.atom_lines(ref, short=prefix is not None, shared=True),
                    ref,
                    child_depth,
                    readable=self.request.readable is not None,
                )
                cells.append(head)
                trailing.extend(tail)
            self.lines.append("\t" * child_depth + " | ".join(cells))
            self.lines.extend(trailing)
            return
        if block.kind == "field" and block.primary_ref is None:
            heading = indent + "Field:"
            if block.label_ref is not None:
                heading = self.write_field_heading(block.label_ref, depth, context)
            else:
                self.lines.append(heading)
            child_context = (*context, (key, heading))
            for child in block.children:
                member = self.document.blocks[child]
                if member.primary_ref == block.label_ref:
                    self.write_declarations(child, depth)
                    for nested in member.children:
                        self.write(nested, depth + 1, child_context)
                else:
                    self.write(child, depth + 1, child_context)
            return
        if block.kind == "region":
            role = {"navigation": "Navigation", "toolbar": "Toolbar"}.get(block.region_role, "Region")
            heading = indent + role
            if block.position:
                heading += " [" + block.position + "]"
            heading += ":"
            label = block.primary_ref or block.label_ref
            if label is not None:
                self.remember(label, depth, context)
                heading += " " + self.atom_lines(label, shared=True)[0]
            self.lines.append(heading)
            if label is not None:
                self.lines.extend(indent + line for line in self.atom_lines(label, shared=True)[1:])
            context = (*context, (key, heading))
            for child in block.children:
                member = self.document.blocks[child]
                if member.primary_ref == label:
                    self.write_declarations(child, depth)
                    for nested in member.children:
                        self.write(nested, depth + 1, context)
                else:
                    self.write(child, depth + 1, context)
            return
        if block.primary_ref is not None:
            self.write_atom(block.primary_ref, depth, context, short=inherited_prefix and block.kind == "record")
            depth += 1
        for child in block.children:
            self.write(child, depth, context)


def format_interface_blocks(request: InterfaceTextRequest) -> FormattedInterface:
    """Render a fully prepared block graph without invoking the legacy line layout pipeline."""
    writer = InterfaceWriter(request)
    for key in request.interface.roots:
        writer.write(key)
    writer.validate_declarations()
    expected = set(request.interface.target_refs)
    if set(writer.nodes) != expected:
        raise ValueError("Serialized interface did not account for every projected target")
    menu_groups: dict[int | None, list[str]] = {}
    for ref in writer.menus:
        menu_groups.setdefault(writer.atoms[ref].coverage.total, []).append(ref)
    suffix = (
        (
            "Menus unexpanded: "
            + "; ".join(
                reference_set(refs) + (f" (0/{total} children)" if total is not None else " (total unknown)")
                for total, refs in menu_groups.items()
            )
            + ".",
        )
        if menu_groups
        else ()
    )
    native_notes = tuple(line for ref in writer.nodes for line in note_lines(writer, ref))
    if native_notes:
        writer.lines.extend(("Native notes (original element references):", *("\t" + line for line in native_notes)))
    prefix = (
        "Fields group visually related controls; each number keeps its original target."
        if request.interface.format_version != "geometric-2"
        and any(block.kind == "field" and block.origin == "visual" for block in request.interface.blocks.values())
        else ""
    )
    if request.atoms.shared_index_basis:
        prefix += ("\n" if prefix else "") + "Index is 0-based."
        if any(slot.key in ("rowRange", "columnRange") for atom in request.atoms.atoms.values() for slot in atom.slots):
            prefix += " Row/Column are 1-based."
    return FormattedInterface(
        request,
        tuple(writer.lines),
        tuple(writer.nodes.values()),
        suffix,
        prefix,
        MappingProxyType(writer.background_content),
    )
