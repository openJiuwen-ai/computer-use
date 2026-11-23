# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Live grounding evaluation on real applications.

Runs a natural-language task through the reference agent against one of:

  wemeet_schedule  Tencent Meeting: OCR-only home controls + accessible form.
  wps_format       WPS Writer: document body is not in the AX tree; ribbon formatting.
  vscode_settings  VS Code: AX tree is empty (Chrome Legacy Window); OCR/visual input.

Each task has an objective verifier that reads application state after the agent
finishes, so the treatment (workspace VisualRef changes) and the baseline (HEAD)
are compared on outcome rather than on what the model claims.

Prerequisites: an interactive unlocked Windows desktop and the built driver.
The model is taken from ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN /
ANTHROPIC_MODEL.

Usage:
  python tests/manual/grounding_task_eval.py --task wemeet_schedule --variant treatment
  python tests/manual/grounding_task_eval.py --task all --variant baseline \
      --run-id baseline-1

For the baseline, launch with PYTHONPATH pointing at a HEAD checkout and pass the
same value through McpConfig (the script forwards os.environ["PYTHONPATH"]).
"""

# ruff: noqa: BLE001, S110, T201  (manual evaluation CLI: best-effort cleanup and operator output)
from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import win32com.client

ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ROOT / "outputs" / "grounding_eval"
WPS_EXE = Path(os.environ.get(
    "WPS_EXE", r"C:\Users\admin\AppData\Local\Kingsoft\WPS Office\12.1.0.29161\office6\wps.exe"
))
WEMEET_EXE = Path(os.environ.get("WEMEET_EXE", r"C:\Program Files\Tencent\WeMeet\wemeetapp.exe"))
CODE_EXE = Path(os.environ.get("CODE_EXE", r"D:\Microsoft VS Code\Code.exe"))
TRAE_EXE = Path(os.environ.get(
    "TRAE_EXE", r"C:\Users\admin\AppData\Local\Programs\Trae CN\Trae CN.exe"
))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# --------------------------------------------------------------------------- window helpers


_user32 = ctypes.windll.user32
_EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def visible_windows() -> list[tuple[int, str, int]]:
    """Return (hwnd, title, pid) for every visible top-level titled window."""
    result: list[tuple[int, str, int]] = []

    def visit(hwnd, _):
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buffer, length + 1)
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        result.append((hwnd, buffer.value, pid.value))
        return True

    _user32.EnumWindows(_EnumWindowsProc(visit), 0)
    return result


def wait_window(predicate: Callable[[str], bool], timeout: float = 30.0) -> tuple[int, str, int] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for item in visible_windows():
            if predicate(item[1]):
                return item
        time.sleep(0.4)
    return None


def activate(title_prefix: str) -> bool:
    """Bring a window to the foreground using the same workaround as a human taskbar click."""
    try:
        return bool(win32com.client.Dispatch("WScript.Shell").AppActivate(title_prefix))
    except Exception:
        return False


def _bring_to_front(hwnd: int) -> None:
    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    foreground = _user32.GetForegroundWindow()
    if foreground == hwnd:
        return
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    foreground_thread = _user32.GetWindowThreadProcessId(foreground, None)
    target_thread = _user32.GetWindowThreadProcessId(hwnd, None)
    _user32.AttachThreadInput(foreground_thread, current_thread, True)
    _user32.AttachThreadInput(target_thread, current_thread, True)
    try:
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        _user32.AttachThreadInput(foreground_thread, current_thread, False)
        _user32.AttachThreadInput(target_thread, current_thread, False)


# --------------------------------------------------------------------------- agent


@dataclass
class Case:
    name: str
    app: str
    prompt: str
    setup: Callable[[str], dict]
    verify: Callable[[dict], dict]
    cleanup: Callable[[dict], None] = lambda context: None


@dataclass
class RunRecord:
    task: str
    variant: str
    run_id: str
    started: str
    seconds: float = 0.0
    success: bool = False
    error: str | None = None
    tool_calls: int = 0
    tool_errors: int = 0
    read_calls: int = 0
    visual_reads: int = 0
    visual_refs: int = 0
    click_calls: int = 0
    answer: str = ""
    events: list[dict] = field(default_factory=list)
    verification: dict = field(default_factory=dict)
    setup: dict = field(default_factory=dict)


def _model():
    from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

    return Model(
        model_client_config=ModelClientConfig(
            client_provider="Anthropic",
            api_base=os.environ["ANTHROPIC_BASE_URL"],
            api_key=os.environ["ANTHROPIC_AUTH_TOKEN"],
            timeout=300,
        ),
        model_config=ModelRequestConfig(model=os.environ["ANTHROPIC_MODEL"], max_tokens=4096),
    )


def _summarize_event(kind: str, data: Any) -> dict:
    if kind == "tool_call":
        arguments = data.get("arguments") if isinstance(data, dict) else None
        code = arguments.get("code", "") if isinstance(arguments, dict) else ""
        return {"kind": kind, "name": data.get("name"), "code": code[:4000], "title": str(
            (arguments or {}).get("title", "")
        )[:200]}
    if kind == "tool_result":
        result = data.get("result") if isinstance(data, dict) else None
        text = ""
        if isinstance(result, dict):
            blocks = result.get("content") or []
            if isinstance(blocks, list):
                text = " ".join(
                    block.get("text", "") for block in blocks if isinstance(block, dict) and block.get("text")
                )
        return {
            "kind": kind,
            "name": data.get("name") if isinstance(data, dict) else None,
            "isError": bool(result.get("isError")) if isinstance(result, dict) else None,
            "text": text[:4000],
        }
    return {"kind": kind, "data": str(data)[:1000]}


async def run_case(case: Case, variant: str, run_id: str, max_iterations: int) -> RunRecord:
    from computer_use_agent import AgentConfig, ComputerUseAgent, McpConfig, RoutingConfig

    record = RunRecord(task=case.name, variant=variant, run_id=run_id, started=time.strftime("%Y-%m-%dT%H:%M:%S"))
    context = case.setup(run_id)
    record.setup = {key: str(value) for key, value in context.items() if key != "process"}

    events: list[dict] = []

    def on_event(kind, data):
        if kind in ("tool_call", "tool_result", "tool_error", "tool_cancelled"):
            events.append(_summarize_event(kind, data))

    workspace = Path(tempfile.mkdtemp(prefix=f"cua-eval-{variant}-{case.name}-"))
    mcp_env = {"PYTHONPATH": os.environ.get("PYTHONPATH", "")}
    agent = ComputerUseAgent(
        _model(),
        config=AgentConfig(
            max_iterations=max_iterations,
            context_window=200_000,
            workspace=str(workspace),
            routing=RoutingConfig(enabled=False),
            mcp=McpConfig(
                command=sys.executable,
                args=("-m", "computer_use_mcp"),
                env=mcp_env,
                cwd=os.environ.get("CUA_EVAL_CWD", str(ROOT)),
            ),
        ),
        on_event=on_event,
    )
    started = time.monotonic()
    try:
        answer = await asyncio.wait_for(
            agent.invoke({"query": case.prompt, "conversation_id": f"{variant}-{case.name}-{run_id}"}),
            timeout=900,
        )
        record.answer = str(answer)[:6000]
    except Exception as error:
        record.error = f"{type(error).__name__}: {error}"
    finally:
        record.seconds = round(time.monotonic() - started, 2)
        await agent.close()

    record.events = events
    record.tool_calls = sum(1 for item in events if item.get("kind") == "tool_call")
    record.tool_errors = sum(1 for item in events if item.get("isError"))
    for item in events:
        if item.get("kind") != "tool_call":
            continue
        code = item.get("code", "")
        record.read_calls += len(re.findall(r"\.read\(", code))
        record.visual_refs += len(re.findall(r"VisualRef|visual\s*=\s*[\"'](?:always|auto)", code))
        record.click_calls += len(re.findall(r"\.click\(", code))
        if re.search(r"visual\s*=\s*[\"'](?:always|auto)[\"']|marks\s*=\s*True", code):
            record.visual_reads += 1

    try:
        record.verification = case.verify(context)
        record.success = bool(record.verification.get("success"))
    except Exception as error:
        record.verification = {"success": False, "error": f"{type(error).__name__}: {error}"}
        record.success = False
    try:
        case.cleanup(context)
    except Exception as error:
        record.verification.setdefault("cleanup_error", f"{type(error).__name__}: {error}")
    return record


# --------------------------------------------------------------------------- WPS Writer


def _docx_text_and_format(path: Path) -> dict:
    from docx import Document
    from docx.oxml.ns import qn

    def east_asia(rpr):
        if rpr is not None and rpr.rFonts is not None:
            return rpr.rFonts.get(qn("w:eastAsia"))
        return None

    document = Document(path)
    paragraphs = []
    for paragraph in document.paragraphs:
        runs = []
        for run in paragraph.runs:
            font = run.font
            runs.append(
                {
                    "text": run.text,
                    "bold": font.bold,
                    "size_pt": font.size.pt if font.size else None,
                    "name": font.name,
                    "east_asia": east_asia(run._element.rPr),
                }
            )
        xml = paragraph._p.xml
        first_line_chars = re.search(r'w:firstLineChars="(\d+)"', xml)
        first_line = re.search(r'w:firstLine="(\d+)"', xml)
        style_element = paragraph.style.element
        paragraphs.append(
            {
                "text": paragraph.text,
                "style": paragraph.style.name,
                "alignment": paragraph.alignment,
                "runs": runs,
                # Formatting may be direct, paragraph-level, or inherited from the style;
                # accept any layer that produces the requested human-visible font.
                "paragraph_east_asia": east_asia(getattr(paragraph._p.pPr, "rPr", None))
                if paragraph._p.pPr is not None
                else None,
                "style_east_asia": east_asia(getattr(style_element, "rPr", None)) if style_element is not None else None,
                "first_line_chars": int(first_line_chars.group(1)) if first_line_chars else None,
                "first_line_twips": int(first_line.group(1)) if first_line else None,
            }
        )
    return {"paragraphs": paragraphs, "tables": len(document.tables)}


def _wps_format_prompt(path: Path) -> str:
    return f"""
