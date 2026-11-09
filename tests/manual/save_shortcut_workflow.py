# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run one fresh native SaveProbe via the SDK; compare key and menu separately.

Build SaveProbe.swift into a disposable .app before running. Launch this harness
from a permission-authorized terminal. Never rerun a write within an episode.
"""

import argparse
import json
import os
import subprocess
from pathlib import Path
from time import monotonic, sleep

from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.session.events import set_action_handler, set_observation_handler
from computer_use_native_tools.evaluation.ledger import Check, Episode
from computer_use_native_tools.evaluation.provenance import digest, runtime_snapshot


def stop_fixture(fixture):
    (fixture / "stop").touch()
    deadline = monotonic() + 2
    while monotonic() < deadline:
        event_log = fixture / "events.jsonl"
        if event_log.exists() and '"kind":"exit"' in event_log.read_text():
            return True
        sleep(0.1)
    return False


def fixture_window(computer, episode_id):
    title = f"Q4 Save Probe {episode_id}"
    deadline = monotonic() + 15
    while monotonic() < deadline:
        catalogue = computer.list_windows(app_name="Q4 Save Probe")
        matches = [window for window in catalogue["windows"] if window["title"] == title]
        if len(matches) == 1 and not catalogue["stop_reasons"]:
            return computer.app(matches[0]["app_id"]), matches[0]["window_ref"]
        sleep(0.2)
    raise RuntimeError("Fixture window not uniquely discovered")


def run(bundle, output, route, target="responder", initial_focus="background", fault="none"):
    episode = Episode(
        output,
        {
            "task": "native-save-probe",
            "route": route,
            "menu_target": target,
            "fault": fault,
            "initial_focus": initial_focus,
            "desktop_mode": os.environ.get("COMPUTER_USE_DESKTOP", "shared"),
            "foreground_assist": os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST") == "1",
            "demo_foreground": os.environ.get("COMPUTER_USE_DEMO_FOREGROUND") == "1",
            "split": "development",
            "runtime": runtime_snapshot(Path(__file__).resolve().parents[2]),
            "fixture_version": "1.0",
            "fixture_binary_sha256": digest((bundle / "Contents/MacOS/SaveProbe").read_bytes()),
            "model": {"kind": "not_used", "calls": 0},
            "program": None,
        },
    )
    fixture = episode.directory / "fixture"
    fixture.mkdir()
    fixtures = [fixture]
    computer = None
    set_diagnostic_handler(episode.diagnostic)
    set_action_handler(episode.action)
    set_observation_handler(episode.observation)
    reset_duration = 0
    result = Check("UNKNOWN", str(fixture), {})
    try:
        start = monotonic()
        try:
            computer = Computer(allow_global_input=False)
            subprocess.run(
                [
                    "open",
                    "-n",
                    *(["-g"] if initial_focus == "background" else []),
                    str(bundle),
                    "--args",
                    str(fixture),
                    target,
                ],
                check=True,
                timeout=10,
            )
            app, window = fixture_window(computer, episode.id)
        finally:
            reset_duration = monotonic() - start
        with episode.step("observe"):
            app.read(view="tree", window=window)
            editor = app.element([{"identifier": "q4-editor"}], window=window)
            if editor.read_text().text != "Q4 initial probe":
                raise RuntimeError("Wrong initial contents")
        if fault == "replace-target":
            with episode.step("replace_native_instance"):
                if not stop_fixture(fixture):
                    raise RuntimeError("Original fixture did not acknowledge exit")
                replacement = episode.directory / "replacement"
                replacement.mkdir()
                fixtures.append(replacement)
                # Same visible title, independent process/window identity.
                subprocess.run(
                    ["open", "-n", "-g", str(bundle), "--args", str(replacement), target], check=True, timeout=10
                )
                deadline = monotonic() + 10
                while not (replacement / "events.jsonl").exists():
                    if monotonic() >= deadline:
                        raise RuntimeError("Replacement fixture did not start")
                    sleep(0.1)
            with episode.step("write_using_destroyed_target"):
                try:
                    editor.type_text("MUST NOT REACH REPLACEMENT", replace=True)
                except ComputerUseError as error:
                    episode.record("expected_stale_target", code=error.code, effect=error.effect)
                    if error.code != "stale_target" or error.effect != "none":
                        raise
                else:
                    raise RuntimeError("Destroyed target unexpectedly accepted input")
            with episode.step("independent_replacement_check"):
                replacement_app, replacement_window = fixture_window(computer, episode.id)
                replacement_app.read(view="tree", window=replacement_window)
                replacement_editor = replacement_app.element([{"identifier": "q4-editor"}], window=replacement_window)
                content = replacement_editor.read_text()
                events = [json.loads(line) for line in (replacement / "events.jsonl").read_text().splitlines()]
                untouched = (
                    content.next is None
                    and content.text == "Q4 initial probe"
                    and not any(e["kind"] in ("key", "save") for e in events)
                )
                result = Check(
                    "TRUE" if untouched else "FALSE",
                    str(replacement),
                    {"content_sha256": digest(content.text.encode()), "events": events},
                )
        else:
            result = replace_and_save(episode, app, editor, fixture, route, window=window)
    except BaseException as error:
        episode.failed(error)
    finally:
        start = monotonic()
        exited = []
        try:
            exited = [stop_fixture(item) for item in fixtures]
        except OSError as error:
            episode.failed(error)
        finally:
            try:
                if computer is not None:
                    computer.close()
            except Exception as error:  # noqa: BLE001 - Close failure remains part of the episode.
                episode.failed(error)
            finally:
                set_diagnostic_handler(None)
                set_action_handler(None)
                set_observation_handler(None)
        cleanup = {
            "status": "fixture_exit_recorded" if exited and all(exited) else "fixture_exit_unconfirmed",
            "duration_s": monotonic() - start,
        }
    return episode.finish(result, reset_duration_s=reset_duration, cleanup=cleanup)


def replace_and_save(episode, app, editor, fixture, route, *, window=None):
    with episode.step("replace"):
        editor.type_text("Q4 replaced probe", replace=True)
        if editor.read_text().text != "Q4 replaced probe":
            raise RuntimeError("Replacement not observed")
    with episode.step("save"):
        if route == "key":
            editor.press_key("super+s")
        else:
            app.read(view="tree", menu_path=["File"], window=window)
            save = app.element("Save", window=window)
            episode.require_log()
            save.click()
    with episode.step("independent_acceptance"):
        deadline = monotonic() + 3
        while not (fixture / "saved.txt").exists() and monotonic() < deadline:
            sleep(0.1)
        actual = (fixture / "saved.txt").read_bytes() if (fixture / "saved.txt").exists() else None
        events = [json.loads(line) for line in (fixture / "events.jsonl").read_text().splitlines()]
        result = Check(
            "TRUE" if actual == b"Q4 replaced probe" else "FALSE",
            str(fixture),
            {
                "saved_sha256": digest(actual) if actual else None,
                "saves": sum(e["kind"] == "save" for e in events),
                "events": events,
            },
        )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--route", choices=("key", "menu"), required=True)
    parser.add_argument("--target", choices=("responder", "explicit"), default="responder")
    parser.add_argument("--initial-focus", choices=("background", "foreground"), default="background")
    parser.add_argument("--fault", choices=("none", "replace-target"), default="none")
    args = parser.parse_args()
    result = run(args.bundle.resolve(), args.output.resolve(), args.route, args.target, args.initial_focus, args.fault)
    print(json.dumps({k: result[k] for k in ("episode_id", "outcome", "acceptance", "journal")}, ensure_ascii=False))  # noqa: T201
