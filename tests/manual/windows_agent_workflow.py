# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run independent, real DeepAgent attempts against disposable Windows targets."""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from artifact_json import captured_interface_metadata
from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver
from computer_use.runtime_provenance import startup_provenance
from windows_browser_fixture import browser_fixture
from windows_calculator_fixture import calculator_display, calculator_fixture, calculator_operands
from windows_desktop_batch import manifest
from windows_paste_workflow import read_state, wait_state
from windows_terminal_workflow import quote_powershell, wait_file

from computer_use_agent.cli.config import load

MANUAL = Path(__file__).resolve().parent
ROOT = MANUAL.parents[1]
RUNS = Path.home() / ".computer-use/agent/runs"
CASES = {
    "edit": "WAG-003", "terminal": "WAG-004", "browser": "WAG-005",
    "calculator": "WAG-008", "modal": "WAG-009", "visual": "WOBS-004",
}


@contextmanager
def fixture(source: str, name: str, state: Path, *args: str):
    executable = state.parent / (name + ".exe")
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run(
        [
            str(csc), "/nologo", "/target:winexe", "/platform:x64", "/warnaserror+",
            "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
            "/reference:System.Web.Extensions.dll", f"/out:{executable}", str(MANUAL / source),
        ],
        check=True,
    )
    with subprocess.Popen([str(executable), str(state), *args]) as process:
        try:
            if not wait_state(state, bool, 8):
                raise RuntimeError("The Windows fixture did not publish initial state")
            yield
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=5)


def journal_result(task: str, existing: set[Path]) -> dict:
    for path in set(RUNS.glob("*/events.jsonl")) - existing:
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if not any(event["kind"] == "user" and event["data"].get("content") == task for event in events):
            continue
        start = next(event["data"] for event in events if event["kind"] == "start")
        results = [event["data"]["result"] for event in events if event["kind"] == "tool_result"]
        calls = [event["data"]["name"] for event in events if event["kind"] == "tool_call"]
        answers = [
            event["data"]["payload"].get("output")
            for event in events
            if event["kind"] == "agent" and isinstance(event["data"], dict) and event["data"].get("type") == "answer"
        ]
        return {
            "journal": str(path), "model": start.get("model"),
            "allow_global_input": start.get("allow_global_input"),
            "answer": answers[-1] if answers else None,
            "desktop_calls": calls.count("computer_use"),
            "reset_calls": calls.count("computer_use_reset"),
            "tool_errors": sum(bool(result.get("isError")) for result in results),
            "delivered_images": sum(
                item["type"] == "image" for result in results for item in result.get("content", ())
            ),
        }
    return {}


def invoke(task: str, log: Path, config: Path | None, timeout: int) -> dict:
    before = set(RUNS.glob("*/events.jsonl"))
    command = [sys.executable, "-m", "computer_use_agent.cli", "--max-iterations", "16"]
    if config is not None:
        command.extend(("--config", str(config.resolve())))
    command.append(task)
    started = time.monotonic()
    with log.open("xb") as stdout:
        try:
            result = subprocess.run(
                command, cwd=ROOT, stdout=stdout, stderr=subprocess.STDOUT, timeout=timeout,
                env={**os.environ, "PYTHONUTF8": "1"}, creationflags=subprocess.CREATE_NO_WINDOW, check=False,
            )
            status = {"exit": result.returncode}
        except subprocess.TimeoutExpired:
            status = {"timeout": True}
    return {
        **status, "seconds": round(time.monotonic() - started, 3),
        **journal_result(task, before),
    }


def edit(nonce: str, directory: Path, launch) -> dict:
    title = "CUA Paste Fixture " + nonce
    value = "CUA_" + nonce + "_draft"
    task = (
        f'在测试窗口“{title}”的 Paste target 文本框输入“{value}”，保持 Bystander 不变。'
        "查看实际界面核对输入，然后报告文本框中的内容。仅操作这个测试窗口。"
    )
    state = directory / "state.json"
    with fixture("windows_paste_fixture.cs", "CuaPasteFixture", state, "0", nonce, "False"):
        with Computer() as setup:
            setup.app("CuaPasteFixture").read(view="tree", allow_foreground=True)
        result = launch(task)
        actual = read_state(state)
    return {
        **result, "task": task, "actual": actual, "expected_answer": value,
        "effect_ok": actual.get("text") == value and actual.get("bystander") == "untouched",
    }


