# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native stage resolve_mutation_recipient; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from __future__ import annotations


def resolve_mutation_recipient(contract, ctx, target_service):
    target, within = contract["target"], contract["within"]
    recipient, constraints = target_service.selected(
        target, within, contract.get("selection"), ctx, check=True, require_editable=True
    )
    target_service.validate_scope(recipient, constraints, ctx)


CONTRACT = {
    "input": ("contract", "ctx", "target_service"),
    "output": "original native stage result",
    "effects": ("QUERY",),
    "borrows": ("contract", "ctx", "target_service"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