打开 WPS 文字窗口，标题为 "grounding_eval_wps.docx - WPS Office"（如果窗口未在前台，请先切换到它）。
文档是空白的。请在当前文档中完成以下排版任务：

1. 输入两行文字，第一行是标题：`季度总结报告`，第二行是正文：`本季度共完成 12 个项目，客户满意度提升 15%。`
2. 将第一行设置为：黑体、三号（16 磅）、加粗、居中对齐。
3. 将第二行设置为：宋体、小四（12 磅）、首行缩进 2 字符、两端对齐。
4. 保存文档。最终文件必须是：{path}

要求：
- 必须通过 WPS 功能区/界面完成字体、字号、加粗、对齐和缩进设置，不要用 COM、python-docx 或直接改文件。
- 保存后请读回文档或窗口状态，确认两行文字和格式都已生效。
""".strip()


def _setup_wps_document(run_id: str, filename: str) -> dict:
    from docx import Document

    run_dir = OUTPUTS / run_id / "wps"
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / filename
    if path.exists():
        path.unlink()
    Document().save(path)

    subprocess.Popen([str(WPS_EXE), str(path)], cwd=str(run_dir))
    title = f"{filename} - WPS Office"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not any(title == item[1] for item in visible_windows()):
        time.sleep(0.5)
    if not any(title == item[1] for item in visible_windows()):
        # WPS may open the document as a tab under a generic window title.
        activate("WPS Office")
        time.sleep(1.0)
    activate(title)
    time.sleep(1.5)
    return {"path": path, "title": title}


def _setup_wps(run_id: str) -> dict:
    return _setup_wps_document(run_id, "grounding_eval_wps.docx")


def _setup_wps_append(run_id: str) -> dict:
    context = _setup_wps_document(run_id, "grounding_eval_append.docx")
    context["line"] = f"Grounding Append {run_id}"
    return context


def _verify_wps(context: dict) -> dict:
    path: Path = context["path"]
    if not path.exists():
        return {"success": False, "error": "saved document is missing", "path": str(path)}
    data = _docx_text_and_format(path)
    paragraphs = data["paragraphs"]
    title = next((p for p in paragraphs if "季度总结报告" in p["text"]), None)
    body = next((p for p in paragraphs if "本季度共完成 12 个项目" in p["text"]), None)
    checks: dict[str, Any] = {
        "title_found": title is not None,
        "body_found": body is not None,
        "title_alignment": str(title["alignment"]) if title else None,
        "body_alignment": str(body["alignment"]) if body else None,
    }
    if title:
        runs = [run for run in title["runs"] if run["text"].strip()]
        checks["title_bold"] = bool(runs) and all(run["bold"] is True for run in runs)
        checks["title_size"] = [run["size_pt"] for run in runs]
        checks["title_font"] = [run["east_asia"] or run["name"] for run in runs] + [
            title["paragraph_east_asia"],
            title["style_east_asia"],
        ]
        checks["title_centered"] = "CENTER" in str(title["alignment"])
        checks["title_ok"] = (
            checks["title_bold"]
            and all(size is not None and abs(size - 16) < 0.6 for size in checks["title_size"])
            and any(name in ("黑体", "SimHei") for name in checks["title_font"])
            and checks["title_centered"]
        )
    if body:
        runs = [run for run in body["runs"] if run["text"].strip()]
        checks["body_size"] = [run["size_pt"] for run in runs]
        checks["body_font"] = [run["east_asia"] or run["name"] for run in runs] + [
            body["paragraph_east_asia"],
            body["style_east_asia"],
        ]
        checks["body_indent_chars"] = body["first_line_chars"]
        checks["body_indent_twips"] = body["first_line_twips"]
        checks["body_justified"] = "JUSTIFY" in str(body["alignment"])
        indent_ok = body["first_line_chars"] == 200 or (
            body["first_line_twips"] is not None and abs(body["first_line_twips"] - 480) <= 24
        )
        checks["body_ok"] = (
            all(size is not None and abs(size - 12) < 0.6 for size in checks["body_size"])
            and any(name in ("宋体", "SimSun") for name in checks["body_font"])
            and indent_ok
            and checks["body_justified"]
        )
    success = bool(checks.get("title_ok") and checks.get("body_ok"))
    return {"success": success, "checks": checks, "document": data}


def _wps_append_prompt(path: Path, line: str) -> str:
    return f"""
