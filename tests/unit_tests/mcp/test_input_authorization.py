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


@pytest.mark.parametrize("value", ["true", 1, None])
def test_session_authorization_rejects_non_boolean(value):
    with pytest.raises(TypeError, match="boolean"):
        ExecutionSession(allow_global_input=value)
