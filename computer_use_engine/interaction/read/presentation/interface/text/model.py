# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed inputs and outputs at the final interface text boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.interface.readable.model import ReadablePlan
    from computer_use.interaction.read.presentation.preparation import CapturedSnapshot
    from computer_use.interaction.read.presentation.interface.disclosure.groups.model import GroupDisclosurePlan
    from computer_use.interaction.read.presentation.interface.sharing.text_slices.model import TextSliceDocument
    from computer_use.interaction.read.presentation.interface.coverage.model import CoveragePlan
    from computer_use.interaction.read.presentation.interface.sharing.model import SharingPlan
    from computer_use.interaction.read.presentation.interface.composition.content.model import FieldContentPlan
    from computer_use.interaction.read.presentation.interface.collections.model import CollectionDocument
    from computer_use.interaction.read.presentation.interface.atoms.model import AtomDocument
    from computer_use.interaction.read.presentation.interface.disclosure.model import DisclosurePlan
    from computer_use.interaction.read.presentation.interface.model import InterfaceDocument
    from computer_use.interaction.read.presentation.ir import ProjectionResult
    from computer_use.interaction.read.presentation.rendering.output import RenderedNode


@dataclass(frozen=True)
class InterfaceTextRequest:
    """Frozen content and layout selected before full/diff delivery.

    Attributes:
        interface: Validated block graph with exact original source references.
        atoms: Typed role, state and field slots, with their source dispositions.
        projection: Retained source projection for accounting and explicit element reads.
        disclosure: Preselected excerpts with same-observation detail recovery.
        readable: Frozen status, field-heading and native-note placement decisions.
    """

    interface: InterfaceDocument
    atoms: AtomDocument
    projection: ProjectionResult
    disclosure: DisclosurePlan | None = None
    collections: CollectionDocument | None = None
    fields: FieldContentPlan | None = None
    sharing: SharingPlan | None = None
    coverage: CoveragePlan | None = None
    text_slices: TextSliceDocument | None = None
    groups: GroupDisclosurePlan | None = None
    snapshot: CapturedSnapshot | None = None
    readable: ReadablePlan | None = None


@dataclass(frozen=True)
class FormattedInterface:
    """Serialized blocks and their independent nodes for contextual diff delivery."""

    request: InterfaceTextRequest
    lines: tuple[str, ...]
    nodes: tuple[RenderedNode, ...]
    suffix: tuple[str, ...] = ()
    prefix: str = ""
    background_content: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
