# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify normal Windows close against two disposable windows and a native modal.

Run on an unlocked Windows test desktop with the current driver built.
Fixture callbacks score actual results. The SDK selects targets from its own
observation and sends a separate explicit choice to the delivered dialog.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import computer_use
from artifact_json import captured_interface_metadata, encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.paths import native_driver
from windows_paste_workflow import read_state, wait_state

FIXTURE = Path(__file__).with_name("windows_close_fixture.cs")
CONFIRMATION = "CUA Close Confirmation"


def verify(state_path, report, *, last):
    with Computer() as computer:
        app = computer.app("CuaCloseFixture")

        def call(label, operation):
            started = time.monotonic()
            entry = {"call": label}
            report["calls"].append(entry)
            try:
                result = operation()
            except ComputerUseError as error:
                entry.update(code=error.code, effect=error.effect, detail=error.detail)
                if error.observation is not None:
                    entry["observation"] = error.observation.text
                raise
            finally:
                entry["seconds"] = round(time.monotonic() - started, 3)
            entry["result"] = result
            return result

        def effect(predicate):
            state = wait_state(state_path, predicate, 5)
            assert predicate(state), state
            return state

        def modal(result):
            assert result.status == "dispatched" and result.window_lifecycle == "observed", result
            assert result.observation is not None and CONFIRMATION in result.observation.text, result
            dialogs = [choice.id for choice in result.choices if choice.title == CONFIRMATION]
            assert len(dialogs) == 1, result.choices
            return dialogs[0]

        observed = call("discover", lambda: app.read(full=True))
        windows = captured_interface_metadata(observed)["windows"]
        if last:
            a = next(int(window["ref"]) for window in windows if window["title"] == "CUA Close Document A")
            assert len(windows) == 1, windows
            report["windows"] = {"a": a}
            closed = call("close final A", lambda: app.close_window(a))
            actual = effect(lambda state: state.get("closed_a"))
            assert actual["close_a"] == 1, actual
            assert closed.intent == "closeWindow" and closed.status == "dispatched", closed
            assert closed.window_lifecycle == "closed", closed
            return
        a, b = (
            next(int(window["ref"]) for window in windows if window["title"] == f"CUA Close Document {name}")
            for name in ("A", "B")
        )
        report["windows"] = {"a": a, "b": b}
        original = effect(lambda state: not state.get("foreground_a", True))
        report["foreground_before_close"] = {"a": original["foreground_a"], "b": original["foreground_b"]}
        closed = call("close inactive A", lambda: app.close_window(a))
        assert closed.intent == "closeWindow" and closed.status == "dispatched", closed
        assert closed.window_lifecycle == "closed", closed
        after_a = effect(lambda state: state.get("closed_a"))
        assert after_a["close_a"] == 1 and after_a["close_b"] == 0, after_a
        try:
            call("reject ended A", lambda: app.close_window(a))
        except ComputerUseError as error:
            assert error.code == "stale_target" and error.effect == "none", error
        else:
            raise AssertionError("The ended A reference remained actionable")

        first = call("request close B", lambda: app.close_window(b))
        dialog = modal(first)
        pending = effect(lambda state: state.get("modal_b"))
        assert pending["close_b"] == 1 and not pending["closed_b"], pending
        call("cancel close", lambda: app.press_key("Escape", window=dialog))
        cancelled = effect(lambda state: state.get("cancel_b") == 1 and not state.get("modal_b"))
        assert not cancelled["closed_b"] and cancelled["text_b"] == "Unsaved test document", cancelled
        assert cancelled["close_b"] == 1, cancelled

        second = call("request close B after cancellation", lambda: app.close_window(b))
        dialog = modal(second)
        pending = effect(lambda state: state.get("modal_b") and state.get("close_b") == 2)
        assert not pending["closed_b"] and pending["confirm_b"] == 0, pending
        call("confirm close", lambda: app.press_key("Return", window=dialog))
        final = effect(lambda state: state.get("closed_b"))
        assert final["close_a"] == 1 and final["close_b"] == 2, final
        assert final["cancel_b"] == 1 and final["confirm_b"] == 1, final


