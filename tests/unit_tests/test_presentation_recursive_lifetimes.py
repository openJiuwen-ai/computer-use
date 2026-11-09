"""Finished pure traversals release their captured graphs without waiting for cyclic GC."""

import asyncio
import gc
import weakref

import pytest
from computer_use.interaction.read.presentation.compression.caption_equivalence import (
    PassiveCaptionSource,
    caption_equivalence,
)
from computer_use.interaction.read.presentation.compression.evidence import CompressionEvidence
from computer_use.interaction.read.presentation.compression.passes.merge_owned_passive_content import _branch
from computer_use.interaction.read.presentation.ir import FactKey, PresentedValue, SourceKey, ValuePart
from computer_use.interaction.read.presentation.semantics.controls import _editor_path, compose_controls
from computer_use.interaction.read.presentation.tree import interaction_view, value_label, with_interactions
from computer_use.protocol.observation import TextInfo


class Payload:
    pass


def node(ref, role, children=(), **fields):
    return {"ref": ref, "role": role, "children": list(children), **fields}


def test_control_label_composition_does_not_retain_its_completed_output():
    gc.collect()

    def compose():
        payload = Payload()
        source = node(
            "owner",
            "button",
            [node("layout", "group", [node("label", "staticText", name="Save")])],
            capabilities={"click": "available"},
            payload=payload,
        )
        projected = compose_controls(source)
        assert projected["control"] == {
            "kind": "button",
            "members": [{"ref": "layout", "part": "layout"}, {"ref": "label", "part": "label"}],
        }
        assert "control" not in source
        return weakref.ref(payload)

    reference = compose()
    assert reference() is None
    assert gc.isenabled()


@pytest.mark.parametrize("editor", ["editor", "missing"])
def test_editor_search_releases_its_owner_after_success_or_miss(editor):
    gc.collect()

    def search():
        payload = Payload()
        source = node("owner", "group", [node("layout", "group", [node("editor", "textField")])], payload=payload)
        found = _editor_path(source, editor)
        assert ([part["ref"] for part in found] if found else None) == (
            ["layout", "editor"] if editor == "editor" else None
        )
        return weakref.ref(payload)

    reference = search()
    assert reference() is None
    assert gc.isenabled()


def test_modal_projection_releases_source_and_keeps_original_scope_rules():
    gc.collect()

    def project():
        payload = Payload()
        source = node(
            "window",
            "window",
            [node("wrap", "group", [node("modal", "sheet")]), node("background", "button")],
            blockedBy=["modal"],
            payload=payload,
        )
        result = interaction_view(source, include_background=False)
        assert [child["ref"] for child in result["children"]] == ["wrap"]
        assert result["partialReason"] == "background blocked by modal"
        assert len(source["children"]) == 2
        return weakref.ref(payload)

    reference = project()
    assert reference() is None
    assert gc.isenabled()


class CapturedView:
    def __init__(self, *, fail=None):
        self.owner = node(
            "owner", "button", [node("branch", "group")], name="One Two", capabilities={"click": "available"}
        )
        self.branch = self.owner["children"][0]
        self.native_nodes = {"owner": self.owner, "branch": self.branch}
        self.parents = {"branch": "owner"}
        self.scopes = {"owner": "window", "branch": "window"}
        self.evidence = CompressionEvidence(
            complete_text=frozenset({("owner", "name")}),
            complete_children={"owner": ("branch",), "branch": ()},
        )
        self.context = {"compressionEvidence": self.evidence}
        self.fail = fail

    def keep(self, _node):
        if self.fail:
            raise self.fail()
        return False

    def field_value(self, ref, field):
        value = self.native_nodes[ref].get(field)
        return PresentedValue((ValuePart(value, (FactKey(SourceKey(ref), field),)),)) if value is not None else None


def test_caption_equivalence_miss_does_not_retain_captured_view():
    gc.collect()

    def compare():
        view = CapturedView()
        assert caption_equivalence(view, view.owner, view.evidence, {}, frozenset()) is None
        return weakref.ref(view)

    reference = compare()
    assert reference() is None
    assert gc.isenabled()


@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
def test_caption_equivalence_releases_view_on_success_error_and_cancellation(failure):
    gc.collect()

    def compare():
        view = CapturedView(fail=failure)
        children = [node("one", "staticText", value="One"), node("two", "staticText", value="Two")]
        view.owner["children"] = children
        view.native_nodes = {"owner": view.owner, **{child["ref"]: child for child in children}}
        view.parents = {child["ref"]: "owner" for child in children}
        view.scopes = {ref: "window" for ref in view.native_nodes}
        view.evidence = CompressionEvidence(
            complete_text=frozenset({("owner", "name"), ("one", "value"), ("two", "value")}),
            complete_children={"owner": ("one", "two"), "one": (), "two": ()},
            caption_sources={
                child["ref"]: PassiveCaptionSource(child["ref"], child["value"], (), TextInfo("value"), ("owner",))
                for child in children
            },
        )
        view.context = {"compressionEvidence": view.evidence}
        try:
            result = caption_equivalence(view, view.owner, view.evidence, view.native_nodes, frozenset())
        except (RuntimeError, asyncio.CancelledError) as error:
            assert failure is not None and type(error) is failure
        else:
            assert failure is None
            assert result.owner == "owner" and result.scope == "window"
            assert result.composition.value == "One Two"
            assert [part.facts[0].source for part in result.composition.parts] == ["one", "two"]
        return weakref.ref(view)

    reference = compare()
    assert reference() is None
    assert gc.isenabled()


@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
def test_passive_branch_releases_view_on_success_error_and_cancellation(failure):
    gc.collect()

    def collect():
        view = CapturedView(fail=failure)
        try:
            result = _branch(view, view.owner, view.branch, view.evidence)
        except (RuntimeError, asyncio.CancelledError) as error:
            assert failure is not None and type(error) is failure
        else:
            assert failure is None
            assert result == ([view.branch], [])
        return weakref.ref(view)

    reference = collect()
    assert reference() is None
    assert gc.isenabled()


def test_stateless_modal_and_text_walks_preserve_boundaries_and_short_circuit():
    nested_window = node("nested", "window", [node("other-modal", "sheet", modal=True)])
    source = node("window", "window", [nested_window, node("wrap", "group", [node("modal", "sheet", modal=True)])])
    result = with_interactions(source)
    assert result["blockedBy"] == ["modal"]
    assert result["children"][0]["blockedBy"] == ["other-modal"]
    assert result["children"][1]["blockedBy"] == ["modal"]
    assert "blockedBy" not in source
    assert (
        value_label(node("number", "group", [node("text", "staticText", value="value")], valueIsNumber=True))
        == "Native value"
    )
    assert value_label(node("number", "group", [node("text", "staticText", value="value")])) == "Value"
    assert value_label(node("doc", "textArea", valueKind="document")) == "Document text"
