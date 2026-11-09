# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve a new Calculator from a public app or desktop catalogue and verify its arithmetic."""

import argparse
import asyncio
import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import redirect_stdout
from datetime import timedelta
from io import StringIO
from pathlib import Path

from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver
from computer_use.runtime_provenance import startup_provenance
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from windows_calculator_fixture import calculator_display, calculator_fixture, calculator_operands

ROOT = Path(__file__).resolve().parents[2]
DESKTOP_ROW = re.compile(
    r'(?mi)^([ \t]+"(?:[^"\\]|\\.)*" — "(?:Calculator|计算器)" [^;\n]+?'
    r' window=(\d+); application: computer\.app\((\d+)\))$'
)
ELEMENT = re.compile(r"(?mi)^[ \t]*(\d+)[ \t]+(按钮|button|文本|text)((?:[ \t]+\([^()\n]*\))*)[ \t]+(.+)$")
RECEIPT = re.compile(r"(?m)^CUA_CALC (\d+) (dispatched|verified)$")
APP_CHOICE = re.compile(r"(?m)^CUA_CALC_APP (\d+) (True|False) (.+)$")
CALCULATOR_AUMID = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
BUTTONS = {
    str(digit): {str(digit), zh, en}
    for digit, (zh, en) in enumerate(zip(
        "零一二三四五六七八九", ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"),
        strict=True,
    ))
}
BUTTONS.update({"C": {"clear", "清除", "全部清除", "c"}, "×": {"multiply by", "乘以", "×"}, "=": {"equals", "等于", "="}})


def rows(text: str):
    for ref, role, flags, name in ELEMENT.findall(text):
        name = name.split(", Visual label:", 1)[0].split(", Shortcut:", 1)[0]
        if name.startswith('"'):
            name, _ = json.JSONDecoder().raw_decode(name)
        yield int(ref), role.casefold(), flags, name.strip()


def display_value(label: str | None) -> str | None:
    match = re.fullmatch(r"(?:Display is|显示为)[ \t]*([\d,\u00a0\u202f ]+)", label or "", re.IGNORECASE)
    if match is None:
        return None
    value = re.sub(r"[,\u00a0\u202f ]", "", match[1])
    return str(int(value)) if value.isdecimal() else None


def expression_value(label: str | None) -> str | None:
    match = re.fullmatch(r"(?:Expression is|表达式为)[ \t]*(.*)", label or "", re.IGNORECASE)
    if match is None:
        return None
    return re.sub(r"\s", "", match[1]).replace("*", "×")


def result_rows(text: str) -> dict:
    names = {name for _, role, _, name in rows(text) if role in ("文本", "text")}
    result = {name for name in names if display_value(name) is not None}
    expression = {name for name in names if expression_value(name) is not None}
    if len(result) != 1 or len(expression) != 1:
        raise RuntimeError("The public Calculator read did not contain one result and one expression")
    return {"result": result.pop(), "expression": expression.pop()}


def calculator_keys(text: str, symbols: str) -> dict[str, int]:
    controls = [
        (ref, name.casefold())
        for ref, role, flags, name in rows(text)
        if role in ("按钮", "button") and "disabled" not in flags.casefold()
    ]
    refs = {}
    for symbol in dict.fromkeys(symbols):
        matches = {ref for ref, name in controls if name in BUTTONS[symbol]}
        if len(matches) != 1:
            raise RuntimeError(f"Could not resolve one displayed Calculator key for {symbol!r}")
        refs[symbol] = matches.pop()
    if len(set(refs.values())) != len(refs):
        raise RuntimeError("Several Calculator keys resolved to the same element")
    return refs


