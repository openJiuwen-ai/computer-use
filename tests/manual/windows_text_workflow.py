# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify Windows edit, paste and cancellation against an independent WinForms oracle."""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use.client.execution import ExecutionCancellation, execution_scope
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from windows_paste_workflow import paste_fixture, read_state, wait_state

EDIT_CASES = ("caret", "selection", "replace", "readonly")
CASES = (
    *EDIT_CASES, "no-consumer", "external-overwrite", "cross-session",
    "cancel-before-paste", "cancel-after-text",
)


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def text_evidence(text: str) -> dict:
    return {
        "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "codepoints": len(text),
        "utf16_units": utf16_units(text),
    }


def receipt_data(receipt) -> dict:
    return {key: getattr(receipt, key) for key in ("status", "detail", "observation_status")}


def fixture_state(path: Path, condition, timeout: float = 3) -> dict:
    state = wait_state(path, lambda value: value.get("fixture_error") or condition(value), timeout)
    assert not state.get("fixture_error"), state
    assert state.get("bystander") == "untouched", state
    assert condition(state), state
    return state


def fixture_command(path: Path, command: str) -> dict:
    path.with_suffix(path.suffix + ".command").write_text(command, encoding="utf-8")
    return fixture_state(path, lambda state: state.get("last_command") == command)


async def mcp_edit(case: str, payload: str, expected: str, report: dict) -> None:
    from computer_use_agent.config import AgentConfig
    from computer_use_agent.connection import McpConnection

    connection = McpConnection(AgentConfig().mcp_config())
    report["calls"] = []

    async def call(code, *, rejected=False):
        result = await connection.call("computer_use", {"title": "Test document edit", "code": code})
        lines = "\n".join(item.text for item in result.content if item.type == "text").splitlines()
        report["calls"].append({"is_error": bool(result.isError), "lines": lines})
        assert bool(result.isError) == rejected, lines
        return lines

    try:
        # The stdio client must receive the lookup scope before selecting by name.
        lines = await call('app = computer.app("CuaPasteFixture")\napp.read(view="tree")')
        assert any("Paste target" in line for line in lines), lines
        element = 'app.element("Paste target")'
        if case == "readonly":
            for operation in (
                f"{element}.type_text({payload!r})", f"{element}.type_text({payload!r}, replace=True)",
                f"{element}.paste({payload!r})",
            ):
                lines = await call(operation, rejected=True)
                assert any("Effect: none" in line for line in lines), lines
        else:
            lines = await call(
                f"action = {element}.type_text({payload!r}, replace={case == 'replace'})\n"
                'print("CUA_EDIT", action.status)'
            )
            assert set(lines) & {"CUA_EDIT dispatched", "CUA_EDIT verified"}, lines
        lines = await call('print(app.read(view="tree", full=True).text)')
        assert any(expected in line for line in lines), lines
    finally:
        await connection.close()


def edit(case: str, path: Path, initial: dict, payload: str, report: dict, *, mcp: bool) -> None:
    original = "left[middle]right"
    assert initial["text"] == original and initial["selection_start"] == 5, initial
    length = 6 if case in ("selection", "replace") else 0
    assert initial["selection_length"] == length, initial
    expected = original
    if case != "readonly":
        expected = payload if case == "replace" else original[:5] + payload + original[5 + length :]
    if mcp:
        try:
            asyncio.run(mcp_edit(case, payload, expected, report))
        finally:
            report["actual"] = read_state(path)
    else:
        with Computer() as computer:
            app = computer.app("CuaPasteFixture")
            app.read(view="tree")
            element = app.element("Paste target")
            if case == "readonly":
                errors = []
                for action in (
                    lambda: element.type_text(payload),
                    lambda: element.type_text(payload, replace=True),
                    lambda: element.paste(payload),
                ):
                    try:
                        action()
                    except ComputerUseError as error:
                        errors.append({"code": error.code, "effect": error.effect})
                        assert error.effect == "none", error
                    else:
                        raise AssertionError("A known read-only edit admitted a write")
                report["errors"] = errors
            else:
                report["receipt"] = receipt_data(element.type_text(payload, replace=case == "replace"))
                assert report["receipt"]["status"] in ("dispatched", "verified"), report["receipt"]
    actual = fixture_state(path, lambda state: state.get("text") == expected)
    report.update(actual=actual, expected=text_evidence(expected), output=text_evidence(actual["text"]))
    assert actual["paste_count"] == actual["returns"] == 0, actual
    if case in ("caret", "selection"):
        assert actual["selection_start"] == 5 + utf16_units(payload) and actual["selection_length"] == 0, actual
    if case == "readonly":
        assert actual["text_changes"] == 0 and actual["selection_start"] == initial["selection_start"], actual
        assert fixture_command(path, "sample")["clipboard_sample"] == "prior-" + report["nonce"]


