"""RESOURCE: register_observed_objects; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""


def register_observed_objects(scope, result):
    if scope.operation.writable and scope.kind in ("nodes", "relation"):
        scope.operation.selection_facts.retain(result)


CONTRACT = {
    "input": ("scope", "result"),
    "output": "original native stage result",
    "effects": ("RESOURCE",),
    "borrows": ("scope", "result"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
