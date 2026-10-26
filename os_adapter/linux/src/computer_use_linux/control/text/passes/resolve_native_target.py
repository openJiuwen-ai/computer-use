"""Native stage resolve_native_target; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from __future__ import annotations

from computer_use_linux.contracts.protocol import fields, require, string


def resolve_native_target(operation, command):
    fields(command, ("kind", "primitiveId", "target", "foreground"))
    require(
        command["kind"] == "native_text_edit" and command["foreground"] is False,
        "The selected native text contract does not acquire the foreground.",
        "invalid_request",
    )
    string(command["primitiveId"])
    contract = operation.contracts.get(command["primitiveId"])
    require(
        contract is not None and contract["unit"] == "native_text_edit" and command["target"] == contract["target"],
        "The native text contract expired or changed its target.",
        "stale_target",
    )
    return contract


CONTRACT = {
    "input": ("operation", "command"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("operation", "command"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
