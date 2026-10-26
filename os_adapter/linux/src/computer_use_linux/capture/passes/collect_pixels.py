"""QUERY/RESOURCE: capture with the existing immutable image registry."""


def collect_pixels(captures, target, params, ctx):
    return captures.read(target, params["kind"], params["maxDimension"], ctx)


CONTRACT = {
    "input": ("captures", "target", "params", "ctx"),
    "output": "original native stage result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("captures", "target", "params", "ctx"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
