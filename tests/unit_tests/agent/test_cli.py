# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import io
import json
import os
import signal
import subprocess
import sys
import time

from computer_use_cli.config import initialize, load
from computer_use_cli.input import MessageInput
from computer_use_cli.journal import Journal
from computer_use_cli.main import main
from computer_use_cli.terminal import Terminal
from rich.console import Console

from .conftest import answer, tool_call


def test_cli_configuration_and_legacy_aliases(tmp_path, monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    path = tmp_path / "config.json"
    initialize(path)
    path.write_text(json.dumps({"model": "configured", "baseUrl": "http://127.0.0.1/v1", "apiKey": "file-key"}))
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    value = load(path, overrides={"model": "override"})
    assert value["model"] == "override"
    assert value["api_base"] == "http://127.0.0.1/v1"
    assert value["api_key"] == "environment-key"


def test_cli_config_init_reports_once_and_preserves_existing_file(tmp_path, capsys):
    path = tmp_path / "config.json"
    args = ["--init-config", "--config", str(path)]
    assert asyncio.run(main(args)) == 0
    output = capsys.readouterr()
    assert output.out == f"模型配置：{path}\n"
    assert output.err == ""
    path.write_text("existing configuration", encoding="utf-8")
    assert asyncio.run(main(args)) == 0
    output = capsys.readouterr()
    assert output.out == f"模型配置已存在：{path}\n"
    assert output.err == ""
    assert path.read_text(encoding="utf-8") == "existing configuration"


def test_cli_startup_error_uses_stderr(tmp_path, capsys):
    path = tmp_path / "missing.json"
    assert asyncio.run(main(["--config", str(path), "--no-preview"])) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert str(path) in output.err


def test_journal_saves_images_and_redacts_secrets(tmp_path):
    import base64

    journal = Journal(tmp_path, secrets=["configured-secret"])
    journal.record(
        "test",
        {
            "api_key": "other-secret",
            "text": "configured-secret",
            "content": [
                {"type": "image", "mimeType": "image/png", "data": base64.b64encode(b"image-bytes").decode()},
            ],
        },
    )
    journal.close()
    text = (journal.directory / "events.jsonl").read_text()
    assert "configured-secret" not in text
    assert "other-secret" not in text
    assert (journal.directory / "image-0001.png").read_bytes() == b"image-bytes"


def test_terminal_titles_and_debug_do_not_duplicate_final_answer():
    from types import SimpleNamespace

    output = io.StringIO()
    terminal = Terminal(console=Console(file=output, color_system=None))
    terminal.event("tool_call", {"name": "computer_use", "arguments": {"title": "Open form", "code": "secret_code"}})
    terminal.chunk(SimpleNamespace(type="llm_output", payload={"content": "Done"}))
    terminal.chunk(SimpleNamespace(type="answer", payload={"content": "Done"}), already_streamed=True)
    terminal.finish()
    assert "Open form" in output.getvalue()
    assert "secret_code" not in output.getvalue()
    assert output.getvalue().count("Done") == 1


def test_redirected_multiturn_input():
    async def run():
        source = MessageInput(stream=io.StringIO("first\nsecond\n"), interactive=False)
        assert await source.read() == "first"
        assert await source.read() == "second"

    asyncio.run(run())


def test_cli_single_task(model_server, tmp_path, monkeypatch, capsys):
    server = model_server([tool_call("print('cli evidence')"), answer("CLI task complete")])
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {"model": "test-model", "api_key": "test-only", "api_base": server.model().model_client_config.api_base}
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("API_BASE", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert asyncio.run(main(["--config", str(path), "--no-preview", "Run the CLI test"])) == 0
    assert "CLI task complete" in capsys.readouterr().out
    records = list((tmp_path / ".computer-use/agent/runs").glob("*/events.jsonl"))
    assert len(records) == 1
    assert "cli evidence" in records[0].read_text()


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
            }
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("API_BASE", "OPENAI_BASE_URL", "MODEL_NAME", "OPENAI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    result = subprocess.run(
        [sys.executable, "-m", "computer_use_cli", "--config", str(path), "--no-preview"],
        input="/debug\nInitialize counter\n/normal\nIncrement counter\n/compact\n/new\n/exit\n",
        text=True,
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


def test_multiline_terminal_input_restores_key_sequences(monkeypatch):
    import prompt_toolkit
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.input.vt100_parser import ANSI_SEQUENCES
    from prompt_toolkit.output import DummyOutput

    original = prompt_toolkit.PromptSession
    previous = ANSI_SEQUENCES.get("\x1b[13;2u")

    async def run():
        with create_pipe_input() as pipe:
            monkeypatch.setattr(
                prompt_toolkit,
                "PromptSession",
                lambda **kwargs: original(
                    **kwargs,
                    input=pipe,
                    output=DummyOutput(),
                ),
            )
            async with MessageInput(interactive=True) as source:
                pending = asyncio.create_task(source.read())
                await asyncio.sleep(0)
                pipe.send_text("first\nsecond\r")
                assert await asyncio.wait_for(pending, 2) == "first\nsecond"

    asyncio.run(run())
    assert ANSI_SEQUENCES.get("\x1b[13;2u") == previous


def test_cli_cancel_does_not_repeat_tool(model_server, tmp_path, monkeypatch):
    import pytest

    if os.name != "posix":
        pytest.skip("This test sends POSIX process signals.")
    server = model_server([tool_call("await __import__('asyncio').sleep(60)")])
    path = tmp_path / "model.json"
    path.write_text(
        json.dumps(
            {
                "model": "test-model",
                "api_key": "test-only",
                "api_base": server.model().model_client_config.api_base,
            }
        )
    )
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("API_BASE", "OPENAI_BASE_URL", "MODEL_NAME", "OPENAI_MODEL"):
        monkeypatch.delenv(key, raising=False)
    process = subprocess.Popen(
        [sys.executable, "-m", "computer_use_cli", "--config", str(path), "--no-preview", "Wait until cancelled"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        records = []
        while time.monotonic() < deadline:
            records = list((tmp_path / ".computer-use/agent/runs").glob("*/events.jsonl"))
            if records and '"kind": "tool_call"' in records[0].read_text():
                break
            time.sleep(0.05)
        assert records
        time.sleep(0.4)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 130, stdout + stderr
        events = [json.loads(line) for line in records[0].read_text().splitlines()]
        assert sum(event["kind"] == "tool_call" and event["data"]["name"] == "computer_use" for event in events) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