打开 WPS 文字窗口，标题为 "grounding_eval_append.docx - WPS Office"（如果窗口未在前台，请先切换到它）。
文档是空白的。请在文档末尾追加一行：

`{line}`

然后保存文档。最终文件必须是：{path}

要求：
- 必须通过 WPS 界面把光标放到文档末尾并输入，不要用 COM、python-docx 或直接改文件。
- 保存后请读回文档或窗口状态，确认这一行已经写入。
""".strip()


def _verify_wps_append(context: dict) -> dict:
    path: Path = context["path"]
    line: str = context["line"]
    if not path.exists():
        return {"success": False, "error": "saved document is missing", "path": str(path)}
    data = _docx_text_and_format(path)
    text = "\n".join(paragraph["text"] for paragraph in data["paragraphs"])
    return {
        "success": line in text,
        "expected_line": line,
        "found": line in text,
        "paragraph_count": len(data["paragraphs"]),
        "document_excerpt": text[:2000],
    }


def _setup_wps_type(run_id: str) -> dict:
    context = _setup_wps_document(run_id, "grounding_eval_type.docx")
    context["line"] = f"Grounding Type {run_id}"
    return context


def _wps_type_prompt(path: Path, line: str) -> str:
    return f"""
打开 WPS 文字窗口，标题为 "grounding_eval_type.docx - WPS Office"（如果窗口未在前台，请先切换到它）。
文档是空白的。请输入以下两行：

`{line}`
`- second line`

然后保存文档。最终文件必须是：{path}

要求：
- 必须通过 WPS 编辑器界面输入，不要用 COM、python-docx 或直接改文件。
- 保存后请读回文档或窗口状态，确认两行都已写入。
""".strip()


def _verify_wps_type(context: dict) -> dict:
    path: Path = context["path"]
    line: str = context["line"]
    if not path.exists():
        return {"success": False, "error": "saved document is missing", "path": str(path)}
    data = _docx_text_and_format(path)
    text = "\n".join(paragraph["text"] for paragraph in data["paragraphs"])
    return {
        "success": line in text and "- second line" in text,
        "expected_line": line,
        "first_found": line in text,
        "second_found": "- second line" in text,
        "paragraph_count": len(data["paragraphs"]),
        "document_excerpt": text[:2000],
    }


def _cleanup_wps(context: dict) -> None:
    title = context.get("title")
    if title:
        activate(title)
        time.sleep(0.3)
        # Closing would prompt to save; the task already saved. Use Escape-safe close via Alt+F4.
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "$w = Get-Process wps -ErrorAction SilentlyContinue | "
                        f"Where-Object {{$_.MainWindowTitle -eq '{title}'}}; "
                        "if ($w) { $w.CloseMainWindow() | Out-Null }"
                    ),
                ],
                timeout=15,
                check=False,
            )
        except Exception:
            pass


# --------------------------------------------------------------------------- Tencent Meeting


def _wemeet_prompt(topic: str) -> str:
    return f"""
