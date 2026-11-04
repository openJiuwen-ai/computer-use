# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify one background AppKit command on its original window or element.

Use --key delegate|document|select-all and --scope app|element. Each invocation
owns fresh applications and sends one press_key request. The default background
oracle requires actual effects, unchanged foreground/cursor and no sentinel
input. --expect observe records the outcome for an older driver comparison.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import plistlib
import subprocess
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from document_action_workflow import cleanup_fixture
from input_receiver_workflow import state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
SEED = "KEYBOARD-SEED"
SOURCES = (
    "os_adapter/macos/Sources/Control/NativeKeyboardUnit.swift",
    "os_adapter/macos/Sources/Control/Prepare/Focus/NativeFocusPipeline.swift",
    "os_adapter/macos/Sources/Control/NativeApplicationContextUnit.swift",
    "os_adapter/macos/Sources/Control/Focus/SyntheticFocusEvents.swift",
    "os_adapter/macos/Sources/Control/Focus/SyntheticFocusCoordinator.swift",
    "tests/fixtures/KeyboardWindowCommandProbe.swift",
    "tests/fixtures/KeyboardCommandSentinel.swift",
    "tests/fixtures/KeyboardWindowAXProbe.swift",
    "tests/manual/keyboard_window_command_workflow.py",
    "tests/manual/document_action_workflow.py",
    "tests/manual/input_receiver_workflow.py",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def build_owned(directory, bundle, name):
    app = directory / (name + ".app")
    binary = app / "Contents/MacOS" / name
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
            str(ROOT / "tests/fixtures" / (name + ".swift")),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    return app


async def launch(app):
    process = await asyncio.create_subprocess_exec("open", "-n", str(app))
    if await process.wait():
        raise RuntimeError("The owned keyboard fixture failed to launch")


async def wait_state(directory, predicate):
    for _ in range(100):
        current = await state(directory)
        if predicate(current):
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(current)


async def ax_facts(helper, owned):
    process = await asyncio.create_subprocess_exec(
        str(helper),
        str(owned["pid"]),
        str(owned["window"]),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, error = await process.communicate()
    if process.returncode:
        raise AssertionError({"ax_query_exit": process.returncode, "stdout": output.decode(), "stderr": error.decode()})
    return json.loads(output)


def effect_confirmed(key, before, after):
    direct = after["delegateActions"] - before["delegateActions"]
    document = after["documentActions"] - before["documentActions"]
    if key == "delegate":
        return direct == 1 and document == 0
    if key == "document":
        return document == 1 and direct == 0
    selection = after["selection"]
    return direct == document == 0 and selection == {"location": 0, "length": len(SEED)}


def sentinel_unchanged(before, after):
    return all(before[key] == after[key] for key in ("keys", "keyUps", "modifiers", "mouseEvents", "other"))


def received_events(before, after):
    return [item for item in after["events"] if item["index"] > before["lastEvent"]]


def complete_key_pair(before, after):
    return [item["type"] for item in received_events(before, after) if item["type"] in (10, 11)] == [10, 11]


def native_input(diagnostics):
    return [
        value["value"]
        for value in diagnostics
        if value.get("kind") == "backend_diagnostic"
        and isinstance(value.get("value"), dict)
        and value["value"].get("kind") == "input_activity"
        and value["value"].get("phase") == "willCall"
    ]


async def close_and_observe(computer, directory, before):
    computer._controller.transport.close()
    await computer._controller.transport.wait_closed()
    samples = []
    for _ in range(40):
        current = await state(directory)
        samples.append(current)
        if current["sequence"] > before["sequence"] and not current["active"]:
            break
        await asyncio.sleep(0.05)
    return {
        "inactive_confirmed": current["sequence"] > before["sequence"] and not current["active"],
        "same_original_window": all(current[key] == before[key] for key in ("pid", "window", "windowObjectID")),
        "samples": samples,
    }


async def verify(folder, options):
    bundle = "local.computer-use.keyboard-window-command." + uuid.uuid4().hex
    driver = Path(options.driver or native_driver()["command"]).resolve()
    helper = folder / "KeyboardWindowAXProbe"
    report = {
        "key": options.key,
        "scope": options.scope,
        "expect": options.expect,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": digest(driver),
        "source_sha256": {name: digest(ROOT / name) for name in SOURCES},
        "requested_actions": 0,
        "requested_unicode_text": False,
    }
    computer = owned = sentinel = None
    sentinel_folder = folder / "sentinel"
    diagnostics = []
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(value):
        diagnostics.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    try:
        app_path = await asyncio.to_thread(build_owned, folder, bundle, "KeyboardWindowCommandProbe")
        sentinel_app = await asyncio.to_thread(
            build_owned, sentinel_folder, bundle + ".sentinel", "KeyboardCommandSentinel"
        )
        await asyncio.to_thread(
            subprocess.run,
            [
                "swiftc",
                "-swift-version",
                "6",
                "-parse-as-library",
                str(ROOT / "tests/fixtures/KeyboardWindowAXProbe.swift"),
                "-o",
                str(helper),
            ],
            check=True,
        )
        await launch(app_path)
        owned = await state(folder)
        report["owned_pid"] = owned["pid"]
        await wait_state(folder, lambda value: value["active"] and value["frontmostPID"] == value["pid"])
        set_diagnostic_handler(record)
        computer = Computer()
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        app = computer.app(bundle)
        (folder / "before.txt").write_text((await app.read(view="tree")).text)
        target = app if options.scope == "app" else await app.element("Shortcut input")
        report["root_binding"] = asdict(computer._controller.state(app).selection.root)
        if options.scope == "element":
            report["element_binding"] = asdict(target._snapshot["binding"])
        await launch(sentinel_app)
        sentinel = await state(sentinel_folder)
        report["sentinel_before"] = await wait_state(
            sentinel_folder, lambda value: value["active"] and value["frontmostPID"] == value["pid"]
        )
        before = await wait_state(
            folder,
            lambda value: (
                not value["active"]
                and value["frontmostPID"] == sentinel["pid"]
                and value["keyWindow"] == value["mainWindow"] == 0
            ),
        )
        report["before"] = before
        report["ax_before"] = await ax_facts(helper, owned)
        assert report["ax_before"]["predicateMatches"], report["ax_before"]
        assert before["text"] == SEED and before["guard"] == "GUARD"
        assert before["delegateActions"] == before["documentActions"] == 0
        report["setup_ready"] = True
        key = {"delegate": "super+l", "document": "super+k", "select-all": "super+a"}[options.key]
        report["requested_actions"] += 1
        try:
            report["receipt"] = dict(await target.press_key(key))
        except ComputerUseError as error:
            report["error"] = {"code": error.code, "effect": error.effect, "detail": error.detail}
        for _ in range(40):
            after = await state(folder)
            if effect_confirmed(options.key, before, after) and complete_key_pair(before, after):
                break
            await asyncio.sleep(0.05)
        report["after"] = after
        report["ax_after"] = await ax_facts(helper, owned)
        report["sentinel_after"] = await state(sentinel_folder)
        report["foreground_changed"] = after["frontmostPID"] != before["frontmostPID"]
        report["cursor_changed"] = after["cursor"] != before["cursor"]
        report["sentinel_input_unchanged"] = sentinel_unchanged(report["sentinel_before"], report["sentinel_after"])
        report["delegate_delta"] = after["delegateActions"] - before["delegateActions"]
        report["document_delta"] = after["documentActions"] - before["documentActions"]
        report["text_unchanged"] = after["text"] == before["text"] and after["guard"] == before["guard"]
        report["effect_confirmed"] = effect_confirmed(options.key, before, after)
        report["received_events"] = received_events(before, after)
        report["received_key_events"] = [item for item in report["received_events"] if item["type"] in (10, 11)]
        (folder / "after.txt").write_text((await app.read(view="tree")).text)
        report["session_cleanup"] = await close_and_observe(computer, folder, await state(folder))
        computer = None
        report["sentinel_after_close"] = await wait_state(
            sentinel_folder, lambda value: value["sequence"] > report["sentinel_after"]["sequence"]
        )
        if options.expect == "background":
            assert "error" not in report and report["effect_confirmed"] and report["text_unchanged"], report
            assert not report["foreground_changed"] and not report["cursor_changed"], report
            assert report["sentinel_input_unchanged"], report
            assert report["session_cleanup"]["inactive_confirmed"], report["session_cleanup"]
            assert report["session_cleanup"]["same_original_window"], report["session_cleanup"]
            assert sentinel_unchanged(report["sentinel_before"], report["sentinel_after_close"]), report
            assert report["sentinel_after_close"]["frontmostPID"] == sentinel["pid"], report
            keys = report["received_key_events"]
            assert [item["type"] for item in keys] == [10, 11], keys
            assert all(item.get("charactersIgnoringModifiers") == key[-1] for item in keys), keys
            assert all(item.get("device") != "mouse" for item in native_input(diagnostics)), report
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        if computer is not None:
            computer._controller.transport.close()
            await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        report["native_input"] = native_input(diagnostics)
        report["cleanup"] = [
            await cleanup_fixture("target", folder, "KeyboardWindowCommandProbe", owned["pid"] if owned else None),
            await cleanup_fixture(
                "sentinel", sentinel_folder, "KeyboardCommandSentinel", sentinel["pid"] if sentinel else None
            ),
        ]
        report["cleanup_complete"] = all(
            item["outcome"] in ("terminated", "already_exited", "absent")
            or item["outcome"] == "exited_or_identity_changed"
            and not item["matching_pids"]
            for item in report["cleanup"]
        )
        cleanup_failed = options.expect == "background" and not report["cleanup_complete"] and "failure" not in report
        if cleanup_failed:
            report["failure"] = {"type": "AssertionError", "detail": "Owned fixture cleanup was not confirmed"}
        report["source_drift"] = [
            name for name, value in report["source_sha256"].items() if digest(ROOT / name) != value
        ]
        journal.close()
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        if cleanup_failed:
            raise AssertionError(report["failure"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver")
    parser.add_argument("--key", choices=("delegate", "document", "select-all"), required=True)
    parser.add_argument("--scope", choices=("app", "element"), default="app")
    parser.add_argument("--expect", choices=("observe", "background"), default="background")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="keyboard-window-command-", dir=output))
    try:
        asyncio.run(verify(folder, options))
    finally:
        LOG.info("Keyboard window command evidence: %s", folder)


if __name__ == "__main__":
    main()
