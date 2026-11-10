# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run one fresh native SaveProbe via the SDK; compare key and menu separately.

Build SaveProbe.swift into a disposable .app before running. Launch this harness
from a permission-authorized terminal. Never rerun a write within an episode.
"""

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path
from time import monotonic

from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.session.events import set_action_handler, set_observation_handler
from computer_use_native_tools.evaluation.ledger import Check, Episode
from computer_use_native_tools.evaluation.provenance import digest, runtime_snapshot


async def stop_fixture(fixture):
    (fixture / "stop").touch()
    deadline = monotonic() + 2
    while monotonic() < deadline:
        event_log = fixture / "events.jsonl"
        if event_log.exists() and '"kind":"exit"' in event_log.read_text():
            return True
        await asyncio.sleep(0.1)
    return False


async def run(bundle, output, route, target="responder", initial_focus="background", fault="none"):
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
    computer = Computer()
    set_diagnostic_handler(episode.diagnostic)
    set_action_handler(episode.action)
    set_observation_handler(episode.observation)
    reset_duration = 0
    result = Check("UNKNOWN", str(fixture), {})
    try:
        start = monotonic()
        try:
            await computer.list_apps()
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
            )
            deadline = monotonic() + 15
            while monotonic() < deadline:
                matches = [a for a in await computer.list_apps() if episode.id in a.display_name]
                if len(matches) == 1:
                    app = computer.app(matches[0].id)
                    break
                await asyncio.sleep(0.2)
            else:
                raise RuntimeError("Fixture window not uniquely discovered")
        finally:
            reset_duration = monotonic() - start
        with episode.step("observe"):
            await app.read(view="tree")
            editor = await app.element([{"identifier": "q4-editor"}])
            if (await editor.read_text()).text != "Q4 initial probe":
                raise RuntimeError("Wrong initial contents")
        if fault == "replace-target":
            with episode.step("replace_native_instance"):
                if not await stop_fixture(fixture):
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
                    await asyncio.sleep(0.1)
            with episode.step("write_using_destroyed_target"):
                try:
                    await editor.type_text("MUST NOT REACH REPLACEMENT", replace=True)
                except ComputerUseError as error:
                    episode.record("expected_stale_target", code=error.code, effect=error.effect)
                    if error.code != "stale_target" or error.effect != "none":
                        raise
                else:
                    raise RuntimeError("Destroyed target unexpectedly accepted input")
            with episode.step("independent_replacement_check"):
                entries = [a for a in await computer.list_apps() if episode.id in a.display_name]
                if len(entries) != 1:
                    raise RuntimeError("Replacement fixture not uniquely observed")
                replacement_app = computer.app(entries[0].id)
                await replacement_app.read(view="tree")
                replacement_editor = await replacement_app.element([{"identifier": "q4-editor"}])
                content = await replacement_editor.read_text()
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
            result = await replace_and_save(episode, app, editor, fixture, route)
    except BaseException as error:
        episode.failed(error)
    finally:
        start = monotonic()
        exited = [await stop_fixture(item) for item in fixtures]
        computer._controller.transport.close()
        set_diagnostic_handler(None)
        set_action_handler(None)
        set_observation_handler(None)
        cleanup = {
            "status": "fixture_exit_recorded" if all(exited) else "owned_fixture_stop_requested",
            "duration_s": monotonic() - start,
        }
    return episode.finish(result, reset_duration_s=reset_duration, cleanup=cleanup)


async def replace_and_save(episode, app, editor, fixture, route):
    with episode.step("replace"):
        await editor.type_text("Q4 replaced probe", replace=True)
        if (await editor.read_text()).text != "Q4 replaced probe":
            raise RuntimeError("Replacement not observed")
    with episode.step("save"):
        if route == "key":
            await editor.press_key("super+s")
        else:
            await app.read(view="tree", menu_path=["File"])
            save = await app.element("Save")
            episode.require_log()
            await save.click()
    with episode.step("independent_acceptance"):
        deadline = monotonic() + 3
        while not (fixture / "saved.txt").exists() and monotonic() < deadline:
            await asyncio.sleep(0.1)
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
    result = asyncio.run(
        run(args.bundle.resolve(), args.output.resolve(), args.route, args.target, args.initial_focus, args.fault)
    )
    print(json.dumps({k: result[k] for k in ("episode_id", "outcome", "acceptance", "journal")}, ensure_ascii=False))  # noqa: T201
