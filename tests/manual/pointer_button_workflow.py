# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify real AppKit right/middle/double click callbacks through the public SDK.

Run after rebuilding the driver: uv run python tests/manual/pointer_button_workflow.py
No global input is authorized. Each case invokes click exactly once, records any
failure, and never retries. Only this independently built fixture is targeted.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import ClickOption, Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
CASES = {"left": ("left", 1), "right": ("right", 1), "middle": ("middle", 1), "double": ("left", 2)}


async def snapshot(ui, folder, name):
    observation = await asyncio.to_thread(ui.read, view="both")
    (folder / f"{name}.txt").write_text(observation.text)
    assert observation.screenshot, "Coordinate input requires delivered pixels"
    (folder / f"{name}.png").write_bytes(observation.screenshot)


def expected_callbacks(button, count):
    names = {
        "left": ("mouseDown", "mouseUp"),
        "right": ("rightMouseDown", "rightMouseUp"),
        "middle": ("otherMouseDown", "otherMouseUp"),
    }
    number = {"left": 0, "right": 1, "middle": 2}[button]
    return [(name, number, click) for click in range(1, count + 1) for name in names[button]]


async def verify(folder, selected, driver_option, *, weak_ax=False):
    bundle = "local.computer-use.pointer-buttons." + uuid.uuid4().hex
    driver = Path(driver_option or native_driver()["command"]).resolve()
    computer = Computer(allow_global_input=False)
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    events, initial = [], None
    report = {
        "passed": False,
        "allow_global_input": False,
        "weak_ax": weak_ax,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "head": (await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)).strip(),
        "cases": [],
    }
    sources = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory, suffix in (("computer_use_engine", ".py"), ("os_adapter/macos/Sources", ".swift"))
        for path in sorted((ROOT / directory).rglob("*" + suffix))
    }
    for path in (Path(__file__), ROOT / "tests/fixtures/PointerButtonProbe.swift"):
        sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "source-manifest.json").write_text(json.dumps(sources, indent=2) + "\n")

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(
            build_fixture, folder, bundle, "PointerButtonProbe", arguments=("--weak-ax",) if weak_ax else ()
        )
        initial = await state(folder)
        assert initial["weakAX"] == weak_ax
        report["initial"] = initial
        app = computer.app(bundle)
        for case in selected:
            await snapshot(app, folder, f"{case}-before")
            before = await state(folder)
            assert not any(before["heldButtons"].values()), before["heldButtons"]
            image = app._image
            root = computer._controller.state(app).selection.root
            pieces = [p for p in image.pieces if p.image.target.surface == root.surface]
            assert len(pieces) == 1, "Expected one original root screenshot piece"
            piece = pieces[0]
            x, y, width, height = piece.box
            point = (
                x + before["targetPoint"][0] * width / before["width"],
                y + before["targetPoint"][1] * height / before["height"],
            )
            assert image.hit(point)[0] is piece, "Target point must belong to the observed original root"
            button, count = CASES[case]
            row = {"case": case, "request": {"button": button, "count": count, "at": point}, "before": before}
            report["cases"].append(row)
            start, started = len(events), time.monotonic()
            receipt, caught = None, None
            try:
                receipt = await asyncio.to_thread(app.click, ClickOption(button=button, count=count), at=point)
                row["receipt"] = dict(receipt)
            except ComputerUseError as error:
                caught = error
                row["error"] = {
                    "code": error.code,
                    "effect": error.effect,
                    "detail": error.detail,
                    "impact": error.impact,
                }
            row["seconds"] = time.monotonic() - started
            # Waiting only observes the fixture; it never resends the action.
            for _ in range(60):
                after = await state(folder)
                if caught is not None or len(after["callbacks"]) >= len(before["callbacks"]) + 2 * count:
                    break
                await asyncio.sleep(0.05)
            # A short settled sample catches duplicate callbacks queued after return.
            await asyncio.sleep(0.15)
            after = await state(folder)
            row["after"] = after
            row["input_activity"] = [
                item["value"]
                for item in events[start:]
                if item.get("kind") == "backend_diagnostic"
                and isinstance(item.get("value"), dict)
                and item["value"].get("kind") == "input_activity"
            ]
            if weak_ax and case == "left":
                receiver_evidence = [
                    item["value"]
                    for item in events[start:]
                    if item.get("kind") == "backend_diagnostic"
                    and isinstance(item.get("value"), dict)
                    and item["value"].get("kind") == "pointer_receiver_evidence"
                ]
                row["receiver_evidence"] = receiver_evidence
                assert any(item.get("qualification") == "originalWindowPoint" for item in receiver_evidence), (
                    "The weak AX case must exercise the retained-window qualification, not a concrete child",
                    receiver_evidence,
                )
            if caught is not None:
                raise caught
            callbacks = after["callbacks"][len(before["callbacks"]) :]
            row["callback_delta"] = callbacks
            assert [(c["callback"], c["button"], c["clickCount"]) for c in callbacks] == expected_callbacks(
                button, count
            )
            assert all(c["window"] == before["window"] for c in callbacks), callbacks
            event_delta = after["inputEvents"][len(before["inputEvents"]) :]
            assert len(event_delta) == 2 * count and all(e["kind"] == "mouse" for e in event_delta), event_delta
            assert [(e["button"], e["clickCount"]) for e in event_delta] == [
                (number, click) for _, number, click in expected_callbacks(button, count)
            ], event_delta
            for name in (
                "guardCallbacks",
                "guardText",
                "cursor",
                "heldButtons",
                "clipboardDigest",
                "clipboardChangeCount",
            ):
                assert after[name] == before[name], (name, before[name], after[name])
            assert before["cursor"] is not None, "Missing system cursor evidence cannot prove preservation"
            impact = receipt.impact
            assert impact is not None and impact.global_input_allowed is False
            assert impact.attempts, impact
            actuals = [attempt.actual for attempt in impact.attempts]
            assert all(actual is not None and actual.coverage == "complete" for actual in actuals), impact
            effects = [effect for actual in actuals for effect in actual.effects]
            assert not any(e.transport == "globalHID" and e.state != "notInvoked" for e in effects), impact
            mouse = [e for e in effects if e.device == "mouse" and e.state != "notInvoked"]
            assert mouse and all(e.transport == "process" and e.state == "invoked" for e in mouse), impact
            assert any(e.stage == "input" for e in mouse), impact
            assert all(attempt.cleanup == "complete" for attempt in impact.attempts), impact
            row["foreground_changed"] = after["frontmostPID"] != before["frontmostPID"]
            await snapshot(app, folder, f"{case}-after")
            row["passed"] = True
        report["passed"] = True
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        if initial is not None:
            report["final"] = await state(folder)
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )
        await asyncio.to_thread(computer.close)
        set_diagnostic_handler(None)
        journal.close()
        if initial is not None:
            try:
                os.kill(initial["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    parser.add_argument("--driver", help="Rebuilt driver path; defaults to this checkout's driver")
    parser.add_argument("--weak-ax", action="store_true", help="Expose the target as its AX window only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="pointer-buttons-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, CASES if args.case == "all" else [args.case], args.driver, weak_ax=args.weak_ax))
    finally:
        LOG.info("Pointer button evidence: %s", folder)


if __name__ == "__main__":
    main()