def modal(_nonce: str, directory: Path, launch) -> dict:
    task = (
        "只操作 CUA Close Document A 和 CUA Close Document B 这两个测试窗口。"
        "保留 A。尝试关闭 B，第一次在确认框选取消并确认 B 仍然打开；"
        "再关闭 B，这次在确认框选择确定。核对 A 仍在、B 已关闭，然后报告结果。"
    )
    state = directory / "state.json"
    with fixture("windows_close_fixture.cs", "CuaCloseFixture", state):
        result = launch(task)
        actual = read_state(state)
    return {
        **result, "task": task, "actual": actual, "expected_answer": "B",
        "effect_ok": (
            actual.get("close_a") == 0 and actual.get("closed_b") is True
            and actual.get("close_b") == 2 and actual.get("cancel_b") == actual.get("confirm_b") == 1
        ),
    }


def close_terminal(title: str, pid: str) -> str | None:
    try:
        with Computer() as computer:
            app = computer.app("WindowsTerminal")
            windows = captured_interface_metadata(app.read(view="tree", full=True))["windows"]
            window = next((int(window["ref"]) for window in windows if window["title"] == title), None)
            if window is not None:
                app.close_window(window)
    except (ComputerUseError, RuntimeError) as error:
        return str(error)
    finally:
        subprocess.run(
            ["taskkill.exe", "/PID", pid], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=5,
        )
    return None


def terminal(nonce: str, directory: Path, launch) -> dict:
    title, marker = f"CUA Terminal {nonce}", f"CUA_RESULT_{nonce}"
    ready, transcript = directory / "ready.txt", directory / "terminal.txt"
    script = (
        f"Start-Transcript -Path {quote_powershell(transcript)}; "
        f"[IO.File]::WriteAllText({quote_powershell(ready)}, [string]$PID)"
    )
    subprocess.run(
        [
            "wt.exe", "-w", "new", "new-tab", "--title", title, "--suppressApplicationTitle",
            "powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
        ],
        check=True, timeout=15,
    )
    pid = wait_file(ready, 12)
    if not pid or not pid.isdecimal():
        raise RuntimeError("The disposable PowerShell session did not initialize")
    task = (
        f'仅在已打开的独立 Terminal 测试窗口“{title}”执行命令 Write-Output \'{marker}\' 一次。'
        "查看终端实际输出并告诉我结果，保留窗口以便核对。"
    )
    try:
        result = launch(task)
    finally:
        cleanup_error = close_terminal(title, pid)
    lines = transcript.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    actual = {"output_count": sum(line.strip() == marker for line in lines)}
    return {
        **result, "task": task, "actual": actual, "expected_answer": marker,
        "effect_ok": actual["output_count"] == 1, "cleanup_error": cleanup_error,
    }


def browser(nonce: str, directory: Path, launch) -> dict:
    marker = f"CUA_{nonce}_browser"
    draft = f"{marker}\n中文草稿🙂"
    with browser_fixture(nonce, directory) as (title, state):
        task = (
            f'只读取和操作浏览器中的本地测试窗口“{title}”，跳过其他窗口和标签页。'
            '把 Draft editor 中已有内容替换成以下两行，'
            f'保持 Bystander 不变，点击 Apply draft 一次，核对页面显示的结果并报告。\n{draft}'
        )
        result = launch(task)
        actual = state()
    return {
        **result, "task": task, "actual": actual, "expected_answer": marker,
        "effect_ok": (
            actual.get("text") == actual.get("saved") == draft and actual.get("other") == "untouched"
            and actual.get("applied") == 1
        ),
    }


def calculator(nonce: str, directory: Path, launch) -> dict:
    a, b = calculator_operands(nonce)
    expected = str(a * b)
    task = (
        f"只在刚打开的 Windows 计算器中完成 {a} × {b}，按等号并读回计算器显示的结果。"
        "把结果告诉我，保留计算器窗口和结果。"
    )
    with calculator_fixture(directory) as (oracle, pid):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if calculator_display(oracle, pid).get("result"):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("Calculator launched but its display was not observable")
        result = launch(task)
        actual = calculator_display(oracle, pid)
    displayed = "".join(char for char in actual.get("result", "") if char.isdecimal())
    return {
        **result, "task": task, "actual": actual, "expected_answer": expected, "effect_ok": displayed == expected,
    }


