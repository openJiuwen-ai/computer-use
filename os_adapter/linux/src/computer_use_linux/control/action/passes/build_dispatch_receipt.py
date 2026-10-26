"""Native stage build_dispatch_receipt; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""


def build_dispatch_receipt(target, dispatch, detail, error_code):
    return dict(
        target=dict(target),
        dispatch=dispatch,
        detail=detail,
        preparation=[],
        confirmation="unavailable",
        cleanup="complete",
        errorCode=error_code,
        retryReason=None,
        context=None,
    )


CONTRACT = {
    "input": ("target", "dispatch", "detail", "error_code"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("target", "dispatch", "detail", "error_code"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
