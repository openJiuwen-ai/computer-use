# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observation reuse preserves native evidence, immutable sources and byte budgets."""

import json

import pytest
from compat_read_fixtures import element, scalar, snapshot_from_wire
from computer_use.interaction.read.accessibility.frame import NativeReadFrame
from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.json import dumps, utf8

pytestmark = pytest.mark.usefixtures("native_read")


def wire(url=None, *, title="中文🙂\n"):
    root = element("root", "AXWindow", title)
    if url is not None:
        root["properties"].append({"key": ["macos.ax", "AXURL"], "value": url})
    return {
        "kind": "observation",
        "session": "session",
        "platform": "macos",
        "roots": ["root"],
        "nodes": [root],
        "read": {"complete": True, "intervalNs": [1000000, 2000000], "details": [
            ["surface", scalar("surface")], ["observation_id", scalar("capture")],
        ]},
    }


def open_frame(engine, raw):
    body = json.dumps(raw).encode()
    return engine.open_read_frame("json", body, len(body))


def test_query_and_semantic_consumers_share_one_validated_response(native_read):
    raw = wire(title="Original")
    frame = NativeReadFrame(open_frame(native_read, raw))
    diagnostic = frame.raw_result()
    assert diagnostic == raw and frame.raw_result() is diagnostic
    capture = frame.capture("window", [])
    assert capture.lookup_evidence(["AXTitle"])["nodes"][0]["values"]["AXTitle"] == "Original"
    assert capture.node_view("root", ["name"]) == {"name": "Original"}
    with pytest.raises(ValueError, match="captured"):
        frame.capture("window", [])


def test_relative_native_urls_keep_their_distinct_semantic_resolution(native_read):
    frame = open_frame(native_read, wire({
        "nativeType": "CFURL",
        "data": "../target?q=1",
        "metadata": [["base", {"nativeType": "CFURL", "data": "app://localhost/profile/current"}]],
    }, title="Fixture"))
    assert frame.has_semantic_urls
    capture = frame.capture("window", [])
    assert capture.lookup_evidence(["AXURL"])["nodes"][0]["values"]["AXURL"] == "../target?q=1"
    assert capture.node_view("root", ["url"]) == {"url": "app://localhost/target?q=1"}


def test_alias_rewrite_reuses_the_original_source_index():
    original = snapshot_from_wire(wire(title="Fixture"))
    numbered = original.with_aliases({"root": "1"})
    assert numbered.capture is original.capture
    assert numbered.refs == {"1": "root"}
    assert original.refs == {"0": "root"}


def test_shared_facts_remain_immutable_and_display_edits_are_local():
    snapshot = snapshot_from_wire(wire(title="Original"))
    view = snapshot.capture.node_view("root")
    view["name"] = "local edit"
    with pytest.raises(TypeError):
        snapshot.refs["0"] = "different target"
    assert snapshot.capture.node_view("root", ["name"]) == {"name": "Original"}
    tree = DisplayDocument.tree(prepare_snapshot(snapshot))
    assert "Original" in tree.text and "local edit" not in tree.text


def test_byte_budget_counts_repeated_records_and_unicode_exactly():
    snapshot = snapshot_from_wire(wire())
    prepared = prepare_snapshot(snapshot)
    tree = DisplayDocument.tree(prepared)
    for document in (
        DisplayDocument(()),
        DisplayDocument.join(("中文🙂\n\ud800", "\ud83d\ude42")),
        tree,
        DisplayDocument.join((tree, tree, DisplayText("details_hint", (prepared, "0")))),
    ):
        assert document.estimated_bytes == len(utf8(dumps(document.to_data())))
