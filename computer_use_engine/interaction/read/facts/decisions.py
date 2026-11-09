# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Complete decision fields that can be displayed with their original control."""

from collections.abc import Mapping
from typing import Any

from computer_use.protocol.primitives import finite

_DECISIONS = {
    "numeric_value.minimum": ("rangeMinimum", "Minimum", "number"),
    "numeric_value.maximum": ("rangeMaximum", "Maximum", "number"),
    "numeric_value.small_step": ("rangeSmallStep", "Small step", "number"),
    "numeric_value.large_step": ("rangeLargeStep", "Large step", "number"),
    "numeric_value.allowed_values": ("rangeAllowedValues", "Allowed values", "literal"),
    "states.has_popup": ("hasPopup", "Has popup", "literal"),
    "states.protected": ("protected", "Protected", "literal"),
    "states.offscreen": ("offscreen", "Offscreen", "literal"),
    "structure.can_select_multiple": ("canSelectMultiple", "Multiple selection", "literal"),
    "structure.selection_required": ("selectionRequired", "Selection required", "literal"),
}
DECISION_SLOTS = (*_DECISIONS.values(), ("itemStatus", "Item status", "text"))
_RANGE_ROLES = frozenset(("slider", "spinner", "scrollbar", "progress_indicator", "level_indicator"))
_ACTION_ROLES = _RANGE_ROLES | {
    "button",
    "popup_button",
    "menu_button",
    "checkbox",
    "radio_button",
    "switch",
    "link",
    "text_field",
    "text_area",
    "combo_box",
    "menu_item",
}
_SUPPORT_ACTIONS = frozenset(("focus", "set_value", "replace_selection", "show_menu", "scroll_into_view"))
_FALSE_STATE = frozenset(("states.has_popup", "states.protected", "states.offscreen"))


def decision_fields(source: Mapping[str, Any]) -> dict[str, tuple[str, Any]]:
    """Project complete evidence. Keep incidental and constrained facts in their source ledger."""
    values, paths = source["values"], source["fieldPaths"]
    statuses = tuple(paths[key] for key in source["fieldStatus"])

    def unrestricted(path):
        return all(path[: len(status)] != status and status[: len(path)] != path for status in statuses)

    def complete(key):
        return unrestricted(paths[key])

    selected = {}
    for key, (attribute, _, _) in _DECISIONS.items():
        if key not in values or not complete(key):
            continue
        value = values[key]
        if key == "numeric_value.allowed_values":
            valid = isinstance(value, tuple) and all(
                type(item) in (str, bool) or finite(item) or isinstance(item, Mapping) and set(item) == {"element"}
                for item in value
            )
        else:
            valid = finite(value) if key.startswith("numeric_value.") else value is True or value is False
        if valid:
            selected[key] = (attribute, value)
    if selected:
        role = values.get("role") if "role" in values and complete("role") else None
        actions = (
            tuple(item for index, item in enumerate(source["actions"]["items"]) if unrestricted(("actions", index)))
            if source["actions"]["state"] == "complete"
            else ()
        )
        numeric = (
            role in _RANGE_ROLES
            or (finite(values.get("numeric_value.value")) and complete("numeric_value.value"))
            or any(
                key in selected
                for key in ("numeric_value.small_step", "numeric_value.large_step", "numeric_value.allowed_values")
            )
            or any(tuple(action.get("target", ())) == ("numeric_value", "value") for action in actions)
        )
        actionable = role in _ACTION_ROLES or any(
            action.get("key") is not None and action["kind"] not in _SUPPORT_ACTIONS for action in actions
        )
        retained = {}
        for key, item in selected.items():
            if not numeric and key.startswith("numeric_value."):
                continue
            if not actionable and key in _FALSE_STATE and item[1] is False:
                continue
            retained[key] = item
        selected = retained
    statuses = {
        key: report
        for key, report in values.items()
        if key.startswith("supplements[") and report["key"] == "item_status"
    }
    if (
        statuses
        and all(isinstance(report.get("value"), str) and complete(key) for key, report in statuses.items())
        and len({report["value"] for report in statuses.values()}) == 1
    ):
        selected.update((key, ("itemStatus", report["value"])) for key, report in statuses.items())
    return selected