async def verify_mcp(state_path, report, *, last):
    from computer_use_agent.config import AgentConfig
    from computer_use_agent.connection import McpConnection

    connection = McpConnection(AgentConfig().mcp_config())

    async def call(label, code, *, expected_error=None):
        entry = {"call": label}
        report["calls"].append(entry)
        started = time.monotonic()
        try:
            result = await connection.call("computer_use", {"title": label, "code": code})
        finally:
            entry["seconds"] = round(time.monotonic() - started, 3)
        lines = "\n".join(item.text for item in result.content if item.type == "text").splitlines()
        entry.update(lines=lines, is_error=bool(result.isError))
        if expected_error is None:
            assert not result.isError, lines
        else:
            assert result.isError and any(expected_error in line for line in lines), lines
            assert any("Effect: none" in line for line in lines), lines
        return lines

    async def close(label, ref, lifecycle):
        lines = await call(
            label,
            f"action = app.close_window({ref})\n"
            'print("CUA_CLOSE", action.intent, action.status, action.window_lifecycle)\n'
            f'print("CUA_DIALOG", [choice.id for choice in action.choices if choice.title == {CONFIRMATION!r}])',
        )
        assert f"CUA_CLOSE closeWindow dispatched {lifecycle}" in lines, lines
        if lifecycle == "observed":
            assert any(CONFIRMATION in line for line in lines), lines
            dialog = next((re.fullmatch(r"CUA_DIALOG \[(\d+)]", line) for line in lines
                           if line.startswith("CUA_DIALOG ")), None)
            assert dialog is not None, lines
            return int(dialog[1])
        return None

    try:
        lines = await call(
            "discover", 'app = computer.app("CuaCloseFixture")\n'
            'observation = app.read(full=True)\nprint(observation.read_details(length=16384).text)',
        )
        prefix = "Captured interface metadata: "
        details = "\n".join(lines).partition(prefix)[2]
        assert details, lines
        metadata, _ = json.JSONDecoder().raw_decode(details)
        windows = {window["title"]: int(window["ref"]) for window in metadata["windows"]}
        a = windows["CUA Close Document A"]
        report["windows"] = {"a": a}
        if not last:
            b = windows["CUA Close Document B"]
            report["windows"]["b"] = b
            original = wait_state(state_path, lambda state: not state.get("foreground_a", True), 5)
            assert not original.get("foreground_a", True), original
            report["foreground_before_close"] = {"a": original["foreground_a"], "b": original["foreground_b"]}
        else:
            assert len(windows) == 1, windows

        await close("close final A" if last else "close inactive A", a, "closed")
        after_a = wait_state(state_path, lambda state: state.get("closed_a"), 5)
        assert after_a.get("closed_a") and after_a.get("close_a") == 1 and after_a.get("close_b") == 0, after_a
        if last:
            return
        await call("reject ended A", f"app.close_window({a})", expected_error="fixed Window identity no longer exists")

        dialog = await close("request close B", b, "observed")
        pending = wait_state(state_path, lambda state: state.get("modal_b"), 5)
        assert pending.get("modal_b") and pending.get("close_b") == 1 and not pending.get("closed_b"), pending
        await call("cancel close", f'app.press_key("Escape", window={dialog})')
        cancelled = wait_state(state_path, lambda state: state.get("cancel_b") == 1 and not state.get("modal_b"), 5)
        assert cancelled.get("cancel_b") == 1 and not cancelled.get("modal_b"), cancelled
        assert not cancelled.get("closed_b"), cancelled
        assert cancelled.get("close_b") == 1 and cancelled.get("text_b") == "Unsaved test document", cancelled

        dialog = await close("request close B after cancellation", b, "observed")
        pending = wait_state(state_path, lambda state: state.get("modal_b") and state.get("close_b") == 2, 5)
        assert pending.get("modal_b") and pending.get("close_b") == 2 and pending.get("confirm_b") == 0, pending
        await call("confirm close", f'app.press_key("Return", window={dialog})')
        final = wait_state(state_path, lambda state: state.get("closed_b"), 5)
        assert final.get("closed_b") and final.get("close_a") == 1 and final.get("close_b") == 2, final
        assert final.get("cancel_b") == final.get("confirm_b") == 1, final
    finally:
        await connection.close()


def run(*, last, driver_override=None, mcp=False):
    framework = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319"
    driver = driver_override.resolve() if driver_override is not None else Path(native_driver()["command"])
    report = {
        "passed": False,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=FIXTURE.parents[2], text=True).strip(),
        "calls": [],
        "last_window": last,
        "transport": "mcp" if mcp else "sdk",
    }
    with tempfile.TemporaryDirectory(prefix="cua-close-") as folder:
        exe, state_path = (Path(folder) / name for name in ("CuaCloseFixture.exe", "state.json"))
        subprocess.run(
            [
                str(framework / "csc.exe"),
                "/nologo",
                "/target:winexe",
                "/platform:x64",
                "/reference:System.Windows.Forms.dll",
                "/reference:System.Drawing.dll",
                "/reference:System.Web.Extensions.dll",
                f"/out:{exe}",
                str(FIXTURE),
            ],
            check=True,
        )
        fixture = subprocess.Popen([str(exe), str(state_path), *(["--last"] if last else [])])
        try:
            first = wait_state(state_path, bool, 8)
            assert first, "The fixture did not publish its initial state"
            selected = {"command": str(driver), "args": []}
            with (
                patch("computer_use.native.connection.native_driver", return_value=selected)
                if driver_override
                else nullcontext()
            ):
                if mcp:
                    asyncio.run(verify_mcp(state_path, report, last=last))
                else:
                    verify(state_path, report, last=last)
            assert fixture.wait(timeout=5) == 0
            report["passed"] = True
        except (AssertionError, ComputerUseError, KeyError, RuntimeError, StopIteration, ValueError, TimeoutError,
                subprocess.TimeoutExpired) as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
        finally:
            report["actual"] = read_state(state_path)
            if fixture.poll() is None:
                fixture.terminate()
            fixture.wait(timeout=5)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--driver", type=Path, help="Use an isolated comparison build")
    parser.add_argument("--last", action="store_true", help="Close the fixture's sole remaining window")
    parser.add_argument("--mcp", action="store_true", help="Exercise a separate stdio MCP host")
    args = parser.parse_args()
    if args.mcp and args.driver is not None:
        parser.error("--driver is only supported for the SDK comparison")
    report = run(last=args.last, driver_override=args.driver, mcp=args.mcp)
    serialized = json.dumps(report, ensure_ascii=True, indent=2, default=encode_artifact)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    sys.stdout.write(serialized + "\n")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
