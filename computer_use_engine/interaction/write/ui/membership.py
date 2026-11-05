# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Keep the addressed image's original window binding before native input."""

from __future__ import annotations

from computer_use.session.interface_images import Membership


def compatible_membership(captured: Membership, current: Membership, target: str) -> bool:
    """Keep the addressed member and its captured ancestor bindings.

    The existing state owner validates the target and instance identities.
    This predicate retains the addressed member and its captured ancestor path.
    Other member lifetimes do not change its frozen local-coordinate map.
    Modality is current input state, not capture identity: native dispatch
    checks it against the original binding immediately before each input.

    Args:
        captured: Ordered member/modal signature retained with the image.
        current: Fresh signature produced by the same signature function.
        target: Original member selected by hit testing the delivered pixels.

    Returns:
        Whether the original member and every captured ancestor retain their
        surface and parent binding. New or changed peers, including unknown
        parent or modal evidence, cannot invalidate this image. Native dispatch
        still checks the original capture, current modality and physical hit.
    """
    previous = {item[0]: item for item in captured}
    fresh = {item[0]: item for item in current}

    path = set()
    member = target
    if member not in previous:
        return False
    while member in previous:
        if member in path or member not in fresh or previous[member][:4] != fresh[member][:4]:
            return False
        path.add(member)
        member = previous[member][2]
    return True
