"""Native stage check_dispatch_conditions; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from __future__ import annotations

from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.control.native_text_codec import text_codec


def check_dispatch_conditions(contract, ctx, guard, ax):
    target, within, kind = contract["target"], contract["within"], contract["kind"]
    selection = contract.get("selection")
    obj, enabled, editable, interfaces, _ = guard(target, within, ctx, selection, check=True)
    if not enabled or not editable or kind == "insert" and "Text" not in interfaces:
        raise Failure("The original control no longer supports this native text operation.", "input_unavailable")
    if kind == "insert" and text_codec(ax, obj) != contract["codec"]:
        raise Failure("The native text provider changed before insertion.", "stale_target")
    return obj


CONTRACT = {
    "input": ("contract", "ctx", "guard", "ax"),
    "output": "original native stage result",
    "effects": ("QUERY",),
    "borrows": ("contract", "ctx", "guard", "ax"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
