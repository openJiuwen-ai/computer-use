# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Create a local typed declaration plan for one finalized interface."""

from computer_use.pipeline import Contract, Effect, Pass
from computer_use.pipeline.core import Control
from ..model import SharingDraft, SharingRequest


def execute(request: SharingRequest, control: Control) -> SharingDraft:
    """Retain immutable inputs without changing atomic fields or target bindings."""
    if set(request.interface.target_refs) != set(request.atoms.atoms):
        raise ValueError("Sharing requires matching interface and atom sources")
    return SharingDraft(request)


stage = Pass.define(
    name="initialize_interface_sharing",
    function=execute,
    contract=Contract(
        SharingRequest,
        SharingDraft,
        {Effect.PURE},
        requires=("final interface and atom identities",),
        writes=("local declaration plan",),
    ),
)
