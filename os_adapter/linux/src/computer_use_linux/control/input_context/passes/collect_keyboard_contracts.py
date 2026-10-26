"""QUERY/RESOURCE: collect_keyboard_contracts; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import Failure


def collect_keyboard_contracts(scope, options, result, ctx, typed_text):
    operation, target, request = scope.operation, scope.target, scope.request
    # Declare independent object and keyboard contracts. The
    # common policy owns preference, preparation and fallback.
    try:
        keyboard = typed_text.inspect(operation, target, request["within"], ctx, **options)
    except (Failure, typed_text.ax.GLib.Error) as error:
        ctx.check()
        # Optional receiver/desktop observations cannot erase
        # an independently established object edit. Cancellation,
        # stale identity and modal/scope errors still propagate;
        # execution retains the object's normal final guards.
        object_contract = any(
            p["unit"] == "native_text_edit" and p["channel"] == "object" and p["supported"] == "yes"
            for p in result["primitives"]
        )
        local_failure = not isinstance(error, Failure) or (
            error.effect == "none"
            and error.code in ("input_unavailable", "mode_unsupported", "observation_incomplete", "ambiguous_target")
        )
        if not local_failure or not object_contract or ctx.effect != "none":
            raise
        result["unavailableReason"] = (
            "The optional keyboard query is unavailable; the independent object edit remains available."
        )
    else:
        keyboard["primitives"] = result["primitives"] + keyboard["primitives"]
        result = keyboard
    return result


CONTRACT = {
    "input": ("scope", "options", "result", "ctx", "typed_text"),
    "output": "original native stage result",
    "effects": ("QUERY", "RESOURCE"),
    "borrows": ("scope", "options", "result", "ctx", "typed_text"),
    "writes": ("original operation Owner/registry",),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
