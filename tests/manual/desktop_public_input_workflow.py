# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Opt-in Computer desktop input acceptance on one disposable owned window.

Only after explicit user authorization and a matching build:
  .venv/bin/python tests/manual/desktop_public_input_workflow.py --allow-global-input
Without the flag this exits before creating artifacts, constructing Computer or
launching a fixture. The flag is an execution guard, not a request for permission.
Setup may use explicit App observation preparation; all measured input uses the
public Computer click/type_text/press_key/scroll/drag methods. No failed action
is replayed. Cleanup never restores focus, moves the mouse or sends more input.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import math
import re
import struct
import tempfile
import time
import uuid
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from document_action_workflow import cleanup_fixture
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
NAME = "DesktopPublicInputProbe"
QUOTED = r'"(?:[^"\\]|\\.)*"'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def released(value):
    return not value["keysHeld"] and not value["buttonsHeld"] and not value["modifierHeld"]


def contains(rect, point):
    x, y, width, height = rect
    return x <= point[0] < x + width and y <= point[1] < y + height


def segment_intersects(rect, start, end):
    """Conservative inclusive segment/rectangle overlap, including its boundary."""
    near, far = 0.0, 1.0
    for axis in (0, 1):
        low, high = rect[axis], rect[axis] + rect[axis + 2]
        delta = end[axis] - start[axis]
        if delta == 0:
            if not low <= start[axis] <= high:
                return False
        else:
            bounds = sorted(((low - start[axis]) / delta, (high - start[axis]) / delta))
            near, far = max(near, bounds[0]), min(far, bounds[1])
            if near > far:
                return False
    return True


def desktop_points(observation, current, names):
    """Use only public PNG/window geometry; never inspect private desktop state."""
    text, png = observation.text, observation.screenshot
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    dimensions = struct.unpack(">II", png[16:24])
    header = re.search(r"(?m)^Screenshot: desktop (\d+)x(\d+) px; origin=top-left$", text)
    assert header and tuple(map(int, header.groups())) == dimensions, text
    assert not re.search(r"(?m)^(?:Catalogue:|Window catalogue truncated:)", text), text
    assert current["fullyOnDisplay"], "Cannot scale a clipped window rectangle as a full fixture window"
    window_header = "On-screen windows (rectangles may overlap; not element references):"
    assert text.count(window_header) == 1, text
    display_text, windows_text = text.split(window_header, 1)
    captured = [
        tuple(map(float, match.split(",")))
        for match in re.findall(rf"(?m)^  {QUOTED} \(([^)]+)\) captured$", display_text)
    ]
    pattern = re.compile(
        rf"^  ({QUOTED}) — ({QUOTED}|<untitled>) \(([^)]+)\)"
        rf"(?: computer\.window\((\d+)\))?"
        rf"(?:; application: (?:computer\.app\(\d+\)|app={QUOTED}))?$"
    )
    windows, matches = [], []
    for line in windows_text.splitlines():
        match = pattern.fullmatch(line)
        if match:
            rect = tuple(map(float, match[3].split(",")))
            assert len(rect) == 4 and all(math.isfinite(v) for v in rect) and min(rect[2:]) > 0
            title = None if match[2] == "<untitled>" else json.loads(match[2])
            if json.loads(match[1]) == NAME and title == current["title"]:
                assert match[4] is not None, "The owned window must have a direct published Window entry"
                matches.append(len(windows))
            windows.append((rect, line))
    assert len(matches) == 1, ("Expected one exact owned window in the public desktop", matches)
    owned_index = matches[0]
    (px, py, pw, ph), line = windows[owned_index]
    qx, qy, qw, qh = current["frameQuartz"]
    assert qw > 0 and qh > 0
    points = {name: (px + (current[name][0] - qx) * pw / qw, py + (current[name][1] - qy) * ph / qh) for name in names}
    # One convex captured display must contain every endpoint. This also proves
    # that a straight drag path stays out of uncaptured display gaps.
    assert any(all(contains(rect, point) for point in points.values()) for rect in captured), (points, captured)
    assert all(contains((px, py, pw, ph), point) for point in points.values()), points
    # CGWindowList(optionOnScreenOnly) is front-to-back (official CGWindow.h);
    # DesktopCapture and the public renderer preserve this catalogue order.
    # A foreground app alone does not prove its point is above an overlay.
    for rect, other_line in windows[:owned_index]:
        assert not any(contains(rect, point) for point in points.values()), (
            "Owned point is covered",
            other_line,
            points,
        )
        if len(points) == 2:
            start, end = points.values()
            assert not segment_intersects(rect, start, end), ("Owned drag path crosses a covering window", other_line)
    return points, {
        "png_size": dimensions,
        "published_window": line,
        "frameQuartz": current["frameQuartz"],
        "windows_above_owned": [other_line for _, other_line in windows[:owned_index]],
    }


