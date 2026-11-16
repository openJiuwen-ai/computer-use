# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Normal operation titles, streamed replies and optional execution details."""

import ast
import json
import re
import time

from rich.console import Console

_CONTROL = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_TOOL_TITLES = {
    "compile_task": "编译刚才的任务",
    "run_compiled": "运行已编译程序",
    "route_task": "选择执行路径",
    "computer_use_reset": "重置桌面会话",
}
_DESKTOP_TITLES = {
    "type_text": "输入文字",
    "paste": "粘贴内容",
    "close_window": "关闭窗口",
    "drag": "拖动目标",
    "scroll": "滚动界面",
    "open_menu": "打开菜单",
    "click": "点击目标",
    "press_key": "发送按键",
    "read_text": "读取控件文本",
    "read_details": "查看控件详情",
    "list_windows": "查找窗口",
    "list_apps": "列出应用",
    "read": "读取内容",
    "element": "定位控件",
}


def plain(text):
    return _CONTROL.sub("", str(text))


def _operation_title(name, arguments):
    title = arguments.get("title")
    if isinstance(title, str):
        title = plain(title).strip()
        if title and title != name:
            return title
    if name != "computer_use":
        return _TOOL_TITLES.get(name, name)

    code = arguments.get("code")
    if not isinstance(code, str):
        return "执行桌面脚本"
    try:
        script = ast.parse(code)
    except SyntaxError:
        return "执行桌面脚本"
    methods = {
        node.func.attr
        for node in ast.walk(script)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    return next((label for method, label in _DESKTOP_TITLES.items() if method in methods), "执行桌面脚本")


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
            title = _operation_title(data["name"], arguments)
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
        elif kind == "desktop_check":
            self.line("正在检查桌面后端…", style="dim")
        elif kind == "compile_source" and self.debug:
            self.line(f"Compile 任务 ID  {data['task_id']}", style="dim")
        elif kind == "desktop_paused":
            self.line("检测到桌面正在使用，暂停派发。", style="yellow")
        elif kind == "desktop_resumed":
            self.line("桌面已空闲，Agent 可以重新观察后继续。", style="dim")
        elif kind == "desktop_pause_unavailable":
            self.line("当前会话无法读取桌面活动，用户输入暂停未生效。", style="yellow")

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
