# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Reproduce the Terminal input chain in a new, disposable PowerShell tab."""

import argparse
import asyncio
import base64
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import computer_use
from artifact_json import captured_interface_metadata, encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver

from computer_use_agent.config import AgentConfig
from computer_use_agent.connection import McpConnection


def quote_powershell(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def wait_file(path: Path, timeout: float) -> str | None:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if path.is_file():
            content = path.read_text(encoding="utf-8-sig").strip()
            if content:
                return content
        time.sleep(0.05)
    return None


def terminal_receiver(observed: str, title: str) -> tuple[int, str]:
    prefix = r"(?m)^[ \t]*(\d+)[ \t]+[^\n]+\(focusable, reports focus\) "
    matches = set(re.findall(prefix + re.escape(title) + r", Value: (.+)$", observed))
    assert len(matches) == 1, "No unambiguous focused Terminal element was delivered"
    ref, value = matches.pop()
    return int(ref), json.JSONDecoder().raw_decode(value)[0]


def pending_command(observed: str, title: str, command: str) -> bool:
    _, value = terminal_receiver(observed, title)
    lines = value.rstrip().splitlines()
    return bool(lines and lines[-1].endswith("> " + command))


def printed_marker(observed: str, title: str, marker: str) -> bool:
    _, value = terminal_receiver(observed, title)
    return [line.strip() for line in value.splitlines()].count(marker) == 1


async def mcp_terminal(title: str, command: str, marker: str, result_path: Path, route: str, report: dict) -> None:
    connection = McpConnection(AgentConfig().mcp_config())

    async def call(code: str) -> str:
        async with asyncio.timeout(45):
            response = await connection.call("computer_use", {"title": "Verify Terminal input", "code": code})
        output = "\n".join(item.text for item in response.content if item.type == "text")
        report["calls"].append({"error": bool(response.isError), "text": output})
        assert not response.isError, output
        return output

    try:
        catalog = await call('app = computer.app("WindowsTerminal")\nprint(app.read(view="tree", full=True).text)')
        refs = set(re.findall(r'(?m)^Window (\d+): "' + re.escape(title) + r'"', catalog))
        assert len(refs) == 1, "No unambiguous Terminal window was delivered"
        ref = int(refs.pop())
        report["window"] = ref
        read = f'print(app.read(view="tree", window={ref}, full=True).text)'
        try:
            report["initial"] = await call(read)
            receiver, _ = terminal_receiver(report["initial"], title)
            operations = {
                "insert": f"app.type_text({command!r}, window={ref})",
                "paste": f"app.paste({command!r}, window={ref})",
                "element-insert": f"app.element({receiver}, window={ref}).type_text({command!r})",
                "raw-insert": f"computer.type_text({command!r})",
            }
            output = await call(f'r = {operations[route]}\nprint("CUA_TERMINAL_INSERT", r.status)')
            assert {"CUA_TERMINAL_INSERT dispatched", "CUA_TERMINAL_INSERT verified"} & set(output.splitlines()), output
            for _ in range(3):
                report["before_submit"] = await call(read)
                if pending_command(report["before_submit"], title, command):
                    break
            else:
                raise AssertionError("Terminal did not display the exact pending command")
            assert not result_path.exists(), "Terminal executed the command before the explicit Return"
            submit = 'computer.press_key("Return")' if route == "raw-insert" else f'app.press_key("Return", window={ref})'
            output = await call(f'r = {submit}\nprint("CUA_TERMINAL_RETURN", r.status)')
            assert {"CUA_TERMINAL_RETURN dispatched", "CUA_TERMINAL_RETURN verified"} & set(output.splitlines()), output
            report["actual"] = wait_file(result_path, 5)
            assert report["actual"] == marker, report["actual"]
            for _ in range(3):
                report["final"] = await call(read)
                if printed_marker(report["final"], title, marker):
                    break
            else:
                raise AssertionError("Terminal did not display the exact command output once")
        finally:
            try:
                closed = await call(f'r = app.close_window({ref})\nprint("CUA_TERMINAL_CLOSE", r.window_lifecycle)')
                report["closed"] = "CUA_TERMINAL_CLOSE closed" in closed.splitlines()
            except (AssertionError, RuntimeError, TimeoutError) as error:
                report["close_error"] = str(error)
    finally:
        await connection.close()


def sdk_terminal(title: str, command: str, marker: str, result_path: Path, route: str, report: dict) -> None:
    with Computer() as computer:
        app = computer.app("WindowsTerminal")
        observed = app.read(view="tree", full=True)
        windows = captured_interface_metadata(observed)["windows"]
        matching = [int(window["ref"]) for window in windows if title == window["title"]]
        assert len(matching) == 1, observed.text
        ref = matching[0]
        report["window"] = ref
        try:
            report["initial"] = app.read(view="tree", window=ref, full=True).text
            if route == "paste":
                receipt = app.paste(command, window=ref)
            elif route == "raw-insert":
                terminal_receiver(report["initial"], title)
                with Computer() as raw:
                    receipt = raw.type_text(command)
            elif route == "element-insert":
                receiver, _ = terminal_receiver(report["initial"], title)
                receipt = app.element(receiver, window=ref).type_text(command)
            else:
                receipt = app.type_text(command, window=ref)
            report["calls"].append(receipt)
            for _ in range(3):
                report["before_submit"] = app.read(view="tree", window=ref, full=True).text
                if pending_command(report["before_submit"], title, command):
                    break
            else:
                raise AssertionError("Terminal did not display the exact pending command")
            assert not result_path.exists(), "Terminal executed the command before the explicit Return"
            if route == "raw-insert":
                with Computer() as raw:
                    report["calls"].append(raw.press_key("Return"))
            else:
                report["calls"].append(app.press_key("Return", window=ref))
            report["actual"] = wait_file(result_path, 5)
            assert report["actual"] == marker, report["actual"]
            for _ in range(3):
                report["final"] = app.read(view="tree", window=ref, full=True).text
                if printed_marker(report["final"], title, marker):
                    break
            else:
                raise AssertionError("Terminal did not display the exact command output once")
        finally:
            try:
                closed = app.close_window(ref)
                report["calls"].append(closed)
                report["closed"] = closed.window_lifecycle == "closed"
            except ComputerUseError as error:
                report["close_error"] = str(error)


def run(route: str, mcp: bool = False) -> dict:
    nonce = uuid.uuid4().hex[:12]
    title = f"CUA Terminal {nonce}"
    result_marker = f"CUA_RESULT_{nonce}"
    driver = Path(native_driver()["command"])
    report = {
        "passed": False,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "route": route,
        "transport": "mcp" if mcp else "sdk",
        "calls": [],
    }
    with tempfile.TemporaryDirectory(prefix="cua-terminal-") as folder:
        ready, result_path = (Path(folder) / name for name in ("ready.txt", "result.txt"))
        # A short interactive command avoids terminal line wrapping. Appending
        # independently records duplicate executions of the same command.
        init = (
            f"$cuaResult = {quote_powershell(result_path)}; "
            "function cuaCheck($suffix) { $marker = 'CUA_RESULT_' + $suffix; "
            "[IO.File]::AppendAllText($cuaResult, $marker + [char]10); $marker }; "
            f"[IO.File]::WriteAllText({quote_powershell(ready)}, [string]$PID)"
        )
        command = f"cuaCheck '{nonce}'"
        encoded = base64.b64encode(init.encode("utf-16-le")).decode("ascii")
        subprocess.run(
            [
                "wt.exe", "-w", "new", "new-tab", "--title", title, "--suppressApplicationTitle",
                "powershell.exe", "-NoLogo", "-NoProfile", "-NoExit", "-EncodedCommand", encoded,
            ],
            check=True,
            timeout=15,
        )
        pid = wait_file(ready, 12)
        if not pid or not pid.isdecimal():
            raise RuntimeError("The disposable Terminal tab did not report its own shell PID")
        try:
            if mcp:
                asyncio.run(mcp_terminal(title, command, result_marker, result_path, route, report))
            else:
                sdk_terminal(title, command, result_marker, result_path, route, report)
            assert report.get("closed"), report.get("close_error", "Terminal closure was not observed")
            effects = result_path.read_text(encoding="utf-8-sig").splitlines()
            report["effect_count"] = len(effects)
            assert effects == [result_marker], effects
            report["passed"] = True
        except (AssertionError, ComputerUseError, OSError, RuntimeError, TimeoutError, ValueError) as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
        finally:
            # Only the dedicated child created above is eligible for cleanup.
            # The Windows Terminal host can be shared with other tabs.
            subprocess.run(
                ["taskkill.exe", "/PID", pid],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--route", choices=("insert", "paste", "element-insert", "raw-insert"), default="paste")
    parser.add_argument("--mcp", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run(args.route, args.mcp)
    serialized = json.dumps(report, ensure_ascii=True, indent=2, default=encode_artifact)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    sys.stdout.write(serialized + "\n")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
