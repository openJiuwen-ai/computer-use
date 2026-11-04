# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Evidence for one technical post-input refresh, independent of task success."""

from dataclasses import replace


def _content(node):
    # TextInfo.source identifies a capture-local text space. We compare only
    # the same native node in the same window, so that allocation token cannot
    # demonstrate a UI change. Text contents, ranges and geometry still do.
    return replace(
        node,
        states=replace(node.states, focused=None, active=None, main_window=None),
        text=replace(node.text, source="") if node.text is not None else None,
    )


def same_window_content(before, after) -> bool:
    """Compare captured facts, excluding capture clocks and focus preparation.

    Values, children, geometry, checked/selected/expanded states and coverage
    remain significant. A changed focus alone does not show that a queued
    activation has updated the window's content.
    """
    if before is None or after is None:
        return False
    left, right = before.native, after.native
    if (left.session, left.roots, left.nodes.keys(), left.issues) != (
        right.session, right.roots, right.nodes.keys(), right.issues
    ):
        return False
    for key in left.nodes:
        a, b = left.nodes[key], right.nodes[key]
        if _content(a) != _content(b):
            return False
    return True