def delayed(case: str, path: Path, payload: str, report: dict) -> None:
    with Computer() as computer:
        report["first"] = receipt_data(computer.paste(payload))
        assert report["first"]["status"] == "dispatched", report["first"]
        report["before_consumption"] = fixture_state(
            path, lambda state: state.get("pending") == 1 and state.get("paste_count") == 1
        )
        assert report["before_consumption"]["text"] == "", report["before_consumption"]
    original_publication = fixture_command(path, "sample")["clipboard_sample"]
    assert original_publication == payload, original_publication
    report["published"] = text_evidence(original_publication)
    if case == "no-consumer":
        with Computer() as read_only:
            observation = read_only.app("CuaPasteFixture").read(view="tree", full=True)
            assert payload not in observation.text, observation.text
        after_sample = read_state(path)["sample"]
        report["actual"] = fixture_state(path, lambda state: state.get("sample", 0) >= after_sample + 4)
        assert report["actual"]["paste_count"] == report["actual"]["pending"] == 1, report["actual"]
        assert report["actual"]["consumed"] == 0 and report["actual"]["text"] == "", report["actual"]
        return
    if case == "external-overwrite":
        external = fixture_command(path, "overwrite")["clipboard_sample"]
        assert external == "external-" + report["nonce"], external
        expected, count = external, 1
    else:
        # A second session can observe and issue an independent key while A
        # remains pending. B is a deliberate new publication before A consumes.
        second = "second-" + report["nonce"]
        with Computer() as other:
            observation = other.app("CuaPasteFixture").read(view="tree", full=True)
            assert payload not in observation.text, observation.text
            report["independent_key"] = receipt_data(other.press_key("Left"))
            report["second"] = receipt_data(other.paste(second))
            assert report["independent_key"]["status"] == report["second"]["status"] == "dispatched"
        fixture_state(path, lambda state: state.get("pending") == 2)
        expected, count = second * 2, 2
    actual = fixture_command(path, "consume")
    report.update(actual=actual, expected=text_evidence(expected), output=text_evidence(actual["text"]))
    assert actual["text"] == expected and actual["paste_count"] == actual["consumed"] == count, actual
    assert actual["returns"] == actual["pending"] == 0, actual
    # Re-sample only after the deliberately delayed consumer has released the clipboard.
    final_clipboard = fixture_command(path, "sample")["clipboard_sample"]
    assert final_clipboard == (expected if count == 1 else second), final_clipboard
    report["final_clipboard"] = text_evidence(final_clipboard)


