# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A native capture retains the original SDK bindings and continuation scope."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from computer_use.errors import ComputerUseError
from computer_use.interaction.controller import SessionController
from computer_use.interaction.read.accessibility.snapshot import NativeSnapshot
from computer_use.interaction.read.pages import ContentPages
from computer_use.native.contracts import Binding
from computer_use.native.query.observed import LookupFactsUnavailable
from computer_use.session.observed_lookup import captured_lookup_scopes, scope_key

TARGET = Binding("session", "app", "window", "surface", "root")


def test_rust_snapshot_preserves_host_bindings_page_scope_and_captured_lookup():
    continuation = {"cursor": "native-children", "offset": 3, "total": 7}
    observed = {"id": "original"}
    rows = [
        {
            "reference": "9",
            "node": "root",
            "container": True,
            "scope_kind": "window",
            "observed": observed,
            "text": {"text_ref": "native-text", "total_units": 10},
            "children": continuation,
        },
        {
            "reference": "1",
            "node": "child",
            "container": False,
            "scope_kind": "window",
            "observed": observed,
            "text": {"value": "captured"},
            "children": None,
        },
    ]
    pages = [{"reference": "9", "kind": "children", "node": "root", "property": "AXChildren", **continuation}]
    export = Mock(return_value=(rows, pages))
    resolve = Mock(return_value=("matched", "child", None))
    capture = SimpleNamespace(backend="macos", session="session", source_tokens=("root", "child"), resolve_observed=resolve)
    snapshot = NativeSnapshot(capture, SimpleNamespace(retained_bindings=export), {"root": "9", "child": "1"})
    state = SimpleNamespace(refs={}, lookup_captures=set())
    controller = SimpleNamespace(
        pages=ContentPages(),
        transport=SimpleNamespace(capabilities=("nativeNodePages",)),
        bindings=SimpleNamespace(remember=lambda owner, records: owner.refs.update(records)),
        _native_snapshots=SessionController._native_snapshots,
    )

    SessionController.remember_observation(controller, state, TARGET, snapshot)
    assert tuple(state.refs) == ("9", "1")
    assert state.refs["9"]["children"].offset == 3
    assert state.refs["9"]["text"].text_ref == "native-text"
    assert state.refs["1"]["text"].value == "captured"
    assert controller.pages.resolve("p1", TARGET)["cursor"] == "native-children"
    assert '9 children: next offset 3 of 7; cursor="p1" (live order)' in controller.pages.hints(TARGET, snapshot)
    export.assert_called_once_with(scope_key(TARGET), True)
    with pytest.raises(ComputerUseError, match="different element or window"):
        controller.pages.resolve("p1", state.refs["1"]["binding"])

    (lookup,) = captured_lookup_scopes(state, ((TARGET, snapshot),), (capture,), 3, ("nativeNodePages",))
    path = ({"role": "button", "name": "Save"},)
    def remaining():
        return 500

    assert asyncio.run(lookup.resolve(path, remaining)) is state.refs["1"]
    assert asyncio.run(lookup.resolve(path, remaining)) is state.refs["1"]
    resolve.assert_called_once_with(scope_key(TARGET), list(path), ("nativeNodePages",), remaining)
    lookup.release()
    with pytest.raises(LookupFactsUnavailable):
        asyncio.run(lookup.resolve(path, remaining))
