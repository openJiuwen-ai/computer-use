# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Screenshot-carrying tools must show the model their text, not the image bytes.

openjiuwen 0.1.19 renders each tool result through ``render_for_llm``; for a
function tool that returns an ``McpToolResult`` the default is ``str(result)``,
which put the base64 PNG into the tool message. Four screenshots (from the
since-removed run_pyautogui) then overflowed Opus 5's 1M-token context after nine tool calls (chrome
480bcfea, 2026-10-08). The image still reaches the model through the separate
multimodal message. Needs openjiuwen (this repo's [agent] extra); see osworld/pytest.ini for how to run it.
"""

from __future__ import annotations

import base64
import io

import pytest

try:  # the root venv's older openjiuwen has no per-tool rendering
    from openjiuwen.core.foundation.tool.mcp.base import McpToolResult
    from openjiuwen.core.single_agent.ability_manager import AbilityManager
    from PIL import Image

    from osworld.tools.osworld_tools import make_osworld_tools
except ImportError as exc:
    pytest.skip(f"needs openjiuwen ({exc})", allow_module_level=True)
if not hasattr(AbilityManager, "_render_tool_result"):
    pytest.skip("this openjiuwen predates per-tool result rendering", allow_module_level=True)


def _png(color: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1920, 1080), color).save(out, format="PNG")
    return out.getvalue()


class FakeGuest:
    def __init__(self) -> None:
        self.shots = 0

    def get_screenshot(self) -> bytes:
        self.shots += 1
        return _png("white" if self.shots < 2 else "black")

    @staticmethod
    def run_python_script(script: str, timeout: float = 90) -> dict:
        return {"status": "success", "output": "", "error": "", "returncode": 0}


class _Call:
    name = "close_window"


def test_close_window_renders_its_text_and_keeps_the_image_for_the_image_message():
    # close_window's screenshot is full 1920 px: in the text it would cost ~2.7 MB of base64 per call.
    tools = {t.card.name: t for t in make_osworld_tools(FakeGuest(), image_width=1920, settle_s=0)}
    data_url = "data:image/png;base64," + base64.b64encode(_png("white")).decode("ascii")
    multimodal = [{"type": "image", "data_url": data_url}]
    result = McpToolResult(data={"content": "Closed the window.", "multimodal": multimodal})

    text = AbilityManager._render_tool_result(tools["close_window"], _Call(), result)

    assert text == "Closed the window."
    assert result.data["multimodal"][0]["data_url"] == data_url  # still delivered
