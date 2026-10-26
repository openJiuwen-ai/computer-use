"""Native stage check_dispatch_conditions; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from computer_use_linux.contracts.protocol import require


def check_dispatch_conditions(contract, selected, target, within, index, ctx, target_service):
    # The catalogue query can run callbacks. Check the original target
    # again, then the current index immediately before its native call.
    obj, _, _, role, enabled = target_service.read(target, within, ctx)
    require(enabled and role == contract["role"], "The original action target changed.", "input_unavailable")
    require(
        target_service.call("Action", "get_action_name", obj, index) == selected["name"],
        "The action index changed before dispatch.",
        "input_unavailable",
    )
    if "description" in selected:
        require(
            target_service.call("Action", "get_action_description", obj, index) == selected["description"],
            "The action description changed before dispatch.",
            "input_unavailable",
        )
    target_service.validate_scope(target, within, ctx)
    return obj


CONTRACT = {
    "input": ("contract", "selected", "target", "within", "index", "ctx", "target_service"),
    "output": "original native stage result",
    "effects": ("QUERY",),
    "borrows": ("contract", "selected", "target", "within", "index", "ctx", "target_service"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
