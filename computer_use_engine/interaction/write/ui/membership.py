# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare captured membership with fresh evidence before image input."""

from __future__ import annotations

from computer_use.session.interface_images import Membership


def compatible_membership(captured: Membership, current: Membership, target: str) -> bool:
    """Keep the addressed member's original scope and current modal evidence.

    The existing instance owner validates the root and instance identities.
    This predicate retains the addressed member and its captured ancestor path.
    Other member lifetimes do not change its frozen local-coordinate map.

    Args:
        captured: Ordered member/modal signature retained with the image.
        current: Fresh signature produced by the same signature function.
        target: Original member selected by hit testing the delivered pixels.

    Returns:
        Whether the original member is still in the captured scope and every
        changed or new current member is a confirmed nonmodal child. Native
        dispatch still checks the original capture, modality and physical hit.
    """
    previous = {item[0]: item for item in captured}
    fresh = {item[0]: item for item in current}

    def same(before, after):
        return before[:4] == after[:4] and (before[4] is after[4] or before[4] is None and after[4] is False)

    path = set()
    member = target
    if member not in previous:
        return False
    while member in previous:
        if member in path or member not in fresh or not same(previous[member], fresh[member]):
            return False
        path.add(member)
        member = previous[member][2]
    # Changed non-addressed members are current surfaces, not new input
    # targets. The delivered image continues to resolve their old bindings.
    confirmed = {key for key, item in fresh.items() if key in previous and same(previous[key], item)}
    remaining = {key: item for key, item in fresh.items() if key not in confirmed}
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
