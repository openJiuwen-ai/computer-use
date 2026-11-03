# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A retained window image survives known nonmodal overlays, not target changes."""

import pytest
from computer_use.interaction.write.ui.membership import compatible_membership

ROOT = ("root", "surface", None, "none", False)
CHILD = ("child", "child-surface", "root", "known", False)


def test_nonmodal_scrollbar_window_does_not_expire_original_window_pixels():
    assert compatible_membership((ROOT,), (CHILD, ROOT), "root")
    grandchild = ("grandchild", "nested-surface", "child", "known", False)
    assert compatible_membership((ROOT,), (grandchild, CHILD, ROOT), "root")


@pytest.mark.parametrize(
    "child",
    [
        (*CHILD[:4], True),
        (*CHILD[:4], None),
        ("child", "child-surface", "root", "unknown", False),
        ("child", "child-surface", "other", "known", False),
    ],
)
def test_new_modal_or_unconfirmed_surfaces_still_expire_image(child):
    assert not compatible_membership((ROOT,), (ROOT, child), "root")


@pytest.mark.parametrize("surface", ["other-surface", "replacement-surface"])
def test_independent_nonmodal_peer_does_not_change_captured_target(surface):
    peer = ("other", "other-surface", None, "none", False)
    fresh_peer = ("other", surface, None, "none", False)
    child = ("other-child", "other-child-surface", "other", "known", False)
    assert compatible_membership((ROOT,), (ROOT, fresh_peer, child), "root")
    assert compatible_membership((ROOT, peer), (ROOT, fresh_peer, child), "root")
    if surface != peer[1]:
        assert not compatible_membership((ROOT, peer), (ROOT, fresh_peer), "other")


@pytest.mark.parametrize("modal", [True, None])
def test_new_independent_modal_or_unknown_modality_is_not_assumed_irrelevant(modal):
    peer = ("other", "other-surface", None, "none", modal)
    assert not compatible_membership((ROOT,), (ROOT, peer), "root")


def test_unknown_parent_peer_cannot_be_treated_as_independent():
    peer = ("other", "other-surface", None, "unknown", False)
    assert not compatible_membership((ROOT,), (ROOT, peer), "root")


def test_unread_captured_ancestor_is_not_a_new_unrelated_peer():
    child = ("child", "child-surface", "root", "known", False)
    assert not compatible_membership((child,), (child, ROOT), "child")


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
    assert not compatible_membership((ROOT,), current, "root")


def test_unknown_nonmodal_evidence_can_be_refined_and_composite_targets_stay_fixed():
    assert compatible_membership(((*ROOT[:4], None),), (ROOT,), "root")
    sibling = ("other", "other-surface", None, "none", False)
    assert compatible_membership((ROOT, sibling), (ROOT, sibling), "root")
    assert compatible_membership((ROOT, sibling), (ROOT, sibling, CHILD), "root")
    for modal in (True, None):
        assert not compatible_membership((ROOT, sibling), (ROOT, sibling, (*CHILD[:4], modal)), "root")
