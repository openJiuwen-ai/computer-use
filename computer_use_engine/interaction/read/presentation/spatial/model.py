# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Stable display relationships derived from local geometry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict


class LayoutGroup(TypedDict):
    """Unnumbered visual row whose members keep their independent identities.

    Attributes:
        kind: Visual grouping form; row denotes locally aligned neighbors.
        members: Ordered references of the independently addressable elements.
    """

    kind: Literal["row"]
    members: tuple[str, ...]


@dataclass(frozen=True)
class LabelMatch:
    """One unambiguous visual caption and its existing control target.

    Attributes:
        label: Source reference contributing the unchanged caption.
        target: Original control reference receiving only presentation text.
        relation: Local spatial evidence explaining the association.
    """

    label: str
    target: str
    relation: Literal["inside", "left", "right", "above"]
