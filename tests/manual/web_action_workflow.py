# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare automatic click and explicit AXPress on real WebKit objects.

Default: one operation with the existing production policy; record activation,
pointer changes, callback state and native diagnostics without asserting that
background execution succeeds. No files are selected and no form is submitted.
--build-only creates the fixture bundle without opening it. --launch-only keeps
the fixture available for an independently controlled UI comparison.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import plistlib
import re
import signal
import subprocess
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
LABELS = {
    "button": "Web ordinary button",
    "checkbox": "Web checkbox",
    "select": "Web selection",
    "file": "Web file upload",
}


def timestamp():
    return datetime.now(UTC).isoformat()


def file_reference(text):
    matches = re.findall(r"(?m)^\s*(\d+) (file upload button[^\n]*\bDescription: Web file upload(?:,.*)?$)", text)
    if len(matches) != 1:
        raise AssertionError(f"Expected one observed file-upload button with its fixture description: {matches}")
    reference, line = matches[0]
    return int(reference), line


def effect_confirmed(control, before, current):
    old, page = before["page"], current["page"]
    if control == "button":
        return page.get("buttonActions", 0) == old.get("buttonActions", 0) + 1
    if control == "checkbox":
        return page.get("checked") != old.get("checked")
    if control == "file":
        return (
            current["filePanelRequests"] == before["filePanelRequests"] + 1
            and current["filePanelVisible"]
            and any(window["sheet"] and window["title"] == "Web fixture file picker" for window in current["windows"])
            and page.get("files") == 0
        )
    # A click callback alone does not prove that a select's native popup opened.
    return None


async def settle_effect(directory, control, before):
    started = timestamp()
    for attempt in range(40):
        current = await state(directory)
        confirmed = effect_confirmed(control, before, current)
        if confirmed is not False:
            break
        await asyncio.sleep(0.05)
    return {
        "started_at": started,
        "sampled_at": timestamp(),
        "polls": attempt + 1,
        "effect_confirmed": confirmed,
        "state": current,
    }


