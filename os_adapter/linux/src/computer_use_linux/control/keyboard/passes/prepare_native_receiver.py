"""Native stage prepare_native_receiver; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""


def prepare_native_receiver(r, ctx, preparation, desktop, target_service):
    if r["can_grab"] and not r["ready"]:
        desktop.require_idle_input()
        ctx.sent()
        preparation.append("nativeFocus")
        # This is addressed preparation; it is not the requested key.
        target_service.call("Component", "grab_focus", r["obj"])


CONTRACT = {
    "input": ("r", "ctx", "preparation", "desktop", "target_service"),
    "output": "original native stage result",
    "effects": ("PREPARE", "QUERY"),
    "borrows": ("r", "ctx", "preparation", "desktop", "target_service"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
