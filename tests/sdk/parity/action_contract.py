"""Adapt historical action records to the platform-independent presentation IR.

The frozen oracle still checks the original strings, references and compression
decisions. This adapter only adds the semantic action kind now supplied by the
native observation boundary; it does not change expected display text.
"""


def action_contract(value):
    if isinstance(value, list):
        return [action_contract(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: action_contract(item) for key, item in value.items()}
    kinds = {
        "AXPress": "invoke",
        "AXPick": "select",
        "AXShowMenu": "show_menu",
        "AXExpand": "expand",
        "AXCollapse": "collapse",
        "AXShowDefaultUI": "show_default_ui",
    }
    if isinstance(result.get("actions"), list):
        result["actions"] = [
            {**action, "kind": kinds.get(action["name"], "custom")}
            if isinstance(action, dict) and "name" in action
            else action
            for action in result["actions"]
        ]
    return result
