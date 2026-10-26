"""QUERY/RESOURCE: collect only current-focus evidence for container forwarding."""


def collect_current_receiver(scope, ctx, service):
    return service.inspect(scope.target, scope.request["within"], ctx)


CONTRACT = {
    "input": ("scope", "ctx", "service"),
    "output": "current receiver evidence without primitives",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("scope", "ctx", "service"),
    "writes": ("observed native references",),
    "failure_boundary": "original scope failures propagate; focus gaps stay local; no input or retry",
}
