# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

def action_matches(catalogue, selected):
    """Exact native identity, including description when it was observed.

    Names may be duplicated by a provider. An explicit unique description can
    distinguish them; an unread description cannot be assumed to differ.
    """
    if catalogue["state"] != "complete":
        return []
    matches = [(i, a) for i, a in enumerate(catalogue["items"]) if a["name"] == selected["name"]]
    if "description" in selected:
        if any(a.get("descriptionStatus", {}).get("state") not in (None, "absent") for _, a in matches):
            return []
        matches = [(i, a) for i, a in matches if a.get("description") == selected["description"]]
    return [i for i, _ in matches]
