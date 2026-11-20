# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import os
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

IMAGE_SCRIPT = """
from io import BytesIO
from PIL import Image
from computer_use.client.images import register_image
from computer_use.protocol.png import PNG

buffer = BytesIO()
Image.new('RGB', (80, 60), 'white').save(buffer, format='PNG')
desktop = PNG(buffer.getvalue())
register_image(desktop, {'kind': 'desktop'})
print_image(desktop)

window = PNG(buffer.getvalue())
register_image(window, {'kind': 'window', 'selector': 'fixture', 'windows': [9]})
print_image(window)
"""


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("allowed", [True, False])
async def test_stdio_image_captions_reflect_global_input_permission(tmp_path, allowed):
    settings = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp", "--allow-global-input" if allowed else "--no-allow-global-input"],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "COMPUTER_USE_LOG_DIR": str(tmp_path)},
    )
    async with (
        stdio_client(settings) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=35)) as client,
    ):
        await client.initialize()
        result = await client.call_tool("computer_use", {"code": IMAGE_SCRIPT})
    assert not result.isError
    captions = [block.meta["computer-use"]["caption"] for block in result.content if block.type == "image"]
    text = [block.text for block in result.content if block.type == "text" and block.text.startswith("Image ")]
    assert len(captions) == len(text) == 2
    for desktop, window in (captions, text):
        assert "Image: desktop 80x60 px" in desktop
        assert "80x60 px" in window
        assert "Image point: p = computer.point((x, y))" in desktop
        assert ("Global click: computer.click(target=p)" in desktop) is allowed
        assert "Window reference: 9" in window
        assert "Image point: p = computer.app('fixture').point((x, y))" in window
        assert "App click: computer.app('fixture').click(at=p)" in window
        assert ("Global click (explicit): computer.click(target=p)" in window) is allowed
