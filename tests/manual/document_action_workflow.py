# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe one original A-button action after B becomes the receiving window.

Use --receiver direct|responder and --operation click|action|coordinate.
Every invocation owns a fresh two-window fixture, sends one target
action, and records real A/B callback counters. It never retries an input or
selects another route. Default observation mode records wrong-window/no effects;
--expect target is an assertion for a fix verification.
With --state background, an independent sentinel takes system foreground after
B is selected. Foreground and cursor changes are recorded separately.
--enable-on-main enables each window's buttons only while that window is main,
using real NSWindow delegate callbacks.
--keep-target-disabled, with --enable-on-main, keeps A disabled even when main.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import signal
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
from input_receiver_workflow import build_fixture, state
from web_action_workflow import build, command, timestamp, wait_for

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
SOURCES = (
    "os_adapter/macos/Sources/Control/NativeActionDocumentPolicy.swift",
    "os_adapter/macos/Sources/Control/Focus/NativeDocumentContext.swift",
    "os_adapter/macos/Sources/Control/NativeActionUnit.swift",
    "os_adapter/macos/Sources/Control/Click/NativeClickPreparationPipeline.swift",
    "os_adapter/macos/Sources/Control/Click/Passes/DispatchNativeClick.swift",
    "os_adapter/macos/Sources/Control/Action/NativeObjectActionPipeline.swift",
    "os_adapter/macos/Sources/Control/Action/NativeObjectActionModel.swift",
    "os_adapter/macos/Sources/Control/Action/NativeObjectActionPolicy.swift",
    "os_adapter/macos/Sources/Control/Action/Passes/ResolveNativeObjectAction.swift",
    "os_adapter/macos/Sources/Control/NativeApplicationContextUnit.swift",
    "os_adapter/macos/Sources/Control/Prepare/DocumentAction/NativeDocumentActionPreparationPipeline.swift",
    "os_adapter/macos/Sources/Control/Prepare/DocumentAction/Passes/PrepareActionDocumentContext.swift",
    "os_adapter/macos/Sources/Control/Prepare/DocumentAction/Passes/PrepareActionDocumentForeground.swift",
    "os_adapter/macos/Sources/Control/Prepare/DocumentAction/Passes/ConfirmActionDocumentContext.swift",
    "os_adapter/macos/Sources/Control/InputContext/NativeActionDocumentContexts.swift",
    "os_adapter/macos/Sources/Contracts/NativeActionDocumentContract.swift",
    "os_adapter/macos/Sources/Control/NativePointerUnit.swift",
    "os_adapter/macos/Sources/Control/BackgroundEvents.swift",
    "os_adapter/macos/Sources/Capture/NativeCapture.swift",
    "os_adapter/macos/Sources/Capture/NativeCapturedPointerPlan.swift",
    "computer_use_engine/session/interface_images.py",
    "tests/fixtures/DocumentActionProbe.swift",
    "tests/fixtures/WebActionProbe.swift",
    "tests/manual/web_action_workflow.py",
    "tests/manual/input_receiver_workflow.py",
    "tests/manual/document_action_workflow.py",
)


def callback_counts(value, receiver):
    return [document[receiver + "Actions"] for document in value["documents"]]


def window_ready(value, index):
    document = value["documents"][index]
    return (
        value["active"]
        and value["frontmostPID"] == value["pid"]
        and value["keyWindow"] == value["mainWindow"] == document["window"]
        and document["firstResponder"]
        and (
            not value.get("enableOnMain", False)
            or all(
                button["enabled"]
                == (
                    item["window"] == value["mainWindow"]
                    and not (value.get("keepTargetDisabled", False) and item["document"] == "A")
                )
                for item in value["documents"]
                for button in item["buttons"]
            )
        )
    )


async def snapshot(app, folder, name):
    observation = await app.read(view="both", full=True)
    (folder / (name + ".txt")).write_text(observation.text)
    if observation.screenshot is not None:
        (folder / (name + ".png")).write_bytes(observation.screenshot)
    return observation


