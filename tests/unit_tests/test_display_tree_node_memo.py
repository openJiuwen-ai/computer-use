"""A frozen display tree reuses only its own depth-adjusted immutable nodes."""

import gc
import weakref
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch

import pytest
from computer_use.interaction.read.facts.normalize import normalize_facts
from computer_use.interaction.read.numbering.references import DisplayReferences
from computer_use.interaction.read.presentation import document as display_document
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.preparation import prepare_snapshot
from computer_use.protocol.observation import NativeNode, NativeObservation, Relation


def prepared(name="Save"):
    native = NativeObservation(
        "macos",
        "display-tree-memo",
        "capture",
        (1, 2),
        ("window",),
        {
            "window": NativeNode(
                role="window", name="Document", relations={"children": Relation("children", ("button",))}
            ),
            "button": NativeNode(role="button", name=name),
        },
    )
    return prepare_snapshot(normalize_facts(native, DisplayReferences(native.session)), compact=False)


def test_repeated_document_summaries_construct_display_nodes_only_once():
    capture = prepared()
    document = DisplayDocument.tree(capture)
    with patch.object(display_document, "replace", wraps=replace) as copy:
        first = document.summary_data()
        assert document.summary_data() == first
        assert document.parts[0].nodes == tuple(replace(item) for item in capture.nodes)
    assert first["trees"][0]["refs"] == [item.ref for item in capture.nodes]
    assert copy.call_count == len(capture.nodes)


def test_scope_indent_and_details_replacements_own_independent_node_memos():
    document = DisplayDocument.tree(prepared())
    original = document.parts[0]
    initial = original.nodes
    scoped = document.scoped(("app", "window")).parts[0]
    indented = document.indented().parts[0]
    detailed = replace(original, details=True)
    for variant in (scoped, indented, detailed):
        assert variant.nodes is not initial
        assert [item.ref for item in variant.nodes] == [item.ref for item in initial]
        assert [item.parent for item in variant.nodes] == [item.parent for item in initial]
        assert [item.fields for item in variant.nodes] == [item.fields for item in initial]
        assert variant.nodes is variant.nodes
    assert [item.depth for item in indented.nodes] == [item.depth + 1 for item in initial]
    assert [item.depth for item in scoped.nodes] == [item.depth for item in initial]
    assert scoped.scope == ("app", "window") and original.scope == ()
    assert detailed.details and not original.details
    assert original.nodes is initial


def test_replacing_capture_with_same_refs_does_not_reuse_old_fields():
    original = DisplayDocument.tree(prepared("Save")).parts[0]
    initial = original.nodes
    changed = replace(original, prepared=prepared("Save changes"))
    assert [item.ref for item in changed.nodes] == [item.ref for item in initial]
    assert changed.nodes != initial
    assert "Save changes" in [item.fields.get("name") for item in changed.nodes]
    assert "Save" in [item.fields.get("name") for item in original.nodes]


def test_cached_display_nodes_keep_their_existing_immutable_contract():
    tree = DisplayDocument.tree(prepared()).parts[0]
    current = tree.nodes[0]
    with pytest.raises(FrozenInstanceError):
        current.depth = 7
    with pytest.raises(TypeError):
        current.fields["name"] = "Changed"
    assert tree.nodes[0] == current


def test_retained_nodes_do_not_keep_their_display_tree_or_capture_alive():
    gc.collect()

    def create():
        capture = prepared()
        tree = DisplayDocument.tree(capture).parts[0]
        return tree.nodes, weakref.ref(tree), weakref.ref(capture)

    nodes, tree_ref, capture_ref = create()
    assert nodes
    assert tree_ref() is None
    assert capture_ref() is None
    assert gc.isenabled()
