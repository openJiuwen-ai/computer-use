# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare captured membership with fresh evidence before image input."""

from __future__ import annotations

from computer_use.session.interface_images import Membership


def compatible_membership(captured: Membership, current: Membership) -> bool:
    """Allow only unchanged scope or unknown-to-nonmodal evidence refinement.

    The existing instance owner validates the root and instance identities.
    This predicate retains every captured window, surface and parent fact; it
    neither updates the capture nor grants authority to another native target.

    Args:
        captured: Ordered member/modal signature retained with the image.
        current: Fresh signature produced by the same signature function.

    Returns:
        Whether membership is unchanged apart from modal None becoming False.
    """
    return len(captured) == len(current) and all(
        before[:4] == after[:4] and (before[4] is after[4] or before[4] is None and after[4] is False)
        for before, after in zip(captured, current)
    )
