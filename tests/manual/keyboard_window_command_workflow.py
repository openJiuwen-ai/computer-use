# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify one background AppKit command on its original window or element.

Use --key delegate|document|select-all and --scope app|element. Each invocation
owns fresh applications and sends one press_key request. The default background
oracle requires actual effects, unchanged foreground/cursor and no sentinel
input. --expect observe records the outcome for an older driver comparison.
--operation menu queries Commands without sending menu input, then clicks its
retained document command. AppKit itself may invoke menu delegate callbacks
during AXChildren getters; those callbacks are not proof of SDK input.
--rebuild-menu arms one legal menuNeedsUpdate rebuild after target observation,
requiring an observed tracking-began notification. Use --expect observe first:
AppKit does not promise the relative callback order needed by this diagnostic.
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
from artifact_json import encode_artifact
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
    "os_adapter/macos/Sources/Control/NativeMenuUnit.swift",
    "os_adapter/macos/Sources/Control/Routes/Passes/BuildNativeMenuRoute.swift",
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


async def launch(app, *, arguments=()):
    process = await asyncio.create_subprocess_exec(
        "open", "-n", str(app), *(["--args", *arguments] if arguments else [])
    )
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


def menu_read_evidence(diagnostics, before, after):
    """Provider lifecycle is separate from SDK input and command callbacks."""
    commands = [event for event in diagnostics if event.get("kind") == "command_start"]
    result = {
        "provider_before": before["menu"],
        "provider_after": after["menu"],
        "native_input": native_input(diagnostics),
        "wire_methods": [event.get("method") for event in commands],
        "write_commands": [
            event
            for event in commands
            if event.get("method") not in ("begin", "inspect", "finish")
            or event.get("method") == "begin"
            and event.get("params", {}).get("mode") != "read"
        ],
        "command_state_unchanged": all(
            before[key] == after[key] for key in ("documentActions", "delegateActions", "text", "guard")
        ),
        "provider_lifecycle_query_evidence": ".artifacts/menu-read-query-wab79c3t/result.json",
    }
    assert not result["native_input"] and not result["write_commands"] and result["command_state_unchanged"], result
    return result


