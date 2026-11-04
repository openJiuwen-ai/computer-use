# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture complete source evidence without rendering fields or touching native state."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.actions.catalogue import capture_catalogues
from computer_use.interaction.read.presentation.actions.control_raises import capture_control_raises
from computer_use.interaction.read.presentation.actions.menu_mechanisms import capture_menu_mechanisms
from computer_use.interaction.read.presentation.compact import display_actions
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.compression.policy import secondary_actions
from .model import SharingEvidence

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.model import BranchFacts
    from computer_use.interaction.read.presentation.records import NodeView


def capture_sharing_evidence(branch: BranchFacts, projection: ProjectionResult, atoms: AtomDocument) -> SharingEvidence:
    """Retain native target/availability/shortcut records omitted by normalized action labels.

    Catalogue labels are raw label scalars for equality evidence; this function
    does not format application text. Final sharing also checks the selected
    typed atom actions against their original native catalogue names.
    """
    snapshot = branch.request.snapshot
    menu = capture_menu_mechanisms(snapshot, projection)
    raises = capture_control_raises(snapshot, projection)

    def labels(node: NodeView) -> list[str]:
        return [
            action.get("description") or action["name"]
            for action in display_actions(node, secondary_actions(node), menu_mechanisms=menu, control_raises=raises)
        ]

    catalogues = capture_catalogues(
        snapshot.native,
        snapshot.refs,
        branch.native_nodes,
        projection["roots"],
        tuple((owner, projection["nativeNodes"][ref]) for ref, owner in projection["foldedInto"].items()),
        labels,
        source_scopes=projection.source.scopes,
        menu_mechanisms=menu,
        control_raises=raises,
    )
    # Explicit subtree/noncompact captures may have a different selected action
    # list; they cannot borrow a proof for a whole-view display filter.
    compatible = {
        ref: source
        for ref, source in catalogues.items()
        if ref in atoms.atoms
        and tuple(action.name for action in atoms.atoms[ref].actions) == source.names
        and all(action.source_ref == ref for action in atoms.atoms[ref].actions)
    }
    compression = projection.source.context.get("compressionEvidence")
    complete = set(compression.complete_text if isinstance(compression, CompressionEvidence) else ())
    # Compression's caption proof deliberately covers only name/description/value.
    # Help sharing uses its own exact native path and field completeness proof.
    for (ref, field), source in snapshot.fields.items():
        attribute = source.get("field")
        token = snapshot.refs.get(ref)
        record = branch.native_nodes.get(token)
        native = snapshot.native.nodes.get(token)
        if (
            attribute != "help"
            or tuple(source.get("path", ())) != ("help",)
            or native is None
            or record is None
            or record.get("observed") is False
            or type(native.help) is not str
            or record["values"].get(field) != native.help
            or any(
                key == field or key.startswith(field + ".") or field.startswith(key + ".")
                for key in record["fieldStatus"]
            )
        ):
            continue
        complete.add((ref, attribute))
    # Native selections may remain outside normalized selectionRange when the
    # provider omitted its indexing unit. Preserve actual or unknown selection
    # context, while recognizing the complete, collapsed zero-range metadata
    # reported on passive nodes by some providers.
    protected_values = set()
    for ref, token in snapshot.refs.items():
        native = snapshot.native.nodes.get(token)
        if native is None:
            continue
        states, text = native.states, native.text
        spans = text.selections if text is not None else None
        selection_context = bool(spans) and (
            len(spans) != 1
            or any(
                type(span.start) is not int
                or type(span.end) is not int
                or span.start != 0
                or span.end != 0
                or span.collapsed is not True
                for span in spans
            )
        )
        if (
            any(getattr(states, name) is True for name in ("focused", "selected", "editable", "protected"))
            or text is not None
            and (selection_context or bool(text.selected_text) or text.caret_active is True or text.read_only is False)
            or native.control_value is not None
            and native.control_value.read_only is False
            or native.numeric_value is not None
            and native.numeric_value.read_only is False
        ):
            protected_values.add(ref)
    return SharingEvidence(
        MappingProxyType(compatible),
        frozenset(complete),
        compression.contested_sources | compression.splice_conflicts
        if isinstance(compression, CompressionEvidence)
        else frozenset(),
        frozenset(protected_values),
    )
