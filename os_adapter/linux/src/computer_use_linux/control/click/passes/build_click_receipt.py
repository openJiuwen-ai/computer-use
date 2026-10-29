# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Project native call evidence without upgrading it to widget consumption."""


def build_click_receipt(operation, plan, ledger, result):
    result["dispatch"] = ledger.dispatch
    result["preparation"] = ["foreground" for row in ledger.preparation if row["kind"] == "activation"]
    semantic = plan.delivery == "object_action"
    if ledger.calls_entered:
        submitted = (1 if ledger.action_accepted is True else None) if semantic else ledger.calls_returned
        transport = dict(
            kind="nativeAction" if semantic else "xtest",
            callEntered=True,
            callReturned=ledger.calls_pending == 0,
            submitted=submitted,
            expected=ledger.expected_requests,
        )
    else:
        transport = dict(kind="none", callEntered=False, callReturned=False, submitted=None, expected=None)
    ambiguous_preparation = any(
        row["effect"] != "contextOnly" or row["continuation"] == "observe" for row in ledger.preparation
    )
    if ledger.dispatch != "notDispatched" or ambiguous_preparation:
        continuation = "observe"
        result["retryReason"] = None
    elif result["cleanup"] != "complete":
        continuation = "stop"
        result["retryReason"] = None
    elif result.get("retryReason") == "preconditionChanged":
        continuation = "sameCandidateRefresh"
    elif result.get("retryReason") is not None:
        continuation = "reselectWithinScope"
    else:
        continuation = "stop"
    result["click"] = dict(
        preparationEvidence=ledger.preparation,
        transportEvidence=transport,
        continuation=dict(kind=continuation, target=dict(plan.contract["target"]), routeFamily=plan.route_family),
    )
    return result


CONTRACT = {
    "input": ("operation", "plan", "ledger", "result"),
    "output": "v1 click receipt",
    "effects": ("PURE",),
    "borrows": ("operation", "plan", "ledger", "result"),
    "writes": (),
    "failure_boundary": "evidence only, no query or input",
}
