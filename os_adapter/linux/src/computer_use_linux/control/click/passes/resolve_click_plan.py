"""Resolve only the explicitly selected, retained click contract."""

from computer_use_linux.contracts.protocol import require

from ..model import SelectedClickPlan


def resolve_click_plan(operation, command, action, pointer, element_pointer):
    require(
        operation.click_contract == "v1" and operation.intent["kind"] == "click",
        "Native click v1 requires an explicitly enabled click operation.",
    )
    require(
        command["kind"] in ("native_action", "pointer_sequence"),
        "Click v1 prepares and dispatches in one execution; prepare_receiver is not valid.",
    )
    contract = operation.contracts.get(command["primitiveId"])
    require(
        contract is not None
        and contract["target"] == command["target"]
        and contract["unit"] == command["kind"]
        and "click" in contract,
        "The retained native click contract expired or changed target.",
        "stale_target",
    )
    offer = contract["click"]
    unit = (
        action
        if command["kind"] == "native_action"
        else (element_pointer if contract.get("elementPointer") is True else pointer)
    )
    require(
        not command["foreground"] or "foreground" in offer["preparationOptions"],
        "The selected click offer does not permit foreground preparation.",
    )
    return SelectedClickPlan(contract, unit, offer["routeFamily"], offer["delivery"], command["foreground"])


CONTRACT = {
    "input": ("operation", "command", "action", "pointer", "element_pointer"),
    "output": "SelectedClickPlan",
    "effects": ("PURE",),
    "borrows": ("operation", "command", "action", "pointer", "element_pointer"),
    "writes": (),
    "failure_boundary": "reject a changed contract before preparation or input",
}