def build(directory, bundle, name="WebActionProbe"):
    app = directory / (name + ".app")
    binary = app / ("Contents/MacOS/" + name)
    binary.parent.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": bundle,
                "CFBundleExecutable": name,
                "CFBundleName": name,
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
                "NSHighResolutionCapable": True,
            }
        )
    )
    subprocess.run(
        [
            "swiftc",
            "-swift-version",
            "6",
            "-parse-as-library",
            str(ROOT / "tests/fixtures/WebActionProbe.swift"),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    return app


async def wait_for(directory, predicate):
    for _ in range(100):
        current = await state(directory)
        if predicate(current):
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(f"Fixture state did not settle: {current}")


async def command(directory, kind, **parameters):
    identity = uuid.uuid4().hex
    (directory / "command.json").write_text(json.dumps({"id": identity, "kind": kind, **parameters}))
    return await wait_for(directory, lambda current: current["lastCommand"] == identity)


async def verify(directory, bundle, options, sentinel_app):
    initial = None
    sentinel_initial = None
    computer = None
    events = []
    journal = (directory / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    driver = Path(options.driver or native_driver()["command"]).resolve()
    sources = [
        ROOT / "os_adapter/macos/Sources/Control/NativeActionDocumentPolicy.swift",
        ROOT / "os_adapter/macos/Sources/Control/NativeActionUnit.swift",
        ROOT / "tests/fixtures/WebActionProbe.swift",
        Path(__file__).resolve(),
    ]
    result = {
        "control": options.control,
        "operation": options.operation,
        "preparation": options.preparation,
        "requested_state": options.state,
        "stage": "setup",
        "setup": [],
        "setup_ready": False,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_sha256": {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources},
    }
    try:
        initial = await state(directory)
        ready = await wait_for(directory, lambda current: current["page"].get("ready"))
        result["setup"].append({"stage": "target_ready", "target": ready})
        if sentinel_app is not None:
            launch = await asyncio.create_subprocess_exec("open", "-g", str(sentinel_app), "--args", "--sentinel")
            if await launch.wait() != 0:
                raise RuntimeError("The foreground sentinel could not be launched")
            sentinel_initial = await state(sentinel_app.parent)
            result["setup"].append({"stage": "sentinel_started", "sentinel": sentinel_initial})
            yielded = await command(directory, "yield", sentinelPID=sentinel_initial["pid"])
            result["setup"].append({"stage": "target_yielded", "target": yielded})
            assert yielded["yieldedPID"] == sentinel_initial["pid"] and not yielded["setupError"], yielded
            activated = await command(sentinel_app.parent, "activate")
            result["setup"].append({"stage": "sentinel_requested_activation", "sentinel": activated})
            sentinel_initial = await wait_for(
                sentinel_app.parent, lambda current: current["active"] and current["frontmostPID"] == current["pid"]
            )
            await wait_for(directory, lambda current: not current["active"])
            result["sentinel_before"] = sentinel_initial
        result["setup_ready"] = True
        if options.launch_only:
            return
        result["stage"] = "observation"
        computer = Computer()
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        app = computer.app(bundle)
        observation = await app.read(view="tree")
        (directory / "before-tree.txt").write_text(observation.text)
        if options.state == "foreground":
            await command(directory, "foreground")
            await wait_for(directory, lambda current: current["active"])
        before = await state(directory)
        assert before["visible"] and not before["miniaturized"], before
        if options.state == "background" and before["active"]:
            raise AssertionError("Background comparison requires another application to remain in front")
        if sentinel_initial is not None:
            assert before["frontmostPID"] == sentinel_initial["pid"], before
            assert sentinel_initial["keys"] == 0 and sentinel_initial["keyUps"] == 0, sentinel_initial
            assert sentinel_initial["other"] == "unchanged", sentinel_initial
        result["before"] = before
        result["before_timestamp"] = timestamp()
        identifier = options.element if options.element is not None else LABELS[options.control]
        if options.element is None and options.control == "file":
            identifier, matched_line = file_reference(observation.text)
            result["target_lookup"] = {"source": "before-tree.txt", "published_ref": identifier, "line": matched_line}
        identifier = int(identifier) if str(identifier).isdecimal() else identifier
        target = await app.element(identifier)
        start = len(events)
        if options.preparation == "element-focus":
            result["stage"] = "element_focus_preparation"
            result["focus_receipt"] = dict(await target.focus())
            result["after_focus"] = await state(directory)
            result["after_focus_timestamp"] = timestamp()
            if options.state == "background":
                assert not result["after_focus"]["active"], result["after_focus"]
                assert result["after_focus"]["frontmostPID"] == sentinel_initial["pid"], result["after_focus"]
        result["stage"] = "dispatch"
        result["dispatch_started_at"] = timestamp()
        try:
            receipt = await target.click() if options.operation == "click" else await target.perform_action("AXPress")
            result["receipt"] = dict(receipt)
        except ComputerUseError as error:
            result["error"] = {"type": type(error).__name__, "message": str(error)}
        result["dispatch_returned_at"] = timestamp()
        await asyncio.sleep(0.35)
        result["after"] = await state(directory)
        result["after_timestamp"] = timestamp()
        if sentinel_initial is not None:
            result["sentinel_after_early"] = await state(sentinel_app.parent)
        result["native_input"] = [
            item["value"]
            for item in events[start:]
            if item.get("kind") == "backend_diagnostic"
            and isinstance(item.get("value"), dict)
            and item["value"].get("kind") == "input_activity"
            and item["value"].get("phase") == "willCall"
        ]
        observation = await app.read(view="tree")
        (directory / "after-tree.txt").write_text(observation.text)
        result["after_observation"] = await state(directory)
        result["after_observation_timestamp"] = timestamp()
        result["settle"] = await settle_effect(directory, options.control, before)
        result["application_effect"] = "confirmed" if result["settle"]["effect_confirmed"] is True else "unconfirmed"
        if sentinel_initial is not None:
            result["sentinel_after"] = await state(sentinel_app.parent)
            result["sentinel_unchanged"] = all(
                result["sentinel_after"][key] == sentinel_initial[key]
                for key in ["other", "keys", "keyUps", "mouseEvents"]
            )
        result["foreground_changed"] = result["before"]["frontmostPID"] != result["after"]["frontmostPID"]
        result["foreground_changed_after_observation"] = (
            result["before"]["frontmostPID"] != result["after_observation"]["frontmostPID"]
        )
        result["foreground_changed_settled"] = (
            result["before"]["frontmostPID"] != result["settle"]["state"]["frontmostPID"]
        )
        result["cursor_changed"] = any(result["before"][key] != result["after"][key] for key in ["mouseX", "mouseY"])
        if sentinel_initial is not None:
            assert result["sentinel_unchanged"], result["sentinel_after"]
        result["stage"] = "complete"
    except (AssertionError, RuntimeError, TimeoutError, ComputerUseError, OSError) as error:
        result["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        cleanup = []
        try:
            if computer is not None:
                computer._controller.transport.close()
                await computer._controller.transport.wait_closed()
        except (RuntimeError, OSError) as error:
            cleanup.append({"component": "transport", "error": str(error)})
        set_diagnostic_handler(None)
        journal.close()
        keep_launched = options.launch_only and result["setup_ready"] and "failure" not in result
        for label, folder, owned, keep in [
            ("sentinel", sentinel_app.parent if sentinel_app else None, sentinel_initial, keep_launched),
            ("target", directory, initial, keep_launched or options.retain and "failure" not in result),
        ]:
            if folder is not None and (folder / "state.json").exists():
                result[label + "_final"] = json.loads((folder / "state.json").read_text())
            if owned is None or keep:
                continue
            try:
                os.kill(owned["pid"], signal.SIGTERM)
                cleanup.append({"component": label, "pid": owned["pid"], "terminated": True})
            except OSError as error:
                cleanup.append({"component": label, "pid": owned["pid"], "error": str(error)})
        result["cleanup"] = cleanup
        (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", choices=LABELS, default="button")
    parser.add_argument("--operation", choices=["click", "action"], default="click")
    parser.add_argument("--preparation", choices=["none", "element-focus"], default="none")
    parser.add_argument("--state", choices=["background", "foreground"], default="background")
    parser.add_argument("--element", help="Observed SDK reference or exact label override")
    parser.add_argument("--driver", type=Path, help="Native driver for an isolated before/after comparison")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--launch-only", action="store_true")
    parser.add_argument("--retain", action="store_true")
    options = parser.parse_args()
    if options.preparation != "none" and options.operation != "action":
        parser.error("element-focus isolates preparation before explicit AXPress; choose --operation action")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="web-action-", dir=output))
    bundle = "local.computer-use.web-action." + uuid.uuid4().hex
    app = build(directory, bundle)
    sentinel_app = None
    if options.state == "background":
        sentinel_directory = directory / "sentinel"
        sentinel_directory.mkdir()
        sentinel_app = build(sentinel_directory, bundle + ".sentinel", "WebActionSentinel")
    (directory / "fixture.json").write_text(
        json.dumps({"bundle": bundle, "app": str(app), "sentinel": str(sentinel_app) if sentinel_app else None})
    )
    try:
        if not options.build_only:
            subprocess.run(["open", "-g", str(app)], check=True)
            asyncio.run(verify(directory, bundle, options, sentinel_app))
    finally:
        LOG.info("Web action evidence: %s", directory)


if __name__ == "__main__":
    main()
