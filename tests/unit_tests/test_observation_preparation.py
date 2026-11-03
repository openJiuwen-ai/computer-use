# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Explicit observation preparation keeps identity, effect evidence and read-only defaults."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.interaction.read.views.observation_preparation import ObservationPreparationPipeline
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline
from computer_use.native.contracts import Binding
from computer_use.native.scopes import FixedWindowInvalidated
from computer_use.protocol.execution import decode_execution_result, decode_observation_preparation_request

MODULE = "computer_use.interaction.read.views.pipeline"
TARGET = Binding("session", "instance", "root", "surface")
WIRE = {
    "session": "session",
    "instance": "instance",
    "rootWindow": "root",
    "surface": "surface",
    "node": None,
    "capture": None,
}
RECEIPT = {
    "kind": "observationPreparation",
    "binding": WIRE,
    "activation": "requested",
    "raiseStatus": "requested",
    "foreground": True,
    "redraw": "unknown",
}


def setup(monkeypatch):
    calls = []

    @asynccontextmanager
    async def open_scope(target, timeout):
        calls.append((target, timeout))
        yield SimpleNamespace(token="operation", target=target, remaining=lambda: 2800)

    port = SimpleNamespace(calls=SimpleNamespace(open=open_scope), prepare_observation=AsyncMock(return_value=RECEIPT))
    item = {"instance": "instance", "window": "root", "surface": "surface", "node": "node"}
    directory = {"session": "session", "application": "application", "surfaces": [item]}
    instance = SimpleNamespace(anchor=SimpleNamespace(root=TARGET), directory=directory)
    ui = SimpleNamespace(_instance=instance, _anchor=None)
    resolve = AsyncMock(return_value=(directory, None))
    monkeypatch.setattr(MODULE + ".resolve", resolve)
    monkeypatch.setattr(MODULE + ".allowed", lambda _: {"root": item})
    monkeypatch.setattr(MODULE + ".select_surface", lambda *_: SimpleNamespace(surface="surface"))
    pipeline = InterfaceReadPipeline(SimpleNamespace(port=port))
    pipeline._collect = AsyncMock(return_value="observation")
    return pipeline, ui, port, resolve, calls, directory


def test_default_observation_does_not_prepare_or_add_a_directory_query(monkeypatch):
    pipeline, ui, port, resolve, calls, _ = setup(monkeypatch)
    assert asyncio.run(pipeline.run(ui, "image", False, None)) == "observation"
    port.prepare_observation.assert_not_awaited()
    assert not calls and resolve.await_count == 1
    assert pipeline._collect.call_args.args[6] is None


def test_explicit_preparation_refreshes_original_scope_before_observing(monkeypatch):
    pipeline, ui, port, resolve, calls, original_directory = setup(monkeypatch)
    fresh_directory = {**original_directory, "observed": {"id": "fresh"}}
    resolve.side_effect = [(original_directory, "old pixels"), (fresh_directory, None)]
    assert asyncio.run(pipeline.run(ui, "both", False, None, allow_foreground=True)) == "observation"
    assert calls == [(TARGET, 3000)]
    port.prepare_observation.assert_awaited_once_with("operation", TARGET, 2800)
    args = pipeline._collect.call_args.args
    assert args[4] is fresh_directory and args[5] is None and args[6] is RECEIPT
    assert ui._instance.anchor.root == TARGET


def test_later_window_closure_preserves_lifetime_error_and_preparation_effect(monkeypatch):
    pipeline, ui, _, resolve, _, directory = setup(monkeypatch)
    error = FixedWindowInvalidated(TARGET, "root closed")
    resolve.side_effect = [(directory, None), error]
    with pytest.raises(FixedWindowInvalidated) as failure:
        asyncio.run(pipeline.run(ui, "image", False, None, allow_foreground=True))
    assert failure.value is error and error.binding == TARGET
    assert error.effect == "possible" and error.observation_preparation["redraw"] == "unknown"
    pipeline._collect.assert_not_awaited()


def test_cleanup_failure_keeps_returned_preparation_receipt():
    @asynccontextmanager
    async def failed_cleanup(target, timeout):
        yield SimpleNamespace(token="operation", target=target, remaining=lambda: 2000)
        raise RuntimeError("cleanup unconfirmed")

    port = SimpleNamespace(
        calls=SimpleNamespace(open=failed_cleanup), prepare_observation=AsyncMock(return_value=RECEIPT)
    )
    with pytest.raises(RuntimeError) as failure:
        asyncio.run(ObservationPreparationPipeline(port).arun(TARGET))
    assert failure.value.observation_preparation["activation"] == "requested"
    assert "Earlier observation preparation" in failure.value.__notes__[0]


def test_explicit_preparation_without_selected_root_stays_observation_only(monkeypatch):
    pipeline, ui, port, _, calls, _ = setup(monkeypatch)
    ui._instance.anchor = None
    asyncio.run(pipeline.run(ui, "image", False, None, allow_foreground=True))
    assert not calls
    port.prepare_observation.assert_not_awaited()


@pytest.mark.parametrize("field,value", [("surface", None), ("rootWindow", None), ("node", "node"), ("capture", "old")])
def test_preparation_wire_requires_original_window_scope(field, value):
    with pytest.raises(ValueError):
        decode_observation_preparation_request({"operation": "operation", "target": {**WIRE, field: value}})


def test_preparation_receipt_never_claims_redraw_confirmation():
    assert decode_execution_result("prepareObservation", RECEIPT) == RECEIPT
    with pytest.raises(ValueError):
        decode_execution_result("prepareObservation", {**RECEIPT, "redraw": "confirmed"})
