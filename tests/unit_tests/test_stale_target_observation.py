# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""A real target ending returns evidence without renewing or replaying its input."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.requests import ActOnElement, ObserveElement, ObserveText, ResolveElement
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.views.publication import publish
from computer_use.interaction.requests import ExecuteAction
from computer_use.native.contracts import Binding, Intent, Knowledge
from computer_use.session.events import set_observation_handler
from computer_use.session.interfaces import DirectoryMembership, WindowIdentity, WindowLifetime, WindowMembership
from computer_use.session.observation import CapturedTextDocument, lazy_observation
from computer_use.session.observation_data import retain_observation_data
from unit_tests.mcp import test_action_receipts as receipt_tests
from unit_tests.test_d34_read_contracts import UI, directory, setup, window


def scenario():
    original = window("original")
    controller, app, instance = setup(directory(original))
    target = Binding("session", original["instance"], original["window"], original["surface"], "button")
    snapshot = {
        "binding": target, "container": False, "window_identity": WindowIdentity.of(original), "scope_kind": "window"
    }
    instance.refs["7"] = snapshot
    controller.engine.execute = AsyncMock(return_value={"status": "dispatched", "target": "button", "detail": "Once"})
    observation = lazy_observation(CapturedTextDocument("Current related content"))
    retain_observation_data(observation, (), (), directory=instance.directory)
    controller.interfaces.read = AsyncMock(return_value=observation)
    return controller, app, instance, snapshot, observation


def failure(code="stale_target", effect="none"):
    return ComputerUseError(
        {
            "code": code, "effect": effect, "target": "original button",
            "detail": "Original target ended", "next": "read again",
        },
        method="click",
        cause=ValueError("native evidence"),
    )


@pytest.mark.parametrize("code", ["stale_target", "observation_incomplete", "blocked_by_modal"])
def test_native_admission_failure_has_current_observation_without_replay(code):
    async def run():
        controller, app, _, snapshot, observation = scenario()
        error = failure(code)
        context, impact = object(), {"attempts": ()}
        error._failure_context, error.impact = context, impact
        controller.engine.execute.side_effect = error
        with pytest.raises(ComputerUseError) as caught:
            await controller.run(ExecuteAction(app, Intent("click", {}), snapshot["binding"]))
        assert caught.value is error
        assert error.effect == "none" and error.detail == "Original target ended"
        assert error.__cause__.args == ("native evidence",)
        assert error._failure_context is context and error.impact is impact
        assert error.observation is observation and error.observation_status == "available"
        assert error.window_lifecycle == "observed"
        assert "next" not in vars(error)
        assert "read again" not in str(error)
        assert str(error).startswith("click [Window 0 / original button]:")
        controller.engine.execute.assert_awaited_once()
        controller.interfaces.read.assert_awaited_once()
        assert controller.interfaces.read.call_args.args == (app, "tree", False, None)

    asyncio.run(run())


def test_preflight_window_loss_is_inside_observation_boundary():
    async def run():
        controller, app, instance, snapshot, observation = scenario()
        old = snapshot["window_identity"]
        replacement = window("replacement")
        current = directory(replacement)
        retain_observation_data(observation, (), (), directory=current)

        async def close_during_refresh(_controller, owner):
            assert owner is app
            instance.reconcile(current, DirectoryMembership(True, ()))

        with patch(
            "computer_use.interaction.write.request.passes.validate_element_scope.refresh",
            side_effect=close_during_refresh,
        ) as refresh, pytest.raises(ComputerUseError) as caught:
            await controller.run(ActOnElement(app, snapshot, "click", {}))
        error = caught.value
        assert error.code == "stale_target" and error.effect == "none"
        assert error.observation is observation and error.window_lifecycle == "closed"
        assert instance.evidence[old].lifetime is WindowLifetime.RETIRED
        assert snapshot["binding"].root_window == "original"
        assert error.choices and error.choices[0]["title"] == "Same title"
        controller.engine.execute.assert_not_awaited()
        controller.interfaces.read.assert_awaited_once()
        refresh.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("fixed", [False, True])
