# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare captured membership with fresh evidence before image input."""

from __future__ import annotations

from computer_use.session.interface_images import Membership


def compatible_membership(captured: Membership, current: Membership, target: str) -> bool:
    """Keep the addressed member's original scope and current modal evidence.

    The existing state owner validates the target and instance identities.
    This predicate retains the addressed member and its captured ancestor path.
    Other member lifetimes do not change its frozen local-coordinate map.

    Args:
        captured: Ordered member/modal signature retained with the image.
        current: Fresh signature produced by the same signature function.
        target: Original member selected by hit testing the delivered pixels.

    Returns:
        Whether the original member is still in the captured scope and every
        changed or new current member is a confirmed nonmodal child or an
        independent nonmodal window outside its ancestor path. Native dispatch
        still checks the original capture, modality and physical hit.
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
    if member is not None:
        # A captured edge to an unread ancestor is still part of this target's
        # boundary. Its later appearance cannot be classified as a new peer.
        path.add(member)
    # Changed non-addressed members are current surfaces, not new input
    # targets. The delivered image continues to resolve their old bindings.
    confirmed = {key for key, item in fresh.items() if key in previous and same(previous[key], item)}
    # App now observes an application catalogue rather than one fixed-root
    # subtree. A separate, explicitly nonmodal window does not change the
    # addressed window's local pixel map. Unknown parents or modality supply
    # no such proof; confirmed descendants are handled by the same gate below.
    confirmed.update(
        key for key, item in fresh.items()
        if key not in path and item[2] is None and item[3] == "none" and item[4] is False
    )
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
