# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exact macOS window-close relation; no title, screen-coordinate or key guesses."""

from dataclasses import replace

from computer_use.diagnostics import local_failure


async def close_control(native, operation, root, remaining):
    page = await native.query_relation(operation, root, "AXCloseButton", None, 1, remaining())
    relation = page["relation"]
    scope_changed = page["session"] != root.session or page["surface"] != root.surface or page["platform"] != "macos"
    if scope_changed or relation["source"] != root.node or relation["property"] != "AXCloseButton":
        raise ValueError("Window close relation changed its source or scope")
    state = relation["state"]
    if state in ("absent", "unsupported"):
        raise local_failure("the selected window does not expose a normal close control", "input_unavailable")
    confirmed = state == "complete" and relation["certainty"] == "confirmed"
    if confirmed and not relation["targets"]:
        raise local_failure("the selected window does not expose a normal close control", "input_unavailable")
    complete_close_control = confirmed and len(relation["targets"]) == 1
    if not complete_close_control or relation.get("offset", 0) != 0 or relation.get("cursor") is not None:
        raise local_failure("the selected window's close control could not be confirmed", "observation_incomplete")
    return replace(root, node=relation["targets"][0])
