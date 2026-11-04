# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Immutable evidence for a single control and its coincident source wrappers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompoundControl:
    """One proved native single-child chain containing exactly one semantic control.

    Attributes:
        outer_key: Original source block at the top of the chain.
        target_ref: Unique button, link, tab or other supported semantic control.
        members: All original block keys in native parent-to-child order.
        member_refs: Exact source reference of each member, with no new input alias.
        label_ref: A contained final text caption, when its content is available.
        evidence: Captured ownership and geometry proof used by the composition.
    """

    outer_key: str
    target_ref: str
    members: tuple[str, ...]
    member_refs: tuple[str, ...]
    label_ref: str | None = None
    evidence: tuple[str, ...] = ()
