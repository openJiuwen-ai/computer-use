"""PURE: attach_selected_context; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""


def attach_selected_context(target, selected, result):
    if selected is not None:
        result.update(actuator=selected.actuator, basis=selected.basis)
        if selected.actuator["relation"] == "containedEditor":
            result["input"].update(editor=selected.recipient["node"], editorOwner=target["node"])
    return result


CONTRACT = {
    "input": ("target", "selected", "result"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("target", "selected", "result"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
