# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Capture identity is stable across current modal state and unrelated windows."""

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
def test_new_modal_or_unconfirmed_surfaces_leave_input_readiness_to_native(child):
    assert compatible_membership((ROOT,), (ROOT, child), "root")


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
def test_new_independent_modal_or_unknown_modality_does_not_change_capture_identity(modal):
    peer = ("other", "other-surface", None, "none", modal)
    assert compatible_membership((ROOT,), (ROOT, peer), "root")


def test_unknown_parent_peer_does_not_change_original_target_binding():
    peer = ("other", "other-surface", None, "unknown", False)
    assert compatible_membership((ROOT,), (ROOT, peer), "root")


def test_later_observation_of_original_parent_does_not_replace_the_child_binding():
    child = ("child", "child-surface", "root", "known", False)
    assert compatible_membership((child,), (child, ROOT), "child")


@pytest.mark.parametrize(
    "current",
    [
        (),
        (("root", "replacement", *ROOT[2:]),),
        (("root", "surface", "new-parent", "known", False),),
        (("root", "surface", None, "unknown", False),),
    ],
)
def test_original_identity_and_parent_binding_cannot_change(current):
    assert not compatible_membership((ROOT,), current, "root")


def test_unknown_nonmodal_evidence_can_be_refined_and_composite_targets_stay_fixed():
    assert compatible_membership(((*ROOT[:4], None),), (ROOT,), "root")
    sibling = ("other", "other-surface", None, "none", False)
    assert compatible_membership((ROOT, sibling), (ROOT, sibling), "root")
    assert compatible_membership((ROOT, sibling), (ROOT, sibling, CHILD), "root")
    for modal in (True, None):
        assert compatible_membership((ROOT, sibling), (ROOT, sibling, (*CHILD[:4], modal)), "root")


@pytest.mark.parametrize("modal", [True, False, None])
def test_modal_state_of_addressed_member_or_ancestor_is_not_capture_identity(modal):
    assert compatible_membership((ROOT,), ((*ROOT[:4], modal),), "root")
    assert compatible_membership((ROOT, CHILD), ((*ROOT[:4], modal), (*CHILD[:4], modal)), "child")


@pytest.mark.parametrize(
    "current",
    [
        (CHILD,),
        (("root", "replacement", *ROOT[2:]), CHILD),
        (("root", "surface", "new-parent", "known", False), CHILD),
    ],
)
def test_captured_ancestor_remains_part_of_addressed_child_binding(current):
    assert not compatible_membership((ROOT, CHILD), current, "child")


def test_unknown_target_and_captured_parent_cycle_cannot_resolve_an_original_binding():
    assert not compatible_membership((ROOT,), (ROOT,), "uncaptured")
    root_cycle = ("root", "surface", "child", "known", False)
    assert not compatible_membership((root_cycle, CHILD), (root_cycle, CHILD), "child")
