# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check Windows common application data propagation to MCP workers."""

import asyncio
import os

import pytest
from computer_use_agent import AgentConfig


@pytest.mark.skipif(os.name != "nt", reason="Windows common application data environment")
@pytest.mark.parametrize("override", [False, True])
def test_windows_common_data_reaches_mcp_worker(monkeypatch, tmp_path, override):
    from computer_use_agent import McpConfig
    from computer_use_agent.connection import McpConnection

    host = str(tmp_path / "host-data")
    expected = str(tmp_path / "configured-data") if override else host
    monkeypatch.setenv("ProgramData", host)
    monkeypatch.setenv("CUA_TEST_UNRELATED_ENV", "do-not-inherit")
    default = AgentConfig().mcp_config()
    config = McpConfig(command=default.command, args=default.args, env={"PROGRAMDATA": expected} if override else {})

    async def run():
        connection = McpConnection(config)
        try:
            result = await connection.call(
                "computer_use",
                {
                    "title": "Check common data environment",
                    "code": (
                        f"import os; assert os.environ.get('ProgramData') == {expected!r}; "
                        "assert 'CUA_TEST_UNRELATED_ENV' not in os.environ; print('common data available')"
                    ),
                },
            )
            assert not result.isError and any(
                getattr(item, "text", "") == "common data available" for item in result.content
            ), result
        finally:
            await connection.close()

    asyncio.run(run())
