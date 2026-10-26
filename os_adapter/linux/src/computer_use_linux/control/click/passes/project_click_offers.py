"""Project retained native contracts; no native preparation or input."""

from copy import deepcopy


def project_click_offers(operation, result):
    for primitive in result["primitives"]:
        contract = operation.contracts[primitive["id"]]
        action = primitive["unit"] == "native_action"
        pending = primitive["pending"]
        options = []
        if primitive["supported"] != "yes":
            admission, reason = "unavailable", "The selected native mechanism is unavailable."
        elif primitive["readiness"] == "yes":
            admission, reason = "ready", None
        elif "enabled" in pending:
            admission, reason = "unavailable", "The selected control is disabled."
        elif pending == ["foreground"] and not action:
            admission, reason = "preparationRequired", "The selected point is currently covered."
            options = ["foreground"]
        else:
            admission, reason = (
                "insufficientEvidence",
                "The selected mechanism lacks current geometry or target evidence.",
            )
        offer = dict(
            routeFamily="linux.atspi.action" if action else "linux.x11.xtest",
            interaction="semantic" if action else "pointer",
            delivery="object_action" if action else "desktop_pointer",
            admission=admission,
            reason=reason,
            preparationOptions=options,
            preferenceProfile="default",
        )
        # The same retained offer authorizes dispatch; callers cannot supply or
        # alter a mechanism/target by sending a fresh public record back.
        contract["click"] = deepcopy(offer)
        primitive["click"] = offer
    return result


CONTRACT = {
    "input": ("operation", "result"),
    "output": "input context with click offers",
    "effects": ("RESOURCE",),
    "borrows": ("operation", "result"),
    "writes": ("native operation contracts",),
    "failure_boundary": "no preparation, dispatch, fallback or retry",
}
