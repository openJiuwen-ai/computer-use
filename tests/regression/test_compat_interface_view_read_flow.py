# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The interface read keeps its caller, exact window scope, and exception context."""

import asyncio
import sys
from types import SimpleNamespace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.views import observation_preparation, pipeline as views
from computer_use.native.contracts import Binding

ORIGINAL = Binding("session", "instance", "window", "surface")


def stale_target():
    return ComputerUseError({
        "code": "stale_target", "effect": "none", "target": "native request", "detail": "directory lost",
    })


@pytest.mark.parametrize("foreground", [False, True])
def test_interface_read_prepares_the_refreshed_fixed_target_in_the_caller(monkeypatch, foreground):
    log = []
    source, fresh = {"platform": "macos"}, {"platform": "macos", "sample": "fresh"}
    first, receipt, result = object(), {}, object()
    member = {"surface": "surface"}
    new_instance = SimpleNamespace(anchor=None)
    ui = SimpleNamespace(_instance=object(), _anchor="window", _strict_window=True)
    controller = SimpleNamespace(port=object())
    reader = views.InterfaceReadPipeline(controller)

    async def check():
        caller = asyncio.current_task()
        menu_path, callback = ["File"], object()

        async def resolve(actual_controller, handle, view, *, allow_windows_launch=False):
            assert asyncio.current_task() is caller
            assert actual_controller is controller and handle is ui and view == "both"
            assert allow_windows_launch is False
            if not log:
                log.append("resolve")
                ui._instance = new_instance
                return source, first
            log.append("recheck")
            return fresh, object()

        def select(directory, anchor, *, follow_modal):
            assert directory is source and anchor == "window" and follow_modal is False
            log.append("select")
            return SimpleNamespace(surface="surface")

        def member_target(instance, item):
            assert instance is new_instance and item is member
            return ORIGINAL

        async def prepare(binding):
            assert asyncio.current_task() is caller and binding is ORIGINAL
            log.append("prepare")
            return receipt

        async def collect(handle, view, full, menu, directory, initial, preparation, related, on_observation):
            assert asyncio.current_task() is caller and sys.exception() is None
            assert handle is ui and view == "both" and full is True
            assert menu is menu_path and related is None and on_observation is callback
            if foreground:
                assert directory is fresh and initial is None and preparation is receipt
            else:
                assert directory is source and initial is first and preparation is None
            log.append("collect")
            return result

        monkeypatch.setattr(views, "resolve", resolve)
        monkeypatch.setattr(observation_preparation, "resolve", resolve)
        monkeypatch.setattr(views, "allowed", lambda _ui: {"window": member})
        monkeypatch.setattr(views, "select_surface", select)
        monkeypatch.setattr(views, "member_target", member_target)
        monkeypatch.setattr(views, "ObservationPreparationPipeline", lambda _port: SimpleNamespace(arun=prepare))
        reader._collect = collect
        assert await reader.run(ui, "both", True, menu_path, allow_foreground=foreground, on_observation=callback) is result
        assert log == (["resolve", "select", "prepare", "recheck", "collect"] if foreground else ["resolve", "collect"])

    asyncio.run(check())


def test_stale_close_family_does_not_chain_a_swallowed_metadata_timeout(monkeypatch):
    original_error, metadata_error = stale_target(), TimeoutError("metadata deadline")
    directory = {"platform": "macos", "session": "session", "application": "application"}
    ui = SimpleNamespace(_instance=SimpleNamespace(directory=directory))
    related = SimpleNamespace(
        window_family_only=True, original=ORIGINAL, original_window=None, window_family_application="application",
    )
    log = []

    async def check():
        caller = asyncio.current_task()

        async def resolve(*_, allow_windows_launch=False):
            raise original_error

        async def applications(*, include_ended):
            assert asyncio.current_task() is caller and sys.exception() is original_error
            assert include_ended is True
            log.append("metadata")
            raise metadata_error

        def ended(session, application):
            assert asyncio.current_task() is caller and sys.exception() is original_error
            assert (session, application) == ("session", "application")
            log.append("ended")
            return False

        controller = SimpleNamespace(
            transport=SimpleNamespace(session="session"),
            interfaces=SimpleNamespace(windows=SimpleNamespace(application_ended=ended)),
            reader=SimpleNamespace(directory=SimpleNamespace(applications=applications)),
        )
        monkeypatch.setattr(views, "resolve", resolve)
        monkeypatch.setattr(observation_preparation, "resolve", resolve)
        monkeypatch.setattr(views, "original_lifecycle", lambda *_: "unknown")
        with pytest.raises(ComputerUseError) as raised:
            await views.InterfaceReadPipeline(controller).run(ui, "tree", False, None, related=related)
        assert raised.value.code == "observation_incomplete"
        assert raised.value.__context__ is original_error and raised.value.__cause__ is None
        assert metadata_error.__context__ is original_error and log == ["ended", "metadata", "ended"]

    asyncio.run(check())


def test_prepared_collect_can_replace_an_error_with_its_original_cause(monkeypatch):
    original_error, annotated_error = RuntimeError("read failed"), RuntimeError("prepare evidence")
    member, receipt = {"surface": "surface"}, {}
    ui = SimpleNamespace(_instance=object(), _anchor="window")

    async def check():
        caller = asyncio.current_task()

        async def resolve(*_, allow_windows_launch=False):
            return {"platform": "macos"}, None

        async def prepare(_):
            return receipt

        async def collect(*_):
            raise original_error

        def annotate(error, preparation):
            assert asyncio.current_task() is caller and sys.exception() is error is original_error
            assert preparation is receipt
            return annotated_error

        monkeypatch.setattr(views, "resolve", resolve)
        monkeypatch.setattr(observation_preparation, "resolve", resolve)
        monkeypatch.setattr(views, "allowed", lambda _ui: {"window": member})
        monkeypatch.setattr(views, "select_surface", lambda *_a, **_kw: SimpleNamespace(surface="surface"))
        monkeypatch.setattr(views, "member_target", lambda *_: ORIGINAL)
        monkeypatch.setattr(views, "ObservationPreparationPipeline", lambda _port: SimpleNamespace(arun=prepare))
        monkeypatch.setattr(views, "annotate_preparation_failure", annotate)
        reader = views.InterfaceReadPipeline(SimpleNamespace(port=object()))
        reader._collect = collect
        with pytest.raises(RuntimeError) as raised:
            await reader.run(ui, "tree", False, None, allow_foreground=True)
        assert raised.value is annotated_error
        assert raised.value.__cause__ is original_error and raised.value.__context__ is original_error
        assert raised.value.__suppress_context__

    asyncio.run(check())
