# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Replay the prepared Tencent meeting draft through date and popup selections.

Never opens the home booking button, submits a meeting, or closes the user's
draft. SDK global input is disabled. --external-click authorizes only physical
clicks on this workflow's independently verified AppKit fixture. Partial runs
retain that fixture so terminating the test does not itself change foreground;
its exact PID and bundle are recorded for the operator's later cleanup.
"""

import argparse
import asyncio
import json
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture
from qt_cocoa_activation_workflow import ROOT, save_observation, sha, state
from vscode_quickopen_continuity_workflow import observed_value, owned_window_ref

LOG = logging.getLogger(__name__)


def hour_menu_visible(text):
    return len(set(re.findall(r"(?<!\d)(?:[01]\d|2[0-3]):(?:00|15|30|45)(?!\d)", text))) >= 8


def duration_choice_ref(text, label):
    matches = []
    for line in text.splitlines():
        for part in line.split(" | "):
            match = re.match(r"\s*(\d+)\s+Index:\s*\d+,\s*(.*?)\s*$", part)
            if match and match[2] == label:
                matches.append(int(match[1]))
    assert len(matches) == 1, ("Expected one actually delivered duration option", label, matches)
    return matches[0]


def duration_value_ref(text):
    refs = re.findall(r"(?m)^\s*\d+\s+时长:\s*(\d+)\s+(?:文本栏|text field)\s+\(disabled\)\s+Value:", text)
    assert len(refs) == 1, ("Expected one disabled duration value field in the actual form", refs)
    return int(refs[0])


async def verify(folder, until, duration_label):
    if sys.platform != "darwin":
        raise RuntimeError("macOS acceptance only")
    external_folder = folder / "external"
    external_folder.mkdir()
    driver = Path(native_driver()["command"])
    head = await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
    report = {
        "head": head.strip(),
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "sdk_allow_global_input": False,
        "until": until,
        "actions": [],
        "completed_stage": None,
        "passed": False,
        "meeting_submitted": False,
        "user_draft_closed": False,
    }
    sources = [
        path
        for directory, pattern in (("computer_use_engine", "*.py"), ("os_adapter/macos/Sources", "*.swift"))
        for path in sorted((ROOT / directory).rglob(pattern))
    ]
    sources += [Path(__file__), ROOT / "tests/fixtures/ForegroundContinuityProbe.swift"]
    (folder / "source-manifest.json").write_text(
        json.dumps({str(path.relative_to(ROOT)): sha(path) for path in sources})
    )
    computer = Computer(allow_global_input=False)
    events, external_pid = [], None
    bundle = "local.computer-use.continuity." + uuid4().hex
    with (folder / "diagnostics.jsonl").open("w") as journal:

        def record(event):
            events.append(event)
            journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        set_diagnostic_handler(record)
        try:
            app = computer.app("com.tencent.meeting")
            initial = await asyncio.to_thread(app.read, view="both", full=True)
            save_observation(folder, "initial", initial)
            ref = owned_window_ref(initial.text, "请输入会议主题")
            assert ref is not None, "The prepared booking form was not uniquely observed; no input sent"
            draft = app.window(ref)
            topic = await asyncio.to_thread(draft.element, "请输入会议主题")
            topic_before = await asyncio.to_thread(topic.read)
            save_observation(folder, "topic-before", topic_before)
            assert "CUA 菜单测试" in topic_before.text, "The prepared draft title changed; no input sent"
            date = await asyncio.to_thread(draft.element, "开始日期")
            hour = await asyncio.to_thread(draft.element, "时间编辑框")
            duration = await asyncio.to_thread(draft.element, "选择会议时长")
            build_fixture(external_folder, bundle, "ForegroundContinuityProbe")
            external_pid = (await state(external_folder))["pid"]
            report["external_fixture"] = {"pid": external_pid, "bundle": bundle, "folder": str(external_folder)}
            report["foreground_seed"] = {"before": await state(external_folder)}
            started = time.monotonic()
            await asyncio.to_thread(
                subprocess.run, ["open", "-a", str(external_folder / "ForegroundContinuityProbe.app")], check=True
            )
            report["foreground_seed"]["after"] = await state(
                external_folder, lambda value: value["uptime"] >= started and value["foregroundPID"] == external_pid
            )

            async def external_state():
                started = time.monotonic()
                current = await state(external_folder, lambda value: value["uptime"] >= started)
                assert current["foregroundPID"] == external_pid, current
                return current

            async def invoke(name, operation):
                started, first = time.monotonic(), len(events)
                action = {"name": name, "before_external": await external_state()}
                report["actions"].append(action)
                try:
                    result = await asyncio.to_thread(operation)
                    action["result"] = result
                    save_observation(folder, name, result.observation)
                    action["after_external"] = await external_state()
                    assert result.status in ("dispatched", "verified"), result
                    return result
                finally:
                    action["elapsed_seconds"] = time.monotonic() - started
                    action["native_effects"] = [
                        event["value"]
                        for event in events[first:]
                        if event.get("kind") == "backend_diagnostic"
                        and isinstance(event.get("value"), dict)
                        and event["value"].get("kind") == "input_activity"
                        and event["value"].get("phase") == "willCall"
                    ]

            async def read(name):
                observed = await asyncio.to_thread(app.read, view="both", full=True)
                save_observation(folder, name, observed)
                return observed

            async def external_click(name, visible):
                before = await state(external_folder)
                binary = external_folder / "ForegroundContinuityProbe.app/Contents/MacOS/ForegroundContinuityProbe"
                output = await asyncio.to_thread(
                    subprocess.check_output, [str(binary), "--click", str(external_pid), bundle], text=True
                )
                report[name] = {
                    "helper": output,
                    "external": await state(external_folder, lambda value: value["clicks"] == before["clicks"] + 1),
                }
                await asyncio.sleep(0.3)
                observed = await read(name + "-read")
                assert visible(observed.text), "The menu disappeared after the owned external physical click"
                await external_state()
                return observed

            await invoke("title", lambda: topic.type_text("CUA 菜单测试", replace=True))
            await invoke("date-click", date.click)
            await invoke("date-replace", lambda: date.type_text("2026/11/9", replace=True))
            date_after = await asyncio.to_thread(date.read)
            save_observation(folder, "date-value", date_after)
            assert "2026/11/9" in date_after.text or "2026-11-09" in date_after.text, date_after.text
            await invoke("hour-open", hour.click)
            opened = await read("hour-opened")
            assert hour_menu_visible(opened.text), "Hour menu was not observed after one click"
            await external_click("hour-external-click", hour_menu_visible)
            report["completed_stage"] = "hour-menu"
            if until == "hour-menu":
                report["passed"] = True
                return
            choice = await asyncio.to_thread(app.element, "10:00")
            await invoke("hour-select", choice.click)
            selected = await asyncio.to_thread(hour.read)
            save_observation(folder, "hour-value", selected)
            assert "10:00" in selected.text, selected.text
            report["completed_stage"] = "hour-selected"
            if until == "hour-selected":
                report["passed"] = True
                return
            await invoke("duration-open", duration.click)
            opened = await read("duration-opened")
            assert duration_label in opened.text, "Requested duration is absent; inspect saved tree before choosing"
            menu = await external_click(
                "duration-external-click", lambda text: duration_choice_ref(text, duration_label) is not None
            )
            report["completed_stage"] = "duration-menu"
            if until == "duration-menu":
                report["passed"] = True
                return
            option_ref = duration_choice_ref(menu.text, duration_label)
            report["duration_option"] = {"reference": option_ref, "label": duration_label}
            choice = await asyncio.to_thread(app.element, option_ref)
            await invoke("duration-select", choice.click)
            selected = await asyncio.to_thread(draft.read, view="both", full=True)
            save_observation(folder, "duration-value-form", selected)
            value_ref = duration_value_ref(selected.text)
            value = observed_value(selected.text, value_ref)
            report["duration_actual_value"] = {"reference": value_ref, "actual": value, "expected": duration_label}
            assert value == duration_label, report["duration_actual_value"]
            report["completed_stage"] = "complete"
            report["passed"] = True
        except Exception as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
            raise
        finally:
            try:
                await asyncio.to_thread(computer.close)
            finally:
                set_diagnostic_handler(None)
                global_effects = [
                    effect
                    for action in report["actions"]
                    for effect in action["native_effects"]
                    if effect.get("transport") == "globalHID"
                ]
                report["sdk_global_effects"] = global_effects
                if global_effects:
                    report["passed"] = False
                if external_pid is not None:
                    if until == "complete" and report["passed"]:
                        os.kill(external_pid, signal.SIGTERM)
                        report["external_fixture"]["retained"] = False
                    else:
                        report["external_fixture"]["retained"] = True
                (folder / "result.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--until", choices=["hour-menu", "hour-selected", "duration-menu", "complete"], default="hour-menu"
    )
    parser.add_argument(
        "--duration-label", default="1小时", help="Must be present verbatim in the newly observed duration menu"
    )
    parser.add_argument("--external-click", action="store_true")
    args = parser.parse_args()
    if not args.external_click:
        parser.error("Explicit --external-click is required for physical clicks on the owned fixture")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = args.output or Path(tempfile.mkdtemp(prefix="tencent-continuity-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(verify(folder.resolve(), args.until, args.duration_label))
    finally:
        LOG.info("Tencent continuity evidence (draft retained): %s", folder)


if __name__ == "__main__":
    main()
