# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Measure ready Windows desktop screenshot and raw short-input latency."""

import argparse
import ctypes
import json
import math
import statistics
import string
import sys
import time
import uuid
from pathlib import Path

from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from computer_use.runtime_provenance import startup_provenance
from windows_paste_workflow import paste_fixture, read_state, wait_state

PNG = b"\x89PNG\r\n\x1a\n"


def foreground_fixture(title: str):
    user = ctypes.windll.user32
    user.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user.FindWindowW.restype = ctypes.c_void_p
    user.GetForegroundWindow.restype = ctypes.c_void_p
    handle = user.FindWindowW(None, title)

    def require_foreground():
        if not handle or user.GetForegroundWindow() != handle or user.FindWindowW(None, title) != handle:
            raise RuntimeError("The independently identified test window does not own the desktop foreground")

    require_foreground()
    return require_foreground


def elapsed_ms(start: float, end: float) -> float:
    return round((end - start) * 1000, 3)


def screenshot(computer: Computer, sample: dict):
    start = time.perf_counter()
    image = computer.read(view="image").screenshot
    sample["screenshot_ms"] = elapsed_ms(start, time.perf_counter())
    if not image or not image.startswith(PNG):
        raise RuntimeError("The public SDK did not return a desktop PNG")
    sample["png_bytes"] = len(image)


def run(repeat: int) -> dict:
    nonce = uuid.uuid4().hex[:10]
    report = {"provenance": startup_provenance(native_driver()["command"]), "requested": repeat, "samples": []}
    dispatches = []

    def record(event):
        if event["kind"] == "command_end" and event["method"] == "execute":
            dispatches.append((time.perf_counter(), (event.get("result") or {}).get("dispatch")))

    try:
        with paste_fixture(0, nonce, True) as (state, initial), Computer() as computer:
            require_foreground = foreground_fixture("CUA Paste Fixture " + nonce)
            report["cold_screenshot"] = cold = {}
            screenshot(computer, cold)
            expected = initial["text"]
            set_diagnostic_handler(record)
            try:
                for index in range(repeat):
                    sample = {"index": index}
                    report["samples"].append(sample)
                    require_foreground()
                    screenshot(computer, sample)
                    require_foreground()
                    dispatches.clear()
                    text = string.ascii_letters[index % len(string.ascii_letters)]
                    start = time.perf_counter()
                    receipt = computer.type_text(text)
                    sample["sdk_return_ms"] = elapsed_ms(start, time.perf_counter())
                    sample["receipt"] = receipt.status
                    sample["native_dispatches"] = [outcome for _, outcome in dispatches]
                    if receipt.status != "dispatched" or sample["native_dispatches"] != ["dispatched"]:
                        raise RuntimeError("Expected one confirmed native dispatch for this short input")
                    sample["native_receipt_ms"] = elapsed_ms(start, dispatches[0][0])
                    expected += text
                    actual = wait_state(state, lambda value, expected=expected: value.get("text") == expected, 2)
                    sample["actual"] = actual.get("text")
                    if sample["actual"] != expected or actual.get("bystander") != initial["bystander"]:
                        raise RuntimeError("Independent fixture rejected the input effect or companion state")
                    sample["effect_verified"] = True
            finally:
                set_diagnostic_handler(None)
                report["final_oracle"] = read_state(state)
    except (ComputerUseError, OSError, RuntimeError, ValueError) as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}

    summary = {}
    for field in ("screenshot_ms", "native_receipt_ms", "sdk_return_ms"):
        values = sorted(sample[field] for sample in report["samples"] if field in sample)
        if values:
            summary[field] = {
                "n": len(values), "p50": statistics.median(values),
                "p95": values[math.ceil(.95 * len(values)) - 1], "max": values[-1],
            }
    report["summary"] = summary
    report["pass"] = (
        "error" not in report and len(report["samples"]) == repeat
        and all(sample.get("effect_verified") for sample in report["samples"])
        and all(summary[field]["p95"] <= 1500 and summary[field]["max"] <= 5000
                for field in ("screenshot_ms", "native_receipt_ms"))
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeat", type=int, default=50)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    report = run(args.repeat)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    sys.stdout.write(json.dumps({key: report[key] for key in ("requested", "summary", "pass")}) + "\n")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
