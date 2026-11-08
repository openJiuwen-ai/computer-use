# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""The full-input default and explicit opt-out survive the worker boundary."""

import asyncio
import json

import pytest

from computer_use_mcp.session import ExecutionSession


@pytest.mark.parametrize("setting", [None, False, True])
def test_worker_receives_global_input_policy(tmp_path, monkeypatch, setting):
    monkeypatch.setenv("COMPUTER_USE_LOG_DIR", str(tmp_path))
    allowed = True if setting is None else setting

    async def verify():
        session = ExecutionSession() if setting is None else ExecutionSession(allow_global_input=setting)
        try:
            result = await session.execute(
                "from computer_use import Computer\n"
                "from concurrent.futures import ThreadPoolExecutor\n"
                "def policy(computer):\n"
                "    controller = computer._controller\n"
                "    return controller.port.policy.allow_global_input, controller.transport.allow_global_input\n"
                "def new_policy(**options):\n"
                "    with Computer(**options) as created:\n"
                "        return policy(created)\n"
                "with ThreadPoolExecutor(max_workers=1) as pool:\n"
                "    print(policy(computer), new_policy(), new_policy(allow_global_input=True),\n"
                "          new_policy(allow_global_input=False), pool.submit(new_policy).result())"
            )
            assert not result.get("isError"), result
            text = "\n".join(block.get("text", "") for block in result["content"])
            expected = [(allowed, allowed)] * 3 + [(False, False), (allowed, allowed)]
            assert " ".join(map(str, expected)) in text
            records = [json.loads(line) for line in session._journal.path.read_text().splitlines()]
            assert next(item for item in records if item["kind"] == "session")["globalInputAllowed"] is allowed
        finally:
            await session.close()

    asyncio.run(verify())


@pytest.mark.parametrize("allowed", [False, True])
def test_worker_image_click_guidance_respects_global_input_policy(allowed):
    async def verify():
        session = ExecutionSession(allow_global_input=allowed)
        try:
            result = await session.execute(
                "from io import BytesIO\n"
                "from PIL import Image\n"
                "from computer_use.client.images import register_image\n"
                "from computer_use.protocol.png import PNG\n"
                "buffer = BytesIO()\n"
                "Image.new('RGB', (80, 60)).save(buffer, format='PNG')\n"
                "window = dict(kind='window', selector='fixture', windows=(0, 2),\n"
                "              coordinate_windows=lambda: (2, 0), coordinateTarget='observed window',\n"
                "              coordinateRegions=((2, (9, 12, 12, 14)), (0, (0, 0, 80, 60))))\n"
                "for source in (window, dict(kind='desktop'), dict(window, current=lambda: False)):\n"
                "    picture = PNG(buffer.getvalue())\n"
                "    register_image(picture, source)\n"
                "    print_image(picture)\n"
            )
            assert not result.get("isError"), result
            texts = [block["text"] for block in result["content"] if block["type"] == "text"]
            metadata = [
                block["_meta"]["computer-use"]["caption"] for block in result["content"] if block["type"] == "image"
            ]
            assert len(texts) == len(metadata) == 3
            for window, desktop, stale in (texts, metadata):
                assert "Coordinate target=observed window" in window
                assert "Window 2 bounds (9, 12) to (20, 25)" in window
                assert "Image point for window=2: p = computer.app('fixture').point((x, y), window=2)" in window
                assert "Image point for window=0: p = computer.app('fixture').point((x, y), window=0)" in window
                assert "App click: computer.app('fixture').click(at=p)" in window
                assert ("Global click (explicit): computer.click(target=p)" in window) is allowed
                assert "Image point: p = computer.point((x, y))" in desktop
                assert ("Global click: computer.click(target=p)" in desktop) is allowed
                assert "stale" in stale
                assert "Image point" not in stale
                assert "Global click" not in stale
        finally:
            await session.close()

    asyncio.run(verify())


@pytest.mark.parametrize("value", ["true", 1, None])
def test_session_authorization_rejects_non_boolean(value):
    with pytest.raises(TypeError, match="boolean"):
        ExecutionSession(allow_global_input=value)
