"""QUERY/RESOURCE: collect_nodes using only the named query service."""


def collect_nodes(scope, ctx, facts):
    return facts.collect(scope.target, scope.request, ctx)


CONTRACT = {
    "input": ("scope", "ctx", "facts"),
    "output": "native result or original validation result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("scope", "ctx", "facts"),
    "writes": ("original registry during collection",),
    "failure_boundary": "original failure propagation; no retry or input",
}
