# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Annotate busy content."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.loading import busy_nodes
from computer_use.interaction.read.acquisition.model import AcquiredObservation
from computer_use.interaction.read.facts.groups import reference_set
from computer_use.interaction.read.numbering.observation.model import PURE
from computer_use.pipeline import Contract, Pass

if TYPE_CHECKING:
    from computer_use.pipeline.core import Control


def add_busy_gap(acquired: AcquiredObservation, control: Control) -> AcquiredObservation:
    """Append a content gap identifying the numbered elements that report native busy state."""
    tree = acquired.components.tree
    busy = busy_nodes(tree)
    if not busy:
        return acquired
    references = reference_set(ref for ref, node in tree.refs.items() if node in set(busy))
    gap = ObservationGap(
        "content",
        "observation_incomplete",
        "The provider reports elements " + references + " are busy; their contents may still change.",
        "use the available regions; read again when the loading region is needed",
    )
    return replace(acquired, gaps=acquired.gaps + (gap,))


stage = Pass.define(
    name="annotate_busy_content",
    function=add_busy_gap,
    contract=Contract(AcquiredObservation, AcquiredObservation, PURE),
)
