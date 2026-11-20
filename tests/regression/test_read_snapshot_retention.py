# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check native read ownership through rendering, publication, and lookup."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from compat_read_fixtures import element, scalar
from computer_use.errors import ComputerUseError
from computer_use.interaction.controller import SessionController
from computer_use.interaction.read.accessibility.preparation import prepare_snapshot
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.numbering.references import DisplayReferences, read_references
from computer_use.interaction.read.pages import ContentPages
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.presentation.element_details import CapturedElementDetails
from computer_use.interaction.read.presentation.rendering.document import prepare_document
from computer_use.native.contracts import Binding
from computer_use.native.query.observed import LookupFactsUnavailable
from computer_use.session.observation import lazy_observation
from computer_use.session.observed_lookup import captured_lookup_scopes
from computer_use.session.operations import current_operation_budget, operation_budget

from regression.compatibility.test_sparse_relation_pages import page
from regression.test_compat_observed_name_lookup import captured_tree
from regression.test_migrated_read_details import read_with_issue
from regression.test_migrated_read_provider_facts import property_row, provider_value, value


TARGET = Binding("session", "app", "window", "surface", "root")


def test_native_read_keeps_one_opaque_capture_through_admission_and_delivery(native_read):
    raw = {
        "kind": "observation",
        "session": TARGET.session,
        "platform": "macos",
        "roots": [TARGET.node],
        "nodes": [element(TARGET.node, "AXWindow", "Settings")],
        "read": {
            "intervalNs": [0, 1000000],
            "details": [["surface", scalar(TARGET.surface)], ["observation_id", scalar("capture")]],
        },
    }
    body = json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode()
    frame = native_read.open_read_frame("json", body, 8192)
    assert frame.diagnostic_bytes() == body
    with pytest.raises(ValueError, match="changed its requested scope or budget"):
        frame.admit_nodes(TARGET.session, None, None, "wrong", TARGET.node, "macos", 1, [], None)
    assert frame.admit_nodes(TARGET.session, None, None, TARGET.surface, TARGET.node, "macos", 1, [], None) == []

    capture = frame.capture("window", [])
    assert capture.source_tokens == [TARGET.node]
    assert capture.observed == {"id": "capture", "startedAt": 0, "completedAt": 1}
    assert not hasattr(capture, "facts")
    assert not hasattr(capture, "observation")
    snapshot = NativeSnapshot.number(capture, {TARGET.node: "0"})

    async def render():
        with operation_budget(30000):
            budget = current_operation_budget()
            assert budget is not None
            assert snapshot.numbered.preview_tokens(budget.remaining) == [TARGET.node]
            return snapshot.numbered.prepare(checkpoint=budget.remaining)

    native = asyncio.run(render())
    assert native.render(False).text() == "0 window Settings, Secondary Actions: <unavailable>"
    details = native.render(True).details()
    assert 'Native read issues {"session":"session","observation":"capture"}' in details
    assert "0 actions: not_read: Action catalogue was not read" in details
    prepared = prepare_snapshot(snapshot)
    assert prepared.snapshot.capture is capture
    elements = snapshot.retained(TARGET)
    assert [(row["reference"], row["node"], row["container"]) for row in elements] == [("0", "root", True)]
    assert snapshot.public_pages(TARGET) == []


@pytest.mark.usefixtures("native_read")
def test_native_numbering_has_one_owner_for_mixed_capture_keys():
    refs = read_references("session", "macos")
    assert refs.reference("source") == "0"
    assert refs.scoped_batch("window", "window", ("source", "other")) == ("1", "2")
    assert refs.reference_batch((("window", "window", "other"), "source", "next")) == ("2", "0", "3")
    assert read_references("next-session", "macos").reference("source") == "0"


def test_native_publication_keeps_exact_cursor_and_captured_lookup_scopes(native_read):
    raw = page(["first", "third"], total=7, cursor="native-children")
    raw["nodes"][1] = element("first", "AXTextField", "Input")
    raw["nodes"][1]["properties"].append({"key": ["macos.ax", "AXValue"], "value": scalar("captured")})
    raw["nodes"][2] = element("third", "AXTextArea", "Long input")
    raw["nodes"][2]["properties"].append(
        property_row(
            "macos.ax",
            "AXValue",
            value("Captured"),
            read={
                "complete": False,
                "issues": [
                    {"reason": "partial", "continuation": provider_value({"textRef": "text-tail", "totalUnits": 20})}
                ],
            },
        )
    )
    body = json.dumps(raw).encode()
    capture = native_read.open_read_frame("json", body, len(body)).capture("window", [])
    snapshot = NativeSnapshot.number(capture, {"root": "9", "first": "1", "third": "2"})
    state = SimpleNamespace(refs={}, lookup_captures=set())
    controller = SimpleNamespace(
        pages=ContentPages(),
        transport=SimpleNamespace(capabilities=("nativeNodePages",)),
        bindings=SimpleNamespace(remember=lambda owner, records: owner.refs.update(records)),
        _native_snapshots=SessionController._native_snapshots,
    )
    SessionController.remember_observation(controller, state, TARGET, snapshot)
    assert tuple(state.refs) == ("9", "1", "2")
    assert state.refs["9"]["children"].offset == 3
    assert state.refs["1"]["text"].value == "captured"
    assert state.refs["2"]["text"].text_ref == "text-tail"
    assert state.refs["2"]["text"].total_units == 20
    assert controller.pages.resolve("p1", TARGET)["cursor"] == "native-children"
    assert '9 children: next offset 3 of 7; cursor="p1" (live order)' in controller.pages.hints(TARGET, snapshot)
    for other in (state.refs["1"]["binding"], replace(TARGET, surface="other-surface")):
        with pytest.raises(ComputerUseError, match="different element or window"):
            controller.pages.resolve("p1", other)

    # A complete capture can confirm a unique path. Its lookup lifetime is
    # independent from the continuation retained from the earlier partial read.
    tree, button = captured_tree(TARGET, DisplayReferences(TARGET.session))
    SessionController.remember_observation(controller, state, TARGET, tree)
    (lookup,) = captured_lookup_scopes(state, ((TARGET, tree),), (tree.capture,), 3, ("nativeNodePages",))
    path = ({"role": "button", "name": "Save"},)
    selected = asyncio.run(lookup.resolve(path, lambda: 500))
    assert selected["binding"] == replace(TARGET, node=button)
    assert selected is state.refs[selected["source"]["ref"]]
    assert asyncio.run(lookup.resolve(path, lambda: 500)) is selected
    lookup.release()
    with pytest.raises(LookupFactsUnavailable):
        asyncio.run(lookup.resolve(path, lambda: 500))
    assert controller.pages.resolve("p1", TARGET)["cursor"] == "native-children"


def test_deferred_details_keep_issue_evidence_and_original_reference(native_read):
    def capture_observation():
        prepared = prepare_snapshot(read_with_issue(native_read))
        body = DisplayDocument.tree(prepared)
        details = DisplayDocument.tree(prepared, details=True)
        observed = lazy_observation(
            body, details_document=details, element_details=CapturedElementDetails.from_document(body)
        )
        assert "provider detail 中文" not in observed.text
        assert not prepare_document(body).snapshots[id(prepared)].includes_details
        return observed

    observed = capture_observation()
    details = observed.read_details(element=0).text
    assert "Captured element 0" in details
    assert "provider detail 中文" in details
    assert "original-capture" in details
    assert observed.read_details(element=0).text == details