打开“腾讯会议”主窗口（标题就是“腾讯会议”）。请创建一个预定会议：

1. 点击主界面上的“预定会议”。
2. 在预定会议面板中，把“主题”改为：`{topic}`
3. 将“时长”设置为 60 分钟（当前默认 30 分钟）。
4. 打开“入会密码”，密码设置为 `2468`。
5. 打开“开启等候室”。
6. 将“成员入会时静音”选择为“开启”。
7. 取消勾选“添加到日历”。
8. 点击“预定”完成创建。

完成后读取会议列表，确认列表中出现主题 `{topic}` 的会议，并报告实际结果。
""".strip()


def _wemeet_form_prompt(topic: str) -> str:
    return f"""
打开“腾讯会议”主窗口（标题就是“腾讯会议”），点击主界面上的“预定会议”，然后只填写属性、不要提交：

1. 把“主题”改为：`{topic}`
2. 将“时长”设置为 60 分钟（当前默认 30 分钟）。
3. 打开“入会密码”，密码设置为 `2468`。
4. 打开“开启等候室”。
5. 将“成员入会时静音”选择为“开启”。
6. 取消勾选“添加到日历”。
7. 不要点击“预定”，保持预定会议面板打开。

完成后重新读取面板，逐项核对主题、时长、密码、等候室、静音和日历的当前值，并报告每一项的实际状态。
""".strip()


def _setup_wemeet_form(run_id: str) -> dict:
    context = _setup_wemeet(run_id)
    context["topic"] = f"Grounding Form {run_id}"
    return context


def _verify_wemeet_form(context: dict) -> dict:
    from computer_use import Computer

    topic = context["topic"]
    with Computer() as computer:
        app = computer.app(window_title="预定会议")
        observation = app.read(view="tree", full=True)
        text = observation.text or ""
    checks = {
        "topic": topic in text,
        "duration": ("60分钟" in text) or ("1小时" in text) or ("60 分钟" in text),
        "password_enabled": re.search(r"\d+\s+复选框\s+\(on[^)]*\)\s+入会密码", text) is not None,
        # The password edit is a protected field; its value is deliberately not
        # readable through accessibility. Only its presence is verifiable here.
        "password_field_present": re.search(r"编辑 .*Protected: true", text) is not None,
        "password_value": "2468" in text,
        "waiting_room": re.search(r"\d+\s+复选框\s+\(on[^)]*\)\s+开启等候室", text) is not None,
        "calendar_off": re.search(r"\d+\s+复选框\s+\(off[^)]*\)\s+添加到日历", text) is not None,
        "mute_on": re.search(r"\d+\s+单选按钮\s+\((?:selected|on)[^)]*\)\s+开启", text) is not None,
    }
    success = all(
        checks[name]
        for name in ("topic", "duration", "password_enabled", "password_field_present", "waiting_room", "calendar_off", "mute_on")
    )
    return {"success": success, "checks": checks, "ui_excerpt": text[-3500:]}


def _cleanup_wemeet_form(context: dict) -> None:
    _close_wemeet_dialog()
    activate("腾讯会议")


def _wemeet_join_prompt() -> str:
    return """
打开“腾讯会议”主窗口（标题就是“腾讯会议”），点击主界面上的“加入会议”，只打开加入会议面板，不要真正加入：

1. 点击“加入会议”。
2. 确认面板中出现“会议号”和“您的名称/入会名称”字段。
3. 不要点击“加入”/“加入会议”提交。
4. 保持加入会议面板打开，读取并报告面板中的字段。

如果面板没有打开，请重新读取主窗口并重试。
""".strip()


def _setup_wemeet_join(run_id: str) -> dict:
    return _setup_wemeet(run_id)


def _verify_wemeet_join(context: dict) -> dict:
    from computer_use import Computer

    texts: list[str] = []
    with Computer() as computer:
        app = computer.app("wemeetapp")
        refs = [int(x) for x in re.findall(r"window=(\d+)", app.read(view="image").text or "")]
        for ref in refs:
            try:
                observation = _read(app, view="tree", full=True, window=ref)
                texts.append(observation.text or "")
            except Exception as error:
                texts.append(f"<window {ref}: {type(error).__name__}: {error}>")
    combined = "\n".join(texts)
    name_field = any(marker in combined for marker in ("您的名称", "入会名称", "我的名称"))
    number_field = "会议号" in combined
    return {
        "success": name_field and number_field,
        "name_field": name_field,
        "number_field": number_field,
        "ui_excerpt": combined[-2500:],
    }


def _cleanup_wemeet_join(context: dict) -> None:
    _close_wemeet_dialog("加入会议")
    activate("腾讯会议")
    _press_escape()


def _wemeet_join_fill_prompt() -> str:
    return """
打开“腾讯会议”主窗口（标题就是“腾讯会议”），点击主界面上的“加入会议”，只填写、不加入：

