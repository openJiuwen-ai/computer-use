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
            result = await session.execute("print(computer._controller.port.policy.allow_global_input)")
            assert not result.get("isError"), result
            text = "\n".join(block.get("text", "") for block in result["content"])
            assert str(allowed) in text
            records = [json.loads(line) for line in session._journal.path.read_text().splitlines()]
            assert next(item for item in records if item["kind"] == "session")["globalInputAllowed"] is allowed
        finally:
            await session.close()

    asyncio.run(verify())


@pytest.mark.parametrize("value", ["true", 1, None])
def test_session_authorization_rejects_non_boolean(value):
    with pytest.raises(TypeError, match="boolean"):
        ExecutionSession(allow_global_input=value)