def calculator_shell_identity() -> dict:
    # Windows' Start catalogue is independent of the SDK window directory.
    command = (
        "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        f"$app = @(Get-StartApps | Where-Object AppID -CEQ '{CALCULATOR_AUMID}'); "
        "if ($app.Count -ne 1) { throw 'The fixture requires one registered Calculator' }; "
        "$app[0] | Select-Object Name,AppID | ConvertTo-Json -Compress"
    )
    item = json.loads(subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], encoding="utf-8", timeout=12,
    ))
    if item.get("AppID") != CALCULATOR_AUMID or not item.get("Name"):
        raise RuntimeError("The independent Start catalogue did not identify the Calculator fixture")
    return {"aumid": item["AppID"], "display_name": item["Name"]}


def calculator_scope(text: str, display_name: str) -> dict:
    name = re.escape(json.dumps(display_name, ensure_ascii=False))
    # A single expanded member may put the App and window in one heading.
    single = re.findall(rf"(?m)^(Window (\d+)(?:: {name})?, App: {name}(?: \(window scope\))?; [^\n]+)$", text)
    if re.search(rf"(?m)^App: {name}\.$", text):
        members = re.findall(rf"(?m)^(Window (\d+): {name}(?: \([^\n]+\))?; window=\2)$", text)
    else:
        members = []
    matched = {(heading, int(ref)) for heading, ref in single + members if "outside read scope" not in heading}
    if len(matched) != 1:
        raise RuntimeError("The selected public App did not deliver one exact Calculator window scope")
    heading, ref = matched.pop()
    return {"window": ref, "heading": heading}


async def calculator_application(call, symbols: str, identity: dict, report: dict) -> tuple[int, int, dict[str, int]]:
    deadline = time.monotonic() + 15
    while True:
        listed = await call(
            'for item in computer.list_apps():\n    print("CUA_CALC_APP", item.id, item.is_running, item.display_name)'
        )
        choices = [
            {"id": int(ref), "running": running == "True", "display_name": name}
            for ref, running, name in APP_CHOICE.findall(listed)
        ]
        report["advertised_apps"] = choices
        matches = {item["id"] for item in choices if item["running"] and item["display_name"] == identity["display_name"]}
        if len(matches) > 1:
            raise RuntimeError("More than one running App advertised the expected Calculator name")
        if matches:
            selector = matches.pop()
            try:
                tree = await call(f'print(computer.app({selector}).read(view="tree", full=True).text)')
                scope = calculator_scope(tree, identity["display_name"])
                keys = calculator_keys(tree, symbols)
                result_rows(tree)
            except (ComputerUseError, RuntimeError, ValueError) as error:
                report["app_read_error"] = str(error)
            else:
                report.pop("app_read_error", None)
                report["selected_app"] = {"id": selector, **identity}
                report["selected_scope"] = scope
                return selector, scope["window"], keys
        if time.monotonic() >= deadline:
            raise RuntimeError("No unique advertised Calculator App provided the expected window and keypad")
        await asyncio.sleep(0.2)


async def calculator_window(call, symbols: str, report: dict) -> tuple[int, int, dict[str, int]]:
    deadline = time.monotonic() + 15
    while True:
        desktop = await call('print(computer.read(view="image", full=True).text)')
        candidates = {(int(app), int(window)): row.strip() for row, window, app in DESKTOP_ROW.findall(desktop)}
        ready, rejected = [], []
        for selector, ref in candidates:
            try:
                tree = await call(f'print(computer.app({selector}).read(view="tree", window={ref}, full=True).text)')
                keys = calculator_keys(tree, symbols)
                result_rows(tree)
            except (ComputerUseError, RuntimeError, ValueError) as error:
                rejected.append({"app": selector, "window": ref, "detail": str(error)})
            else:
                ready.append((selector, ref, keys))
        report.update(desktop_rows=list(candidates.values()), rejected_windows=rejected)
        if len(ready) == 1:
            return ready[0]
        if len(ready) > 1 or time.monotonic() >= deadline:
            raise RuntimeError(f"Expected one Calculator window with the complete delivered keypad, found {len(ready)}")
        await asyncio.sleep(0.2)


