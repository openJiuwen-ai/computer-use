# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Report a newly observed local AX menu without attributing its opener."""

from computer_use.native.scopes import window_binding
from computer_use.session.observation_data import observation_data


def _complete_targets(relation, kind):
    if (
        relation is not None
        and relation.kind == kind
        and relation.evidence in ("reported", "verified")
        and relation.enumeration == "enumerated"
        and relation.offset in (None, 0)
        and relation.total == len(relation.targets)
        and not relation.unavailable_positions
        and relation.positions in (None, tuple(range(len(relation.targets))))
    ):
        return relation.targets
    return None


def _new_local_menu(before, after, target, root, item_tokens):
    """Only compare complete local lists along the same original AX path."""
    token, child = target, None
    visited, menus = set(), set()
    while token not in visited:
        visited.add(token)
        old, new = before.nodes.get(token), after.nodes.get(token)
        if old is None or new is None or old.role != new.role or old.role in (None, "unknown"):
            return False
        if child is not None and any(
            (relation := node.relations.get("children")) is None
            or relation.evidence == "candidate" or child not in relation.targets
            for node in (old, new)
        ):
            return False
        # A common window root alone does not link another control's menu.
        if token != root or token == target:
            old_children = _complete_targets(old.relations.get("children"), "children")
            new_children = _complete_targets(new.relations.get("children"), "children")
            if old_children is not None and new_children is not None:
                for menu in set(new_children) - set(old_children) - before.nodes.keys():
                    node = after.nodes.get(menu)
                    if node is None or node.role != "menu" or node.states.visible is False or node.states.showing is False:
                        continue
                    if _complete_targets(node.relations.get("parent"), "parent") != (token,):
                        continue
                    children = _complete_targets(node.relations.get("children"), "children")
                    if children and any(
                        _complete_targets(after.nodes[item].relations.get("parent"), "parent") == (menu,)
                        for item in item_tokens.intersection(children)
                    ):
                        menus.add(menu)
        if token == root:
            return len(menus) == 1
        parents = _complete_targets(old.relations.get("parent"), "parent")
        if parents is None or len(parents) != 1 or parents != _complete_targets(new.relations.get("parent"), "parent"):
            return False
        child, token = token, parents[0]
    return False


def observed_menu_window(target, prior_target, before_tree, observation, windows):
    """Return the existing public Window reference only for its included AX tree."""
    if prior_target is None or before_tree is None or not target.node or not target.root_window:
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
    before = before_tree.native
    for binding, tree in metadata["captured_trees"]:
        if binding != prior_target:
            continue
        after = tree.native
        if (
            before.backend != "macos" or after.backend != "macos"
            or before.session != target.session or after.session != target.session
            or before.roots != (binding.node,) or after.roots != before.roots
            or before.id == after.id or after.interval_ns[0] < before.interval_ns[1]
        ):
            continue
        actionable = {
            token for ref, token in tree.refs.items() if ref in metadata["refs"]
            and (item := after.nodes.get(token)) is not None and item.role == "menu_item"
            and item.states.enabled is True and item.states.visible is not False and item.states.showing is not False
            and any(action.key == "AXPress" and action.available is not False for action in item.actions or ())
        }
        if actionable and _new_local_menu(before, after, target.node, binding.node, actionable):
            return next((ref for ref, key in windows.items() if key == target.root_window), None)
    return None
