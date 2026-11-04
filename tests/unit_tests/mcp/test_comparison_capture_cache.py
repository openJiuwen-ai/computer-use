"""Only an exact immutable display tree reuses its small comparison evidence."""

import gc
import weakref
from dataclasses import replace
from unittest.mock import patch

from computer_use.identity import WeakIdentityMap
from computer_use.interaction.read.delivery.diff import baseline
from computer_use.interaction.read.delivery.diff.baseline import ComparisonNode, ComparisonTree
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import NativeNode, NativeObservation


def captured_tree(*, scope="one"):
    facts = NativeObservation(
        "macos", "comparison", "capture", (1, 2), ("window",), {"window": NativeNode(role="window", name="Form")}
    )
    snapshot = normalize_facts(facts, DisplayReferences("comparison"))
    return replace(DisplayDocument.tree(prepare_snapshot(snapshot)).parts[0], scope=(scope,))


def test_exact_capture_hashes_once_and_a_new_capture_still_hashes_its_own_facts():
    tree = captured_tree()
    with patch.object(ComparisonNode, "capture", wraps=ComparisonNode.capture) as capture:
        first = ComparisonTree.capture(tree)
        count = capture.call_count
        assert count > 0
        assert ComparisonTree.capture(tree) is first
        assert capture.call_count == count
        equivalent_tree = replace(tree)
        assert ComparisonTree.capture(equivalent_tree) == first
        assert ComparisonTree.capture(equivalent_tree) is not first
        assert capture.call_count == count * 2
        fresh_tree = captured_tree()
        assert ComparisonTree.capture(fresh_tree) is not first
        assert capture.call_count == count * 3


def test_full_details_indentation_and_multiwindow_sources_remain_independent():
    tree = captured_tree()
    normal = ComparisonTree.capture(tree)
    details = ComparisonTree.capture(replace(tree, details=True))
    indented = ComparisonTree.capture(replace(tree, indent=2))
    other_window = ComparisonTree.capture(replace(tree, scope=("two",)))
    assert normal.details is False and details.details is True
    assert [node.depth for node in indented.nodes] == [node.depth + 2 for node in normal.nodes]
    assert normal.scope == ("one",) and other_window.scope == ("two",)
    assert len({id(normal), id(details), id(indented), id(other_window)}) == 4


def test_cached_hash_record_does_not_retain_tree_or_prepared_native_capture():
    tree = captured_tree()
    tree_ref, prepared_ref = weakref.ref(tree), weakref.ref(tree.prepared)
    comparison = ComparisonTree.capture(tree)
    del tree
    gc.collect()
    assert tree_ref() is None and prepared_ref() is None
    assert comparison.nodes and all(isinstance(node.signature, str) for node in comparison.nodes)


def test_stale_numeric_identity_entry_cannot_reuse_another_objects_evidence(monkeypatch):
    cache = WeakIdentityMap()
    monkeypatch.setattr(baseline, "_comparison_trees", cache)
    previous = captured_tree(scope="old-window")
    old = ComparisonTree.capture(previous)
    current = captured_tree(scope="current-window")
    # Simulate an obsolete numeric id slot: a correct identity cache must verify
    # its weak reference, rather than trusting the dictionary's numeric key.
    cache._items[id(current)] = (weakref.ref(previous), old)
    result = ComparisonTree.capture(current)
    assert result is not old and result.scope == ("current-window",)
    assert ComparisonTree.capture(previous) is old
