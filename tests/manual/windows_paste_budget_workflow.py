# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify one real Windows paste whose consumer runs after the SDK deadline."""

import argparse
import asyncio
import ctypes
import json
import sys
import time
import uuid
from pathlib import Path

from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from computer_use.runtime_provenance import startup_provenance
from computer_use.session.operations import operation_budget
from windows_paste_workflow import paste_fixture, wait_state


def clipboard_sequence() -> int:
    sequence = ctypes.windll.user32.GetClipboardSequenceNumber
    sequence.argtypes, sequence.restype = (), ctypes.c_uint32
    value = sequence()
    if value == 0:
        raise RuntimeError("The test desktop did not expose a clipboard sequence number")
    return value


async def paste_with_budget(computer: Computer, payload: str, budget_ms: int, report: dict):
    def record(event):
        if event["kind"] == "command_start" and event.get("method") in ("begin", "execute"):
            report["native_requests"].append({key: event[key] for key in ("method", "traceId", "timeoutMs")})

    # The host supplies the enclosing original deadline. The public paste
    # follows the normal SDK route and passes the budget across its executor.
    with operation_budget(budget_ms) as budget:
        set_diagnostic_handler(record)
        try:
            receipt = await asyncio.to_thread(computer.paste, payload)
            report["returned_ms"] = round(budget.elapsed_ms, 3)
        finally:
            set_diagnostic_handler(None)
        return receipt, budget


def sample_state(path: Path, condition, timeout: float) -> dict:
    state = wait_state(path, lambda row: row.get("fixture_error") or condition(row), timeout)
    assert not state.get("fixture_error") and condition(state), state
    assert state.get("bystander") == "untouched" and state.get("returns") == 0, state
    return state


def require_pending(state: dict) -> None:
    # This is the real timer mode. The fixture's `pending` counter belongs
    # to the explicitly held mode, so use WM_PASTE versus actual consumption.
    assert state.get("paste_count") == 1 and state.get("consumed") == 0 and state.get("text") == "", state


def run(delay_ms: int, budget_ms: int, anonymous: bool) -> dict:
    nonce = uuid.uuid4().hex[:10]
    expected = f"CUA-BUDGET {nonce} 中文 😀\r\nsecond\tline"
    report = {
        "pass": False, "delay_ms": delay_ms, "original_budget_ms": budget_ms,
        "anonymous_consumer": anonymous, "expected": expected, "native_requests": [],
        "provenance": startup_provenance(native_driver()["command"]),
    }
    try:
        with paste_fixture(delay_ms, nonce, anonymous) as (path, initial):
            report["initial"] = initial
            assert initial["focused"] and initial["paste_count"] == initial["consumed"] == 0, initial
            before = clipboard_sequence()
            sequence = report["clipboard_sequence"] = {"before": before}
            with Computer() as computer:
                receipt, budget = asyncio.run(paste_with_budget(computer, expected, budget_ms, report))
                report["receipt"] = {"status": receipt.status, "detail": receipt.detail,
                                     "observation_status": receipt.observation_status}
                assert receipt.status == "dispatched" and receipt.observation_status == "not_requested", report
                assert report["returned_ms"] < budget_ms, report["returned_ms"]
                trace = report["native_requests"]
                assert [row["method"] for row in trace] == ["begin", "execute"], trace
                assert all(0 < row["timeoutMs"] <= budget_ms for row in trace), trace
                published = clipboard_sequence()
                sequence["published"] = published
                assert published != before, (before, published)
                report["at_return"] = sample_state(path, lambda row: row.get("paste_count", 0) >= 1, 0.75)
                report["at_return_ms"] = round(budget.elapsed_ms, 3)
                require_pending(report["at_return"])
            report["sdk_closed_ms"] = round(budget.elapsed_ms, 3)

            # Take a new fixture timer sample after the original deadline.
            time.sleep(max(0, budget.deadline - time.monotonic()))
            marker = sample_state(path, lambda row: row.get("sample") is not None, 0.75)["sample"]
            report["after_deadline"] = sample_state(path, lambda row: row.get("sample", 0) > marker, 0.75)
            report["after_deadline_ms"] = round(budget.elapsed_ms, 3)
            assert report["after_deadline_ms"] > budget_ms, report["after_deadline_ms"]
            require_pending(report["after_deadline"])
            pending_sequence = clipboard_sequence()
            sequence["pending_after_deadline"] = pending_sequence

            report["actual"] = sample_state(path, lambda row: row.get("consumed", 0) >= 1, delay_ms / 1000 + 2)
            report["observed_consumption_ms"] = round(budget.elapsed_ms, 3)
            actual = report["actual"]
            assert actual.get("text") == expected and actual.get("paste_count") == actual.get("consumed") == 1, actual
            final = clipboard_sequence()
            sequence["after_consumption"] = final
            assert published == pending_sequence == final, sequence
        report["pass"] = True
    except (AssertionError, ComputerUseError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=int, default=5000)
    parser.add_argument("--budget-ms", type=int, default=2000)
    parser.add_argument("--window-consumer", action="store_true", help="Use the textbox's native WM_PASTE handling")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.budget_ms < 1 or args.delay_ms < args.budget_ms + 500:
        parser.error("the delay must exceed the positive budget by at least 500 ms for independent sampling")
    report = run(args.delay_ms, args.budget_ms, not args.window_consumer)
    serialized = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    sys.stdout.write(serialized)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