1. 点击“加入会议”。
2. 在“会议号”中填写：`135792468`
3. 在“您的名称”中填写：`Grounding Eval`
4. 不要点击“加入”/“加入会议”提交。
5. 保持加入会议面板打开，读取并报告会议号和名称字段的实际值。
""".strip()


def _setup_wemeet_join_fill(run_id: str) -> dict:
    return _setup_wemeet(run_id)


def _verify_wemeet_join_fill(context: dict) -> dict:
    from computer_use import Computer

    texts: list[str] = []
    with Computer() as computer:
        app = computer.app("wemeetapp")
        refs = [int(x) for x in re.findall(r"window=(\d+)", app.read(view="image").text or "")]
        for ref in refs:
            try:
                observation = _read(app, view="tree", full=True, window=ref)
                texts.append(observation.text or "")
            except Exception as error:  # noqa: BLE001 - try every visible window
                texts.append(f"<window {ref}: {type(error).__name__}: {error}>")
    combined = "\n".join(texts)
    normalized = re.sub(r"\s+", "", combined)
    name_field = any(marker in combined for marker in ("您的名称", "入会名称", "我的名称"))
    number_field = "会议号" in combined
    number_value = "135792468" in normalized
    name_value = "GroundingEval" in normalized
    return {
        "success": name_field and number_field and number_value and name_value,
        "name_field": name_field,
        "number_field": number_field,
        "number_value": number_value,
        "name_value": name_value,
        "ui_excerpt": combined[-2500:],
    }


def _cleanup_wemeet_join_fill(context: dict) -> None:
    _cleanup_wemeet_join(context)


def _wemeet_contacts_prompt() -> str:
    return """
打开“腾讯会议”主窗口（标题就是“腾讯会议”），点击主界面上的“通讯录”，打开通讯录页面：

1. 点击“通讯录”。
2. 确认出现通讯录页面（可能包含“通讯录”“联系人”等）。
3. 不要登录、不要修改任何设置。
4. 保持通讯录页面打开，读取并报告页面内容。
""".strip()


def _setup_wemeet_contacts(run_id: str) -> dict:
    return _setup_wemeet(run_id)


def _verify_wemeet_contacts(context: dict) -> dict:
    from computer_use import Computer

    texts: list[str] = []
    with Computer() as computer:
        app = computer.app("wemeetapp")
        refs = [int(x) for x in re.findall(r"window=(\d+)", app.read(view="image").text or "")]
        for ref in refs:
            try:
                observation = _read(app, view="tree", full=True, window=ref)
                texts.append(observation.text or "")
            except Exception as error:  # noqa: BLE001 - try every visible window
                texts.append(f"<window {ref}: {type(error).__name__}: {error}>")
    combined = "\n".join(texts)
    opened = any(
        "company_contacts_main_widget" in text or ("通讯录" in text and "modal" in text)
        for text in texts
    )
    return {"success": opened, "contacts_page": opened, "ui_excerpt": combined[-2500:]}


def _cleanup_wemeet_contacts(context: dict) -> None:
    activate("腾讯会议")
    _press_escape()
    activate("腾讯会议")


def _setup_wemeet(run_id: str) -> dict:
    if not wait_window(lambda title: title == "腾讯会议", timeout=3):
        subprocess.Popen([str(WEMEET_EXE)])
    found = wait_window(lambda title: title in ("腾讯会议", "预定会议", "加入会议"), timeout=45)
    if found is None:
        raise RuntimeError("Tencent Meeting window did not appear")
    # Close a leftover panel from an earlier run, then activate the home window.
    if found[1] != "腾讯会议":
        _close_wemeet_dialog(found[1])
        found = wait_window(lambda title: title == "腾讯会议", timeout=10) or found
    activate("腾讯会议")
    _press_escape()
    activate("腾讯会议")
    time.sleep(1.0)
    return {"topic": f"Grounding Eval {run_id}", "title": "腾讯会议"}


def _press_escape() -> None:
    try:
        from computer_use import Computer

        with Computer() as computer:
            computer.press_key("escape")
    except Exception:
        pass


def _close_wemeet_dialog(title: str = "预定会议") -> None:
    """Dismiss a schedule/join dialog without submitting it."""
    try:
        activate(title)
        time.sleep(0.4)
        from computer_use import Computer

        with Computer() as computer:
            app = computer.app(window_title=title)
            observation = app.read(view="tree", full=False)
            ref = None
            for line in (observation.text or "").splitlines()[:20]:
                match = re.match(r"\s*(\d+)\s+按钮.*关闭", line)
                if match:
                    ref = int(match.group(1))
                    break
            if ref is not None:
                app.element(ref).click()
        time.sleep(0.8)
    except Exception:
        _press_escape()


def _read(app, **kwargs):
    """Read with visual options when the SDK supports them; baseline has no such kwargs."""
    try:
        return app.read(**kwargs)
    except TypeError:
        for key in ("visual", "marks"):
            kwargs.pop(key, None)
        return app.read(**kwargs)


def _verify_wemeet(context: dict) -> dict:
    from computer_use import Computer

    topic = context["topic"]
    detail_titles = [title for _, title, _ in visible_windows() if title.startswith("会议号：")]
    texts: list[str] = []
    main_windows: list[int] = []
    with Computer() as computer:
        # Enumerate the App's windows first; the returned refs stay usable for the
        # window-scoped reads in this same session.
        app = computer.app("wemeetapp")
        listing = app.read(view="image")
        for ref in re.findall(r"window=(\d+)", listing.text or ""):
            try:
                observation = _read(app, view="both", full=True, visual="auto", window=int(ref))
                texts.append(observation.text or "")
                main_windows.append(int(ref))
            except Exception as error:
                texts.append(f"<window {ref}: {type(error).__name__}: {error}>")
        for title in detail_titles:
            try:
                detail = computer.app(window_title=title)
                observation = _read(detail, view="both", full=True, visual="auto")
                texts.append(observation.text or "")
            except Exception as error:
                texts.append(f"<{type(error).__name__}: {error}>")
    combined = "\n".join(texts)
    found = topic in combined or topic.replace(" ", "") in combined.replace(" ", "")
    hour_long = False
    for start, end in re.findall(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})", combined):
        start_minutes = int(start.split(":")[0]) * 60 + int(start.split(":")[1])
        end_minutes = int(end.split(":")[0]) * 60 + int(end.split(":")[1])
        if end_minutes - start_minutes in (60, 0):  # 0 covers an unparsed overnight wrap
            hour_long = True
    return {
        "success": found,
        "topic": topic,
        "topic_in_ui": found,
        "main_windows": main_windows,
        "detail_windows": detail_titles,
        "meeting_is_one_hour": hour_long,
        "ui_excerpt": combined[-2500:],
    }


def _cleanup_wemeet(context: dict) -> None:
    # Close the meeting detail window; the scheduled meeting itself is left for
    # the account owner to review. A unique topic keeps repeated runs independent.
    for _, title, _ in visible_windows():
        if title.startswith("会议号："):
            activate(title)
            time.sleep(0.3)
            _press_escape()


# --------------------------------------------------------------------------- VS Code


def _vscode_prompt(topic_path: Path) -> str:
    return f"""