async def workflow(call, left: int, right: int, report: dict) -> None:
    symbols = f"{left}×{right}="
    identity = report.get("shell_identity")
    if identity is None:
        selector, window, keys = await calculator_window(call, "C" + symbols, report)
    else:
        selector, window, keys = await calculator_application(call, "C" + symbols, identity, report)
    report.update(application=selector, window=window, keys=keys)
    report["receipts"] = []
    read = f'print(calc.read(view="tree", window={window}, full=True).text)'
    await call(f"calc = computer.app({selector})")

    async def click_sequence(sequence, start):
        program = "\n".join(
            f"r = calc.element({keys[symbol]}, window={window}).click()\nprint('CUA_CALC {index}', r.status)"
            for index, symbol in enumerate(sequence, start)
        )
        receipts = [
            {"index": int(index), "status": status} for index, status in RECEIPT.findall(await call(program))
        ]
        report["receipts"].extend(receipts)
        if [receipt["index"] for receipt in receipts] != list(range(start, start + len(sequence))):
            raise RuntimeError("Each requested Calculator click must have one delivered receipt")

    await click_sequence("C", 0)
    before = result_rows(await call(read))
    report["cleared"] = before
    if display_value(before["result"]) != "0":
        raise RuntimeError("The public read did not confirm a cleared Calculator")
    await click_sequence(symbols, 1)
    observed = await call(read)
    report["observed"] = result_rows(observed)
    if identity is not None:
        report["final_scope"] = calculator_scope(observed, identity["display_name"])
        if report["final_scope"]["window"] != window:
            raise RuntimeError("The final read changed the selected Calculator window")


async def mcp_workflow(left: int, right: int, report: dict) -> None:
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "computer_use_mcp.server"], cwd=str(ROOT), env={"PYTHONSAFEPATH": "1"},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=45)) as client,
    ):
        await client.initialize()

        async def call(code):
            result = await client.call_tool("computer_use", {"title": "Calculator arithmetic", "code": code})
            text = "\n".join(block.text for block in result.content if block.type == "text")
            if result.isError:
                raise RuntimeError("\n".join(text.splitlines()[-3:]))
            return text

        await workflow(call, left, right, report)


def run(mcp: bool, apps: bool = False) -> dict:
    left, right = calculator_operands(uuid.uuid4().hex)
    expected, expected_expression = str(left * right), f"{left}×{right}="
    report = {
        "pass": False, "transport": "mcp" if mcp else "sdk",
        "discovery": "list_apps" if apps else "desktop",
        "expected": expected, "expected_expression": expected_expression,
        "provenance": startup_provenance(native_driver()["command"]),
    }
    started = time.monotonic()
    try:
        if apps:
            report["shell_identity"] = calculator_shell_identity()
        with tempfile.TemporaryDirectory(prefix="cua-calculator-") as folder, calculator_fixture(
            Path(folder)
        ) as (oracle, pid):
            try:
                if mcp:
                    asyncio.run(mcp_workflow(left, right, report))
                else:
                    with Computer() as computer:
                        namespace = {"computer": computer}

                        async def call(code):
                            with redirect_stdout(StringIO()) as output:
                                exec(code, namespace)  # noqa: S102 - fixed commands and integer refs from public reads
                            return output.getvalue()

                        asyncio.run(workflow(call, left, right, report))
            finally:
                # The separate UIA oracle is used only after SDK/MCP activity.
                report["actual"] = calculator_display(oracle, pid)
    except (ComputerUseError, ExceptionGroup, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    report["seconds"] = round(time.monotonic() - started, 3)
    observed, actual = report.get("observed", {}), report.get("actual", {})
    report["pass"] = (
        "error" not in report
        and display_value(observed.get("result")) == display_value(actual.get("result")) == expected
        and expression_value(observed.get("expression")) == expression_value(actual.get("expression")) == expected_expression
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--apps", action="store_true", help="Bind the localized App choice advertised by list_apps()")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.mcp, args.apps)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    args.output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
