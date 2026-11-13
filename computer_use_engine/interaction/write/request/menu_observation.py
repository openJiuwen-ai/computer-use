# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Report a newly observed local AX menu without attributing its opener."""

from computer_use.interaction.read.acquisition.view_facts import captured_metadata
from computer_use.native.scopes import window_binding
from computer_use.session.observation_data import observation_data


def observed_menu_window(target, prior_target, before_tree, observation, windows):
    """Return the existing public Window reference only for its included AX tree."""
    if prior_target is None or before_tree is None:
        return None
    if not target.node or not target.root_window:
        return None
    if window_binding(target) != window_binding(prior_target) or prior_target.node is None:
        return None
    metadata = observation_data(observation)
    if metadata is None or not any(
        (coverage["window"], coverage["surface"], coverage["instance"])
        == (target.root_window, target.surface, target.instance)
        and "tree" in coverage["included"] and not coverage.get("historical")
        for coverage in metadata["window_coverage"]
    ):
        return None
    before = captured_metadata(before_tree)
    for binding, tree in metadata["captured_trees"]:
        if binding != prior_target:
            continue
        after = captured_metadata(tree)
        if before.backend != "macos" or after.backend != "macos":
            continue
        if before.session != target.session or after.session != target.session:
            continue
        if before.roots != (binding.node,) or after.roots != before.roots:
            continue
        if before.id == after.id or after.interval_ns[0] < before.interval_ns[1]:
            continue
        published = {token for ref, token in tree.refs.items() if ref in metadata["refs"]}
        if not published:
            continue
        found = before_tree.capture.new_local_menu(tree.capture, target.node, binding.node, list(published))
        if found:
            return next((ref for ref, key in windows.items() if key == target.root_window), None)
    return None
