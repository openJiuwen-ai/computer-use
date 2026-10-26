"""QUERY/RESOURCE: collect_applications using only the named query service."""


def collect_applications(ctx, directory):
    return directory.applications(ctx)


CONTRACT = {
    "input": ("ctx", "directory"),
    "output": "native result or original validation result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("ctx", "directory"),
    "writes": ("original registry during collection",),
    "failure_boundary": "original failure propagation; no retry or input",
}
