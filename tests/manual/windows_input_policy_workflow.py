# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise installed permissions, enumeration and opt-out on an owned, image-free Windows fixture."""

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from textwrap import indent

import computer_use
from artifact_json import captured_interface_metadata
from computer_use import Computer, ComputerUseError
from computer_use.native.connection import Connection
from computer_use.paths import native_driver
from windows_continuous_workflow import fixture_foreground
from windows_paste_budget_workflow import clipboard_sequence
from windows_paste_workflow import wait_state

from computer_use_agent.config import AgentConfig
from computer_use_agent.connection import McpConnection

FIXTURE = Path(__file__).with_name("windows_input_policy_fixture.cs")
DENIED = ("permission_required", "none")
QUIET = {"text": "unchanged", "bystander": "untouched", "key_messages": 0, "paste_messages": 0, "text_changes": 0}
CAPTURE_VETO = """\
from computer_use.native.connection import Connection
_policy_request = Connection.request
_policy_capture_attempts = []

async def _policy_no_capture(self, method, params, options=None):
    if method == "capture":
        _policy_capture_attempts.append(method)
        raise AssertionError("Image-free policy probe vetoed a native capture request")
    return await _policy_request(self, method, params, options)

Connection.request = _policy_no_capture
"""
CAPTURE_STATUS = 'print("CUA_POLICY_CAPTURES", len(_policy_capture_attempts))'


def record(output: str, label: str):
    prefix = f"CUA_POLICY_{label} "
    lines = [line[len(prefix) :] for line in output.splitlines() if line.startswith(prefix)]
    assert len(lines) == 1, f"Expected one {label} record, got {len(lines)}"
    return json.loads(lines[0])


def permissions() -> dict:
    command = [
        sys.executable,
        "-c",
        "from computer_use_native_tools.native import permissions_cli; raise SystemExit(permissions_cli())",
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        encoding="utf-8",
        timeout=15,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=True,
    )
    return json.loads(result.stdout)


def assert_quiet(state: dict, *, closed: bool = False) -> None:
    assert all(state.get(key) == value for key, value in QUIET.items()), state
    assert state["closed"] is closed and state["close_count"] == int(closed), state
    if not closed:
        assert state["focused"], state


def require_no_capture(report: dict, count: int) -> None:
    report["capture_attempts"] = count
    assert count == 0, f"Image-free policy probe vetoed {count} native capture request(s)"


async def exercise(call, name: str, title: str, pid: int, path: Path, report: dict) -> None:
    startup = await call(
        "import json\nfrom computer_use import ComputerUseError\n"
        + inspect.getsource(captured_interface_metadata)
        + f"choices = [item for item in computer.list_apps() if item.display_name.casefold() == {name.casefold()!r}]\n"
        'print("CUA_POLICY_APPS", json.dumps([[item.id, item.display_name, item.is_running] for item in choices]))\n'
        "assert len(choices) == 1 and choices[0].is_running\n"
        "app = computer.app(choices[0].id)\n"
        'observed = app.read(view="tree", full=True)\n'
        "assert observed.screenshot is None\n"
        'assert "Policy target" in observed.text and "Policy bystander" in observed.text\n'
        'rows = captured_interface_metadata(observed)["windows"]\n'
        'print("CUA_POLICY_WINDOWS", json.dumps([[row["ref"], row["title"]] for row in rows]))'
    )
    choices = report["apps"] = record(startup, "APPS")
    assert len(choices) == 1 and choices[0][1].casefold() == name.casefold() and choices[0][2] is True, choices
    windows = report["windows"] = record(startup, "WINDOWS")
    assert len(windows) == 1 and windows[0][1] == title, windows
    ref = int(windows[0][0])
    hwnd = fixture_foreground(None, pid, title)
    report["foreground_hwnd"] = hwnd

    for action in ('press_key("Left")', 'type_text("policy typing probe")', 'paste("policy paste probe")'):
        fixture_foreground(hwnd, pid, title)
        assert_quiet(wait_state(path, lambda value: "sample" in value, 1))
        before = clipboard_sequence()
        reply = await call(
            f"try:\n    computer.{action}\n"
            "except ComputerUseError as error:\n"
            '    print("CUA_POLICY_DENIAL", json.dumps([error.code, error.effect]))\n'
            "else:\n    raise AssertionError('Raw input did not refuse explicit opt-out')"
        )
        denial = record(reply, "DENIAL")
        sample = wait_state(path, lambda value: "sample" in value, 1)["sample"]
        actual = wait_state(path, lambda value, minimum=sample + 2: value.get("sample", 0) >= minimum, 2)
        after = clipboard_sequence()
        report["denials"].append(
            {
                "action": action,
                "denial": denial,
                "clipboard_before": before,
                "clipboard_after": after,
                "actual": actual,
            }
        )
        assert tuple(denial) == DENIED and before == after, report["denials"][-1]
        assert actual.get("sample", 0) >= sample + 2, actual
        assert_quiet(actual)
        fixture_foreground(hwnd, pid, title)

    reply = await call(
        f"closed = app.close_window({ref})\n"
        "assert closed.observation is None or closed.observation.screenshot is None\n"
        'print("CUA_POLICY_CLOSE", json.dumps([closed.intent, closed.status, closed.window_lifecycle]))'
    )
    report["close"] = record(reply, "CLOSE")
    assert report["close"] == ["closeWindow", "dispatched", "closed"], report["close"]
    report["actual"] = wait_state(path, lambda value: value.get("closed"), 5)
    assert_quiet(report["actual"], closed=True)


