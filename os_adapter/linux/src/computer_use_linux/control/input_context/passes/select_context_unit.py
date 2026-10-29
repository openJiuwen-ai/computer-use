# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PURE: select_context_unit; only explicitly borrowed capabilities are available.

Input/output follow the native stage contract. Failures propagate unchanged;
this leaf never selects another route, retries or acquires another budget.
"""

from computer_use_linux.contracts.protocol import require


def select_context_unit(scope, units, capabilities):
    operation, target, request = scope.operation, scope.target, scope.request
    unit = (
        units.desktop_input
        if operation.target["capture"] is not None and operation.intent["kind"] in ("key", "insert")
        else units.pointer
        if operation.target["capture"] is not None
        else units.paste
        if operation.intent["kind"] == "paste"
        else units.keyboard
        if operation.intent["kind"] in ("focus", "key")
        else (units.text if target["node"] is not None else units.typed_text)
        if operation.intent["kind"] in ("insert", "replace")
        else units.element_pointer
        if operation.intent["kind"] == "scroll"
        else units.action
    )
    if "actuator" in request:
        if request["actuator"]["relation"] == "controlPart":
            require(
                operation.intent["kind"] == "click"
                and target["capture"] is None
                and "nativeControlParts" in capabilities,
                "A control part requires an addressed element click and native pointer input.",
            )
            unit = units.element_pointer
        elif request["actuator"]["relation"] == "scrollPointer":
            require(
                operation.intent["kind"] == "scroll"
                and target["capture"] is None
                and "nativeElementScroll" in capabilities,
                "A scroll member requires an addressed element scroll and native pointer input.",
            )
            require(
                "contentPath" not in request["actuator"] or "nativeScrollContentPath" in capabilities,
                "Moving scroll content requires its native capability.",
            )
            unit = units.element_pointer
        else:
            require(
                operation.intent["kind"] in ("focus", "key", "insert", "replace", "paste"),
                "An internal text editor cannot change this operation intent.",
            )
    return unit


CONTRACT = {
    "input": ("scope", "units", "capabilities"),
    "output": "original native stage result",
    "effects": ("PURE",),
    "borrows": ("scope", "units", "capabilities"),
    "writes": (),
    "failure_boundary": "propagate original native failure; never fallback or retry",
}
