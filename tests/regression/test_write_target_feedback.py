# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A receipt label names the actual bound object, never a replacement slot."""

from types import SimpleNamespace

import pytest
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.pipeline import _target_label
from computer_use.native.contracts import Binding, Intent
from computer_use.session.interfaces import WindowIdentity


@pytest.mark.parametrize("changed", [None, "surface", "instance", "missing"])
def test_receipt_label_does_not_borrow_a_replacement_windows_public_reference(changed):
    target = Binding("session", "original-app-instance", "root", "original-surface", "button")
    snapshot = {"binding": target}
    member = {"window": target.root_window, "instance": target.instance, "surface": target.surface}
    if changed in ("surface", "instance"):
        member[changed] = "replacement"
    instance = SimpleNamespace(
        members={} if changed == "missing" else {"root": member},
        windows={7: "root"},
        refs={3: snapshot},
    )
    request = ExecuteAction(SimpleNamespace(_instance=instance), Intent("click", {}), target, snapshot=snapshot)
    assert _target_label(request, target) == ("Window 7 / Element 3" if changed is None else None)
    assert request.target is target and request.snapshot is snapshot


@pytest.mark.parametrize("changed", [None, "surface", "instance", "missing"])
def test_image_receipt_alias_never_borrows_a_replacement_host(changed):
    target = Binding("session", "popup-instance", "popup", "popup-surface", capture="menu-image")
    host = {"window": "host", "surface": "host-surface", "instance": "host-instance"}
    displayed = WindowIdentity.of(host)
    if changed in ("surface", "instance"):
        host[changed] = "replacement"
    members = {"popup": {"window": "popup", "surface": target.surface, "instance": target.instance}}
    if changed != "missing":
        members["host"] = host
    instance = SimpleNamespace(members=members, windows={0: "host", 1: "popup"})
    request = ExecuteAction(SimpleNamespace(_instance=instance), Intent("click", {}), target, display_window=displayed)
    assert _target_label(request, target) == ("Window 0" if changed is None else "Window 1")
    assert request.target is target and request.target.root_window == "popup"
    # Computer input still describes the global operation, not this optional
    # display alias or the App's captured native receiver.
    global_request = ExecuteAction(None, Intent("click", {}, True), target, display_window=displayed)
    assert _target_label(global_request, target) == "Computer"


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("failed", [False, True])
def test_addressed_write_preserves_caller_task_and_input_effect_when_labeling_result(borrowed, failed):
    import asyncio
    from contextvars import ContextVar

    from computer_use.errors import ComputerUseError
    from computer_use.identity import FrozenRecord
    from computer_use.interaction.write.request.model import ActionPlan
    from computer_use.interaction.write.request.pipeline import ExecuteNativeWrite
    from computer_use.pipeline import Control

    target = Binding("session", "instance", "root", "surface", "button")
    owner = SimpleNamespace(_instance=SimpleNamespace(
        members={"root": {"surface": "surface", "instance": "instance"}}, windows={0: "root"},
    ))
    request = ExecuteAction(owner, Intent("closeWindow"), target, query_timeout_ms=57, forward_current=True)
    mechanism, control = Intent("click"), Control(call_id="call")
    remaining = lambda: 125  # noqa: E731 - borrowed callback identity matters
    plan = ActionPlan(request, remaining=remaining, target=target, mechanism=mechanism)
    receipt = {"status": "dispatched", "detail": "sent exactly once", "target": "raw", "intent": "click"}
    cause = RuntimeError("transport ended after dispatch")
    error = ComputerUseError({
        "code": "execution_unknown", "effect": "possible", "target": "raw", "detail": "input may have arrived",
    }, cause=cause)
    error.action_receipt = FrozenRecord(receipt)
    context = ContextVar("input-context")
    calls = []
    pipeline = object()

    async def run():
        caller = asyncio.current_task()
        context.set("same-write")

        async def dispatch(*args, **kwargs):
            assert asyncio.current_task() is caller and context.get() == "same-write"
            assert node.children == (pipeline,)
            calls.append((args, kwargs))
            await asyncio.sleep(0)
            if failed:
                raise error
            return receipt

        engine = SimpleNamespace(pipeline=pipeline)
        setattr(engine, "execute_in_call" if borrowed else "execute", dispatch)
        node = ExecuteNativeWrite(SimpleNamespace(engine=engine), borrowed=borrowed)
        if failed:
            with pytest.raises(ComputerUseError) as caught:
                await node.arun(plan, control)
            assert caught.value is error and error.__cause__ is cause and error.effect == "possible"
            assert error._method == "closeWindow" and error.target == "Window 0 / raw"
            result = error.action_receipt
        else:
            result = await node.arun(plan, control)
            assert result is receipt
        assert dict(result) == {
            "status": "dispatched", "detail": "sent exactly once", "target": "Window 0", "intent": "closeWindow",
        }
        assert node.children == ()
        (args, kwargs), = calls
        assert args == ((mechanism, target, remaining) if borrowed else (mechanism, target))
        assert kwargs["path"] is None and kwargs["query_timeout_ms"] == 57 and kwargs["forward_current"] is True
        assert kwargs["_control"].call_id == "call"

    asyncio.run(run())