def test_retained_number_can_be_materialized_after_window_ends_then_action_returns_evidence(fixed):
    async def run():
        controller, app, instance, snapshot, observation = scenario()
        original = snapshot["window_identity"]
        owner = app
        if fixed:
            owner = UI(
                _app=app, _instance=instance, _anchor=original.window, _context=original.window, _image=None,
                _window_identity=Binding("session", original.instance, original.window, original.surface),
            )
            controller.register(owner, {"selector": "Fixture"})
        instance.retire(original)
        retained = await controller.run(ResolveElement(owner, 7, 0))
        assert retained["binding"] == snapshot["binding"]
        assert retained["window_identity"] == original
        with pytest.raises(ComputerUseError) as caught:
            await controller.run(ActOnElement(owner, retained, "click", {}))
        assert caught.value.observation is observation
        assert caught.value.effect == "none"
        assert snapshot["binding"].root_window == "original"
        assert instance.evidence[original].lifetime is WindowLifetime.RETIRED
        controller.engine.execute.assert_not_awaited()
        assert controller.interfaces.read.call_args.args[0] is app

    asyncio.run(run())


def test_unknown_number_is_not_manufactured():
    async def run():
        controller, app, _, snapshot, _ = scenario()
        with pytest.raises(ComputerUseError):
            await controller.run(ResolveElement(app, 999, 0))
        assert snapshot["binding"].root_window == "original"
        controller.engine.execute.assert_not_awaited()
        controller.interfaces.read.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("retired", [False, True])
def test_fixed_window_retained_descendant_number_fails_inside_observed_action(retired):
    async def run():
        controller, app, instance, snapshot, observation = scenario()
        parent = window("parent")
        child = window("original", parent="parent")
        original = snapshot["window_identity"]
        current = directory(parent, child)
        instance.reconcile(
            current,
            DirectoryMembership(True, tuple(WindowMembership(WindowIdentity.of(item), Knowledge.YES)
                                             for item in current["surfaces"])),
        )
        owner = UI(
            _app=app, _instance=instance, _anchor="parent", _context="parent", _image=None,
            _window_identity=Binding("session", parent["instance"], "parent", parent["surface"]),
        )
        controller.register(owner, {"selector": "Fixture"})
        if retired:
            instance.retire(original)
        else:
            instance.unconfirm(original)
        retained = await controller.run(ResolveElement(owner, 7, 0))
        assert retained["binding"] == snapshot["binding"]
        with patch(
            "computer_use.interaction.write.request.passes.validate_element_scope.refresh", new_callable=AsyncMock
        ), pytest.raises(ComputerUseError) as caught:
            await controller.run(ActOnElement(owner, retained, "click", {}))
        assert caught.value.code == ("stale_target" if retired else "observation_incomplete")
        assert caught.value.observation is observation
        assert caught.value.effect == "none"
        assert instance.evidence[original].lifetime is (
            WindowLifetime.RETIRED if retired else WindowLifetime.RETAINED
        )
        controller.engine.execute.assert_not_awaited()
        controller.interfaces.read.assert_awaited_once()
        assert controller.interfaces.read.call_args.args[0] is app

    asyncio.run(run())


@pytest.mark.parametrize(
    "intent", ["click", "scroll", "insert", "replace", "paste", "key", "focus", "openMenu", "action"]
)
def test_fixed_window_handle_construction_does_not_grant_outside_input(intent):
    async def run():
        controller, app, instance, snapshot, observation = scenario()
        other = window("other")
        current = directory(window("original"), other)
        instance.reconcile(
            current,
            DirectoryMembership(True, tuple(WindowMembership(WindowIdentity.of(item), Knowledge.YES)
                                             for item in current["surfaces"])),
        )
        owner = UI(
            _app=app, _instance=instance, _anchor="other", _context="other", _image=None,
            _window_identity=Binding("session", other["instance"], "other", other["surface"]),
        )
        controller.register(owner, {"selector": "Fixture"})
        retained = await controller.run(ResolveElement(owner, 7, 0))
        with pytest.raises(ComputerUseError) as caught:
            await controller.run(ActOnElement(owner, retained, intent, {}))
        assert caught.value.code == "stale_target" and caught.value.effect == "none"
        assert caught.value.observation is observation
        assert retained["binding"] == snapshot["binding"]
        controller.engine.execute.assert_not_awaited()
        # Explicit reads use the same live scope guard; constructing a handle
        # cannot bypass that guard to read an unrelated Window either.
        with patch("computer_use.interaction.read.element.agent.AgentElementReadPipeline.run") as read, patch(
            "computer_use.interaction.read.text.agent.AgentTextReadPipeline.run"
        ) as read_text:
            for request in (ObserveElement(owner, retained, None), ObserveText(owner, retained, None, 4096)):
                with pytest.raises(ComputerUseError):
                    await controller.run(request)
            read.assert_not_called()
            read_text.assert_not_called()

    asyncio.run(run())


