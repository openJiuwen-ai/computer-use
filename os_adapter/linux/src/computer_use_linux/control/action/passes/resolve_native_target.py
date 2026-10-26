"""Native stage resolve_native_target; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from computer_use_linux.contracts.protocol import fields, require, string


def resolve_native_target(operation, command):
    fields(command, ("kind", "primitiveId", "target", "foreground"))
    require(
        command["kind"] == "native_action" and command["foreground"] is False,
        "This native action unit does not perform foreground preparation.",
        "invalid_request",
    )
    string(command["primitiveId"])
    contract = operation.contracts.get(command["primitiveId"])
    require(
        contract is not None
        and contract["unit"] == "native_action"
        and contract["usable"]
        and command["target"] == contract["target"],
        "The native action contract expired or changed its target.",
        "stale_target",
    )
    selected = contract["action"]
    if operation.intent["kind"] == "action":
        require(
            operation.intent["parameters"]["action"] in selected.values(),
            "The native action does not match the original request.",
            "invalid_request",
        )
    selected = contract["identity"]
    target, within = contract["target"], contract["within"]
    return contract, selected, target, within


CONTRACT = {
    "input": ("operation", "command"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("operation", "command"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
