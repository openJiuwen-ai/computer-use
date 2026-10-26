"""Native stage observe_preparation; bounded explicit dependencies, no route fallback.

Effect and failure behavior remain the original native stage contract.
"""

import time


def observe_preparation(contract, ctx, guard):
    until = min(ctx.deadline, time.monotonic() + 0.4)
    while True:
        r = guard(contract, ctx)
        if r["ready"]:
            return True
        if time.monotonic() >= until:
            break
        ctx.pause(0.02)
    ctx.check()
    return False


CONTRACT = {
    "input": ("contract", "ctx", "guard"),
    "output": "original native stage result",
    "effects": ("QUERY",),
    "borrows": ("contract", "ctx", "guard"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
