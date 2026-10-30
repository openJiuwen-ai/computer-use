# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normal operation titles, streamed replies and optional execution details."""

import json
import re
import time

from rich.console import Console

_CONTROL = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def plain(text):
    return _CONTROL.sub("", str(text))


class Terminal:
    def __init__(self, *, debug=False, console=None):
        self.debug = debug
        self.console = console or Console(highlight=False, markup=False)
        self.started = 0
        self.streaming = False

    def line(self, text="", *, style=None):
        self.finish()
        self.console.print(plain(text), style=style)

    def finish(self):
        if self.streaming:
            self.console.print()
            self.streaming = False

    def event(self, kind, data):
        if kind == "tool_call":
            self.started = time.monotonic()
            arguments = data["arguments"]
            title = arguments.get("title") or data["name"]
            self.line(f"● {title}", style="cyan")
            if self.debug:
                self.line(json.dumps(arguments, ensure_ascii=False, indent=2), style="dim")
        elif kind == "tool_result":
            result = data["result"]
            failed = result.get("isError", False)
            self.line(
                f"  {'执行返回错误' if failed else '执行返回'} · {time.monotonic() - self.started:.1f}s",
                style="red" if failed else "dim",
            )
            if self.debug or failed:
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        self.line(block["text"], style="dim")
                    elif block.get("type") == "image":
                        self.line("[截图已交付模型并保存到运行记录]", style="dim")
        elif kind in ("tool_error", "tool_cancelled"):
            self.line(data.get("error", "操作已取消；之前的输入可能已发送。"), style="yellow")

    def chunk(self, chunk, *, already_streamed=False):
        kind = getattr(chunk, "type", "")
        payload = getattr(chunk, "payload", "")
        if isinstance(payload, dict):
            text = payload.get("content") or payload.get("output") or ""
        else:
            text = payload if isinstance(payload, str) else ""
        if kind == "llm_output" or kind == "answer" and not already_streamed:
            if text:
                self.console.print(plain(text), end="", soft_wrap=True)
                self.streaming = True
            return bool(text)
        if kind == "llm_reasoning" and self.debug and text:
            self.console.print(plain(text), end="", style="dim", soft_wrap=True)
            self.streaming = True
        return False
