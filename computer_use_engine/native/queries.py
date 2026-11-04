# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable request shapes for the native observation adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from computer_use.protocol.records import RelationPage


@dataclass(frozen=True)
class NativeNodeQuery:
    """One collection profile, separate from its operation, target and deadline.

    Values retain their original identity until the adapter builds the wire
    request. Construction neither consumes iterables nor performs validation.

    Attributes:
        fields: Native fields requested for each collected object.
        max_nodes: Existing object-count budget for this collection.
        max_depth: Existing relation-depth budget for this collection.
        children_page: Optional continuation of the native children relationship.
        relation_page: Optional continuation of another native relationship.
        advertised_fields: Optional fields to query only when advertised.
        follow_relations: Optional native relationships to collect alongside children.
        focused_fields: Optional fields restricted to focused native objects.
        primary_fields: Optional fields restricted to primary text objects.
        text_content: Optional negotiated, read-only capture of independent native text.
    """

    fields: Iterable[str]
    max_nodes: int
    max_depth: int
    children_page: RelationPage | None = None
    relation_page: RelationPage | None = None
    advertised_fields: Iterable[str] | None = None
    follow_relations: Iterable[str] | None = None
    focused_fields: Iterable[str] | None = None
    primary_fields: Iterable[str] | None = None
    text_content: bool | None = None
