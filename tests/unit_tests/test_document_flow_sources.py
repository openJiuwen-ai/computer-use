"""Native prose decisions follow the exact source while display layouts evolve."""

import gc
import weakref
from dataclasses import replace
from types import MappingProxyType

from computer_use.interaction.read.presentation.interface.geometric.document_flow import (
    _source_indexes,
    document_flow_scope,
)
from computer_use.interaction.read.presentation.interface.model import InterfaceBlock, InterfaceDraft
from computer_use.interaction.read.presentation.ir import ProjectionInput


def node(ref, role, *children, **fields):
    return dict(ref=ref, role=role, children=list(children), **fields)


def draft_for(root):
    source = ProjectionInput.build(root)
    return InterfaceDraft(
        source=source,
        projected_nodes=source.display_nodes,
        projected_parents=source.parents,
        order=source.order,
        root_refs=(root["ref"],),
        provenance=MappingProxyType({}),
        folded_refs=MappingProxyType({}),
        enabled=True,
    )


def blocks_for(ref):
    return {"current": InterfaceBlock("current", "tree", ref, (ref,))}


def prose():
    return node(
        "prose", "group", node("h1", "heading"), node("p1", "staticText"),
        node("h2", "heading"), node("p2", "paragraph"),
    )


def test_prose_ancestry_does_not_turn_adjacent_cards_or_navigation_into_document_flow():
    draft = draft_for(node(
        "w", "window",
        node("wrapper", "webArea", prose()),
        node("navigation", "group", node("button", "button")),
        node("cards", "group",
             node("card1", "group", node("ch1", "heading"), node("cp1", "staticText")),
             node("card2", "group", node("ch2", "heading"), node("cp2", "staticText"))),
    ))
    # Repeat in different orders after positive and negative paths were cached.
    for ref in ("p1", "navigation", "wrapper", "card1", "p2", "cards", "button", "w", "cp2", "prose"):
        assert document_flow_scope("current", draft, blocks_for(ref)) is (ref in {"p1", "wrapper", "p2", "prose"})
    assert not document_flow_scope("current", draft, blocks_for("navigation"))
    assert document_flow_scope("current", draft, blocks_for("p1"))


def test_current_display_block_reselects_its_source_after_layout_changes():
    draft = draft_for(node("w", "window", prose(), node("navigation", "group", node("b", "button"))))
    blocks = blocks_for("p1")
    assert document_flow_scope("current", draft, blocks)
    blocks["current"] = replace(blocks["current"], primary_ref="navigation", source_refs=("navigation",))
    assert not document_flow_scope("current", draft, blocks)
    # A synthetic region selects its current first source, not its former block key.
    blocks["current"] = replace(blocks["current"], primary_ref=None, source_refs=("p2",))
    assert document_flow_scope("current", draft, blocks)


def test_new_capture_with_same_references_has_independent_source_predicates():
    first = draft_for(node("w", "window", prose()))
    assert document_flow_scope("current", first, blocks_for("prose"))
    changed = draft_for(node(
        "w", "window", node("prose", "group", node("h1", "heading"), node("p1", "button"),
                             node("h2", "heading"), node("p2", "paragraph")),
    ))
    assert not document_flow_scope("current", changed, blocks_for("prose"))
    assert document_flow_scope("current", first, blocks_for("prose"))


def test_passive_wrapper_and_parent_cycles_do_not_invent_document_flow():
    draft = draft_for(node("w", "window"))
    source = replace(
        draft.source,
        native_nodes=MappingProxyType({
            "a": MappingProxyType(node("a", "group", "b")),
            "b": MappingProxyType(node("b", "group", "a")),
        }),
        parents=MappingProxyType({"a": "b", "b": "a"}),
    )
    draft = replace(draft, source=source)
    assert not document_flow_scope("current", draft, blocks_for("a"))
    assert not document_flow_scope("current", draft, blocks_for("b"))


def test_predicate_cache_releases_the_exact_projection_even_when_index_is_retained():
    draft = draft_for(node("w", "window", prose()))
    source = draft.source
    assert document_flow_scope("current", draft, blocks_for("prose"))
    index = _source_indexes.get(source)
    retained = weakref.ref(source)
    del draft, source
    gc.collect()
    assert retained() is None
    assert index.contains("prose")
