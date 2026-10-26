"""QUERY: collect_captured_text using only the named query service."""


def collect_captured_text(scope, session, text_page):
    request = scope.request
    return dict(
        kind="capturedText",
        session=session,
        textRef=request["textRef"],
        page=text_page(request["textRef"], request["offset"], request["length"]),
    )


CONTRACT = {
    "input": ("scope", "session", "text_page"),
    "output": "native result or original validation result",
    "effects": ("QUERY",),
    "borrows": ("scope", "session", "text_page"),
    "writes": (),
    "failure_boundary": "original failure propagation; no retry or input",
}
