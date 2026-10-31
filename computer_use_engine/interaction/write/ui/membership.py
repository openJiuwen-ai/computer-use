# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare captured membership with fresh evidence before image input."""

from __future__ import annotations

from computer_use.session.interface_images import Membership


def compatible_membership(captured: Membership, current: Membership) -> bool:
    """Keep captured identities and modality while admitting harmless child surfaces.

    The existing instance owner validates the root and instance identities.
    This predicate retains every captured window, surface and parent fact; it
    neither updates the capture nor grants authority to another native target.

    Args:
        captured: Ordered member/modal signature retained with the image.
        current: Fresh signature produced by the same signature function.

    Returns:
        Whether every captured member remains valid. A single-window capture
        may acquire confirmed nonmodal children without changing its coordinate
        mapping. Native dispatch still checks modality and the physical hit.
    """
    remaining = {item[0]: item for item in current}
    for before in captured:
        after = remaining.pop(before[0], None)
        if after is None or before[:4] != after[:4]:
            return False
        if not (before[4] is after[4] or before[4] is None and after[4] is False):
            return False
    if not remaining:
        return True
    # A composed canvas retains its exact member layout. One original window's
    # pixels do not move when a scrollbar/auxiliary nonmodal child appears.
    if len(captured) != 1:
        return False
    confirmed = {captured[0][0]}
    while remaining:
        children = [
            key for key, item in remaining.items() if item[2] in confirmed and item[3] == "known" and item[4] is False
        ]
        if not children:
            return False
        for key in children:
            confirmed.add(key)
            del remaining[key]
    return True
