# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise scoped input and retained global targets in one owned AppKit window.

This workflow intentionally uses global mouse/keyboard input and moves its own
window. Run when desktop input is authorized. It never restores the cursor or
operates business documents. The fixture records application callbacks, a
separate guard editor, cursor and foreground independently of SDK receipts.
"""

import argparse
import hashlib
import json
import logging
import os
import signal
import subprocess
import time
import uuid
from io import BytesIO
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def state(folder, predicate=lambda value: True):
    for _ in range(100):
        try:
            value = json.loads((folder / "state.json").read_text())
            if predicate(value):
                return value
        except FileNotFoundError:
            pass
        time.sleep(0.05)
    raise AssertionError("Fixture state did not settle")


def verify(folder):
    fixture = folder / "fixture"
    fixture.mkdir(parents=True)
    bundle = "local.computer-use.explicit-global." + uuid.uuid4().hex
    driver = Path(native_driver()["command"])
    report = {
        "passed": False,
        "sdk": computer_use.__file__,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "actions": [],
    }
    sources = [
        path
        for base in ("computer_use_engine", "os_adapter/macos/Sources")
        for path in (ROOT / base).rglob("*")
        if path.suffix in (".py", ".swift")
    ]
    (folder / "source-manifest.json").write_text(
        json.dumps(
            {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}, indent=2
        )
    )
    events = []
    owned_pid = None
    with (folder / "diagnostics.jsonl").open("w") as journal:

        def record(event):
            events.append(event)
            journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        set_diagnostic_handler(record)
        try:
            build_fixture(fixture, bundle)
            owned_pid = state(fixture)["pid"]
            with Computer() as computer:
                app = computer.app(bundle)
                observation = app.read(view="both", full=True)
                (folder / "initial.txt").write_text(observation.text)
                (folder / "initial.png").write_bytes(observation.screenshot)
                field = app.element([{"identifier": "live-search"}])
                target = field.target

                def action(name, operation, *, global_device=None, value=None):
                    before = state(fixture)
                    first, started = len(events), time.monotonic()
                    row = {"name": name, "before": before}
                    report["actions"].append(row)
                    result = operation()
                    row["seconds"] = time.monotonic() - started
                    row["result"] = result
                    after = state(fixture, lambda item: value is None or item["value"] == value)
                    row["after"] = after
                    row["cursor_changed"] = (after["mouseX"], after["mouseY"]) != (before["mouseX"], before["mouseY"])
                    effects = [
                        event["value"]
                        for event in events[first:]
                        if event.get("kind") == "backend_diagnostic"
                        and isinstance(event.get("value"), dict)
                        and event["value"].get("kind") == "input_activity"
                        and event["value"].get("phase") == "willCall"
                    ]
                    row["input_effects"] = effects
                    assert after["other"] == "unchanged", after
                    if value is not None:
                        assert after["result"] == "Matched: " + value, after
                        assert after["changes"] > before["changes"], after
                    global_effects = [effect for effect in effects if effect["transport"] == "globalHID"]
                    if global_device is None:
                        assert not global_effects, effects
                    else:
                        assert global_effects and all(effect["device"] == global_device for effect in global_effects)
                    LOG.info("PASS %s", name)

                action("scoped replacement", lambda: field.type_text("scoped", replace=True), value="scoped")
                action(
                    "retained global replacement",
                    lambda: computer.type_text("全局 Target", target=target, replace=True),
                    global_device="keyboard",
                    value="全局 Target",
                )
                action(
                    "retained global paste",
                    lambda: computer.paste("!", target=target),
                    global_device="keyboard",
                    value="全局 Target!",
                )
                action(
                    "scoped input after global input",
                    lambda: field.type_text("scoped again", replace=True),
                    value="scoped again",
                )
                action("retained global element click", lambda: computer.click(target=target), global_device="mouse")

                source = app.read(view="image")
                (folder / "point-source.png").write_bytes(source.screenshot)
                # This owned fixture has a fixed 480x242 screenshot. These are
                # observed fixture locations, not coordinates for another app.
                assert Image.open(BytesIO(source.screenshot)).size == (480, 242)
                point = app.point((220, 82))
                drag_start, drag_end = app.point((200, 15)), app.point((260, 15))
                app.read(view="image")  # The original Target must retain its own map.
                action("scoped retained point after read", lambda: app.click(at=point))
                before_drag = state(fixture)
                action("global titlebar drag", lambda: computer.drag(drag_start, drag_end), global_device="mouse")
                after_drag = state(fixture, lambda item: item["windowX"] != before_drag["windowX"])
                assert abs(after_drag["windowX"] - before_drag["windowX"] - 60) < 2, after_drag
                action(
                    "global retained point after window move",
                    lambda: computer.click(target=point),
                    global_device="mouse",
                )
                after_point = state(fixture)
                assert abs(after_point["mouseX"] - after_drag["windowX"] - 220) < 2, after_point
                action("global element key", lambda: computer.press_key("End", target=field), global_device="keyboard")

            with Computer(allow_global_input=False) as restricted:
                app = restricted.app(bundle)
                app.read()
                button = app.element([{"identifier": "live-search"}])
                try:
                    restricted.click(target=button)
                except ComputerUseError as error:
                    assert error.code == "permission_required" and error.effect == "none", error
                    report["restricted_global_input"] = error.code
                else:
                    raise AssertionError("Restricted session injected global input")
                button.type_text("restricted scoped", replace=True)
                state(fixture, lambda item: item["value"] == "restricted scoped")
            report["passed"] = True
        finally:
            (folder / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact)
            )
            if owned_pid is not None:
                os.kill(owned_pid, signal.SIGTERM)
            set_diagnostic_handler(None)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args.output.mkdir(parents=True)
    verify(args.output)
