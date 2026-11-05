# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Real macOS input acceptance using disposable AppKit recipients.

The default command only prints help. Execute one short case explicitly:
  uv run python tests/manual/input_foundation_workflow.py --execute --case raw --output PATH

The script never targets business windows or submits a meeting. Fixture setup
may activate/reset/move only its own windows through a command file; it never
sends input. All measured input uses the public SDK. Effect checks read actual
application callbacks/first responder, not SDK dispatch acknowledgements.

The hosted-panel case needs --panel-point X,Y in the actual panel window's local
point space. This must be selected from its screenshot; the script neither
finds a search element nor sets AX focus before the coordinate click. After
input it reads the real panel's AX value as a separate effect observation.
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
import threading
import time
import uuid
from io import BytesIO
from pathlib import Path

import computer_use
from artifact_json import captured_interface_metadata, encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.client.execution import ExecutionCancellation, execution_scope
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import search_ref
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
NAME = "GlobalInputFoundationProbe"
LOG = logging.getLogger(__name__)
CASES = ("raw", "focus-change", "scoped", "opaque", "coexist", "image-target", "drag", "cancel", "panel")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode_artifact) + "\n")


def state(folder, predicate=lambda value: True, timeout=5):
    until, value = time.monotonic() + timeout, None
    while time.monotonic() < until:
        try:
            value = json.loads((folder / "state.json").read_text())
            if predicate(value):
                return value
        except FileNotFoundError:
            pass
        time.sleep(0.03)
    raise AssertionError(f"Fixture effect/state did not arrive: {value}")


def command(folder, kind, **parameters):
    token = uuid.uuid4().hex
    write_json(folder / "command.json", dict(id=token, kind=kind, **parameters))
    return state(folder, lambda value: value["lastCommand"] == token)


def released(value):
    return not value["keysHeld"] and not value["buttonsHeld"] and not value["modifierHeld"]


def process_keyboard_evidence(receipt, effects):
    """Coexistence checks only this sequence, never the user's global state."""
    def allowed(effect):
        return (
            effect.get("transport") == "process"
            and effect.get("device") == "keyboard"
            and effect.get("mechanism") == "CGEvent.postToPid"
        )

    assert effects and all(allowed(effect) for effect in effects), effects
    attempts = receipt["impact"]["attempts"]
    assert attempts, "Missing native effect coverage"
    for attempt in attempts:
        actual = attempt.get("actual")
        assert actual and actual.get("coverage") == "complete" and actual.get("omitted_effects") == 0, attempt
        assert actual.get("effects") and all(allowed(effect) for effect in actual["effects"]), actual
    keyboard = [effect for effect in effects if effect.get("device") == "keyboard"]
    assert keyboard, "No actual addressed keyboard events"
    return {
        "keyboard_events": len(keyboard),
        "local_activation_notifications": len(effects) - len(keyboard),
        "native_effect_coverage": "complete",
        "mouse_events": 0,
        "global_events": 0,
        "system_activation_events": 0,
    }


