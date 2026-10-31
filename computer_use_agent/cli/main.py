# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Single-task and interactive entry points for the same DeepAgent."""

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path
from uuid import uuid4

from computer_use_native_tools.console import report

from .config import config_path, initialize, load

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
logger.propagate = False


def parser():
    result = argparse.ArgumentParser(description="Computer Use · openJiuwen DeepAgent")
    result.add_argument("task", nargs="?", help="Execute one task; omit to start a conversation.")
    result.add_argument("--config", type=Path)
    result.add_argument("--init-config", action="store_true", help="Create a model configuration file.")
    result.add_argument("--model")
    result.add_argument("--provider")
    result.add_argument("--api-base")
    result.add_argument("--desktop", choices=("exclusive",), help="Desktop authorization for the local MCP server.")
    result.add_argument("--max-iterations", type=int)
    result.add_argument("--debug", action="store_true")
    return result


async def turn(agent, message, conversation_id, terminal, journal):
    journal.record("user", {"content": message, "conversation_id": conversation_id})
    streamed = False
    async for chunk in agent.stream({"query": message, "conversation_id": conversation_id}):
        journal.record("agent", chunk.model_dump(mode="json") if hasattr(chunk, "model_dump") else str(chunk))
        visible = terminal.chunk(chunk, already_streamed=streamed)
        if getattr(chunk, "type", "") == "llm_output":
            streamed |= visible
    terminal.finish()


async def converse(agent, *, task, terminal, journal):
    from .input import MessageInput

    conversation_id = uuid4().hex
    if task is not None:
        await turn(agent, task, conversation_id, terminal, journal)
        return
    async with MessageInput() as source:
        while True:
            try:
                message = await source.read()
            except EOFError:
                return
            if not message.strip():
                continue
            command = message.strip()
            if command == "/exit":
                return
            if command in ("/debug", "/normal"):
                terminal.debug = command == "/debug"
                terminal.line("Debug：显示工具输入输出。" if terminal.debug else "正常：显示操作标题和执行状态。")
                continue
            if command == "/new":
                await agent.toolkit.reset()
                conversation_id = uuid4().hex
                journal.record("new_session", {})
                terminal.line("已开始新对话。")
                continue
            if command == "/compact":
                if agent.react_agent.context_engine.get_context(session_id=conversation_id) is None:
                    terminal.line("当前还没有可压缩的对话。")
                    continue
                terminal.line("正在压缩上下文…")
                result = await agent.compact(conversation_id=conversation_id)
                journal.record("compaction", {"result": result})
                code = result.get("result") if isinstance(result, dict) else result
                terminal.line(
                    "已压缩上下文；Python 会话保持。" if code == "compressed" else "当前无需压缩；Python 会话保持。"
                )
                continue
            if command == "/help":
                terminal.line("/debug · /normal · /compact · /new · /exit\n回车发送；Ctrl+J 或 Alt+回车换行。")
                continue
            try:
                await turn(agent, message, conversation_id, terminal, journal)
            except Exception as error:
                logger.exception("Conversation turn failed")
                message = journal.redact(str(error))
                journal.record("turn_error", {"message": message})
                terminal.line(message, style="red")


async def main(argv=None):
    args = parser().parse_args(argv)
    if args.init_config:
        destination = args.config or config_path()
        try:
            initialize(destination)
        except FileExistsError:
            report(f"模型配置已存在：{destination}")
            return 0
        report(f"模型配置：{destination}")
        return 0
    journal = terminal = None
    previous_logging = configure_logging = error_handler = None
    installed = []
    try:
        from .journal import Journal
        from .terminal import Terminal

        settings = load(
            args.config,
            overrides={
                "model": args.model,
                "provider": args.provider,
                "api_base": args.api_base,
                "desktop": args.desktop,
                "max_iterations": args.max_iterations,
            },
        )
        terminal = Terminal(debug=args.debug)
        secrets = [settings.get("api_key"), settings.get("model_client_config", {}).get("api_key")]
        journal = Journal(Path.home() / ".computer-use" / "agent" / "runs", secrets=secrets)
        error_handler = logging.FileHandler(journal.directory / "errors.log", encoding="utf-8")
        logger.addHandler(error_handler)
        with (journal.directory / "framework.log").open("w", encoding="utf-8") as framework_log:
            with contextlib.redirect_stdout(framework_log), contextlib.redirect_stderr(framework_log):
                from openjiuwen.core.common.logging.log_config import configure_log_config, get_log_config_snapshot

                configure_logging = configure_log_config
                previous_logging = get_log_config_snapshot()
                configure_logging(
                    {
                        **previous_logging,
                        "log_path": str(journal.directory / "framework"),
                        "output": ["file"],
                        "interface_output": ["file"],
                        "performance_output": ["file"],
                        "propagate": False,
                    }
                )
                from computer_use_agent import create_agent

                from .config import build

                model, config = build(settings, workspace=journal.directory / "workspace")

            def event(kind, data):
                journal.record(kind, data)
                terminal.event(kind, data)

            terminal.line(
                f"Computer Use · DeepAgent\n模型：{settings['model']}\n"
                f"桌面：{config.desktop if config.mcp is None else '由目标 MCP 配置'}\n"
                f"运行记录：{journal.directory}\n"
                "回车发送 · Ctrl+J / Alt+回车换行 · /help 查看命令 · Ctrl+C 中断并退出",
                style="cyan",
            )
            journal.record(
                "start", {"model": settings["model"], "desktop": config.desktop if config.mcp is None else None}
            )
            current = asyncio.current_task()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(sig, current.cancel)
                    installed.append(sig)
                except RuntimeError:
                    pass
            async with create_agent(
                model,
                config=config,
                on_event=event,
                stderr=framework_log,
                completion_timeout=settings.get("model_timeout", 600),
            ) as agent:
                await converse(agent, task=args.task, terminal=terminal, journal=journal)
        return 0
    except (asyncio.CancelledError, KeyboardInterrupt):
        if terminal is not None:
            terminal.line("已中断；之前的输入可能已发送。", style="yellow")
        return 130
    except Exception as error:
        logger.exception("Computer Use CLI failed")
        message = journal.redact(str(error)) if journal else str(error)
        if isinstance(error, ModuleNotFoundError):
            message += "\n安装 Agent 依赖：uv sync --extra agent（Python 3.12 或 3.13）。"
        if terminal is not None:
            terminal.line(message, style="red")
        else:
            report(message, stream=sys.stderr)
        if journal is not None:
            journal.record("error", {"message": message})
        return 1
    finally:
        if terminal is not None:
            terminal.finish()
        if journal is not None:
            journal.close()
        if previous_logging is not None:
            configure_logging(previous_logging)
        if error_handler is not None:
            logger.removeHandler(error_handler)
            error_handler.close()
        for sig in installed:
            asyncio.get_running_loop().remove_signal_handler(sig)


def cli():
    return asyncio.run(main())