def coordinate_target(image, root, observed, receiver):
    pieces = [
        {
            "window": item.window,
            "binding": asdict(item.image.target),
            "box": item.box,
            "png_bytes": len(item.image.image),
        }
        for item in image.pieces
    ]
    candidates = [
        item
        for item in image.pieces
        if item.window == root.root_window
        and item.image.target.surface == root.surface
        and item.image.target.instance == root.instance
        and item.image.target.root_window == root.root_window
    ]
    assert len(candidates) == 1, {"root": asdict(root), "pieces": pieces}
    piece = candidates[0]
    documents = [item for item in observed["documents"] if item["document"] == "A"]
    assert len(documents) == 1
    document = documents[0]
    buttons = [item for item in document["buttons"] if item["receiver"] == receiver]
    assert len(buttons) == 1
    native_center = buttons[0]["screenCenterQuartz"]
    qx, qy, qw, qh = document["frame"]
    px, py, width, height = piece.box
    local_x, local_y = native_center[0] - qx, native_center[1] - qy
    assert 0 <= local_x < qw and 0 <= local_y < qh, {"button": buttons[0], "frame": document["frame"]}
    source_point = (px + local_x * width / qw, py + local_y * height / qh)
    point = source_point
    delivery = image.delivered_coordinates
    if delivery is not None:
        annotation = delivery.delivered_rect({"x": point[0], "y": point[1], "width": 0, "height": 0})
        point = (annotation["x"], annotation["y"])
    hit, local_pixel = image.hit(point)
    assert hit is piece, {"hit": hit.window, "root": root.root_window, "point": point, "pieces": pieces}
    return {
        "point": point,
        "source_canvas_point": source_point,
        "hit_window": hit.window,
        "local_pixel": local_pixel,
        "fixture_button": buttons[0],
        "fixture_window": document,
        "root": asdict(root),
        "capture_binding": asdict(piece.image.target),
        "capture_coordinates": dict(piece.image.coordinates),
        "image_digest": image.digest.hex(),
        "delivery_coordinates": asdict(delivery) if delivery else None,
        "pieces": pieces,
    }


def owned_processes(executable):
    listing = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, check=True, timeout=3).stdout
    return [
        int(parts[0])
        for line in listing.splitlines()
        if len(parts := line.strip().split(None, 1)) == 2 and Path(parts[1]) == executable
    ]


async def cleanup_fixture(label, folder, name, known_pid):
    executable = folder / (name + ".app") / "Contents/MacOS" / name
    result = {"component": label, "executable": str(executable), "known_pid": known_pid}
    try:
        candidates = await asyncio.to_thread(owned_processes, executable)
        result["matching_pids"] = candidates
        if known_pid is not None:
            if known_pid not in candidates:
                return {**result, "outcome": "exited_or_identity_changed"}
            pid = known_pid
        elif len(candidates) == 1:
            pid = candidates[0]
        else:
            return {**result, "outcome": "absent" if not candidates else "ambiguous"}
        os.kill(pid, signal.SIGTERM)
        result.update(pid=pid, signal_sent=True)
        for _ in range(20):
            if pid not in await asyncio.to_thread(owned_processes, executable):
                return {**result, "outcome": "terminated"}
            await asyncio.sleep(0.05)
        return {**result, "outcome": "termination_unconfirmed"}
    except ProcessLookupError:
        return {**result, "outcome": "already_exited"}
    except (OSError, subprocess.SubprocessError) as error:
        return {**result, "outcome": "cleanup_unknown", "detail": str(error)}


