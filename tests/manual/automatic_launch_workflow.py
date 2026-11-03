# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify public read of an installed, nonrunning owned app launches it once.

Run after the matching build: .venv/bin/python tests/manual/automatic_launch_workflow.py
The existing CommandIdentityProbe is built without open/Popen. A UUID-named link
under ~/Applications makes it discoverable by the normal installed-app catalogue.
Only public list_apps/app/read/element/click perform the tested workflow. Cleanup
terminates only the fixture's exact executable path and removes only its own link.
--build-only compiles/signs the bundle without installing the link or opening UI.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import plistlib
import subprocess
import tempfile
import uuid
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from command_identity_workflow import state
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from document_action_workflow import cleanup_fixture, owned_processes
from keyboard_window_command_workflow import build_owned

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
NAME = "CommandIdentityProbe"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(folder, identity):
    app = build_owned(folder, "local.computer-use.auto-launch." + identity, NAME)
    display_name = "CUA SDK Launch " + identity
    metadata = app / "Contents/Info.plist"
    info = plistlib.loads(metadata.read_bytes())
    info.update(CFBundleName=display_name, CFBundleDisplayName=display_name)
    metadata.write_bytes(plistlib.dumps(info))
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    return app, display_name


def input_calls(events):
    return [
        event["value"] for event in events
        if event.get("kind") == "backend_diagnostic" and isinstance(event.get("value"), dict)
        and event["value"].get("kind") == "input_activity" and event["value"].get("phase") == "willCall"
    ]


def launches(events):
    return [event for event in input_calls(events) if event.get("transport") == "launch"]


def error_record(error):
    if isinstance(error, ComputerUseError):
        return {name: getattr(error, name) for name in (
            "code", "effect", "target", "detail", "next", "impact", "observation", "observation_status",
            "observation_error", "window_lifecycle", "choices",
        )}
    return {"type": type(error).__name__, "detail": str(error)}


async def save_observation(folder, name, observation):
    (folder / (name + ".txt")).write_text(observation.text)
    if observation.screenshot is not None:
        (folder / (name + ".png")).write_bytes(observation.screenshot)


async def verify(folder, options):
    identity = uuid.uuid4().hex
    driver = Path(options.driver or native_driver()["command"]).resolve()
    sources = [
        Path(__file__).resolve(), ROOT / "tests/fixtures/CommandIdentityProbe.swift",
        ROOT / "tests/manual/keyboard_window_command_workflow.py",
        ROOT / "os_adapter/macos/Sources/Windowing/ApplicationCatalog.swift",
        ROOT / "os_adapter/macos/Sources/Control/NativeLaunchUnit.swift",
        ROOT / "computer_use_engine/interaction/read/app_read/pipeline.py",
    ]
    report = {
        "sdk": computer_use.__file__, "driver": str(driver), "driver_sha256": digest(driver),
        "source_sha256": {str(path): digest(path) for path in sources},
        "allow_global_input": False, "build_only": options.build_only, "passed": False,
    }
    computer, initial, link = None, None, None
    applications = Path.home() / "Applications"
    created_applications = False
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    events = []

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        app_path, display_name = await asyncio.to_thread(build, folder, identity)
        executable = app_path / "Contents/MacOS" / NAME
        report.update(bundle=str(app_path), display_name=display_name, executable=str(executable))
        assert not (folder / "state.json").exists()
        assert await asyncio.to_thread(owned_processes, executable) == [], "Builder unexpectedly launched the fixture"
        if options.build_only:
            report["build_completed_without_launch"] = True
            return

        if not applications.exists():
            applications.mkdir()
            created_applications = True
        link = applications / (display_name + ".app")
        link.symlink_to(app_path, target_is_directory=True)
        report["installation_link"] = str(link)
        computer = Computer(allow_global_input=False)
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        choices = await computer.list_apps()
        matching = [choice for choice in choices if choice.display_name == display_name]
        report["installation_choices"] = matching
        assert len(matching) == 1 and not matching[0].is_running, matching
        assert not input_calls(events), "Application discovery must not launch or send UI input"
        assert not (folder / "state.json").exists() and await asyncio.to_thread(owned_processes, executable) == []

        # This first public read is the only operation allowed to start the app.
        app = computer.app(matching[0].id)
        start = len(events)
        first = await app.read(view="both", full=True)
        await save_observation(folder, "first-read", first)
        initial = await state(folder)
        report["first_state"] = initial
        report["first_observation"] = first
        report["launch_calls"] = launches(events[start:])
        assert len(report["launch_calls"]) == 1, report["launch_calls"]
        assert "Launch:" in first.text and "Command identity probe" in first.text
        assert first.screenshot is not None and initial["count"] == 0
        assert await asyncio.to_thread(owned_processes, executable) == [initial["pid"]]
        button = await app.element([{"identifier": "command-control"}])

        start = len(events)
        repeated = await app.read(view="both")
        await save_observation(folder, "second-read", repeated)
        unchanged = await state(folder)
        assert unchanged["pid"] == initial["pid"] and unchanged["buttonObjectID"] == initial["buttonObjectID"]
        assert unchanged["count"] == 0 and not input_calls(events[start:]), "Repeated read must remain observational"
        report["second_read_state"] = unchanged
        report["click"] = dict(await button.click())
        after = await state(folder, lambda value: value["count"] == 1)
        report["after_click"] = after
        assert after["pid"] == initial["pid"] and after["buttonObjectID"] == initial["buttonObjectID"]
        assert after["otherClicks"] == 0 and len(launches(events)) == 1
        assert await asyncio.to_thread(owned_processes, executable) == [initial["pid"]]

        # Retiring a running instance must not turn its old App back into an
        # installation selector and silently start a new lifetime.
        report["retire_fixture"] = await cleanup_fixture("retire-owned-app", folder, NAME, initial["pid"])
        assert report["retire_fixture"]["outcome"] == "terminated", report["retire_fixture"]
        start = len(events)
        try:
            ended = await app.read(view="tree")
        except ComputerUseError as error:
            report["ended_read"] = error_record(error)
            assert error.code == "stale_target" and error.effect == "none", report["ended_read"]
        else:
            report["ended_observation"] = ended
            raise AssertionError("The ended App unexpectedly returned a live observation")
        assert not launches(events[start:]) and await asyncio.to_thread(owned_processes, executable) == []
        report["passed"] = True
    except BaseException as error:
        report["error"] = error_record(error)
        raise
    finally:
        if computer is not None:
            try:
                computer._controller.transport.close()
                await computer._controller.transport.wait_closed()
            except (OSError, RuntimeError) as error:
                report["transport_cleanup_error"] = str(error)
        report["cleanup"] = await cleanup_fixture("owned-app", folder, NAME, initial["pid"] if initial else None)
        if link is not None:
            if link.is_symlink() and link.readlink() == folder / (NAME + ".app"):
                link.unlink()
                report["installation_link_removed"] = True
            else:
                report["installation_link_removed"] = False
        if created_applications:
            try:
                applications.rmdir()  # Only the empty directory this invocation created.
                report["created_applications_directory_removed"] = True
            except OSError:
                report["created_applications_directory_removed"] = False
        set_diagnostic_handler(None)
        journal.close()
        report["source_drift"] = [str(path) for path in sources if digest(path) != report["source_sha256"][str(path)]]
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--build-only", action="store_true")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="automatic-launch-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, options))
    finally:
        LOG.info("Automatic launch SDK evidence: %s", folder)


if __name__ == "__main__":
    main()
