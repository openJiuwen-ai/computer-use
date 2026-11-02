# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Interpret an original recipient's own editing facts, independently of its label."""

from collections.abc import Mapping
from typing import Any

from computer_use.native.semantics import UNKNOWN, editing_evidence


def owner_editing_role(role: str | object, values: Mapping[str, Any], *, platform: str) -> str | None:
    """Qualify only this owner, never an unknown descendant or a containment path.

    Missing roles and UIA's standard Custom type do not negate an explicit
    editing declaration. Known nonediting roles retain their existing meaning.
    """
    if role is UNKNOWN or platform == "windows" and role == "custom":
        role = "unknown"
    if isinstance(role, str) and editing_evidence(role, values, platform=platform) is True:
        return role
    return None