def visual(nonce: str, directory: Path, launch) -> dict:
    title = f"CUA Visual Choice {nonce}"
    expected = "VIS-" + uuid.uuid4().hex[:6].upper()
    task = (
        f'仅操作测试窗口“{title}”。看图定位绿色方块并点击一次，保持橙色方块不变。'
        "随后查看窗口显示的结果代码，并把代码告诉我。"
    )
    state = directory / "state.json"
    with fixture("windows_agent_visual_fixture.cs", "CuaVisualChoice", state, nonce, expected):
        with Computer() as setup:
            setup.app("CuaVisualChoice").read(view="tree", allow_foreground=True)
        result = launch(task)
        actual = read_state(state)
    return {
        **result, "task": task, "actual": actual, "expected_answer": expected,
        "effect_ok": actual.get("target_clicks") == 1 and actual.get("bystander_clicks") == 0,
    }


def reports_expected_answer(case: str, report: dict) -> bool:
    answer = str(report.get("answer"))
    expected = report["expected_answer"]
    if case != "calculator":
        return expected in answer
    # Match a complete result, including ordinary thousands grouping.
    formatted = f"{int(expected):,}"
    numbers = re.findall(r"\d+(?:[,，]\d+)*", answer)
    return any(number.replace("，", ",") in (expected, formatted) for number in numbers)


def attempt(case: str, output: Path, config: Path | None, timeout: int) -> dict:
    nonce = uuid.uuid4().hex[:10]
    log = output / f"{case}-{nonce}.log"
    report = {
        "case": CASES[case], "name": case, "attempt": nonce, "cli_log": str(log),
        "provenance": startup_provenance(native_driver()["command"]),
    }
    run = {"edit": edit, "terminal": terminal, "browser": browser, "calculator": calculator,
           "modal": modal, "visual": visual}[case]
    with tempfile.TemporaryDirectory(prefix="cua-agent-") as folder:
        try:
            report.update(run(nonce, Path(folder), lambda task: invoke(task, log, config, timeout)))
        except (OSError, RuntimeError, ValueError, ComputerUseError, subprocess.SubprocessError) as error:
            report["setup_or_capture_error"] = f"{type(error).__name__}: {error}"
    report["automated_pass"] = (
        bool(report.get("effect_ok")) and report.get("exit") == 0 and bool(report.get("desktop_calls"))
        and reports_expected_answer(case, report) and not report.get("cleanup_error")
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=CASES, required=True)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout < 1:
        parser.error("repeat and timeout must be positive")
    settings = load(args.config)
    if settings.get("mcp"):
        parser.error("acceptance requires the CLI's local stdio MCP host")
    initial = manifest()
    initial["agent"] = {
        "model": settings["model"],
        "provider": settings.get("provider", "OpenAI"),
        "allow_global_input": settings.get("allow_global_input", True),
        "max_iterations": 16,
        "model_timeout": settings.get("model_timeout", 600),
        "task_timeout": args.timeout,
        "request_config": {
            key: settings.get("model_request_config", {}).get(key)
            for key in ("temperature", "top_p", "seed", "max_tokens")
        },
    }
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    passed = 0
    total = len(args.case) * args.repeat
    with (output / "attempts.jsonl").open("x", encoding="utf-8") as evidence:
        (output / "manifest.json").write_text(json.dumps(initial, indent=2) + "\n", encoding="utf-8")
        for case in args.case:
            for _ in range(args.repeat):
                result = attempt(case, output, args.config, args.timeout)
                evidence.write(json.dumps(result, ensure_ascii=False) + "\n")
                evidence.flush()
                passed += result["automated_pass"]
                sys.stdout.write(f"{result['case']} {result['attempt']} {result['automated_pass']}\n")
                sys.stdout.flush()
    final = manifest()
    unchanged = all(final[key] == initial[key] for key in ("head", "sources", "native_sha256"))
    summary = {"stable_source_and_native": unchanged, "automated_passes": passed, "attempted": total}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(summary) + "\n")
    raise SystemExit(0 if unchanged and passed == total else 1)


if __name__ == "__main__":
    main()