async def verify(name: str, title: str, pid: int, path: Path, report: dict, *, mcp: bool) -> None:
    if not mcp:
        namespace = {}
        original = Connection.request
        try:
            exec(CAPTURE_VETO, namespace)  # noqa: S102
            with Computer(allow_global_input=False) as computer:
                namespace["computer"] = computer

                async def sdk_call(code: str) -> str:
                    stream = StringIO()
                    try:
                        with redirect_stdout(stream):
                            exec(code, namespace)  # noqa: S102
                    finally:
                        require_no_capture(report, len(namespace["_policy_capture_attempts"]))
                    return stream.getvalue()

                await exercise(sdk_call, name, title, pid, path, report)
        finally:
            Connection.request = original
            require_no_capture(report, len(namespace.get("_policy_capture_attempts", ())))
        return

    connection = McpConnection(AgentConfig(allow_global_input=False).mcp_config())
    try:

        async def host_call(code: str) -> tuple[str, bool]:
            async with asyncio.timeout(45):
                result = await connection.call("computer_use", {"title": "Test input policy", "code": code})
            assert all(item.type == "text" for item in result.content), "Unexpected non-text MCP delivery"
            return "\n".join(item.text for item in result.content), bool(result.isError)

        async def mcp_call(code: str) -> str:
            wrapped = "try:\n" + indent(code, "    ") + "\nfinally:\n    " + CAPTURE_STATUS
            output, is_error = await host_call(wrapped)
            require_no_capture(report, record(output, "CAPTURES"))
            assert not is_error, output
            return output

        output, is_error = await host_call(CAPTURE_VETO)
        assert not is_error, output
        try:
            await exercise(mcp_call, name, title, pid, path, report)
        finally:
            output, is_error = await host_call(CAPTURE_STATUS)
            require_no_capture(report, record(output, "CAPTURES"))
            assert not is_error, output
    finally:
        await connection.close()


def run(mcp: bool) -> dict:
    driver = Path(native_driver()["command"])
    report = {
        "pass": False,
        "module": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "transport": "mcp" if mcp else "sdk",
        "allow_global_input": False,
        "denials": [],
    }
    try:
        state = report["permissions"] = permissions()
        assert state["platform"] == "windows" and state["ready"] is True and state["sessionId"] > 0, state
        assert 8192 <= (state.get("integrityRid") or 0) < 12288 and state.get("uiAccess") is False, state
        nonce = uuid.uuid4().hex[:10]
        name, title = "CuaInputPolicy" + nonce, "CUA Input Policy " + nonce
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        with tempfile.TemporaryDirectory(prefix="cua-input-policy-") as tmp:
            executable, path = Path(tmp) / (name + ".exe"), Path(tmp) / "state.json"
            subprocess.run(
                [
                    str(compiler),
                    "/nologo",
                    "/target:winexe",
                    "/platform:x64",
                    "/reference:System.Windows.Forms.dll",
                    "/reference:System.Drawing.dll",
                    "/reference:System.Web.Extensions.dll",
                    f"/out:{executable}",
                    str(FIXTURE),
                ],
                check=True,
                timeout=20,
            )
            report["fixture_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
            process = subprocess.Popen([str(executable), str(path), title])
            try:
                report["fixture_pid"] = process.pid
                report["initial"] = wait_state(path, lambda value: value.get("focused"), 6)
                assert_quiet(report["initial"])
                asyncio.run(verify(name, title, process.pid, path, report, mcp=mcp))
                report["fixture_exit"] = process.wait(timeout=5)
                assert report["fixture_exit"] == 0, report
                report["pass"] = True
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=5)
    except (
        AssertionError,
        ComputerUseError,
        OSError,
        RuntimeError,
        TimeoutError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp", action="store_true", help="Use a separate stdio MCP host")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("the fixture requires an unlocked Windows desktop")
    report = run(args.mcp)
    serialized = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