def require_global_impact(receipt, device):
    impact = receipt.impact
    assert impact is not None and impact.global_input_allowed is True
    assert impact.attempts and all(attempt.cleanup == "complete" for attempt in impact.attempts), impact
    assert all(attempt.actual is not None for attempt in impact.attempts), impact
    effects = [effect for attempt in impact.attempts for effect in attempt.actual.effects]
    actual = [effect for effect in effects if effect.device == device and effect.state != "notInvoked"]
    assert actual and all(effect.transport == "globalHID" and effect.state == "invoked" for effect in actual), impact


def failure(error):
    if isinstance(error, ComputerUseError):
        return {
            name: getattr(error, name)
            for name in (
                "code",
                "effect",
                "target",
                "detail",
                "next",
                "impact",
                "observation",
                "observation_status",
                "observation_error",
                "window_lifecycle",
                "choices",
            )
        }
    return {"type": type(error).__name__, "detail": str(error)}


async def ready_state(folder, initial, *, field=False):
    value = await state(folder)
    for name in ("pid", "window", "windowObjectID", "fieldObjectID", "frameQuartz", "guard"):
        assert value[name] == initial[name], ("Owned target changed; no next input", name, value)
    assert value["fullyOnDisplay"] and value["active"] and value["frontmostPID"] == initial["pid"], value
    assert released(value), ("Input remains held; no next input", value)
    if field:
        assert value["fieldFocused"], ("The owned input is no longer the current receiver", value)
    return value


async def await_effect(folder, predicate):
    for _ in range(100):
        value = await state(folder)
        if predicate(value) and released(value):
            return value
        await asyncio.sleep(0.05)
    raise AssertionError(f"The owned UI did not reach the expected effect/release: {value}")


