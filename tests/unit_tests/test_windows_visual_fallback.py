# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""An unavailable window image leaves Windows desktop pixels usable."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.diagnostics import local_failure
from computer_use.interaction.query.instances.scope import adopt
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.acquisition.images.fixed import FixedWindowImagePipeline
from computer_use.interaction.read.presentation.content import DisplayText
from computer_use.interaction.read.presentation.document import DisplayDocument
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.protocol.png import PNG
from computer_use.session.observation_data import observation_data
from PIL import Image
from test_d34_read_contracts import directory, setup, window


@pytest.mark.parametrize(("platform", "view"), [("windows", "both"), ("macos", "both"), ("windows", "tree")])
def test_failed_window_capture_delivers_independent_desktop_pixels(platform, view):
    member = window("A")
    catalog = {**directory(member), "platform": platform}
    controller, ui, state = setup(catalog)
    controller.port.connection.platform = platform
    adopt(controller, ui, catalog)
    ui._anchor = member["window"]
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(
        member_target(state, member),
        gaps=(ObservationGap("image", "observation_unavailable", "window geometry unavailable"),),
    ))
    with Image.new("RGB", (20, 10), "white") as bitmap, BytesIO() as output:
        bitmap.save(output, format="PNG")
        screenshot = PNG(output.getvalue())
    desktop = {
        "canvas": {"width": 20, "height": 10},
        "frontmost": None,
        "windows": [],
        "omittedWindows": 0,
        "displays": [{"name": "Display", "rect": {"x": 0, "y": 0, "width": 20, "height": 10}, "captured": True}],
    }
    captured = {
        "desktopRef": "desktop-capture",
        "observation": desktop,
        "screenshot": screenshot,
        "document": DisplayDocument.join((DisplayText("desktop", (desktop,)),)),
    }
    controller.desktop_reader.read = AsyncMock(return_value=captured)

    observation = asyncio.run(pipeline._collect(ui, view, False, None, catalog, None, None, None))

    if platform == "windows" and view == "both":
        controller.desktop_reader.read.assert_awaited_once()
        assert observation.screenshot is screenshot
        assert "Screenshot: desktop 20x10" in observation.text
        assert "Screenshot: unavailable." not in observation.text
        assert "window geometry unavailable" in observation.text
        assert controller.desktop.target(coordinates=True) == "desktop-capture"
        controller.desktop.check_coordinates(9, 4)
        assert ui._image is None
        assert state.input_context is None
        coverage = observation_data(observation)["window_coverage"][0]
        assert "image" in coverage["missing"] and "image" not in coverage["captured"]
    else:
        controller.desktop_reader.read.assert_not_awaited()
        assert observation.screenshot is None


def test_isolated_tree_timeout_captures_the_member_image_in_the_current_worker(monkeypatch):
    member = window("A")
    catalog = {**directory(member), "platform": "windows"}
    controller, ui, _ = setup(catalog)
    controller.port.connection.capabilities = ("nativeReadIsolation",)
    worker = 1

    async def timeout(*args, **kwargs):
        nonlocal worker
        worker += 1
        raise local_failure("the blocked provider worker exited", "execution_failed")

    async def capture(self, target):
        return SimpleNamespace(image=f"capture-worker-{worker}", gap=None)

    controller.reader.reader.read = AsyncMock(side_effect=timeout)
    monkeypatch.setattr(FixedWindowImagePipeline, "arun", capture)

    part = asyncio.run(InterfaceReadPipeline(controller).member(ui, member, "both", component_budget_ms=2000))

    assert part.image == "capture-worker-2"
    assert part.tree is None
    assert part.gaps == (ObservationGap("tree", "execution_failed", "the blocked provider worker exited"),)