打开 VS Code 窗口（标题最初是 "Welcome - vscode_ws - Visual Studio Code"）。请完成：

1. 如果出现欢迎/登录引导页，选择“Continue without Signing In”或“Skip”进入编辑器。
2. 打开设置界面（例如 Ctrl+, 或左下角齿轮），使用设置界面完成以下修改：
   - `Editor: Font Size` 设为 `18`
   - `Editor: Word Wrap` 设为 `on`
   - 关闭 `Editor: Minimap: Enabled`
   - `Files: Auto Save` 设为 `afterDelay`
3. 不要直接编辑 settings.json；必须通过设置界面的控件完成。
4. 完成后打开/读取用户设置文件确认上述四项已经写入。

用户设置文件应位于：{topic_path}
""".strip()


def _vscode_file_prompt(workspace: Path, line: str) -> str:
    return f"""
打开 VS Code 窗口（标题最初是 "Welcome - vscode_ws - Visual Studio Code"）。请完成：

1. 如果出现欢迎/登录引导页，选择“Continue without Signing In”或“Skip”进入编辑器。
2. 在 `vscode_ws` 工作区中新建文件 `grounding_notes.md`。
3. 输入以下三行：
   `{line}`
   `- first item`
   `- second item`
4. 保存文件，最终路径必须是：{workspace / "grounding_notes.md"}

要求：必须通过 VS Code 的资源管理器/编辑器界面完成，不要用 shell 或直接写文件。
""".strip()


def _setup_vscode(run_id: str, *, seed: dict[str, str] | None = None) -> dict:
    base = OUTPUTS / run_id / "vscode"
    user = base / "user"
    extensions = base / "extensions"
    workspace = base / "vscode_ws"
    for path in (user, extensions, workspace):
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
    for name, content in (seed or {}).items():
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    # Stop a previous isolated instance if it is still around.
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'Code.exe' -and "
                f"$_.CommandLine -like '*{base}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            ),
        ],
        timeout=20,
        check=False,
    )
    time.sleep(0.5)
    process = subprocess.Popen(
        [
            str(CODE_EXE),
            "--user-data-dir", str(user),
            "--extensions-dir", str(extensions),
            "--new-window",
            "--disable-workspace-trust",
            str(workspace),
        ]
    )
    found = wait_window(lambda title: "vscode_ws" in title and "Visual Studio Code" in title, timeout=60)
    if found is None:
        raise RuntimeError("VS Code window did not appear")
    activate("Welcome")
    time.sleep(1.0)
    return {
        "process": process,
        "base": base,
        "user": user,
        "workspace": workspace,
        "settings": user / "User" / "settings.json",
        "title": found[1],
        "line": f"Grounding File {run_id}",
    }


def _verify_vscode(context: dict) -> dict:
    settings_path: Path = context["settings"]
    # The Settings UI persists shortly after a change; give it a moment.
    deadline = time.monotonic() + 10
    values: dict = {}
    while time.monotonic() < deadline:
        if settings_path.exists():
            try:
                raw = settings_path.read_text(encoding="utf-8")
                parsed = json.loads(re.sub(r"//.*", "", raw)) if raw.strip().startswith("{") else {}
                values = parsed if isinstance(parsed, dict) else {}
                if len(values) >= 4:
                    break
            except Exception:
                pass
        time.sleep(0.5)
    checks = {
        "fontSize": values.get("editor.fontSize"),
        "wordWrap": values.get("editor.wordWrap"),
        "minimap": (values.get("editor.minimap.enabled")),
        "autoSave": values.get("files.autoSave"),
    }
    success = (
        checks["fontSize"] == 18
        and checks["wordWrap"] == "on"
        and checks["minimap"] is False
        and checks["autoSave"] == "afterDelay"
    )
    return {
        "success": success,
        "checks": checks,
        "settings_path": str(settings_path),
        "settings_excerpt": json.dumps(values, ensure_ascii=False)[:2000],
    }


def _vscode_open_prompt() -> str:
    return """
打开 VS Code 窗口（标题最初是 "Welcome - vscode_ws - Visual Studio Code"）。

1. 如果出现欢迎/登录引导页，选择“Continue without Signing In”或“Skip”进入编辑器。
2. 通过左侧活动栏的 Explorer（资源管理器）打开资源管理器面板。
3. 在资源管理器中点击预置文件 `notes.md`，让编辑器打开它。
4. 不要修改 `notes.md`，也不要修改设置。