async def verify(folder, options):
    if options.allow_global_input is not True:
        raise ValueError("Explicit --allow-global-input is required before any setup or input")
    bundle = "local.computer-use.desktop-public." + uuid.uuid4().hex
    driver = Path(options.driver or native_driver()["command"]).resolve()
    sources = [
        Path(__file__).resolve(),
        Path(computer_use.__file__),
        ROOT / "tests/fixtures/DesktopPublicInputProbe.swift",
        ROOT / "computer_use_engine/interaction/agent/pipeline.py",
        ROOT / "computer_use_engine/interaction/read/presentation/rendering/desktop.py",
        ROOT / "os_adapter/macos/Sources/Control/NativeDesktopUnit.swift",
    ]
    report = {
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": digest(driver),
        "source_sha256": {str(path): digest(path) for path in sources},
        "allow_global_input": True,
        "actions": [],
        "passed": False,
    }
    computer, initial = None, None
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(build_fixture, folder, bundle, NAME)
        initial = await state(folder)
        report["initial"] = initial
        assert initial["field"] == "" and initial["guard"] == "Unrelated draft" and released(initial)
        computer = Computer(allow_global_input=True)
        if options.driver is not None:
            computer._controller.transport.driver = {"command": str(driver), "args": []}
        # Explicitly establish the owned foreground before desktop input begins.
        # This setup does not click, type, or use a hidden global-input workaround.
        prepared = await asyncio.to_thread(computer.app(bundle).read, view="tree", allow_foreground=True)
        report["setup_preparation"] = prepared.preparation
        await await_effect(folder, lambda value: value["frontmostPID"] == initial["pid"] and value["active"])
        current = await ready_state(folder, initial)
        observation = await asyncio.to_thread(computer.read, view="image")
        (folder / "desktop-before.txt").write_text(observation.text)
        assert observation.screenshot is not None
        (folder / "desktop-before.png").write_bytes(observation.screenshot)

        async def execute(name, operation, device, predicate, *, field=False):
            nonlocal observation
            before = await ready_state(folder, initial, field=field)
            row = {"name": name, "before": before}
            report["actions"].append(row)
            started = time.monotonic()
            try:
                receipt = await asyncio.to_thread(operation)
                row["receipt"] = dict(receipt)
                row["after"] = await await_effect(folder, predicate)
                require_global_impact(receipt, device)
                assert receipt.observation_status == "available" and receipt.observation is not None, receipt
                observation = receipt.observation
                assert observation.screenshot is not None, "Desktop input must retain usable desktop observation"
                (folder / f"{len(report['actions'])}-after.png").write_bytes(observation.screenshot)
                (folder / f"{len(report['actions'])}-after.txt").write_text(observation.text)
                await ready_state(folder, initial)
                return row["after"]
            except BaseException as error:
                row["error"] = failure(error)
                try:
                    row["last_fixture_state"] = await state(folder)
                    row["last_fixture_released"] = released(row["last_fixture_state"])
                except (OSError, TimeoutError, json.JSONDecodeError) as state_error:
                    row["fixture_state_error"] = str(state_error)
                raise
            finally:
                row["seconds"] = time.monotonic() - started

        points, evidence = desktop_points(observation, current, ("fieldPointQuartz",))
        report["field_mapping"] = evidence
        await execute(
            "desktop field click",
            lambda: computer.click(at=points["fieldPointQuartz"]),
            "mouse",
            lambda value: value["fieldFocused"] and value["field"] == "",
        )
        await execute(
            "desktop insert desk",
            lambda: computer.type_text("desk"),
            "keyboard",
            lambda value: value["field"] == "desk",
            field=True,
        )
        await execute(
            "desktop Left",
            lambda: computer.press_key("Left"),
            "keyboard",
            lambda value: value["selection"] == {"location": 3, "length": 0},
            field=True,
        )
        await execute(
            "desktop insert at moved caret",
            lambda: computer.type_text("X"),
            "keyboard",
            lambda value: value["field"] == "desXk",
            field=True,
        )
        submitted = await execute(
            "desktop Return",
            lambda: computer.press_key("Return"),
            "keyboard",
            lambda value: value["submissions"] == 1 and value["result"] == "Submitted: desXk",
            field=True,
        )
        for code in (123, 36):
            events = [event for event in submitted["keys"] if event["code"] == code]
            assert [event["type"] for event in events] == [10, 11] and all(
                event["window"] == initial["window"] for event in events
            ), events
        current = await ready_state(folder, initial)
        points, evidence = desktop_points(observation, current, ("scrollPointQuartz",))
        report["scroll_mapping"] = evidence
        await execute(
            "desktop wheel",
            lambda: computer.scroll("down", at=points["scrollPointQuartz"], pages=0.5),
            "mouse",
            lambda value: value["scrollY"] > current["scrollY"] + 10,
        )
        current = await ready_state(folder, initial)
        points, evidence = desktop_points(observation, current, ("dragStartQuartz", "dragEndQuartz"))
        report["drag_mapping"] = evidence
        final = await execute(
            "desktop marker drag",
            lambda: computer.drag(points["dragStartQuartz"], points["dragEndQuartz"]),
            "mouse",
            lambda value: (
                value["dragDowns"] == 1
                and value["dragMoves"] > 0
                and value["dragUps"] == 1
                and abs(value["marker"][0] - 240) < 3
                and abs(value["marker"][1] - 65) < 3
            ),
        )
        assert final["field"] == "desXk" and final["submissions"] == 1 and final["guard"] == initial["guard"]
        report["final"] = final
        report["passed"] = True
    except BaseException as error:
        report["failure"] = failure(error)
        raise
    finally:
        if computer is not None:
            try:
                await asyncio.to_thread(computer.close)
            except (OSError, RuntimeError) as error:
                report["transport_cleanup_error"] = str(error)
        report["cleanup"] = await cleanup_fixture(
            "owned-desktop-target", folder, NAME, initial["pid"] if initial else None
        )
        set_diagnostic_handler(None)
        journal.close()
        report["source_drift"] = [str(path) for path in sources if digest(path) != report["source_sha256"][str(path)]]
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-global-input", action="store_true", help="Use only after explicit user authorization")
    parser.add_argument("--driver", type=Path)
    options = parser.parse_args()
    if not options.allow_global_input:
        parser.error("No global input authorized: obtain explicit user approval before using --allow-global-input")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="desktop-public-input-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, options))
    finally:
        LOG.info("Opt-in desktop SDK evidence: %s", folder)


if __name__ == "__main__":
    main()
