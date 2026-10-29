# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PURE: select_native_unit; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import require


def select_native_unit(operation, command, units):
    contract = operation.contracts.get(command["primitiveId"], {})
    pointer = units.element_pointer if contract.get("elementPointer") is True else units.pointer
    keyboard = units.typed_text if contract.get("typedText") is True else units.keyboard
    unit = {
        "launch_application": units.launch,
        "native_text_edit": units.text,
        "native_action": units.action,
        "prepare_receiver": units.desktop_input
        if contract.get("desktopKeyboard") is True
        else pointer
        if contract.get("unit") == "pointer_sequence"
        else units.paste
        if operation.intent["kind"] == "paste"
        else keyboard,
        "clipboard_paste": units.paste,
        "key_sequence": units.desktop_input if contract.get("desktopKeyboard") is True else keyboard,
        "pointer_sequence": pointer,
    }.get(command["kind"])
    require(unit is not None, "This Linux v5 input unit is not implemented.", "mode_unsupported")
    return unit


CONTRACT = {
    "input": ("operation", "command", "units"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("operation", "command", "units"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
