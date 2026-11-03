# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public SDK insertion, replacement and Return against a real WKWebView form.

DOM events and separate draft/committed/saved models are the effect oracle.
The same original Element is retained; no helper click, focus, JS input or retry
is inserted. --build-only compiles without starting any UI. Global input is
disabled unless the operator explicitly passes --allow-global-input.
"""

import argparse
import asyncio
import json
import logging
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from keyboard_window_command_workflow import build_owned
from qt_cocoa_activation_workflow import ROOT, save_observation, sha, state

LOG = logging.getLogger(__name__)
NAME = "WKWebTextProbe"
INSERTED = "draft seed"
REPLACED = "提交🙂 final query"


def assert_original_document(current, initial):
    page, original = current["page"], initial["page"]
    assert current["pid"] == initial["pid"] and current["documentID"] == initial["documentID"], current
    assert page["documentID"] == page["documentMarker"] == original["documentID"], page
    assert page["url"] == original["url"], page
    assert page["guard"] == "unchanged" and page["guardEvents"] == [], page


def input_effect(current, before, expected):
    page, old = current["page"], before["page"]
    return (
        page["value"] == page["draftModel"] == expected
        and len(page["inputEvents"]) > len(old["inputEvents"])
        and page["inputEvents"][-1]["value"] == expected
    )


async def verify(folder, app_path, *, allow_global_input=False, foreground_diagnostic=False):
    title = "CU Web Text " + uuid4().hex[:12]
    driver = Path(native_driver()["command"])
    binary = app_path / "Contents/MacOS" / NAME
    manifest = {
        str(path.relative_to(ROOT)): sha(path)
        for directory, pattern in (("computer_use_engine", "*.py"), ("os_adapter/macos/Sources", "*.swift"))
        for path in sorted((ROOT / directory).rglob(pattern))
    }
    for relative in (
        "tests/fixtures/WKWebTextProbe.swift",
        "tests/manual/web_text_workflow.py",
        "tests/manual/keyboard_window_command_workflow.py",
        "tests/manual/qt_cocoa_activation_workflow.py",
    ):
        manifest[relative] = sha(ROOT / relative)
    (folder / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    report = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sdk": computer_use.__file__,
        "sdk_python": sys.executable,
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "fixture_sha256": sha(binary),
        "allow_global_input": allow_global_input,
        "window_title": title,
        "diagnostic_only": foreground_diagnostic,
        "foreground_diagnostic": foreground_diagnostic,
        "commands": [],
        "passed": False,
        "outcome": "incomplete",
    }
    computer = Computer(allow_global_input=allow_global_input)
    child = None
    events = []
    with (folder / "fixture-stderr.log").open("w") as errors, (folder / "diagnostics.jsonl").open("w") as journal:

        def record(event):
            events.append(event)
            journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        def effects(start=0):
            return [
                event["value"]
                for event in events[start:]
                if event.get("kind") == "backend_diagnostic"
                and isinstance(event.get("value"), dict)
                and event["value"].get("kind") == "input_activity"
                and event["value"].get("phase") == "willCall"
            ]

        async def invoke(name, call):
            command = {"name": name, "before": await state(folder, process=child)}
            report["commands"].append(command)
            started, event_start = time.monotonic(), len(events)
            try:
                result = await asyncio.to_thread(call)
                command["result"] = result
                assert result.status in ("dispatched", "verified"), result
                save_observation(folder, name, result.observation)
                return command
            except Exception as error:
                command["error"] = {
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "detail": str(error),
                    "effect": getattr(error, "effect", None),
                    "impact": getattr(error, "impact", None),
                }
                raise
            finally:
                command["elapsed_seconds"] = time.monotonic() - started
                command["native_effects"] = effects(event_start)
                command["last_state"] = await state(folder, process=child)

        set_diagnostic_handler(record)
        try:
            before_apps = await asyncio.to_thread(computer.list_apps)
            previous = {choice.id for choice in before_apps if choice.is_running}
            report["application_choices_before_launch"] = before_apps
            child = await asyncio.create_subprocess_exec(
                str(binary),
                "--output",
                str(folder),
                "--title",
                title,
                cwd=ROOT,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=errors,
                stderr=errors,
            )
            initial = await state(
                folder,
                lambda value: value["visible"] and value["page"].get("ready"),
                process=child,
                seconds=15,
            )
            report["initial"] = initial
            assert initial["pid"] == child.pid and initial["page"]["value"] == "", initial
            assert_original_document(initial, initial)
            choices = await asyncio.to_thread(computer.list_apps)
            report["application_choices"] = choices
            matching = [
                choice
                for choice in choices
                if choice.is_running and choice.id not in previous and choice.display_name == NAME
            ]
            assert len(matching) == 1, [dict(choice) for choice in choices if choice.id not in previous]
            app = computer.app(matching[0].id)
            report["selected_application"] = dict(matching[0])
            observation = await asyncio.to_thread(app.read, view="both", allow_foreground=foreground_diagnostic)
            assert title in observation.text, "The published application must contain this owned fixture window"
            report["setup_observation_preparation"] = observation.preparation
            report["before_input"] = await state(folder, process=child)
            save_observation(folder, "before", observation)
            field = await asyncio.to_thread(app.element, "Web callback input")
            report["element_resolved_once"] = True

            for name, expected, replace in (("insert", INSERTED, False), ("replace", REPLACED, True)):
                command = await invoke(name, lambda: field.type_text(expected, replace=replace))
                current = await state(
                    folder,
                    lambda value: input_effect(value, command["before"], expected),
                    process=child,
                )
                command["after"] = current
                assert_original_document(current, initial)
                assert current["page"]["submissions"] == [], "Text editing must not submit the form"
                assert current["page"]["savedModel"] == "", "Only explicit submission updates the saved model"

            command = await invoke("return", lambda: field.press_key("Return"))
            current = await state(folder, lambda value: len(value["page"]["submissions"]) == 1, process=child)
            command["after"] = current
            assert_original_document(current, initial)
            page, old = current["page"], command["before"]["page"]
            assert page["value"] == page["draftModel"] == page["committedModel"] == page["savedModel"] == REPLACED, page
            assert page["inputEvents"] == old["inputEvents"], "Return must not insert or replay the body text"
            assert page["changeEvents"], "The final edit must reach the DOM change consumer"
            assert page["changeEvents"][-1]["value"] == REPLACED, page
            submission = page["submissions"][0]
            assert all(
                submission[key] == REPLACED for key in ("value", "draftModel", "committedModel", "savedModel")
            ), page
            assert submission["documentID"] == initial["documentID"], page
            enter = [event for event in page["keyEvents"] if event["type"] == "keydown" and event["key"] == "Enter"]
            assert len(enter) == 1, "The explicit Return must be delivered once to the original input"
            assert allow_global_input or not any(event["transport"] == "globalHID" for event in effects()), effects()
            report["passed"], report["outcome"] = True, "passed"
        except Exception as error:
            report["error"] = {
                "type": type(error).__name__,
                "code": getattr(error, "code", None),
                "detail": str(error),
                "effect": getattr(error, "effect", None),
            }
            report["outcome"] = (
                "permission_required" if getattr(error, "code", None) == "permission_required" else "failed"
            )
            raise
        finally:
            try:
                await asyncio.to_thread(computer.close)
            finally:
                set_diagnostic_handler(None)
                report["native_effects"] = effects()
                report["unexpected_global_input"] = not allow_global_input and any(
                    event["transport"] == "globalHID" for event in effects()
                )
                try:
                    if child is not None:
                        try:
                            report["final"] = json.loads((folder / "state.json").read_text())
                        except FileNotFoundError:
                            pass
                        if child.returncode is None:
                            child.terminate()
                            try:
                                await asyncio.wait_for(child.wait(), 3)
                            except TimeoutError:
                                child.kill()
                                await child.wait()
                        report["fixture_exit_code"] = child.returncode
                        report["fixture_terminated"] = child.returncode is not None
                finally:
                    (folder / "result.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n",
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--allow-global-input", action="store_true")
    parser.add_argument(
        "--foreground-diagnostic",
        action="store_true",
        help="Permit the initial observation to activate the owned window; diagnostic only, not default-chain acceptance",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This fixture requires macOS WebKit")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = (args.output or Path(tempfile.mkdtemp(prefix="web-text-", dir=ROOT / ".artifacts"))).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        app = build_owned(folder, "local.computer-use.web-text." + uuid4().hex, NAME)
        if args.build_only:
            LOG.info("Built without launching: %s", app)
        else:
            asyncio.run(
                verify(
                    folder,
                    app,
                    allow_global_input=args.allow_global_input,
                    foreground_diagnostic=args.foreground_diagnostic,
                )
            )
    finally:
        LOG.info("Web text evidence: %s", folder)


if __name__ == "__main__":
    main()