async def verify(folder, options):
    bundle = "local.computer-use.document-action." + uuid.uuid4().hex
    driver = Path(options.driver or native_driver()["command"]).resolve()
    report = {
        "receiver": options.receiver,
        "operation": options.operation,
        "coordinate_scope": options.coordinate_scope if options.operation == "coordinate" else None,
        "requested_state": options.state,
        "enable_on_main": options.enable_on_main,
        "keep_target_disabled": options.keep_target_disabled,
        "expect": options.expect,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
        "requested_actions": 0,
    }
    computer = initial = sentinel = None
    sentinel_folder = folder / "sentinel"
    events = []
    journal = (folder / "diagnostics.jsonl").open("w")

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(
            build_fixture,
            folder,
            bundle,
            "DocumentActionProbe",
            arguments=(("--enable-on-main",) if options.enable_on_main else ())
            + (("--keep-target-disabled",) if options.keep_target_disabled else ()),
        )
        initial = await state(folder)
        assert initial["enableOnMain"] == options.enable_on_main, initial
        assert initial["keepTargetDisabled"] == options.keep_target_disabled, initial
        report["owned_pid"] = initial["pid"]
        await command(folder, "front-target")
        await wait_for(folder, lambda value: window_ready(value, 0))
        computer = Computer()
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        chooser = await computer.app(bundle).read(view="tree")
        (folder / "app-choices.txt").write_text(chooser.text)
        candidates = []
        for line in chooser.text.splitlines():
            match = re.fullmatch(r"(\d+) .+; computer\.app\((\d+)\)", line)
            if match and "Document context A" in line and match[1] == match[2]:
                candidates.append({"id": int(match[1]), "published_line": line})
        report["target_choices"] = candidates
        assert len(candidates) == 1, report["target_choices"]
        app = computer.app(candidates[0]["id"])
        observation = await snapshot(app, folder, "a-observed")
        assert "Document context A" in observation.text, observation.text
        ui = app
        if options.operation == "coordinate":
            root = computer._controller.state(app).selection.root
            if options.coordinate_scope == "window":
                ui = app.window(int(root.root_window))
                await snapshot(ui, folder, "a-window-observed")
            root = computer._controller.state(ui).selection.root
            report["target_binding"] = asdict(root)
        else:
            target = await app.element([{"identifier": "document-" + options.receiver + "-A"}])
            report["target_binding"] = asdict(target._snapshot["binding"])
        report["target_before_switch"] = await state(folder)
        if options.operation == "coordinate":
            report["coordinate"] = coordinate_target(ui._image, root, report["target_before_switch"], options.receiver)
        await command(folder, "front-other")
        report["other_window_selected"] = await wait_for(folder, lambda value: window_ready(value, 1))
        if options.state == "background":
            sentinel_app = await asyncio.to_thread(
                build, sentinel_folder, bundle + ".sentinel", "DocumentActionSentinel"
            )
            launch = await asyncio.create_subprocess_exec("open", str(sentinel_app), "--args", "--sentinel")
            if await launch.wait():
                raise RuntimeError("The owned foreground sentinel failed to launch")
            sentinel = await state(sentinel_folder)
            report["sentinel_before"] = await wait_for(
                sentinel_folder, lambda value: value["active"] and value["frontmostPID"] == value["pid"]
            )
            await wait_for(folder, lambda value: not value["active"] and value["frontmostPID"] == sentinel["pid"])
        before = await state(folder)
        assert all(
            document["directActions"] == document["responderActions"] == document["contentDowns"] == 0
            for document in before["documents"]
        ), before
        report["before"] = before
        report["setup_ready"] = True
        event_path = folder / "events.jsonl"
        event_start = len(event_path.read_text().splitlines()) if event_path.exists() else 0
        activity_start = len(events)
        report["started_at"] = timestamp()
        report["requested_actions"] += 1
        try:
            if options.operation == "coordinate":
                result = await ui.click(at=tuple(report["coordinate"]["point"]))
            elif options.operation == "click":
                result = await target.click()
            else:
                result = await target.perform_action("AXPress")
            report["receipt"] = dict(result)
        except ComputerUseError as error:
            report["error"] = {"code": error.code, "effect": error.effect, "detail": error.detail}
        report["returned_at"] = timestamp()
        if sentinel is not None:
            report["sentinel_after_return"] = await state(sentinel_folder)
        samples = []
        for _ in range(40):
            current = await state(folder)
            samples.append({"time": timestamp(), "state": current})
            if sum(callback_counts(current, options.receiver)):
                break
            await asyncio.sleep(0.05)
        report["samples"] = samples
        await snapshot(ui, folder, "a-after")
        after = await state(folder)
        report["after"] = after
        report["system_foreground_changed"] = before["frontmostPID"] != after["frontmostPID"]
        report["system_mouse_changed"] = before["mouse"] != after["mouse"]
        if sentinel is not None:
            report["sentinel_after"] = await state(sentinel_folder)
            report["sentinel_input_unchanged"] = all(
                report["sentinel_after"][key] == report["sentinel_before"][key]
                for key in ("keys", "keyUps", "mouseEvents", "other")
            )
        report["callback_delta"] = [
            after_count - before_count
            for before_count, after_count in zip(
                callback_counts(before, options.receiver), callback_counts(after, options.receiver), strict=True
            )
        ]
        other = "responder" if options.receiver == "direct" else "direct"
        report["unrelated_callback_delta"] = [
            after_count - before_count
            for before_count, after_count in zip(
                callback_counts(before, other), callback_counts(after, other), strict=True
            )
        ]
        report["application_effect"] = {
            (1, 0): "target-window",
            (0, 1): "other-window",
            (0, 0): "unconfirmed",
        }.get(tuple(report["callback_delta"]), "unexpected-callback-count")
        report["native_input"] = [
            item["value"]
            for item in events[activity_start:]
            if item.get("kind") == "backend_diagnostic"
            and isinstance(item.get("value"), dict)
            and item["value"].get("kind") == "input_activity"
            and item["value"].get("phase") == "willCall"
        ]
        report["fixture_events"] = (
            [json.loads(line) for line in event_path.read_text().splitlines()[event_start:]]
            if event_path.exists()
            else []
        )
        report["target_binding_after"] = asdict(
            computer._controller.state(ui).selection.root
            if options.operation == "coordinate"
            else target._snapshot["binding"]
        )
        assert report["target_binding_after"] == report["target_binding"]
        assert report["unrelated_callback_delta"] == [0, 0], report["unrelated_callback_delta"]
        assert all(
            original["window"] == final["window"]
            and original["windowObjectID"] == final["windowObjectID"]
            and original["buttonObjectIDs"] == final["buttonObjectIDs"]
            and final["contentDowns"] == 0
            for original, final in zip(before["documents"], after["documents"], strict=True)
        ), {"before": before, "after": after}
        if options.expect != "observe":
            assert report["application_effect"] == options.expect + "-window", report["application_effect"]
        report["observation_complete"] = True
        report["expectation_passed"] = None if options.expect == "observe" else True
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error), "time": timestamp()}
        raise
    finally:
        cleanup = []
        if computer is not None:
            try:
                computer._controller.transport.close()
                await computer._controller.transport.wait_closed()
            except (OSError, RuntimeError) as error:
                cleanup.append({"component": "transport", "error": str(error)})
        if options.state == "background":
            cleanup.append(
                await cleanup_fixture(
                    "sentinel", sentinel_folder, "DocumentActionSentinel", sentinel["pid"] if sentinel else None
                )
            )
        cleanup.append(
            await cleanup_fixture("target", folder, "DocumentActionProbe", initial["pid"] if initial else None)
        )
        report["cleanup"] = cleanup
        report["source_drift"] = [
            name
            for name, digest in report["source_sha256"].items()
            if not (ROOT / name).is_file() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
        ]
        set_diagnostic_handler(None)
        journal.close()
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receiver", choices=("direct", "responder"), required=True)
    parser.add_argument("--operation", choices=("click", "action", "coordinate"), required=True)
    parser.add_argument("--coordinate-scope", choices=("app", "window"), default="app")
    parser.add_argument("--enable-on-main", action="store_true")
    parser.add_argument("--keep-target-disabled", action="store_true")
    parser.add_argument("--state", choices=("foreground", "background"), default="foreground")
    parser.add_argument("--expect", choices=("observe", "target", "other"), default="observe")
    parser.add_argument("--driver", type=Path)
    options = parser.parse_args()
    if options.keep_target_disabled and not options.enable_on_main:
        parser.error("--keep-target-disabled requires --enable-on-main")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="document-action-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, options))
    finally:
        LOG.info("Document action evidence: %s", folder)


if __name__ == "__main__":
    main()
