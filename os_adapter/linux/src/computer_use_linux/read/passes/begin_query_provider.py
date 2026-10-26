"""QUERY: begin_query_provider; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""


def begin_query_provider(scope, ctx, ax):
    ctx.check()
    if not scope.launch and scope.kind != "capturedText" and scope.target["capture"] is None:
        ax.begin(ctx)


CONTRACT = {
    "input": ("scope", "ctx", "ax"),
    "output": "original native stage result",
    "effects": ("QUERY",),
    "borrows": ("scope", "ctx", "ax"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
