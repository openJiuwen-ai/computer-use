# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.


def action_matches(catalogue, selected, *, match_by="name"):
    """Exact native identity, including description when it was observed.

    Names may be duplicated by a provider. A description request must be unique
    across the whole catalogue, including actions with different names. Missing
    or truncated descriptions cannot be assumed to differ from the request.
    """
    if catalogue["state"] != "complete":
        return []
    if match_by == "description":
        for item in catalogue["items"]:
            status = item.get("descriptionStatus", {}).get("state")
            if status == "absent":
                continue
            if status is not None or "description" not in item:
                return []
        matches = [i for i, item in enumerate(catalogue["items"]) if item.get("description") == selected["description"]]
        if len(matches) == 1 and catalogue["items"][matches[0]]["name"] != selected["name"]:
            return []  # A retained description must not silently bind a different action.
        return matches
    matches = [(i, a) for i, a in enumerate(catalogue["items"]) if a["name"] == selected["name"]]
    if "description" in selected:
        if any(a.get("descriptionStatus", {}).get("state") not in (None, "absent") for _, a in matches):
            return []
        matches = [(i, a) for i, a in matches if a.get("description") == selected["description"]]
    return [i for i, _ in matches]
