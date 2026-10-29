# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.interaction.read.presentation.compression.policy import layout

if TYPE_CHECKING:
    from computer_use.interaction.read.presentation.ir import ViewNode


def anonymous_layout(node: ViewNode) -> bool:
    """A removable wrapper must not carry its own displayed identity/state.

    A role match alone is insufficient: providers can describe two AXGroups as entirely
    different UI regions. Explicit visibility also belongs to the original container; promoting
    its children must not imply they are visible.

    Args:
        node: Source/projected node in this exact view; its native identity is unchanged.
    """
    return (
        layout(node)
        and not node.get("identifier")
        and node.get("visible") is not False
        and node.get("showing") is not False
        and (not node.get("roleDescription") or node["role"] == "group" and node["roleDescription"] in ("group", "组"))
    )
