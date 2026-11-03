# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Public SDK whole-date replacement followed by Return on the same Qt element.

Six seeded cases cover year/month/day starting sections and increasing/decreasing
values. Two subsequent edits retain the editor's naturally changed section/parser
state. Each replacement and Return is one SDK invocation; no focus/click/editor
lookup or input retry is inserted by this harness. All data is disposable.
"""

import argparse
import asyncio
import json
import logging
import os
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
from qt_cocoa_activation_workflow import DEFAULT_PYTHON, ROOT, save_observation, sha, state

FIXTURE = ROOT / "tests/fixtures/qt_cocoa_date_probe.py"
LOG = logging.getLogger(__name__)
CASES = [
    (section + "-" + direction, section, requested)
    for section in ("year", "month", "day")
    for direction, requested in (("decrease", "2026/11/13"), ("increase", "2026/11/23"))
]


async def verify(folder, python, *, allow_global_input=False, only=None, normal_show=False):
    if sys.platform != "darwin":
        raise RuntimeError("This acceptance workflow requires macOS Cocoa.")
    if not python.is_file():
        raise FileNotFoundError(f"Optional PySide6 interpreter not found: {python}; use --python")
    title = "CU Qt Date " + uuid4().hex[:12]
    driver = Path(native_driver()["command"])
    source_hashes = {
        str(path.relative_to(ROOT)): sha(path)
        for directory, pattern in (("computer_use_engine", "*.py"), ("os_adapter/macos/Sources", "*.swift"))
        for path in sorted((ROOT / directory).rglob(pattern))
    }
    for path in (FIXTURE, Path(__file__), ROOT / "tests/fixtures/qt_structured_replacement_probe.py"):
        source_hashes[str(path.relative_to(ROOT))] = sha(path)
    (folder / "source-manifest.json").write_text(json.dumps(source_hashes, indent=2) + "\n")
    report = {
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sdk": computer_use.__file__,
        "sdk_python": sys.executable,
        "fixture_python": str(python),
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "allow_global_input": allow_global_input,
        "normal_show": normal_show,
        "window_title": title,
        "cases": [],
        "passed": False,
    }
    computer = Computer(allow_global_input=allow_global_input)
    child = None
    events = []
    with (folder / "fixture-stderr.log").open("w") as errors, (folder / "diagnostics.jsonl").open("w") as journal:

        def record(event):
            events.append(event)
            journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        set_diagnostic_handler(record)
        try:
            before_apps = await computer.list_apps()
            previous_running = {choice.id for choice in before_apps if choice.is_running}
            report["application_choices_before_launch"] = before_apps
            child = await asyncio.create_subprocess_exec(
                str(python),
                str(FIXTURE),
                "--output",
                str(folder),
                "--title",
                title,
                *(["--normal-show"] if normal_show else []),
                cwd=ROOT,
                env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=errors,
                stderr=errors,
            )
            initial = await state(folder, lambda value: value["visible"], process=child, seconds=10)
            assert initial["pid"] == child.pid and initial["platform"] == "cocoa", initial
            report["initial"] = initial
            choices = await computer.list_apps()
            report["application_choices"] = choices
            matching = [choice for choice in choices if choice.is_running and choice.id not in previous_running]
            assert len(matching) == 1, [dict(choice) for choice in choices]
            app = computer.app(matching[0].id)
            report["selected_application"] = dict(matching[0])
            observation = await app.read(view="both")
            assert title in observation.text, "New application must expose the owned fixture before input"
            save_observation(folder, "before", observation)
            field = await app.element("Meeting date")
            report["element_resolved_once"] = True

            async def configure(section):
                identifier = uuid4().hex
                temporary = folder / "command.tmp"
                temporary.write_text(
                    json.dumps(
                        {
                            "id": identifier,
                            "kind": "configure",
                            "section": section,
                            "value": "2026/11/15",
                        }
                    )
                )
                temporary.replace(folder / "command.json")
                current = await state(folder, lambda value: value["last_command"] == identifier, process=child)
                assert current["section"] == section and current["value"] == "2026/11/15", current
                assert current["submissions"] == [], current

            async def invoke(command, call):
                start, event_start = time.monotonic(), len(events)
                try:
                    result = await call()
                    command["result"] = result
                    assert result.status in ("dispatched", "verified"), result
                    assert result.observation is not None, result
                    return result
                except Exception as error:
                    command["error"] = {
                        "type": type(error).__name__,
                        "detail": str(error),
                        "code": getattr(error, "code", None),
                        "effect": getattr(error, "effect", None),
                    }
                    raise
                finally:
                    command["elapsed_seconds"] = time.monotonic() - start
                    command["native_effects"] = [
                        event["value"]
                        for event in events[event_start:]
                        if event.get("kind") == "backend_diagnostic"
                        and isinstance(event.get("value"), dict)
                        and event["value"].get("kind") == "input_activity"
                        and event["value"].get("phase") == "willCall"
                    ]

            async def case(name, section, requested):
                if section is not None:
                    await configure(section)
                before = await state(folder, process=child)
                case_report = {
                    "name": name,
                    "seeded_section": section,
                    "requested": requested,
                    "before": before,
                    "replacement": {},
                    "return": {},
                }
                report["cases"].append(case_report)
                result = await invoke(case_report["replacement"], lambda: field.type_text(requested, replace=True))
                after_replace = await state(
                    folder, lambda value: value["value"] == requested and value["model"] == requested, process=child
                )
                case_report["after_replace"] = after_replace
                assert after_replace["text"] == requested and after_replace["guard"] == "unchanged", after_replace
                assert after_replace["submissions"] == before["submissions"], "Replacement must not submit Return"
                save_observation(folder, name + "-replace", result.observation)
                result = await invoke(case_report["return"], lambda: field.press_key("Return"))
                after_return = await state(
                    folder, lambda value: len(value["submissions"]) == len(before["submissions"]) + 1, process=child
                )
                case_report["after_return"] = after_return
                assert after_return["submissions"][-1] == requested, after_return
                assert after_return["value"] == after_return["model"] == after_return["text"] == requested, after_return
                assert after_return["guard"] == "unchanged", after_return
                save_observation(folder, name + "-return", result.observation)

            for name, section, requested in CASES:
                if only is None or name in only:
                    await case(name, section, requested)
            # Preserve actual post-edit section/selection/parser state, with no
            # fixture reset and no new Element resolution between these commands.
            if only is None or "continuous" in only:
                await case("continuous-decrease", None, "2026/11/13")
                await case("continuous-month-change", None, "2026/12/1")
            report["final"] = await state(folder, process=child)
            if not allow_global_input:
                global_events = [
                    event["value"]
                    for event in events
                    if event.get("kind") == "backend_diagnostic"
                    and isinstance(event.get("value"), dict)
                    and event["value"].get("kind") == "input_activity"
                    and event["value"].get("phase") == "willCall"
                    and event["value"].get("transport") == "globalHID"
                ]
                assert not global_events, global_events
            report["passed"] = True
        except Exception as error:
            report["error"] = {"type": type(error).__name__, "detail": str(error)}
            raise
        finally:
            try:
                computer._controller.transport.close()
                await computer._controller.transport.wait_closed()
            finally:
                set_diagnostic_handler(None)
                try:
                    if child is not None:
                        if child.returncode is None:
                            child.terminate()
                            try:
                                await asyncio.wait_for(child.wait(), 3)
                            except TimeoutError:
                                child.kill()
                                await child.wait()
                        report["fixture_exit_code"] = child.returncode
                        report["fixture_terminated"] = True
                finally:
                    (folder / "result.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--allow-global-input", action="store_true")
    parser.add_argument(
        "--normal-show", action="store_true",
        help="Use normal Qt window activation to isolate replacement from nonactivating-window preparation",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--only", nargs="+", choices=[item[0] for item in CASES] + ["continuous"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = args.output or Path(tempfile.mkdtemp(prefix="qt-cocoa-date-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(
            verify(
                folder.resolve(),
                args.python.absolute(),
                allow_global_input=args.allow_global_input,
                only=set(args.only) if args.only else None,
                normal_show=args.normal_show,
            )
        )
    finally:
        LOG.info("Qt Cocoa date evidence: %s", folder)


if __name__ == "__main__":
    main()
