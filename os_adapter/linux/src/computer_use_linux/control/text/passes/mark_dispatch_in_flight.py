"""Native stage mark_dispatch_in_flight; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

from __future__ import annotations


def mark_dispatch_in_flight(operation, ctx, dispatch):
    ctx.sent()
    operation.dispatched = True  # Barrier precedes every possible provider effect.
    return "unknown" if dispatch == "notDispatched" else dispatch


CONTRACT = {
    "input": ("operation", "ctx", "dispatch"),
    "output": "original native stage result",
    "effects": ("RESOURCE",),
    "borrows": ("operation", "ctx", "dispatch"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
