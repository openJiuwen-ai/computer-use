# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A retained window image survives known nonmodal overlays, not target changes."""

import pytest
from computer_use.interaction.write.ui.membership import compatible_membership

ROOT = ("root", "surface", None, "none", False)
CHILD = ("child", "child-surface", "root", "known", False)


def test_nonmodal_scrollbar_window_does_not_expire_original_window_pixels():
    assert compatible_membership((ROOT,), (CHILD, ROOT))
    grandchild = ("grandchild", "nested-surface", "child", "known", False)
    assert compatible_membership((ROOT,), (grandchild, CHILD, ROOT))


@pytest.mark.parametrize(
    "child",
    [
        (*CHILD[:4], True),
        (*CHILD[:4], None),
        ("child", "child-surface", "root", "unknown", False),
        ("child", "child-surface", "other", "known", False),
        ("child", "child-surface", None, "none", False),
    ],
)
def test_new_modal_or_unconfirmed_surfaces_still_expire_image(child):
    assert not compatible_membership((ROOT,), (ROOT, child))


@pytest.mark.parametrize(
    "current",
    [
        (),
        (("root", "replacement", *ROOT[2:]),),
        ((*ROOT[:4], True),),
        ((*ROOT[:4], None),),
        (("root", "surface", "new-parent", "known", False),),
    ],
)
def test_original_identity_parent_and_modal_evidence_cannot_be_weakened(current):
    assert not compatible_membership((ROOT,), current)


def test_unknown_nonmodal_evidence_can_be_refined_and_composite_layout_stays_fixed():
    assert compatible_membership(((*ROOT[:4], None),), (ROOT,))
    sibling = ("other", "other-surface", None, "none", False)
    assert compatible_membership((ROOT, sibling), (ROOT, sibling))
    assert not compatible_membership((ROOT, sibling), (ROOT, sibling, CHILD))
