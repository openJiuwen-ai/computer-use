# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Desktop capture keeps one scoped request and its original terminal evidence."""

import asyncio
import base64
import sys
from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.read.acquisition.images.desktop.model import DesktopReadRequest
from computer_use.interaction.read.acquisition.images.desktop.pipeline import DesktopReadPipeline
from computer_use.native.contracts import Binding
from computer_use.pipeline import Control
from computer_use.protocol.captures import decode_capture
from PIL import Image
from regression.test_desktop_window_directory import catalogue


def desktop_png(reasons):
    buffer = BytesIO()
    with Image.new("RGB", (2, 2), "white") as pixels:
        pixels.save(buffer, format="PNG")
    return decode_capture({
        "id": "capture", "kind": "desktop", "scope": "desktop", "startedAt": 1, "completedAt": 2,
        "width": 2, "height": 2, "png": base64.b64encode(buffer.getvalue()).decode(), "stopReasons": reasons,
    }, "desktop")


@pytest.mark.parametrize("outcome", ["captured", "denied", "cancelled", "bound", "bad_dimension"])
def test_desktop_acquisition_preserves_scope_order_and_capture_identity(outcome):
    trace, events = [], []
    png = desktop_png(["One display is unavailable", 'Provider said "partial"'])
    observation = catalogue()
    native = {"observation": observation, "screenshot": png}
    target = Binding("session", "app", "window", "surface") if outcome == "bound" else Binding(
        "session", None, None, None,
    )
    denied = ComputerUseError({
        "code": "permission_denied", "effect": "none", "target": "desktop", "detail": "Screen capture unavailable",
    })
    cancelled = asyncio.CancelledError("caller stopped capture")

    async def run():
        owner = asyncio.current_task()

        def remaining():
            trace.append(("remaining",))
            return 730

        @asynccontextmanager
        async def open_scope(requested_target, timeout):
            assert asyncio.current_task() is owner
            trace.append(("open", requested_target, timeout))
            try:
                yield SimpleNamespace(token="operation", target=target, remaining=remaining)
            finally:
                assert asyncio.current_task() is owner
                trace.append(("release", sys.exception()))

        async def capture(token, bound, size, deadline, *, kind):
            assert asyncio.current_task() is owner and bound is target
            trace.append(("capture", token, bound, size, deadline, kind))
            await asyncio.sleep(0)
            if outcome in ("denied", "cancelled"):
                raise denied if outcome == "denied" else cancelled
            return native

        port = SimpleNamespace(calls=SimpleNamespace(open=open_scope), capture=capture)
        request = DesktopReadRequest(max_dimension=True if outcome == "bad_dimension" else 1024, timeout_ms=850)
        return await DesktopReadPipeline(port).arun(request, Control(diagnostic=events.append, call_id="original"))

    error = None
    if outcome == "captured":
        result = asyncio.run(run())
        assert result is not native and result["observation"] is observation and result["screenshot"] is png
        assert list(result) == ["observation", "screenshot", "document"]
        observation["windows"][0]["app"] = "Changed after capture"
        assert "Provider" in result["document"].text and "Changed after capture" not in result["document"].text
        assert '\nImage note: "One display is unavailable"' in result["document"].text
        assert '\nImage note: "Provider said \\"partial\\""' in result["document"].text
    else:
        error_type = {"denied": ComputerUseError, "cancelled": asyncio.CancelledError}.get(outcome, ValueError)
        with pytest.raises(error_type) as caught:
            asyncio.run(run())
        error = caught.value
        if outcome == "denied":
            assert error is denied
        if outcome == "cancelled":
            assert error is cancelled

    if outcome == "bad_dimension":
        assert trace == []
    elif outcome == "bound":
        assert trace == [("open", None, 850), ("release", error)]
    else:
        assert trace == [("open", None, 850), ("remaining",), ("capture", "operation", target, 1024, 730, "desktop"),
                         ("release", error)]
    expected = ["DesktopReadPipeline", "parse_desktop_request"]
    if outcome != "bad_dimension":
        expected += ["DesktopReadScope", "NativeScopeBody", "DesktopImageObservation", "validate_desktop_scope"]
        if outcome != "bound":
            expected.append("collect_desktop_pixels")
        if outcome == "captured":
            expected += ["format_desktop_observation", "deliver_desktop_observation"]
    assert [event.path[-1] for event in events if event.phase == "enter"] == expected
    assert all(event.call_id == "original" for event in events)
