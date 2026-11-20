# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public desktop capture resets failed state in the original task and error scope."""

import asyncio
import sys
from functools import partial
from types import SimpleNamespace

import pytest
from computer_use.interaction.agent.pipeline import AgentInterfacePipeline
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.identity import freeze
from computer_use.session import events
from computer_use.session.interfaces import ApplicationEntry, ApplicationReferences
from computer_use.session.windows import WindowReferences


def catalogue():
    return {
        "canvas": {"width": 100, "height": 100},
        "frontmost": None,
        "displays": [{"name": "display", "rect": {"x": 0, "y": 0, "width": 100, "height": 100}, "captured": True}],
        "windows": [
            {
                "app": "Provider",
                "title": "",
                "selector": "instance:provider",
                "rect": {"x": 10, "y": 10, "width": 60, "height": 50},
                "window": {
                    "window": "native-reference",
                    "instance": "window-instance",
                    "surface": "native-scope",
                    "node": None,
                    "title": "",
                    "kind": "window",
                    "parent": None,
                    "parentState": "unknown",
                    "modal": None,
                    "nativeWindow": True,
                    "accessibility": False,
                },
            }
        ],
        "omittedWindows": 0,
    }


@pytest.mark.parametrize("stage", ["retention", "capture", "record"])
@pytest.mark.parametrize("reset_fails", [False, True])
def test_desktop_failure_resets_in_the_original_exception_scope(stage, reset_fails):
    original = RuntimeError(f"{stage} failed")
    cleanup = ValueError("reset failed")
    calls = []

    async def run():
        task = asyncio.current_task()
        capture = object()
        observed = object()
        port = SimpleNamespace()

        def retain():
            calls.append("retention")
            if stage == "retention":
                raise original
            return capture

        async def read():
            calls.append("capture")
            assert asyncio.current_task() is task and port.capture_references is capture
            await asyncio.sleep(0)
            if stage == "capture":
                raise original
            return observed

        def record(result):
            calls.append("record")
            assert result is observed
            raise original

        def reset():
            calls.append("reset")
            assert asyncio.current_task() is task and sys.exception() is original
            if reset_fails:
                raise cleanup

        interface = SimpleNamespace(
            windows=SimpleNamespace(capture_references=retain),
            controller=SimpleNamespace(
                port=port, desktop_reader=SimpleNamespace(read=read), desktop=SimpleNamespace(record=record, reset=reset),
            ),
        )
        with pytest.raises(type(cleanup if reset_fails else original)) as caught:
            await AgentInterfacePipeline.read_desktop(interface, "image", False)
        assert caught.value is (cleanup if reset_fails else original)
        assert cleanup.__context__ is (original if reset_fails else None)
        assert sys.exception() is None

    asyncio.run(run())
    stages = ["retention", "capture", "record"]
    assert calls == [*stages[: stages.index(stage) + 1], "reset"]


def test_desktop_image_then_directory_reuse_public_application_and_window_references(monkeypatch):
    desktop = catalogue()
    app = desktop["windows"][0]
    desktop["frontmost"] = {"name": app["app"], "selector": app["selector"]}
    captured = {
        "screenshot": None,
        "observation": desktop,
        "observed": {"id": "desktop", "startedAt": 1, "completedAt": 2},
        "document": DisplayDocument.join((DisplayText("desktop", (freeze(desktop),)),)),
    }
    references = ApplicationReferences()
    windows = WindowReferences()
    captures, delivered = [], []
    monkeypatch.setattr(events, "_observation_handler", delivered.append)

    async def read():
        captures.append("image")
        return captured

    def record(observed):
        assert observed is captured
        captures.append("record")

    async def list_apps():
        captures.append("directory")
        number = references.publish(ApplicationEntry("session", app["selector"], app["app"], True))
        return [SimpleNamespace(id=number, display_name=app["app"], is_running=True)]

    controller = SimpleNamespace(
        transport=SimpleNamespace(session="session", platform="macos"),
        port=SimpleNamespace(),
        desktop_reader=SimpleNamespace(read=read),
        desktop=SimpleNamespace(record=record),
    )
    interface = SimpleNamespace(
        controller=controller, references=references, windows=windows, list_applications=list_apps,
    )
    interface._publish_window_catalogue = partial(AgentInterfacePipeline._publish_window_catalogue, interface)

    async def run():
        image = await AgentInterfacePipeline.read_desktop(interface, "both", True)
        directory = await AgentInterfacePipeline.read_desktop(interface, "tree", False)
        assert image.screenshot is directory.screenshot is None
        assert 'Foreground: "Provider"; computer.app(0)' in image.text
        assert 'window=0; application: computer.app(0)' in image.text
        assert "0 'Provider' (running); computer.app(0)" in directory.text
        assert references.resolve(0, "session").selector == app["selector"]
        assert windows.resolve(0, "session", application=app["selector"]).identity.window == app["window"]["window"]
        assert [event["observation"]["state"] for event in delivered] == [image, directory]

    asyncio.run(run())
    assert captures == ["image", "record", "directory"]
    assert [event["observation"]["full"] for event in delivered] == [True, False]
    assert all(event["observation"]["explicitRead"] for event in delivered)