def test_known_number_cannot_cross_application_or_session():
    async def run():
        controller, app, _, _, _ = scenario()
        other_instance = controller.interfaces.windows.application("session", "other-app", "Other")
        other = UI(_instance=other_instance, _anchor=None, _context=None, _image=None)
        controller.register(other, {"selector": "Other"})
        with pytest.raises(ComputerUseError):
            await controller.run(ResolveElement(other, 7, 0))
        controller.transport.session = "new-session"
        with pytest.raises(ComputerUseError):
            await controller.run(ResolveElement(app, 7, 0))
        controller.engine.execute.assert_not_awaited()
        controller.interfaces.read.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("effect", ["none", "partial", "possible"])
def test_failed_write_preserves_observation_history_and_original_error(effect):
    async def run():
        controller, app, _, snapshot, observation = scenario()
        error = failure(effect=effect)
        controller.engine.execute.side_effect = error
        delivered = []

        def handler(event):
            delivered.append(event["kind"])

        set_observation_handler(handler)
        try:
            with pytest.raises(ComputerUseError) as caught:
                await controller.run(ExecuteAction(app, Intent("click", {}), snapshot["binding"]))
        finally:
            set_observation_handler(None)
        assert caught.value is error and error.effect == effect
        assert error.__cause__.args == ("native evidence",)
        # Earlier facts are published before input. A later failure must not
        # issue the removed discard event or erase the native outcome.
        assert delivered == ["before-write"]
        assert error.observation is observation
        controller.engine.execute.assert_awaited_once()
        controller.interfaces.read.assert_awaited_once()

    asyncio.run(run())


def test_unrelated_window_update_uses_original_binding_with_one_refresh():
    async def run():
        controller, app, _, snapshot, _ = scenario()
        with patch(
            "computer_use.interaction.write.request.passes.validate_element_scope.refresh", new_callable=AsyncMock
        ) as refresh:
            await controller.run(ActOnElement(app, snapshot, "click", {}))
        refresh.assert_awaited_once_with(controller, app)
        assert controller.engine.execute.call_args.args[1] is snapshot["binding"]
        controller.engine.execute.assert_awaited_once()

    asyncio.run(run())


def test_temporary_unknown_membership_recovers_in_one_action_without_new_reference():
    async def run():
        controller, app, instance, snapshot, _ = scenario()
        original = snapshot["window_identity"]
        instance.unconfirm(original)
        retained = await controller.run(ResolveElement(app, 7, 0))

        async def recover(_controller, owner):
            assert owner is app
            instance.reconcile(
                directory(window("original")),
                DirectoryMembership(True, (WindowMembership(original, Knowledge.YES),)),
            )

        with patch(
            "computer_use.interaction.write.request.passes.validate_element_scope.refresh", side_effect=recover
        ) as refresh:
            await controller.run(ActOnElement(app, retained, "click", {}))
        assert retained is snapshot
        assert instance.evidence[original].lifetime is WindowLifetime.RETAINED
        assert controller.engine.execute.call_args.args[1] is snapshot["binding"]
        refresh.assert_awaited_once()
        controller.engine.execute.assert_awaited_once()
        controller.interfaces.read.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("available", [True, False])
def test_stale_recovery_status_distinguishes_partial_content_from_scope_metadata(available):
    async def run():
        controller, app, instance, snapshot, observation = scenario()
        error = failure()
        controller.engine.execute.side_effect = error
        retain_observation_data(
            observation,
            (),
            (),
            directory=instance.directory,
            partial=True,
            content_available=available,
            gaps=(ObservationGap("tree", "observation_incomplete", "Some controls unreadable"),),
        )
        with pytest.raises(ComputerUseError) as caught:
            await controller.run(ExecuteAction(app, Intent("click", {}), snapshot["binding"]))
        assert caught.value is error and error.observation is observation
        assert error.observation_status == ("partial" if available else "unavailable")
        if available:
            assert "next" not in vars(error)
            assert error.observation_error is None
        else:
            assert "next" not in vars(error)
            assert error.observation_error["code"] == "observation_incomplete"
        controller.engine.execute.assert_awaited_once()
        controller.interfaces.read.assert_awaited_once()

    asyncio.run(run())


