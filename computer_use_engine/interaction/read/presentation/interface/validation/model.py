# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed decisions for locally recoverable presentation-layout failures."""

from __future__ import annotations

from dataclasses import dataclass

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft


@dataclass(frozen=True)
class LayoutIssue:
    """A format-plan failure whose source identities have already been validated."""

    block_key: str
    reason: str


@dataclass(frozen=True)
class LayoutAssessment:
    """Validated source graph and isolated layout failures for the recovery gate."""

    draft: InterfaceDraft
    issues: tuple[LayoutIssue, ...]