完成后确认编辑器标题包含 `notes.md`，并报告当前打开的文件。
""".strip()


def _setup_vscode_open(run_id: str) -> dict:
    return _setup_vscode(run_id, seed={"notes.md": "# Notes\n\nGrounding Explorer\n"})


def _verify_vscode_open(context: dict) -> dict:
    titles = [title for _, title, _ in visible_windows()]
    matches = [title for title in titles if "notes.md" in title and "vscode_ws" in title]
    return {"success": bool(matches), "matching_titles": matches, "titles": titles[-10:]}


def _verify_vscode_file(context: dict) -> dict:
    path: Path = context["workspace"] / "grounding_notes.md"
    line: str = context["line"]
    if not path.exists():
        return {"success": False, "error": "file is missing", "path": str(path)}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "success": line in text and "- first item" in text and "- second item" in text,
        "path": str(path),
        "expected_line": line,
        "excerpt": text[:2000],
    }


def _cleanup_vscode(context: dict) -> None:
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'Code.exe' -and "
                f"$_.CommandLine -like '*{context['base']}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            ),
        ],
        timeout=20,
        check=False,
    )


def _trae_start_prompt() -> str:
    return """
打开标题为 "TraeCode CN" 的 Trae CN 窗口。当前是首次运行欢迎页，界面可见“欢迎使用”和“开始”：

1. 点击“开始”。
2. 如果随后出现登录、引导或工作区选择页，选择跳过、关闭或继续，不要输入任何账号或密码。
3. 读取并报告点击后的窗口标题和可见界面文字。

