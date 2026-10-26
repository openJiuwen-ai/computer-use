"""PURE: validate the original capture request and its read lease."""

from computer_use_linux.contracts.protocol import fields, integer, require, string


def resolve_capture_scope(params, session, current, decode_binding):
    fields(params, ("operation", "target", "kind", "maxDimension"))
    string(params["operation"])
    target = decode_binding(params["target"], session)
    require(params["kind"] in ("window", "desktop"), "Invalid capture kind.")
    integer(params["maxDimension"], 1, 4096)
    require(
        "application" not in target
        and target["node"] is None
        and target["capture"] is None
        and (target["surface"] is not None) == (params["kind"] == "window"),
        "Capture needs a window or desktop scope, not an element.",
    )
    operation = current
    require(
        operation is not None and operation.token == params["operation"], "Native operation expired.", "stale_target"
    )
    require(
        not operation.writable and target == operation.target, "Capture requires the operation's original read scope."
    )
    return operation, target


CONTRACT = {
    "input": ("params", "session", "current", "decode_binding"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("params", "session", "current", "decode_binding"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