async def close_and_observe(computer, directory, before):
    await asyncio.to_thread(computer.close)
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
        "operation": options.operation,
        "rebuild_menu": options.rebuild_menu,
        "global_input_allowed": options.allow_global_input,
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
        arguments = ("--menu-probe",) if options.operation == "menu" else ()
        if options.rebuild_menu:
            arguments += ("--rebuild-menu",)
        await launch(app_path, arguments=arguments)
        owned = await state(folder)
        report["owned_pid"] = owned["pid"]
        set_diagnostic_handler(record)
        computer = Computer(allow_global_input=options.allow_global_input)
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        app = computer.app(bundle)
        prepared = await asyncio.to_thread(app.read, view="tree", allow_foreground=True)
        report["setup_target_preparation"] = prepared.preparation
        await wait_state(folder, lambda value: value["active"] and value["frontmostPID"] == value["pid"])
        before_default_read = await state(folder)
        default_read_start = len(diagnostics)
        (folder / "before.txt").write_text((await asyncio.to_thread(app.read, view="tree")).text)
        report["default_read"] = menu_read_evidence(
            diagnostics[default_read_start:], before_default_read, await state(folder)
        )
        if options.operation == "menu":
            before_menu_read = await state(folder)
            menu_read_start = len(diagnostics)
            menu = await asyncio.to_thread(app.read, menu_path=["Commands"])
            (folder / "menu-before.txt").write_text(menu.text)
            target = await asyncio.to_thread(app.element, "Count document command")
            after_menu_read = await state(folder)
            report["menu_read"] = menu_read_evidence(diagnostics[menu_read_start:], before_menu_read, after_menu_read)
            report["menu_validation_limit"] = (
                "Direct AXChildren getters invoke this provider's menu callbacks and can enable the item. "
                "This fixture does not prove an initially-disabled command becomes enabled only by SDK preparation."
            )
            report["menu_element_binding"] = asdict(target._snapshot["binding"])
        else:
            target = app if options.scope == "app" else await asyncio.to_thread(app.element, "Shortcut input")
        report["root_binding"] = asdict(computer._controller.state(app).selection.root)
        if options.scope == "element":
            report["element_binding"] = asdict(target._snapshot["binding"])
        await launch(sentinel_app)
        sentinel = await state(sentinel_folder)
        prepared = await asyncio.to_thread(computer.app(bundle + ".sentinel").read, view="tree", allow_foreground=True)
        report["setup_sentinel_preparation"] = prepared.preparation
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
        if options.rebuild_menu:
            identity = uuid.uuid4().hex
            (folder / "command.json").write_text(json.dumps({"id": identity, "kind": "arm-rebuild"}))
            armed = await wait_state(
                folder, lambda value: value["lastCommand"] == identity and value["menu"]["rebuildArmed"]
            )
            assert armed["menu"]["generation"] == 0 and armed["menu"]["item"] == before["menu"]["item"], armed
            report["rebuild_armed"] = armed
            report["rebuild_attribution_limit"] = (
                "Armed only after exact target observation. Rebuild is allowed only in menuNeedsUpdate while "
                "a main-menu tracking notification is active. If AppKit calls menuNeedsUpdate before "
                "didBeginTracking, no rebuild occurs and this run cannot prove tracking-time replacement. "
                "Native preparation/input timestamps must also bracket the rebuild."
            )
        report["setup_ready"] = True
        key = {"delegate": "super+l", "document": "super+k", "select-all": "super+a"}[options.key]
        report["requested_actions"] += 1
        action_start = len(diagnostics)
        try:
            report["receipt"] = dict(
                await asyncio.to_thread(target.click)
                if options.operation == "menu"
                else await asyncio.to_thread(target.press_key, key)
            )
        except ComputerUseError as error:
            report["error"] = {"code": error.code, "effect": error.effect, "detail": error.detail}
        for _ in range(40):
            after = await state(folder)
            if effect_confirmed(options.key, before, after) and (
                options.operation == "menu" or complete_key_pair(before, after)
            ):
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
        report["action_native_input"] = native_input(diagnostics[action_start:])
        if options.operation == "menu":
            menu_preparation = [
                effect
                for effect in report["action_native_input"]
                if effect.get("stage") == "preparation" and effect.get("mechanism") == "AXPress"
            ]
            menu_cleanup = [
                effect
                for effect in report["action_native_input"]
                if effect.get("stage") == "cleanup" and effect.get("mechanism") == "AXCancel"
            ]
            command_input = [effect for effect in report["action_native_input"] if effect.get("stage") == "input"]
            report["menu_effect"] = {
                "before": before["menu"],
                "after": after["menu"],
                "provider_lifecycle_delta": {
                    key: after["menu"][key] - before["menu"][key]
                    for key in ("opens", "closes", "needsUpdates", "trackingBegins", "trackingEnds")
                },
                "preparation_calls": menu_preparation,
                "cleanup_calls": menu_cleanup,
                "command_input_calls": command_input,
                "opened": bool(menu_preparation),
                "closed": bool(menu_cleanup) and not after["menu"]["tracking"],
                "one_command_input": len(command_input) == 1 and command_input[0].get("mechanism") == "AXPress",
                "current_item_callback": after["menu"]["actionSenders"] == [after["menu"]["item"]],
            }
            assert after["documentActions"] <= 1 and after["delegateActions"] == 0, report["menu_effect"]
            if options.rebuild_menu and after["menu"]["generation"]:
                assert before["menu"]["item"] not in after["menu"]["actionSenders"], report["menu_effect"]
            if options.rebuild_menu:
                rebuild_events = [event for event in after["menu"]["lifecycle"] if event["kind"] == "rebuild"]
                report["menu_effect"]["tracking_rebuild_observed"] = (
                    after["menu"]["generation"] == 1
                    and len(rebuild_events) == 1
                    and rebuild_events[0]["notificationTracking"]
                    and after["menu"]["trackingBegins"] > report["rebuild_armed"]["menu"]["trackingBegins"]
                    and any(
                        preparation["uptime"] <= rebuild_events[0]["uptime"] <= dispatch["uptime"]
                        for preparation in menu_preparation
                        for dispatch in command_input
                    )
                )
        (folder / "after.txt").write_text((await asyncio.to_thread(app.read, view="tree")).text)
        report["session_cleanup"] = await close_and_observe(computer, folder, await state(folder))
        computer = None
        report["sentinel_after_close"] = await wait_state(
            sentinel_folder, lambda value: value["sequence"] > report["sentinel_after"]["sequence"]
        )
        if options.expect in ("background", "effect"):
            assert "error" not in report and report["effect_confirmed"] and report["text_unchanged"], report
            assert not report["cursor_changed"], report
            assert report["sentinel_input_unchanged"], report
            assert report["session_cleanup"]["same_original_window"], report["session_cleanup"]
            assert sentinel_unchanged(report["sentinel_before"], report["sentinel_after_close"]), report
            if options.operation == "menu":
                assert all(
                    report["menu_effect"][key]
                    for key in ("opened", "closed", "one_command_input", "current_item_callback")
                ), report
                assert not report["received_key_events"], report["received_key_events"]
                if options.rebuild_menu:
                    assert report["menu_effect"]["tracking_rebuild_observed"], report["menu_effect"]
        if options.expect == "background":
            assert not report["foreground_changed"], report
            assert report["session_cleanup"]["inactive_confirmed"], report["session_cleanup"]
            assert report["sentinel_after_close"]["frontmostPID"] == sentinel["pid"], report
            if options.operation == "key":
                keys = report["received_key_events"]
                assert [item["type"] for item in keys] == [10, 11], keys
                assert all(item.get("charactersIgnoringModifiers") == key[-1] for item in keys), keys
                assert all(item.get("device") != "mouse" for item in report["action_native_input"]), report
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        if computer is not None:
            await asyncio.to_thread(computer.close)
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
        cleanup_failed = options.expect != "observe" and not report["cleanup_complete"] and "failure" not in report
        if cleanup_failed:
            report["failure"] = {"type": "AssertionError", "detail": "Owned fixture cleanup was not confirmed"}
        report["source_drift"] = [
            name for name, value in report["source_sha256"].items() if digest(ROOT / name) != value
        ]
        journal.close()
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )
        if cleanup_failed:
            raise AssertionError(report["failure"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver")
    parser.add_argument("--key", choices=("delegate", "document", "select-all"), required=True)
    parser.add_argument("--scope", choices=("app", "element"), default="app")
    parser.add_argument("--operation", choices=("key", "menu"), default="key")
    parser.add_argument("--rebuild-menu", action="store_true")
    parser.add_argument("--allow-global-input", action="store_true")
    parser.add_argument("--expect", choices=("observe", "background", "effect"), default="background")
    options = parser.parse_args()
    if options.operation == "menu" and (options.key != "document" or options.scope != "app"):
        parser.error("menu operation requires --key document --scope app")
    if options.rebuild_menu and options.operation != "menu":
        parser.error("--rebuild-menu requires --operation menu")
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