def coexist_sequence_evidence(expected, before, after, effects, traces, switch_state):
    """Use actual recipient events and paired dispatch, not global held keys."""
    original_pid = before["pid"]
    assert after["value"] == expected and after["result"] == "Matched: " + expected, after
    assert after["guard"] == "Unrelated draft" and after["foregroundPID"] != original_pid, after
    assert expected.startswith(switch_state["value"]) and 0 < len(switch_state["value"]) < len(expected), switch_state
    assert switch_state["foregroundPID"] != original_pid, switch_state
    events = [event for event in after["events"][len(before["events"]):] if event["type"] in (10, 11)]
    assert len(events) == len(expected) * 2, "Missing or duplicated actual key events"
    assert "".join(event.get("characters", "") for event in events[::2]) == expected, events
    for down, up in zip(events[::2], events[1::2], strict=True):
        assert (down["type"], up["type"]) == (10, 11), (down, up)
        # A Unicode up may expose the keycode's ordinary character rather than
        # the inserted scalar; keycode/window, not its text, identifies release.
        assert down["keyCode"] == up["keyCode"] and down["window"] == up["window"] == before["window"], (down, up)
        assert down["uptime"] <= up["uptime"], (down, up)
    assert len(effects) == len(expected) * 2, effects
    assert {effect.get("recipientPID") for effect in effects} == {original_pid}, effects
    activity_ids = {effect["activityId"] for effect in effects}
    assert len(activity_ids) == 1, "Text must be one native sequence, not a replay"
    own = [trace for trace in traces if trace.get("activityId") in activity_ids]
    calls = [trace for trace in own if trace.get("phase") == "willCall"]
    returns = [trace for trace in own if trace.get("phase") == "didReturn"]
    assert calls == effects and len(returns) == len(effects), "Native effects must all have return evidence"
    assert {trace["effectId"] for trace in calls} == {trace["effectId"] for trace in returns}, own
    ends = [trace for trace in own if trace.get("phase") == "end"]
    assert len(ends) == 1 and ends[0]["dispatch"] == "dispatched" and ends[0]["cleanup"] == "complete", ends
    assert ends[0]["pendingEffects"] == [], ends
    observed_foregrounds = set()
    for trace in own:
        if trace.get("uptime", 0) < switch_state["uptime"]:
            continue
        for key in ("before", "after", "desktop"):
            foreground = trace.get(key, {}).get("foregroundPID")
            if foreground is not None:
                observed_foregrounds.add(foreground)
                assert foreground != original_pid, "Original receiver was brought forward after user takeover"
    assert observed_foregrounds, "No after-switch foreground evidence"
    return {
        "key_down_count": len(expected), "key_up_count": len(expected), "receiver_pids": [original_pid],
        "native_effects_returned": len(returns), "own_sequence_cleanup": "complete",
        "foreground_pids_after_switch": sorted(observed_foregrounds),
    }


def build_fixture(folder, bundle):
    """Compile and launch this fixture only when the workflow is executed."""
    app = folder / (NAME + ".app")
    binary = app / "Contents/MacOS" / NAME
    binary.parent.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": bundle,
                "CFBundleExecutable": NAME,
                "CFBundleName": NAME,
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
            str(ROOT / "tests/fixtures" / (NAME + ".swift")),
            "-o",
            str(binary),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    subprocess.run(["open", "-g", str(app)], check=True)
    return state(folder)


def image_point(observation, current, name):
    """Map independent fixture geometry into the exact delivered PNG pixels."""
    with Image.open(BytesIO(observation.screenshot)) as image:
        width, height = image.size
    _, _, frame_width, frame_height = current["frameQuartz"]
    x, y = current[name]
    assert abs(width / frame_width - height / frame_height) < 0.02, (
        "Fixture image is not its single window; do not guess a composite-image point",
        image.size,
        current,
    )
    return (x * width / frame_width, y * height / frame_height)


