# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

from mcp.types import CallToolResult, TextContent

from computer_use_agent.cli.journal import Journal
from computer_use_agent.cli.main import main, parser
from computer_use_agent.connection import McpConnection

from .conftest import answer, tool_call


def test_global_input_cli_preserves_config_default_and_explicit_opt_out():
    assert parser().parse_args([]).allow_global_input is None
    assert parser().parse_args(["--allow-global-input"]).allow_global_input is True
    assert parser().parse_args(["--no-allow-global-input"]).allow_global_input is False


def test_journal_preserves_tool_result_utf16_values_and_following_events(tmp_path):
    journal = Journal(tmp_path)
    try:
        journal.record("tool_result", {"content": ["中文🙂", "\ud800", "\ud83d\ude42"]})
        journal.record("agent", {"content": "Complete"})
    finally:
        journal.close()
    path = journal.directory / "events.jsonl"
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["kind"] for line in lines] == ["tool_result", "agent"]
    assert lines[0]["data"]["content"] == ["中文🙂", "\ud800", "🙂"]


def test_cli_single_task(model_server, tmp_path, monkeypatch, capsys):
    server = model_server([tool_call("print('cli evidence')"), answer("CLI task complete")])
    invoke = McpConnection._invoke
    preflight_queries = []

    async def with_desktop_catalogue(connection, name, arguments):
        if name == "computer_use" and arguments.get("code") == "len(computer.list_apps())":
            preflight_queries.append(arguments["title"])
            return CallToolResult(content=[TextContent(type="text", text="0")])
        return await invoke(connection, name, arguments)

    monkeypatch.setattr(McpConnection, "_invoke", with_desktop_catalogue)
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {"model": "test-model", "api_key": "test-only", "api_base": server.model().model_client_config.api_base}
        )
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert asyncio.run(main(["--config", str(path), "Run the CLI test"])) == 0
    assert preflight_queries == ["Check desktop availability"]
    assert "CLI task complete" in capsys.readouterr().out
    records = list((tmp_path / ".computer-use/agent/runs").glob("*/events.jsonl"))
    assert len(records) == 1
    assert "cli evidence" in records[0].read_text(encoding="utf-8")


def test_interactive_commands_preserve_execution_session(model_server, tmp_path, monkeypatch):
    server = model_server(
        [
            tool_call("counter = 7; print(counter)"),
            answer("Counter initialized"),
            tool_call("counter += 1; print(counter)"),
            answer("Counter is 8"),
        ]
    )
    path = tmp_path / "model.json"
    path.write_text(
        json.dumps(
            {
                "model": "test-model",
                "api_key": "test-only",
                "api_base": server.model().model_client_config.api_base,
                "check_desktop": False,
            }
        )
    )
    (tmp_path / "sitecustomize.py").write_text(
        f"from pathlib import Path\nPath.home = classmethod(lambda cls: cls({str(tmp_path)!r}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PYTHONPATH", str(tmp_path), prepend=os.pathsep)
    for key in ("API_BASE", "OPENAI_BASE_URL", "MODEL_NAME", "OPENAI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "computer_use_agent.cli", "--config", str(path)],
        input="/debug\nInitialize counter\n/normal\nIncrement counter\n/compact\n/new\n/exit\n",
        encoding="utf-8",
        capture_output=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Counter is 8" in result.stdout
    assert "已开始新对话" in result.stdout
    assert "Python 会话保持" in result.stdout
    assert "default_trace_id" not in result.stdout
    assert not server.replies