def cancel_input(case: str, path: Path, payload: str, report: dict) -> None:
    token = ExecutionCancellation()
    before = case == "cancel-before-paste"
    trace = []
    cancelled_at = None

    def cancel():
        nonlocal cancelled_at
        if cancelled_at is None:
            cancelled_at = time.monotonic()
        token.cancel()

    def diagnostic(event):
        if event.get("method") != "execute" or event["kind"] not in ("command_start", "command_end"):
            return
        trace.append({
            "kind": event["kind"],
            "dispatch": event.get("result", {}).get("dispatch"),
            "effect": event.get("failure", {}).get("effect"),
            "stage": event.get("stage"),
        })
        if before and event["kind"] == "command_start":
            # The native execute frame can leave the SDK here. The fixture
            # holds the real clipboard until cancellation/cleanup returns.
            cancel()

    def operation():
        with Computer() as computer:
            try:
                with execution_scope(computer._executor, token):
                    result = computer.paste(payload) if before else computer.type_text(payload)
                return receipt_data(result)
            except asyncio.CancelledError:
                return {"status": "cancelled"}
            except ComputerUseError as error:
                return {"status": "failed", "code": error.code, "effect": error.effect}

    if before:
        report["lock"] = fixture_command(path, "lock")
        assert report["lock"]["clipboard_locked"] is True
    set_diagnostic_handler(diagnostic)
    try:
        if before:
            report["outcome"] = operation()
        else:
            with ThreadPoolExecutor(max_workers=1) as executor:
                worker = executor.submit(operation)
                try:
                    report["at_cancel"] = fixture_state(path, lambda state: len(state.get("text", "")) >= 4)
                    assert len(report["at_cancel"]["text"]) < len(payload), report["at_cancel"]
                finally:
                    cancel()
                report["outcome"] = worker.result(timeout=8)
    finally:
        if cancelled_at is not None:
            report["cancel_to_outcome_ms"] = round((time.monotonic() - cancelled_at) * 1000, 3)
        set_diagnostic_handler(None)
        if before:
            report["release"] = fixture_command(path, "release")
    report["trace"] = trace
    assert report["outcome"]["status"] == "cancelled" and any(e["kind"] == "command_start" for e in trace), report
    sampled = read_state(path)["sample"]
    actual = fixture_state(path, lambda state: state.get("sample", 0) >= sampled + 4)
    report["actual"] = actual
    assert actual["paste_count"] == actual["returns"] == 0, actual
    if before:
        assert actual["text"] == "" and report["release"]["clipboard_sample"] == "prior-" + report["nonce"], actual
    else:
        prefix = actual["text"]
        assert 0 < len(prefix) < len(payload) and payload.startswith(prefix), actual
        report["prefix"] = text_evidence(prefix)
    # A different SDK session can enter and complete an independent operation.
    with Computer() as recovered:
        report["next_session"] = receipt_data(recovered.press_key("Left"))
    assert report["next_session"]["status"] == "dispatched", report["next_session"]
    after = fixture_state(path, lambda state: state.get("sample", 0) > actual["sample"])
    assert after["text"] == actual["text"] and after["paste_count"] == 0, after


def run_case(case: str, *, mcp: bool = False) -> dict:
    nonce = uuid.uuid4().hex[:10]
    profile = (
        "selection" if case == "replace" else case if case in ("caret", "selection", "readonly")
        else "hold" if case in ("no-consumer", "external-overwrite", "cross-session") else "default"
    )
    payload = f"{nonce}中e\u0301😀"
    if case == "cancel-after-text":
        payload = (nonce + "-") * 64
    report = {"case": case, "nonce": nonce, "input": text_evidence(payload), "pass": False}
    started = time.monotonic()
    try:
        with paste_fixture(0, nonce, True, profile) as (path, initial):
            report["initial"] = initial
            if case in EDIT_CASES:
                edit(case, path, initial, payload, report, mcp=mcp)
            elif case in ("no-consumer", "external-overwrite", "cross-session"):
                delayed(case, path, payload, report)
            else:
                cancel_input(case, path, payload, report)
            report["pass"] = True
    except (AssertionError, ComputerUseError, RuntimeError, TimeoutError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", choices=CASES, help="Run selected cases (default: all)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mcp", action="store_true", help="Exercise editing through a separate stdio MCP host")
    args = parser.parse_args()
    cases = args.case or (EDIT_CASES if args.mcp else CASES)
    if args.mcp and any(case not in EDIT_CASES for case in cases):
        parser.error("--mcp supports caret, selection, replace and readonly")
    driver = Path(native_driver()["command"])
    root = Path(__file__).resolve().parents[2]
    report = {
        "module": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "transport": "mcp" if args.mcp else "sdk",
        "cases": [run_case(case, mcp=args.mcp) for case in cases],
    }
    result = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result, encoding="utf-8")
    sys.stdout.write(result)
    raise SystemExit(0 if all(case["pass"] for case in report["cases"]) else 1)


if __name__ == "__main__":
    main()
