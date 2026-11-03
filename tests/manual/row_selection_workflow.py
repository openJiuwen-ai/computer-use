# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify an ordinary AppKit row selection and a real re-click through the SDK.

Run after the matching build: .venv/bin/python tests/manual/row_selection_workflow.py
The fixture keeps AppKit's accessibility roles, actions and selection semantics.
It adds identifiers, selection notifications and passive mouse-delivery recording.
No global input is authorized and no failed or uncertain click is replayed.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from document_action_workflow import cleanup_fixture
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def beta_row_reference(text):
    """Select the published row, not its cell or a view's unexported identifier."""
    lines = text.splitlines()
    table_pattern = re.compile(r"^(\s*)(\d+) table(?: \([^)]*\))? Review records(?:,.*)?$")
    tables = [(index, match) for index, line in enumerate(lines) if (match := table_pattern.fullmatch(line))]
    assert len(tables) == 1, ("Expected one observed Review records table", tables)
    start, table = tables[0]
    depth = len(table[1])
    branch = []
    for line in lines[start + 1:]:
        if line.strip() and len(line) - len(line.lstrip()) <= depth:
            break
        branch.append(line)
    headers = [line for line in branch if line.strip().startswith("Records: Rows [table row] | ")]
    assert len(headers) == 1, ("Expected the published table-row record header", branch)
    pattern = re.compile(
        r"^\s*(\d+)(?: \([^)]*\))* Index: 1 \| "
        r"\d+(?: \([^)]*\))* Row: 2 > \d+ text Beta record$"
    )
    rows = [(int(match[1]), line) for line in branch if (match := pattern.fullmatch(line))]
    assert len(rows) == 1, ("Expected one Beta record on the observed row with Index 1", branch)
    ref, line = rows[0]
    return ref, {"table_ref": int(table[2]), "header": headers[0], "row_ref": ref, "published_line": line}


async def wait_state(folder, predicate):
    for _ in range(100):
        value = await state(folder)
        if predicate(value):
            await asyncio.sleep(0.15)
            return await state(folder)
        await asyncio.sleep(0.05)
    raise AssertionError(f"The owned table did not reach the expected state: {value}")


def actual_effects(receipt):
    impact = receipt.impact
    assert impact is not None and impact.global_input_allowed is False
    assert impact.attempts and all(attempt.actual is not None for attempt in impact.attempts)
    assert all(attempt.cleanup == "complete" for attempt in impact.attempts), impact
    effects = [effect for attempt in impact.attempts for effect in attempt.actual.effects]
    assert not any(effect.transport == "globalHID" and effect.state != "notInvoked" for effect in effects), impact
    return effects


def row_click_delivery(before, after, window):
    """Require actual row down plus up consumption, including native tracking."""
    delta = after["mouseDowns"][len(before["mouseDowns"]):]
    assert delta == [{"button": 0, "clickCount": 1, "window": window, "row": 1, "selectedBefore": [1]}], delta
    inputs = after["inputs"][len(before["inputs"]):]
    expected = [
        {"kind": "mouse", "type": event, "window": window, "button": 0, "clickCount": 1}
        for event in (1, 2)
    ]
    tracking = after["mouseTracking"][len(before["mouseTracking"]):]
    dequeued = after["dequeuedInputs"][len(before["dequeuedInputs"]):]
    assert [item["phase"] for item in tracking] == ["entered", "returned"], tracking
    for key in ("eventNumber", "timestamp", "window", "button", "clickCount"):
        assert tracking[0][key] == tracking[1][key], (key, tracking)
    assert (tracking[0]["window"], tracking[0]["button"], tracking[0]["clickCount"]) == (window, 0, 1)
    if inputs == expected:
        evidence = "sendEvent"
    else:
        # sendEvent can miss a control's nested tracking up, but a native post
        # receipt or released device state cannot replace actual dequeue proof.
        assert inputs == expected[:1], inputs
        dequeued_input = [{key: item[key] for key in expected[0]} for item in dequeued]
        assert dequeued_input in (expected, expected[1:]), dequeued
        ups = [item for item in dequeued if item["type"] == 2]
        assert len(ups) == 1 and ups[0]["insideTableMouseDown"] is True, dequeued
        assert ups[0]["timestamp"] >= tracking[0]["timestamp"], (tracking, ups)
        assert ups[0]["eventNumber"] == tracking[0]["eventNumber"], (tracking, ups)
        evidence = "dequeued inside table mouseDown; super returned"
    return {
        "mouse_down_delta": delta, "input_delta": inputs, "mouse_tracking_delta": tracking,
        "dequeued_input_delta": dequeued, "application_up_evidence": evidence,
    }


async def invoke(row, folder, report, name):
    record = {"name": name, "before": await state(folder)}
    report["actions"].append(record)
    started = time.monotonic()
    try:
        result = await row.click()
        record["receipt"] = dict(result)
        return result, record
    except ComputerUseError as error:
        record["error"] = {
            "code": error.code, "effect": error.effect, "detail": error.detail,
            "impact": error.impact, "observation": error.observation,
            "observation_status": error.observation_status,
        }
        raise
    finally:
        record["seconds"] = time.monotonic() - started
        record["returned_state"] = await state(folder)


