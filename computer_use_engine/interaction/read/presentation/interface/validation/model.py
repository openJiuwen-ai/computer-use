# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Typed decisions for locally recoverable presentation-layout failures."""

from __future__ import annotations

from dataclasses import dataclass

from computer_use.interaction.read.presentation.interface.model import InterfaceDraft


@dataclass(frozen=True)
class LayoutIssue:
    """A format-plan failure whose source identities have already been validated.

    Attributes:
        block_key: Internal layout identity of the affected block.
        reason: Source-specific explanation for the selected decision or coverage gap.
    """

    block_key: str
    reason: str


@dataclass(frozen=True)
class LayoutAssessment:
    """Validated source graph and isolated layout failures for the recovery gate.

    Attributes:
        draft: The exact local block draft assessed by the validation stage.
        issues: Structured local validation failures with their affected block identities.
    """

    draft: InterfaceDraft
    issues: tuple[LayoutIssue, ...]