def test_caller_shorter_deadline_is_not_renewed_by_write_or_failure_observation():
    async def run():
        controller, app, _, snapshot, _ = scenario()
        clock = [10.0]
        error = failure()
        controller.engine.execute.side_effect = error
        native_reads = []

        async def read(*_args, **_kwargs):
            with controller.port.calls.claim(30000) as remaining:
                # The enclosing caller's budget has expired. Actual native
                # dispatch checks this before opening any observation lease.
                remaining()
                native_reads.append("read dispatched")

        async def execute(*_args, **_kwargs):
            with controller.port.calls.claim(30000) as remaining:
                assert 0 < remaining() <= 1000
            clock[0] = 11.1
            raise error

        controller.engine.execute.side_effect = execute
        controller.interfaces.read.side_effect = read
        with (
            patch("computer_use.native.lifecycle.time.monotonic", side_effect=lambda: clock[0]),
            controller.port.calls.limit(1000),
        ):
            caller_limit = controller.port.calls._limit
            with pytest.raises(ComputerUseError) as caught:
                await controller.run(ExecuteAction(app, Intent("click", {}), snapshot["binding"]))
            assert controller.port.calls._limit is caller_limit
        assert caught.value is error and error.effect == "none"
        assert error.observation is None
        assert error.observation_error["code"] == "observation_timeout"
        assert not native_reads and not controller.port.calls.retired
        assert controller.port.calls._limit is None and not controller.port.calls.busy
        controller.engine.execute.assert_awaited_once()

    asyncio.run(run())


def test_write_and_failure_observation_share_original_deadline():
    async def run():
        controller, app, _, snapshot, observation = scenario()
        clock = [10.0]
        error = failure()

        async def execute(*_args, **_kwargs):
            with controller.port.calls.claim(30000) as remaining:
                assert remaining() == 30000
            clock[0] = 39.0
            raise error

        async def read(*_args, **_kwargs):
            with controller.port.calls.claim(30000) as remaining:
                assert 0 < remaining() <= 1000
            return observation

        controller.engine.execute.side_effect = execute
        controller.interfaces.read.side_effect = read
        with (
            patch("computer_use.native.lifecycle.time.monotonic", side_effect=lambda: clock[0]),
            pytest.raises(ComputerUseError) as caught,
        ):
            await controller.run(ExecuteAction(app, Intent("click", {}), snapshot["binding"]))
        assert caught.value is error and error.observation is observation
        assert controller.port.calls._limit is None
        assert not controller.port.calls.retired

    asyncio.run(run())


def test_failed_related_read_preserves_original_stale_reason_and_cause():
    async def run():
        controller, app, _, snapshot, _ = scenario()
        error = failure()
        controller.engine.execute.side_effect = error
        controller.interfaces.read.side_effect = TimeoutError("related observation deadline")
        with pytest.raises(ComputerUseError) as caught:
            await controller.run(ExecuteAction(app, Intent("click", {}), snapshot["binding"]))
        assert caught.value is error and error.code == "stale_target" and error.effect == "none"
        assert error.observation_error["code"] == "observation_timeout"
        assert "next" not in vars(error)
        assert error.__cause__.args == ("native evidence",)
        controller.engine.execute.assert_awaited_once()

    asyncio.run(run())


@pytest.mark.parametrize("code", ["invalid_request", "unsupported_operation", "input_unavailable"])
def test_unrelated_failures_do_not_trigger_a_generic_full_read(code):
    async def run():
        controller, app, _, snapshot, _ = scenario()
        controller.engine.execute.side_effect = failure(code)
        with pytest.raises(ComputerUseError):
            await controller.run(ExecuteAction(app, Intent("click", {}), snapshot["binding"]))
        controller.interfaces.read.assert_not_awaited()

    asyncio.run(run())


def test_mcp_delivers_related_content_in_the_same_failed_tool_call():
    async def run():
        harness = receipt_tests.ActionReceiptTests()
        await harness.asyncSetUp()
        try:
            controller, app, instance, snapshot, _ = scenario()
            controller.engine.execute.side_effect = failure()

            async def read(*_args, **_kwargs):
                return publish(
                    controller, app, "Current target choices: 344 Privacy", view="tree", directory=instance.directory
                )

            async def act():
                return await controller.run(ExecuteAction(app, Intent("click", {}), snapshot["binding"]))

            controller.interfaces.read.side_effect = read
            harness.runtime.task_namespace["perform"] = lambda: harness.runtime.executor.run(act)
            result = await harness.execute("perform()")
            assert result["isError"]
            text = "\n".join(block["text"] for block in result["content"] if block["type"] == "text")
            assert "Current target choices: 344 Privacy" in text
            assert "Original target ended" in text
            assert "Effect: none" in text
            assert "Next:" not in text
            assert "Action [dispatched]" not in text
            controller.engine.execute.assert_awaited_once()
            controller.interfaces.read.assert_awaited_once()
        finally:
            await harness.asyncTearDown()

    asyncio.run(run())