async def verify(folder, selected_driver):
    bundle = "local.computer-use.row-selection." + uuid.uuid4().hex
    driver = Path(selected_driver or native_driver()["command"]).resolve()
    sources = [
        Path(__file__).resolve(), ROOT / "tests/fixtures/RowSelectionProbe.swift",
        ROOT / "os_adapter/macos/Sources/Control/NativeRowSelection.swift",
        ROOT / "os_adapter/macos/Sources/Control/InputContext/Passes/SampleNativeClickOffers.swift",
        ROOT / "computer_use_engine/interaction/write/routes/selection/candidates.py",
    ]
    head = await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
    report = {
        "passed": False, "allow_global_input": False, "sdk": computer_use.__file__,
        "head": head.strip(),
        "driver": str(driver), "driver_sha256": digest(driver),
        "source_sha256": {str(path): digest(path) for path in sources}, "actions": [],
        "provider": "Ordinary view-based NSTableView; no AX role/action/selection override.",
    }
    computer, initial = None, None
    try:
        await asyncio.to_thread(build_fixture, folder, bundle, "RowSelectionProbe")
        initial = await state(folder)
        initial = await wait_state(folder, lambda value: all(row["objectID"] is not None for row in value["rows"]))
        report["initial"] = initial
        assert initial["selected"] == [0] and initial["guard"] == "Unrelated draft"
        computer = Computer(allow_global_input=False)
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        app = computer.app(bundle)
        observed = await app.read(view="both", full=True)
        (folder / "before.txt").write_text(observed.text)
        if observed.screenshot:
            (folder / "before.png").write_bytes(observed.screenshot)
        # AppKit's virtual AXRows do not have to export NSTableRowView's custom
        # identifier. Use the actual row reference delivered with Beta's cell.
        row_ref, report["row_selection_source"] = beta_row_reference(observed.text)
        row = await app.element(row_ref)
        row_observation = await row.read()
        (folder / "row-before.txt").write_text(row_observation.text)
        assert row_observation.text.startswith(f"Element {row_ref}:\n"), row_observation.text
        receipt, first = await invoke(row, folder, report, "select previously unselected Beta row")
        selected = await wait_state(folder, lambda value: value["selected"] == [1])
        first["after"] = selected
        assert selected["summary"] == "Selected: Beta record"
        assert selected["mouseDowns"] == first["before"]["mouseDowns"]
        assert selected["inputs"] == first["before"]["inputs"]
        assert selected["selectionChanges"] and selected["selectionChanges"][-1] == [1]
        effects = actual_effects(receipt)
        assert not any(effect.device in ("mouse", "keyboard") and effect.state != "notInvoked" for effect in effects)
        dispatched = [attempt for attempt in receipt.impact.attempts if attempt.dispatch == "dispatched"]
        report["native_selection_exercised"] = (
            len(dispatched) == 1 and dispatched[0].route == "macos.axSelection.replaceRows.v1"
        )
        assert report["native_selection_exercised"], (
            "This run did not exercise native_selection; retain evidence rather than claim its acceptance", dispatched
        )

        # The same retained row is now selected. A second click must keep mouse
        # semantics; another SelectedRows setter cannot stand in for delivery.
        receipt, second = await invoke(row, folder, report, "re-click the already selected Beta row")
        clicked = await wait_state(folder, lambda value: (
            len(value["mouseDowns"]) > len(selected["mouseDowns"])
            and len(value["mouseTracking"]) >= len(selected["mouseTracking"]) + 2
        ))
        second["after"] = clicked
        second.update(row_click_delivery(selected, clicked, initial["window"]))
        assert clicked["selected"] == [1] and clicked["summary"] == "Selected: Beta record"
        effects = actual_effects(receipt)
        assert not any(attempt.route.startswith("macos.axSelection.") for attempt in receipt.impact.attempts), receipt.impact
        mouse = [effect for effect in effects if effect.device == "mouse" and effect.state != "notInvoked"]
        assert mouse and all(effect.transport == "process" and effect.state == "invoked" for effect in mouse), effects
        assert any(effect.stage == "input" for effect in mouse)
        for value in (selected, clicked):
            for key in ("pid", "window", "windowObjectID", "tableObjectID", "rows", "guard", "cursor"):
                assert value[key] == initial[key], (key, initial[key], value[key])
        assert initial["cursor"] is not None
        final_observation = await app.read(view="both", full=True)
        (folder / "after.txt").write_text(final_observation.text)
        if final_observation.screenshot:
            (folder / "after.png").write_bytes(final_observation.screenshot)
        report["final"] = clicked
        report["passed"] = True
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        if computer is not None:
            try:
                computer._controller.transport.close()
                await computer._controller.transport.wait_closed()
            except (OSError, RuntimeError) as error:
                report["transport_cleanup_error"] = str(error)
        report["cleanup"] = await cleanup_fixture(
            "owned-table", folder, "RowSelectionProbe", initial["pid"] if initial else None
        )
        report["source_drift"] = [str(path) for path in sources if digest(path) != report["source_sha256"][str(path)]]
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path)
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="row-selection-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, options.driver))
    finally:
        LOG.info("Public SDK row selection evidence: %s", folder)


if __name__ == "__main__":
    main()