不要使用 shell 或直接修改文件。
""".strip()


def _setup_trae(run_id: str) -> dict:
    base = OUTPUTS / run_id / "trae"
    user = base / "user"
    extensions = base / "extensions"
    workspace = base / "trae_ws"
    for path in (user, extensions, workspace):
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | Where-Object { "
                f"$_.CommandLine -like '*{base}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            ),
        ],
        timeout=20,
        check=False,
    )
    time.sleep(0.5)
    process = subprocess.Popen(
        [
            str(TRAE_EXE),
            "--user-data-dir", str(user),
            "--extensions-dir", str(extensions),
            "--new-window",
            "--disable-workspace-trust",
            str(workspace),
        ]
    )
    found = wait_window(lambda title: "Trae" in title, timeout=90)
    if found is None:
        raise RuntimeError("Trae CN window did not appear")
    activate(found[1])
    time.sleep(1.5)
    return {
        "process": process,
        "base": base,
        "user": user,
        "workspace": workspace,
        "settings": user / "User" / "settings.json",
        "title": found[1],
    }


def _verify_trae_start(context: dict) -> dict:
    from computer_use import Computer

    texts: list[str] = []
    errors: list[str] = []
    with Computer() as computer:
        for app_info in [a for a in computer.list_apps() if "Trae" in a.display_name]:
            try:
                app = computer.app(app_info.id)
                observation = _read(app, view="both", full=True, visual="auto")
                texts.append(observation.text or "")
            except Exception as error:
                errors.append(f"{app_info.id}: {type(error).__name__}: {error}")
    titles = [title for _, title, _ in visible_windows() if "trae_ws" in title and "Trae" in title]
    combined = "\n".join(texts)
    return {
        "success": bool(titles) and bool(combined.strip()) and "欢迎使用" not in combined,
        "welcome_visible": "欢迎使用" in combined,
        "titles": titles[-10:],
        "errors": errors[-5:],
        "ui_excerpt": combined[-2500:],
    }


def _cleanup_trae(context: dict) -> None:
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | Where-Object { "
                f"$_.CommandLine -like '*{context['base']}*' }} | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
            ),
        ],
        timeout=20,
        check=False,
    )


# --------------------------------------------------------------------------- cases


def cases() -> dict[str, Case]:
    return {
        "wps_format": Case(
            name="wps_format",
            app="WPS Writer",
            prompt="",  # replaced per run below
            setup=_setup_wps,
            verify=_verify_wps,
            cleanup=_cleanup_wps,
        ),
        "wps_append": Case(
            name="wps_append",
            app="WPS Writer",
            prompt="",  # replaced per run below
            setup=_setup_wps_append,
            verify=_verify_wps_append,
            cleanup=_cleanup_wps,
        ),
        "wemeet_schedule": Case(
            name="wemeet_schedule",
            app="Tencent Meeting",
            prompt="",  # replaced per run below
            setup=_setup_wemeet,
            verify=_verify_wemeet,
            cleanup=_cleanup_wemeet,
        ),
        "wemeet_form": Case(
            name="wemeet_form",
            app="Tencent Meeting",
            prompt="",  # replaced per run below
            setup=_setup_wemeet_form,
            verify=_verify_wemeet_form,
            cleanup=_cleanup_wemeet_form,
        ),
        "wemeet_join": Case(
            name="wemeet_join",
            app="Tencent Meeting",
            prompt="",  # replaced per run below
            setup=_setup_wemeet_join,
            verify=_verify_wemeet_join,
            cleanup=_cleanup_wemeet_join,
        ),
        "wemeet_contacts": Case(
            name="wemeet_contacts",
            app="Tencent Meeting",
            prompt="",  # replaced per run below
            setup=_setup_wemeet_contacts,
            verify=_verify_wemeet_contacts,
            cleanup=_cleanup_wemeet_contacts,
        ),
        "wemeet_join_fill": Case(
            name="wemeet_join_fill",
            app="Tencent Meeting",
            prompt="",  # replaced per run below
            setup=_setup_wemeet_join_fill,
            verify=_verify_wemeet_join_fill,
            cleanup=_cleanup_wemeet_join_fill,
        ),
        "vscode_settings": Case(
            name="vscode_settings",
            app="VS Code",
            prompt="",  # replaced per run below
            setup=_setup_vscode,
            verify=_verify_vscode,
            cleanup=_cleanup_vscode,
        ),
        "vscode_file": Case(
            name="vscode_file",
            app="VS Code",
            prompt="",  # replaced per run below
            setup=_setup_vscode,
            verify=_verify_vscode_file,
            cleanup=_cleanup_vscode,
        ),
        "vscode_open": Case(
            name="vscode_open",
            app="VS Code",
            prompt="",  # replaced per run below
            setup=_setup_vscode_open,
            verify=_verify_vscode_open,
            cleanup=_cleanup_vscode,
        ),
        "trae_start": Case(
            name="trae_start",
            app="Trae CN",
            prompt="",  # replaced per run below
            setup=_setup_trae,
            verify=_verify_trae_start,
            cleanup=_cleanup_trae,
        ),
        "wps_type": Case(
            name="wps_type",
            app="WPS Writer",
            prompt="",  # replaced per run below
            setup=_setup_wps_type,
            verify=_verify_wps_type,
            cleanup=_cleanup_wps,
        ),
    }


def materialize(case: Case, run_id: str) -> Case:
    """Fill in run-specific paths and unique topics in the task prompt."""
    if case.name == "wps_format":
        path = OUTPUTS / run_id / "wps" / "grounding_eval_wps.docx"
        return Case(case.name, case.app, _wps_format_prompt(path), case.setup, case.verify, case.cleanup)
    if case.name == "wps_append":
        path = OUTPUTS / run_id / "wps" / "grounding_eval_append.docx"
        line = f"Grounding Append {run_id}"
        return Case(case.name, case.app, _wps_append_prompt(path, line), case.setup, case.verify, case.cleanup)
    if case.name == "wemeet_schedule":
        return Case(case.name, case.app, _wemeet_prompt(f"Grounding Eval {run_id}"), case.setup, case.verify, case.cleanup)
    if case.name == "wemeet_form":
        return Case(case.name, case.app, _wemeet_form_prompt(f"Grounding Form {run_id}"), case.setup, case.verify, case.cleanup)
    if case.name == "wemeet_join":
        return Case(case.name, case.app, _wemeet_join_prompt(), case.setup, case.verify, case.cleanup)
    if case.name == "wemeet_contacts":
        return Case(case.name, case.app, _wemeet_contacts_prompt(), case.setup, case.verify, case.cleanup)
    if case.name == "wemeet_join_fill":
        return Case(case.name, case.app, _wemeet_join_fill_prompt(), case.setup, case.verify, case.cleanup)
    if case.name == "vscode_settings":
        settings = OUTPUTS / run_id / "vscode" / "user" / "User" / "settings.json"
        return Case(case.name, case.app, _vscode_prompt(settings), case.setup, case.verify, case.cleanup)
    if case.name == "vscode_file":
        workspace = OUTPUTS / run_id / "vscode" / "vscode_ws"
        line = f"Grounding File {run_id}"
        return Case(case.name, case.app, _vscode_file_prompt(workspace, line), case.setup, case.verify, case.cleanup)
    if case.name == "vscode_open":
        return Case(case.name, case.app, _vscode_open_prompt(), case.setup, case.verify, case.cleanup)
    if case.name == "trae_start":
        return Case(case.name, case.app, _trae_start_prompt(), case.setup, case.verify, case.cleanup)
    if case.name == "wps_type":
        path = OUTPUTS / run_id / "wps" / "grounding_eval_type.docx"
        line = f"Grounding Type {run_id}"
        return Case(case.name, case.app, _wps_type_prompt(path, line), case.setup, case.verify, case.cleanup)
    return case


SUITE = ("wps_append", "vscode_file", "wemeet_form", "wemeet_join", "wemeet_join_fill")


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run live grounding tasks through the reference agent")
    parser.add_argument(
        "--task", default="suite", choices=("suite", "all", *cases())
    )
    parser.add_argument("--variant", default="treatment", choices=("treatment", "baseline"))
    parser.add_argument("--run-id", default=time.strftime("run-%Y%m%d-%H%M%S"))
    parser.add_argument("--max-iterations", type=int, default=40)
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args(argv)

    required = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 2

    if args.task == "all":
        selected = list(cases())
    elif args.task == "suite":
        selected = list(SUITE)
    else:
        selected = [args.task]
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    records: list[RunRecord] = []
    for repeat in range(args.repeat):
        for name in selected:
            run_id = args.run_id if args.repeat == 1 else f"{args.run_id}-r{repeat + 1}"
            case = materialize(cases()[name], run_id)
            print(f"\n=== {case.name} | {args.variant} | {run_id} ===", flush=True)
            record = await run_case(case, args.variant, run_id, args.max_iterations)
            records.append(record)
            print(
                f"success={record.success} seconds={record.seconds} tools={record.tool_calls} "
                f"errors={record.tool_errors} reads={record.read_calls} visual_reads={record.visual_reads} "
                f"visual_refs={record.visual_refs} clicks={record.click_calls}",
                flush=True,
            )
            print(json.dumps(record.verification, ensure_ascii=False, indent=2)[:4000], flush=True)
            target = OUTPUTS / args.run_id
            target.mkdir(parents=True, exist_ok=True)
            record_file = target / f"{case.name}-r{repeat + 1}-{args.variant}.json"
            record_file.write_text(json.dumps(record.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")

    target = OUTPUTS / args.run_id
    target.mkdir(parents=True, exist_ok=True)
    output = target / f"{args.variant}-results.json"
    output.write_text(json.dumps([record.__dict__ for record in records], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {output}")
    return 0 if all(record.success for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
