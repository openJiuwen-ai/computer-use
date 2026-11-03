# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Observe one titlebar or custom-handle drag of an owned NSWindow.

The default captured route uses the original Window screenshot and local points.
--route desktop is a separate global-input control, never an automatic fallback.
The standard branch uses the real titlebar. --custom-handle instead exercises an
application view that moves its own window during mouseDragged; this is not proof
of WindowServer titlebar capture. The final frame is the application-effect oracle.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import plistlib
import signal
import subprocess
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

from artifact_json import encode_artifact

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import state
from web_action_workflow import timestamp

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
SOURCES = (
    "os_adapter/macos/Sources/Control/NativePointerUnit.swift",
    "os_adapter/macos/Sources/Control/BackgroundEvents.swift",
    "os_adapter/macos/Sources/Control/PairedInput.swift",
    "os_adapter/macos/Sources/Capture/CapturedWindowIdentity.swift",
    "os_adapter/macos/Sources/Capture/NativeCapturedPointerPlan.swift",
    "os_adapter/macos/Sources/Capture/NativeCapture.swift",
    "tests/fixtures/CapturedWindowDragProbe.swift",
    "tests/manual/captured_window_drag_workflow.py",
)


def build_fixture(folder, bundle, custom_handle):
    name = "CapturedWindowDragProbe"
    app = folder / (name + ".app")
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
            str(ROOT / "tests/fixtures" / (name + ".swift")),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    arguments = ["--args", "--custom-handle"] if custom_handle else []
    subprocess.run(["open", "-g", str(app), *arguments], check=True)


async def command(folder, kind):
    identity = uuid.uuid4().hex
    (folder / "command.json").write_text(json.dumps({"id": identity, "kind": kind}))
    for _ in range(100):
        current = await state(folder)
        if current["commandId"] == identity:
            return current
        await asyncio.sleep(0.05)
    raise TimeoutError("Owned titlebar fixture did not consume its setup command")


async def snapshot(ui, folder, name, *, allow_foreground=False):
    options = {"allow_foreground": True} if allow_foreground else {}
    observation = await ui.read(view="both", **options)
    (folder / (name + ".txt")).write_text(observation.text)
    if observation.screenshot is not None:
        (folder / (name + ".png")).write_bytes(observation.screenshot)
    return observation


