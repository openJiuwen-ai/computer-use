"""QUERY/RESOURCE: collect_surfaces using only the named query service."""


def collect_surfaces(scope, ctx, directory):
    return directory.read(scope.request["application"], ctx)


CONTRACT = {
    "input": ("scope", "ctx", "directory"),
    "output": "native result or original validation result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("scope", "ctx", "directory"),
    "writes": ("original registry during collection",),
    "failure_boundary": "original failure propagation; no retry or input",
}