class Workflow:
    def __init__(self, folder, options):
        self.folder, self.options = folder, options
        self.events, self.fixtures, self.computers = [], [], []
        self.journal = (folder / "diagnostics.jsonl").open("w")
        self.driver = Path(options.driver or native_driver()["command"]).resolve()
        source = Path(computer_use.__file__).resolve()
        if not source.is_relative_to(ROOT):
            raise RuntimeError(f"Wrong SDK checkout: {source}; expected {ROOT}")
        if self.driver != (ROOT / "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver"):
            raise RuntimeError("Acceptance requires this worktree's built macOS driver")
        self.report = {
            "passed": False,
            "case": options.case,
            "root": str(ROOT),
            "sdk": str(source),
            "driver": str(self.driver),
            "driver_sha256": digest(self.driver),
            "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "started": time.time(),
            "actions": [],
            "setup": [],
            "scope": "Owned fixture only. Tencent Meeting and the complete cross-platform matrix are separate.",
        }
        sources = [
            p
            for base in ("computer_use_engine", "os_adapter/macos/Sources")
            for p in (ROOT / base).rglob("*")
            if p.suffix in (".py", ".swift")
        ]
        sources += [Path(__file__).resolve(), ROOT / "tests/fixtures" / (NAME + ".swift")]
        write_json(folder / "source-manifest.json", {str(p.relative_to(ROOT)): digest(p) for p in sources})
        set_diagnostic_handler(self.diagnostic)

    def diagnostic(self, event):
        self.events.append(event)
        self.journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        self.journal.flush()

    def fixture(self, name):
        folder = self.folder / name
        folder.mkdir()
        bundle = "local.computer-use.foundation." + uuid.uuid4().hex
        initial = build_fixture(folder, bundle)
        self.fixtures.append((folder, initial["pid"]))
        self.report["setup"].append({"kind": "launch-owned-fixture", "folder": str(folder), "state": initial})
        return folder, bundle

    def computer(self, *, global_input=True):
        computer = Computer(allow_global_input=global_input)
        computer._controller.transport.driver = {"command": str(self.driver), "args": []}
        self.computers.append(computer)
        return computer

    def setup(self, folder, kind, **parameters):
        result = command(folder, kind, **parameters)
        if kind == "front":
            # LaunchServices grants foreground activation to this explicit
            # setup request. A background fixture's self-activate alone can be
            # ignored by macOS. This is outside every measured SDK action.
            subprocess.run(["open", "-a", str(folder / (NAME + ".app"))], check=True)
            result = state(folder, lambda value: value["foregroundPID"] == value["pid"])
        self.report["setup"].append({"folder": str(folder), "kind": kind, "parameters": parameters, "after": result})
        return result

    def observe(self, name, ui, *, view="image"):
        observation = ui.read(view=view)
        (self.folder / (name + ".txt")).write_text(observation.text)
        if observation.screenshot:
            (self.folder / (name + ".png")).write_bytes(observation.screenshot)
        self.report.setdefault("observations", []).append({"name": name, "view": view, "value": observation})
        return observation

    def action(
        self, name, operation, folder, predicate, *, device, global_input, stable_foreground=None, effect_pending=False,
        allow_external_desktop_activity=False,
    ):
        before, start, first = state(folder), time.monotonic(), len(self.events)
        row = {"name": name, "before": before, "started": time.time()}
        self.report["actions"].append(row)
        try:
            row["receipt"] = operation()
            row["sdk_seconds"] = time.monotonic() - start
            if allow_external_desktop_activity:
                assert device == "keyboard" and not global_input and self.options.case == "coexist"
            after = state(folder, lambda value: predicate(value) and (allow_external_desktop_activity or released(value)))
            # One later independent sample detects a cleanup that immediately
            # undoes the focus effect; it is not a retry of the operation.
            time.sleep(0.15)
            after = state(folder, lambda value: predicate(value) and (allow_external_desktop_activity or released(value)))
            row["after"] = after
            assert after["guard"] == "Unrelated draft", after
            if stable_foreground is not None:
                assert after["foregroundPID"] == stable_foreground, after
            effects = [
                event["value"]
                for event in self.events[first:]
                if event.get("kind") == "backend_diagnostic"
                and isinstance(event.get("value"), dict)
                and event["value"].get("kind") == "input_activity"
                and event["value"].get("phase") == "willCall"
            ]
            row["input_effects"] = effects
            commands = [event["method"] for event in self.events[first:] if event.get("kind") == "command_start"]
            row["native_commands"] = commands
            actual = [effect for effect in effects if effect.get("device") == device]
            assert actual, ("No native input activity for measured action", name, effects)
            globals_sent = [effect for effect in effects if effect.get("transport") == "globalHID"]
            if global_input:
                assert globals_sent and all(e["transport"] == "globalHID" for e in actual), effects
                assert not {"capture", "inspect", "prepareObservation"}.intersection(commands), commands
            else:
                assert not globals_sent, effects
                if allow_external_desktop_activity:
                    row["cursor_motion"] = {
                        "before": before["cursor"],
                        "after": after["cursor"],
                        "delta": [b - a for a, b in zip(before["cursor"], after["cursor"], strict=True)],
                        "attribution": "User desktop activity is independent; native coverage allows only process keys",
                        "evidence": process_keyboard_evidence(row["receipt"], effects),
                    }
                else:
                    assert after["cursor"] == before["cursor"], ("Addressed input moved real cursor", before, after)
            row["effect_pending"] = effect_pending
            LOG.info("%s %s", "DISPATCH; effect checked below:" if effect_pending else "PASS", name)
            return after
        except BaseException as error:
            row.setdefault("sdk_seconds", time.monotonic() - start)
            row["error"] = {"type": type(error).__name__, "detail": str(error)}
            row["last_fixture_state"] = state(folder)
            raise
        finally:
            row["seconds"] = time.monotonic() - start
            write_json(self.folder / "report.json", self.report)

    def raw(self, folder):
        initial = self.setup(folder, "front")
        state(folder, lambda item: item["foregroundPID"] == initial["pid"])
        computer = self.computer()
        # There has been no Computer/App read in this session.
        self.action(
            "raw Escape without any read",
            lambda: computer.press_key("Escape"),
            folder,
            lambda value: any(e.get("keyCode") == 53 and e["type"] == 10 for e in value["events"]),
            device="keyboard",
            global_input=True,
            stable_foreground=initial["pid"],
        )
        self.action(
            "raw screen click without read",
            lambda: computer.click(at=tuple(initial["searchPoint"])),
            folder,
            lambda value: value["searchFocused"],
            device="mouse",
            global_input=True,
            stable_foreground=initial["pid"],
        )
        self.action(
            "raw text without read",
            lambda: computer.type_text("raw-one"),
            folder,
            lambda value: (
                value["value"] == "raw-one" and value["result"] == "Matched: raw-one" and value["changes"] > 0
            ),
            device="keyboard",
            global_input=True,
        )
        self.action(
            "raw Select All",
            lambda: computer.press_key("super+a"),
            folder,
            lambda value: value["selection"] == [0, len("raw-one")],
            device="keyboard",
            global_input=True,
        )
        self.action(
            "raw text uses current selection",
            lambda: computer.type_text("真实 global"),
            folder,
            lambda value: value["value"] == "真实 global" and value["result"] == "Matched: 真实 global",
            device="keyboard",
            global_input=True,
        )
        self.action(
            "raw paste uses current focus",
            lambda: computer.paste("-paste"),
            folder,
            lambda value: value["value"] == "真实 global-paste" and value["result"] == "Matched: 真实 global-paste",
            device="keyboard",
            global_input=True,
        )
        self.action(
            "raw scroll without capture",
            lambda: computer.scroll("down", at=tuple(initial["opaquePoint"])),
            folder,
            lambda value: value["scrolls"] > 0,
            device="mouse",
            global_input=True,
        )
        return computer

    def peer(self, folder):
        peer, bundle = self.fixture("peer")
        original = state(folder)
        x, y, width, _ = original["frameQuartz"]
        self.setup(peer, "move", x=x + width + 30, y=y)
        self.setup(peer, "front")
        current = state(peer, lambda value: value["foregroundPID"] == value["pid"])
        return peer, bundle, current

    def coexist(self, folder, bundle):
        """Keep typing to the receiver while the user uses other applications."""
        self.setup(folder, "reset")
        peer, _, peer_initial = self.peer(folder)
        initial = self.setup(folder, "front")
        computer = self.computer(global_input=False)
        app = computer.app(bundle)
        image = self.observe("coexist-cold-image", app)
        point = image_point(image, state(folder), "searchImagePoint")
        self.action(
            "coexist cold image click establishes search receiver",
            lambda: app.click(at=point),
            folder,
            lambda value: value["searchFocused"],
            device="mouse",
            global_input=False,
            stable_foreground=initial["pid"],
        )
        # Ordinary SDK delivery pacing supplies the overlap; no product or
        # fixture input delays are introduced for this acceptance case.
        expected = "".join(f"segment{index:02d}_" for index in range(24))
        before = state(folder)
        switched, stop = threading.Event(), threading.Event()
        evidence = {
            "expected": expected,
            "expected_length": len(expected),
            "original_receiver_pid": initial["pid"],
            "peer_pid": peer_initial["pid"],
            "cursor_before": before["cursor"],
            "switch_mechanism": "LaunchServices activation of already-running owned peer; no injected input",
        }
        self.report["coexist"] = evidence

        def switch_after_real_text():
            try:
                while not stop.wait(0.003):
                    observed = state(folder)
                    if not observed["value"]:
                        continue
                    evidence.update(
                        first_text_observed_at=time.time(),
                        first_text_fixture_epoch=observed["epoch"],
                        first_text_value=observed["value"],
                    )
                    assert expected.startswith(observed["value"]) and len(observed["value"]) < len(expected), (
                        "No in-flight text overlap before foreground switch", observed
                    )
                    evidence["switch_requested_at"] = time.time()
                    peer_after = self.setup(peer, "front")
                    during = state(folder)
                    evidence.update(
                        switch_confirmed_at=time.time(),
                        peer_at_switch=peer_after,
                        main_at_switch=during,
                    )
                    assert len(during["value"]) < len(expected), "Foreground switch occurred after all input finished"
                    switched.set()
                    return
            except (AssertionError, OSError, ValueError, subprocess.CalledProcessError) as error:
                evidence["switch_error"] = {"type": type(error).__name__, "detail": str(error)}

        observer = threading.Thread(target=switch_after_real_text, daemon=True)
        observer.start()
        try:
            after = self.action(
                "one App text call survives external foreground change",
                lambda: app.type_text(expected),
                folder,
                lambda value: value["value"] == expected and value["result"] == "Matched: " + expected,
                device="keyboard",
                global_input=False,
                allow_external_desktop_activity=True,
            )
        finally:
            stop.set()
            observer.join(timeout=6)
            evidence["observer_finished"] = not observer.is_alive()
        assert not observer.is_alive() and switched.is_set() and "switch_error" not in evidence, evidence
        row = self.report["actions"][-1]
        traces = [event["value"] for event in self.events if event.get("kind") == "backend_diagnostic"
                  and isinstance(event.get("value"), dict) and event["value"].get("kind") == "input_activity"]
        sequence = coexist_sequence_evidence(
            expected, before, after, row["input_effects"], traces, evidence["main_at_switch"])
        final_peer = state(peer)
        assert final_peer["foregroundPID"] != initial["pid"], final_peer
        evidence.update(
            **sequence, final_peer=final_peer, final_main=after, cursor_motion=row["cursor_motion"],
            sdk_seconds=row["sdk_seconds"], passed=True,
            external_desktop_state={
                "foregroundPID": final_peer["foregroundPID"], "keysHeld": final_peer["keysHeld"],
                "buttonsHeld": final_peer["buttonsHeld"], "modifierHeld": final_peer["modifierHeld"],
                "peer_events": len(final_peer["events"]) - len(peer_initial["events"]),
                "peer_value_changed": final_peer["value"] != peer_initial["value"],
                "attribution": "Observed user state, not owned SDK input; this sequence only targets the original receiver",
            },
        )
        LOG.info("PASS foreground changed after %d/%d characters; remaining input stayed on original receiver",
                 len(evidence["main_at_switch"]["value"]), len(expected))

    def run(self):
        folder, bundle = self.fixture("main")
        case = self.options.case
        if case in ("raw", "focus-change"):
            computer = self.raw(folder)
            if case == "focus-change":
                self.observe("before-new-process", computer)
                peer, _, current = self.peer(folder)
                self.action(
                    "global click new process absent from old desktop",
                    lambda: computer.click(at=tuple(current["searchPoint"])),
                    peer,
                    lambda value: value["searchFocused"],
                    device="mouse",
                    global_input=True,
                    stable_foreground=current["pid"],
                )
                self.action(
                    "global key follows current process without reread",
                    lambda: computer.type_text("new-process"),
                    peer,
                    lambda value: value["value"] == "new-process" and value["result"] == "Matched: new-process",
                    device="keyboard",
                    global_input=True,
                    stable_foreground=current["pid"],
                )
                original = self.setup(folder, "front")
                state(folder, lambda value: value["foregroundPID"] == original["pid"])
                self.action(
                    "global key follows changed focus without mouse or reread",
                    lambda: computer.type_text("-returned"),
                    folder,
                    lambda value: (
                        value["value"] == "真实 global-paste-returned"
                        and value["result"] == "Matched: 真实 global-paste-returned"
                    ),
                    device="keyboard",
                    global_input=True,
                    stable_foreground=original["pid"],
                )
        elif case == "coexist":
            self.coexist(folder, bundle)
        elif case in ("scoped", "opaque"):
            self.setup(folder, "reset", coarseAX=case == "opaque")
            _, _, foreground = self.peer(folder)
            computer = self.computer(global_input=False)
            app = computer.app(bundle)
            observation = self.observe("cold-image", app)
            current = state(folder)
            name = "opaque" if case == "opaque" else "search"
            point = image_point(observation, current, name + "ImagePoint")
            self.report["coordinate_oracle"] = {
                "source": "owned fixture geometry, scaled to delivered PNG",
                "point": point,
                "no_AX_focus_or_element_preparation": True,
            }
            self.action(
                "cold image addressed click",
                lambda: app.click(at=point),
                folder,
                lambda value: value[name + "Focused"],
                device="mouse",
                global_input=False,
                stable_foreground=foreground["pid"],
            )
            expected = "opaque-input" if case == "opaque" else "directed-input"
            self.action(
                "App keyboard after addressed image click",
                lambda: app.type_text(expected),
                folder,
                lambda value: (
                    value["opaqueValue"] == expected and value["opaqueInputs"] > 0
                    if case == "opaque"
                    else value["value"] == expected and value["result"] == "Matched: " + expected
                ),
                device="keyboard",
                global_input=False,
                stable_foreground=foreground["pid"],
            )
            self.observe("after-addressed-chain", app, view="both")
        elif case == "image-target":
            initial = self.setup(folder, "front")
            computer = self.computer()
            app = computer.app(bundle)
            observation = self.observe("point-source", app)
            target = app.point(image_point(observation, initial, "searchImagePoint"))
            x, y, _, _ = initial["frameQuartz"]
            moved = self.setup(folder, "move", x=x + 70, y=y + 40)
            self.action(
                "image Target maps once after window translation",
                lambda: computer.click(target=target),
                folder,
                lambda value: value["searchFocused"],
                device="mouse",
                global_input=True,
                stable_foreground=initial["pid"],
            )
            after = state(folder)
            assert all(abs(a - b) < 2 for a, b in zip(after["cursor"], moved["searchPoint"], strict=True)), after
            self.action(
                "raw keyboard after image Target",
                lambda: computer.type_text("mapped-image"),
                folder,
                lambda value: value["value"] == "mapped-image" and value["result"] == "Matched: mapped-image",
                device="keyboard",
                global_input=True,
            )
        elif case in ("drag", "cancel"):
            peer, _, destination = self.peer(folder)
            initial = self.setup(folder, "front")
            computer = self.computer()
            start, end = tuple(initial["dragPoint"]), tuple(destination["opaquePoint"])
            if case == "drag":
                self.action(
                    "global drag crosses a window boundary without capture",
                    lambda: computer.drag(start, end),
                    folder,
                    lambda value: value["dragDowns"] == 1 and value["dragUps"] == 1 and value["dragMoves"] > 0,
                    device="mouse",
                    global_input=True,
                )
                after = state(folder)
                assert all(abs(a - b) < 2 for a, b in zip(after["dragLastScreenPoint"], end, strict=True)), after
                assert state(peer)["opaqueClicks"] == 0, "Drag release must retain original mouse capture"
            else:
                stop, down = threading.Event(), threading.Event()
                cancellation, cancellation_evidence = ExecutionCancellation(), {}

                def cancel_after_actual_down():
                    while not stop.wait(0.003):
                        observed = state(folder)
                        if observed["dragDowns"] > 0:
                            cancellation_evidence.update(
                                downObservedAt=time.time(), fixtureEpoch=observed["epoch"],
                                dragDowns=observed["dragDowns"], dragUps=observed["dragUps"],
                            )
                            down.set()
                            cancellation_evidence["cancelRequestedAt"] = time.time()
                            cancellation.cancel()
                            return

                observer = threading.Thread(target=cancel_after_actual_down, daemon=True)
                observer.start()
                started = time.monotonic()
                row = {
                    "name": "cancel a globally dispatched drag", "before": initial, "started": time.time(),
                    "cancellation": cancellation_evidence,
                }
                self.report["actions"].append(row)
                try:
                    # Use the same active task cancellation channel as MCP.
                    # A budget predicate alone cannot interrupt an in-flight
                    # native await; it is only checked before subsequent work.
                    with execution_scope(computer._executor, cancellation):
                        computer.drag(start, end)
                except (ComputerUseError, asyncio.CancelledError) as error:
                    row["error"] = {"type": type(error).__name__, "detail": str(error)}
                    assert isinstance(error, asyncio.CancelledError) or "cancel" in str(error).lower(), row
                else:
                    raise AssertionError("Cancellation did not intercept the gesture; no cancellation coverage claimed")
                finally:
                    stop.set()
                    observer.join(timeout=1)
                    row["seconds"] = time.monotonic() - started
                    row["after"] = state(folder, lambda value: released(value) and value["dragUps"] == 1)
                assert down.is_set() and row["after"]["dragDowns"] == 1, row
                LOG.info("PASS cancel after actual mouse-down, paired mouse-up observed")
        elif case == "panel":
            if self.options.panel_point is None:
                raise ValueError("--case panel requires --panel-point X,Y selected from the actual panel screenshot")
            computer = self.computer(global_input=False)
            app = computer.app(bundle)
            observation = self.observe("before-panel", app)
            point = image_point(observation, state(folder), "openImagePoint")
            self.action(
                "addressed coordinate opens real NSOpenPanel",
                lambda: app.click(at=point),
                folder,
                lambda value: value.get("panel", {}).get("visible") is True,
                device="mouse",
                global_input=False,
            )
            current = self.observe("panel-directory", app)
            catalogue = captured_interface_metadata(current)
            panel = app.window(catalogue["current"])
            image = self.observe("panel-cold-image", panel)
            panel_state = state(folder)["panel"]
            proxy = {"frameQuartz": panel_state["frameQuartz"], "point": self.options.panel_point}
            point = image_point(image, proxy, "point")
            self.report["panel_coordinate"] = {"windowLocalPoint": self.options.panel_point, "imagePoint": point}
            self.action(
                "cold panel coordinate click",
                lambda: panel.click(at=point),
                folder,
                lambda value: value.get("panel", {}).get("visible") is True,
                device="mouse",
                global_input=False,
                effect_pending=True,
            )
            expected = "cua-foundation-remote"
            self.action(
                "window keyboard follows panel coordinate click",
                lambda: panel.type_text(expected),
                folder,
                lambda value: value.get("panel", {}).get("visible") is True,
                device="keyboard",
                global_input=False,
                effect_pending=True,
            )
            # Only now read AX: this observes the consequence and does not prepare
            # the click or the text. A dispatched receipt alone cannot pass.
            after = self.observe("panel-after-input", panel, view="both")
            field = panel.element(search_ref(after.text))
            detail = field.read().text
            (self.folder / "panel-search-after.txt").write_text(detail)
            assert "Value: " + expected in detail, detail
            self.report["panel_window_effect"] = {"search_value": expected, "detail": detail}
            LOG.info("PASS cold panel click → Window keyboard: actual search value matched")
            self.action(
                "App keyboard continues hosted focus",
                lambda: app.type_text("-app"),
                folder,
                lambda value: value.get("panel", {}).get("visible") is True,
                device="keyboard",
                global_input=False,
                effect_pending=True,
            )
            detail = field.read().text
            (self.folder / "panel-search-after-app.txt").write_text(detail)
            assert "Value: " + expected + "-app" in detail, detail
            self.report["panel_app_effect"] = {"search_value": expected + "-app", "detail": detail}
            LOG.info("PASS App keyboard continues hosted focus: actual search value matched")
            self.setup(folder, "closePanel")
        self.report["passed"] = True

    def close(self):
        for computer in reversed(self.computers):
            try:
                computer.close()
            except (ComputerUseError, OSError, RuntimeError, asyncio.CancelledError) as error:
                self.report.setdefault("cleanup_errors", []).append(str(error))
        for folder, pid in reversed(self.fixtures):
            try:
                self.report.setdefault("final_fixture_states", []).append(state(folder))
            except (AssertionError, OSError) as error:
                self.report.setdefault("cleanup_errors", []).append(str(error))
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        self.report["finished"] = time.time()
        write_json(self.folder / "report.json", self.report)
        set_diagnostic_handler(None)
        self.journal.close()


def parse_point(value):
    numbers = tuple(float(part) for part in value.split(","))
    if len(numbers) != 2:
        raise argparse.ArgumentTypeError("expected X,Y")
    return numbers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Run owned-window input; never implied by help/build")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--panel-point", type=parse_point)
    options = parser.parse_args()
    if not options.execute:
        parser.print_help()
        return
    if options.case is None or options.output is None:
        parser.error("--execute requires --case and a new --output directory")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    options.output.mkdir(parents=True)
    workflow = Workflow(options.output.resolve(), options)
    try:
        workflow.run()
    finally:
        workflow.close()


if __name__ == "__main__":
    main()
