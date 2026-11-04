# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Native stage collect_action_index; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from computer_use_linux.contracts.protocol import require

from ..support import action_matches


def collect_action_index(contract, selected, target, within, ctx, target_service, facts):
    role_required = contract.get("roleRequired", True)
    obj, _, _, role, enabled = target_service.read(target, within, ctx, include_role=role_required)
    require(
        enabled and (not role_required or role == contract["role"]),
        "The original control role or enabled state changed.",
        "input_unavailable",
    )
    match_by = contract.get("matchBy", "name")
    catalogue = facts.action_catalogue(obj, ctx, descriptions=role_required)
    if not role_required and match_by == "description" and catalogue["state"] == "complete":
        catalogue = facts.action_descriptions(obj, catalogue)
    matches = action_matches(catalogue, selected, match_by=match_by)
    require(
        catalogue["state"] == "complete" and len(matches) == 1,
        "The selected action is missing or ambiguous in the current catalogue.",
        "input_unavailable",
    )
    index = matches[0]
    current = catalogue["items"][index]
    if "description" in selected:
        require(
            "descriptionStatus" not in current and current.get("description") == selected["description"],
            "The selected native action description changed.",
            "input_unavailable",
        )
    return index


CONTRACT = {
    "input": ("contract", "selected", "target", "within", "ctx", "target_service", "facts"),
    "output": "original native stage result",
    "effects": ("QUERY",),
    "borrows": ("contract", "selected", "target", "within", "ctx", "target_service", "facts"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
