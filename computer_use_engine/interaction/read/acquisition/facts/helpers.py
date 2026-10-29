# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.facts.model import FactProfile, FactScope
from computer_use.interaction.read.facts.shortcuts import MACOS_MENU_SHORTCUT_FIELDS
from computer_use.native.semantics import (
    FIELDS,
    RAW_NODE_METADATA_FIELDS,
    TABLE_FIELDS,
    WINDOWS_ON_DEMAND_FIELDS,
    WINDOWS_READ_V2_FIELDS,
    WINDOWS_TEXT_CONTEXT_FIELDS,
    WINDOWS_TEXT_LINE_FIELDS,
    followed_relations,
)

if TYPE_CHECKING:
    from typing import Callable

    from computer_use.interaction.read.acquisition.facts.model import FactReadRequest, NativeReadCapabilities
    from computer_use.native.contracts import Binding
    from computer_use.pipeline.core import Control


def validate_fact_scope(target: Binding, max_nodes: int, max_depth: int | None) -> None:
    """Validate the requested native node against the admitted read scope.

    Args:
        target: Native identity within the current application and window scope.
        max_nodes: Maximum number of native objects this collection may read.
        max_depth: Maximum native relation depth; None uses the existing platform default.
    """
    if target.node is None:
        raise ComputerUseError(
            dict(
                code="observation_incomplete",
                effect="none",
                target="native observation",
                detail=(
                    "This surface has no accessible root; its window identity remains available for visual observation."
                ),
                next="use a supported window capture route; do not infer that the window has no content",
            )
        )
    invalid_max_nodes = (
        True
        # Exact types preserve scalar validation and identity contracts.
        if (
            type(max_nodes) is not int  # pylint: disable=unidiomatic-typecheck
            or not 1 <= max_nodes <= 2000
            or max_depth is not None
            # pylint: disable-next=unidiomatic-typecheck
            and (type(max_depth) is not int or not 0 <= max_depth <= 2000)
        )
        else False
    )
    if invalid_max_nodes:
        raise ValueError("Invalid native read budget")


def resolve_fact_scope(
    request: FactReadRequest,
    control: Control,
    *,
    validate: Callable[[Binding, int, int | None], None] = validate_fact_scope,
) -> FactScope:
    """Resolve the read binding and requested relation-page scope.

    Args:
        request: A bounded native graph read within an already admitted operation.
        validate: Bound dependency for validate; invoked only at its existing stage.
    """
    read, node = request.read, request.node
    if read.target.capture is not None or node is not None and read.target.node not in (None, node):
        raise ValueError("Node collection cannot change an existing node or image scope")
    target = replace(read.target, node=node if node is not None else read.target.node)
    validate(target, request.max_nodes, request.max_depth)
    return FactScope(request, target)


def plan_fact_profile(scope: FactScope, capabilities: NativeReadCapabilities) -> FactProfile:
    """Pure capability-based profile planning; never spends a read budget.

    Args:
        scope: The exact native scope resolved from a fact-read request.
    """
    request = scope.request
    fields, max_nodes, max_depth = request.fields, request.max_nodes, request.max_depth
    children_page, relation_page = request.children_page, request.relation_page
    platform, features = capabilities.platform, capabilities.features
    if max_depth is None:
        # A path through N distinct collected objects has at most N-1
        # edges. Native collectors are iterative and deduplicate identity;
        # the node/time budgets already bound deep layout hierarchies.
        max_depth = max_nodes - 1 if "nativeNodeDepthBudget" in features else 40
    elif max_depth > 40 and "nativeNodeDepthBudget" not in features:
        raise ValueError("This driver supports an explicit depth of at most 40")
    options: Record = {} if children_page is None else dict(children_page=children_page)
    if relation_page is not None:
        options["relation_page"] = relation_page
    # A root's children page and its independent content relationships
    # are separate read dimensions. Keep the same bounded content profile
    # for App.read and Element.read, including continued children pages.
    if fields is None:
        relations = followed_relations(platform, features)
        if relations:
            options["follow_relations"] = list(relations)
        # Popup ownership is operation-specific receiver evidence. Query
        # it for a selected editor, not every node of a display read (or
        # older drivers which do not implement nativeVirtualFocus).
        fields = [key for key, semantic in FIELDS[platform].items() if semantic != "hasPopup"]
        if platform == "windows" and "nativeUiaReadV2" not in features:
            fields = [key for key in fields if key not in WINDOWS_READ_V2_FIELDS]
        if platform == "windows":
            fields = [key for key in fields if key not in WINDOWS_ON_DEMAND_FIELDS]
            if "nativeUiaTextLines" not in features:
                fields = [key for key in fields if key not in WINDOWS_TEXT_LINE_FIELDS]
            else:
                options["primary_fields"] = ["Text.LineRanges"]
            if "nativeUiaTextContext" not in features:
                fields = [key for key in fields if key not in WINDOWS_TEXT_CONTEXT_FIELDS]
            else:
                options["focused_fields"] = ["Text.CaretRange"]
            if "nativeWindowReadiness" not in features:
                fields = [key for key in fields if key != "Win32.ClientRectangle"]
            if "nativeWindowContent" not in features:
                fields = [key for key in fields if key != "Win32.WindowContentScope"]
        fields += ["AXDocument"] if platform == "macos" else []
        if platform == "linux" and "nativeTableFacts" not in features:
            fields = [key for key in fields if key != "visible"]
        if not {"nativeBusyFacts", "nativeAdvertisedFields"} <= set(features):
            fields = [key for key in fields if FIELDS[platform].get(key) != "busy"]
        else:
            options["advertised_fields"] = [key for key in fields if FIELDS[platform].get(key) == "busy"]
        if platform == "macos" and "nativeActuators" not in features:

            def _iter_key(_source_items: Iterator[Any]) -> Iterator[Any]:
                for key in _source_items:
                    if FIELDS[platform].get(key) not in (
                        "editable",
                        "readOnly",
                        "focusWritable",
                        "valueWritable",
                        "textWritable",
                    ):
                        yield key

            fields = list(_iter_key(iter(fields)))
        if "nativeScroll" not in features:
            fields = [key for key in fields if FIELDS[platform].get(key) != "orientation"]
        if not {"nativeTableFacts", "nativeAdvertisedFields"} <= set(features):
            fields = [key for key in fields if FIELDS[platform].get(key) not in TABLE_FIELDS]
        else:
            # Native providers may return successful zero defaults for
            # properties they do not advertise. Query declarations, not
            # an application/role allowlist. Explicit profiles stay raw.
            advertised = [key for key in fields if FIELDS[platform].get(key) in TABLE_FIELDS]
            if advertised:
                options.setdefault("advertised_fields", []).extend(advertised)
        if platform == "macos" and {"nativeMenuShortcuts", "nativeAdvertisedFields"} <= set(features):
            fields.extend(MACOS_MENU_SHORTCUT_FIELDS)
            # Reuse the provider's declared attributes, avoiding getters
            # for menu-only properties on unrelated document controls.
            options.setdefault("advertised_fields", []).extend(MACOS_MENU_SHORTCUT_FIELDS)
        if "nativeRawNodeMetadata" in features:
            fields.extend(RAW_NODE_METADATA_FIELDS[platform])
    return FactProfile(scope, fields, max_depth, options)


if TYPE_CHECKING:
    from computer_use.protocol.records import Record
