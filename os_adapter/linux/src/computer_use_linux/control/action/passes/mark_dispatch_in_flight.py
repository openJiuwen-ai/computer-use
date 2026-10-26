"""Native stage mark_dispatch_in_flight; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""


def mark_dispatch_in_flight(operation, ctx):
    ctx.sent()
    operation.dispatched = True


CONTRACT = {
    "input": ("operation", "ctx"),
    "output": "original native stage result",
    "effects": ("RESOURCE",),
    "borrows": ("operation", "ctx"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