def captured_drag_points(image, root, before):
    summary = [
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
    if len(candidates) != 1:
        raise AssertionError("Expected one original root capture piece: " + json.dumps(summary))
    piece = candidates[0]
    px, py, width, height = piece.box
    qw, qh = before["frameQuartz"][2:]
    preferred_x, local_y = before["dragHandlePoint"]
    attempts = []
    # Both fixture handles have no other controls in this middle region.
    # Keep the observed overlay; choose only start/end points hit on the root.
    for local_x in dict.fromkeys((preferred_x, qw * 0.35, qw * 0.65)):
        start = (px + local_x * width / qw, py + local_y * height / qh)
        end = (start[0] + 120 * width / qw, start[1] + 80 * height / qh)
        if not (px <= start[0] < end[0] < px + width and py <= start[1] < end[1] < py + height):
            continue
        try:
            mapped = [image.hit(point)[0] for point in (start, end)]
            attempts.append({"local": [local_x, local_y], "hit_windows": [item.window for item in mapped]})
            if all(item is piece for item in mapped):
                return piece, start, end, [local_x, local_y], summary, attempts
        except ComputerUseError as error:
            attempts.append({"local": [local_x, local_y], "error": error.code})
    raise AssertionError("No unobscured drag-handle start/end in original root capture: " + json.dumps(attempts))


async def verify(folder, options):
    bundle = "local.computer-use.captured-drag." + uuid.uuid4().hex
    driver = Path(options.driver or native_driver()["command"]).resolve()
    computer = Computer(allow_global_input=options.allow_global_input)
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    journal = (folder / "diagnostics.jsonl").open("w")
    events, initial = [], None
    result = {
        "route": options.route,
        "custom_handle": options.custom_handle,
        "expect": options.expect,
        "allow_global_input": options.allow_global_input,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
        "requested_delta_quartz": [120, 80],
    }

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(build_fixture, folder, bundle, options.custom_handle)
        initial = await state(folder)
        result["setup_before"] = await command(folder, "reset")
        app = computer.app(bundle)
        await snapshot(app, folder, "app-setup")
        window = app.window(0)
        # Background self-activation is not a reliable fixture setup mechanism.
        # Explicitly prepare this already observed window before measuring drag.
        prepared = await snapshot(window, folder, "window-setup", allow_foreground=True)
        result["setup_target_preparation"] = prepared.preparation
        await command(folder, "foreground")
        for _ in range(100):
            before = await state(folder)
            if before["frontmostPID"] == before["pid"] and before["active"]:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("The owned window did not become foreground")
        assert not before["buttonHeld"] and before["ordered"], before
        result["setup_after"] = before
        await snapshot(app, folder, "app-before")
        await snapshot(window, folder, "window-before")
        before = await state(folder)
        qx, qy, qw, qh = before["frameQuartz"]
        local_x, local_y = before["dragHandlePoint"]
        result["drag_handle_kind"] = before["dragHandleKind"]
        if options.route == "captured":
            image = window._image
            root = computer._controller.state(window).selection.root
            result["observed_root"] = asdict(root)
            piece, start, end, local, pieces, attempts = captured_drag_points(image, root, before)
            local_x, local_y = local
            result["image_pieces"] = pieces
            result["titlebar_candidates"] = attempts
            result["original_capture"] = {
                "binding": asdict(piece.image.target),
                "coordinates": dict(piece.image.coordinates),
                "piece_box": piece.box,
                "digest": image.digest.hex(),
            }
            ui = window
        else:
            await snapshot(computer, folder, "desktop-before")
            catalogue = computer._controller.desktop._view["observation"]["windows"]
            app_name = computer._controller.state(app).name
            matches = [
                entry
                for entry in catalogue
                if entry["app"] == app_name and entry["title"] == "Captured window drag probe"
            ]
            result["desktop_lookup"] = {"app": app_name, "title": "Captured window drag probe", "matches": matches}
            assert len(matches) == 1, matches
            rect = matches[0]["rect"]
            px, py, width, height = (rect[key] for key in ("x", "y", "width", "height"))
            result["desktop_window"] = matches[0]
            ui = computer
            start = (px + local_x * width / qw, py + local_y * height / qh)
            end = (start[0] + 120 * width / qw, start[1] + 80 * height / qh)
            assert px <= start[0] < end[0] < px + width and py <= start[1] < end[1] < py + height
        result.update(
            before=before, start=start, end=end, screen_start=[qx + local_x, qy + local_y], started_at=timestamp()
        )
        action_start = len(events)
        result["setup_diagnostic_count"] = action_start
        try:
            result["receipt"] = dict(await ui.drag(start, end))
        except ComputerUseError as error:
            result["error"] = {"code": error.code, "effect": error.effect, "detail": error.detail}
        result["returned_at"] = timestamp()
        for name, delay in (("early", 0.1), ("late", 0.7)):
            await asyncio.sleep(delay)
            result[name] = {"time": timestamp(), "state": await state(folder)}
        final = result["late"]["state"]
        delta = [final["frameQuartz"][0] - qx, final["frameQuartz"][1] - qy]
        result["actual_delta_quartz"] = delta
        inputs = [
            entry["value"]
            for entry in events[action_start:]
            if entry.get("kind") == "backend_diagnostic"
            and isinstance(entry.get("value"), dict)
            and entry["value"].get("kind") == "input_activity"
            and entry["value"].get("phase") == "willCall"
        ]
        result["native_input"] = inputs
        complete = all(abs(actual - expected) <= 4 for actual, expected in zip(delta, (120, 80), strict=True))
        interrupted = (
            any(abs(value) > 1 for value in delta)
            and not complete
            and "error" in result
            and "geometry changed" in result["error"]["detail"]
        )
        result["effect"] = (
            "complete-window-move" if complete else "geometry-interrupted" if interrupted else "unconfirmed"
        )
        assert final["windowNumber"] == before["windowNumber"] and final["windowObjectID"] == before["windowObjectID"]
        assert final["frameQuartz"][2:] == before["frameQuartz"][2:], final
        assert not final["buttonHeld"] and final["otherClicks"] == 0 and final["contentDown"] == 0, final
        await snapshot(window, folder, "window-after")
        if options.expect == "complete":
            assert complete and "receipt" in result, result
        elif options.expect == "interrupted":
            assert interrupted and result["error"]["effect"] in ("partial", "possible"), result
            assert any(event.get("eventType") == 2 for event in inputs), "The owned press was not visibly released"
        result["observation_complete"] = True
        result["expectation_passed"] = None if options.expect == "observe" else True
    except Exception as error:
        result["failure"] = {"type": type(error).__name__, "detail": str(error), "time": timestamp()}
        raise
    finally:
        result["source_drift"] = [
            name
            for name, digest in result["source_sha256"].items()
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
        ]
        (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=encode_artifact) + "\n")
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        journal.close()
        if initial is not None:
            os.kill(initial["pid"], signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--route", choices=("captured", "desktop"), default="captured")
    parser.add_argument("--allow-global-input", action="store_true", help="Explicitly authorize global input for this run")
    parser.add_argument("--custom-handle", action="store_true")
    parser.add_argument("--expect", choices=("observe", "complete", "interrupted"), default="observe")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="captured-window-drag-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, options))
    finally:
        LOG.info("Captured window drag evidence: %s", folder)


if __name__ == "__main__":
    main()
