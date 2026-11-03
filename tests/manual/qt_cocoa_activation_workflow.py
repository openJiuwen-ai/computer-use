# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check public SDK click/AXPress against real Qt Cocoa signals and saved settings.

Run only when desktop acceptance is authorized, after rebuilding the source
native driver. PySide6 remains an optional fixture dependency. No accounts or
real documents are used; the workflow owns and terminates its Qt child process.
"""

import argparse
import asyncio
import hashlib
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

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/qt_cocoa_activation_probe.py"
DEFAULT_PYTHON = ROOT / ".artifacts/tencent-date-investigation/venv/bin/python"
LOG = logging.getLogger(__name__)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def state(folder, predicate=lambda value: True, *, process=None, seconds=6):
    deadline = time.monotonic() + seconds
    current = None
    while time.monotonic() < deadline:
        if process is not None and process.returncode is not None:
            raise RuntimeError(f"Qt fixture exited {process.returncode}; inspect fixture-stderr.log")
        try:
            current = json.loads((folder / "state.json").read_text(encoding="utf-8"))
            if current.get("failure"):
                raise AssertionError(current["failure"])
            if predicate(current):
                return current
        except FileNotFoundError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError(f"Fixture did not reach expected state: {current}")


def save_observation(folder, name, observation):
    if observation is None:
        return
    (folder / (name + ".txt")).write_text(observation.text, encoding="utf-8")
    if observation.screenshot:
        (folder / (name + ".png")).write_bytes(observation.screenshot)


async def verify(folder, python, *, allow_global_input=False, variant="standard", collect_semantic_failures=False):
    if sys.platform != "darwin":
        raise RuntimeError("This acceptance workflow requires macOS Cocoa.")
    if not python.is_file():
        raise FileNotFoundError(f"Optional PySide6 interpreter not found: {python}; use --python")
    if variant != "standard" and allow_global_input:
        raise ValueError("Custom Qt identifier variants must run without global input authorization")
    title = "CU Qt Cocoa " + uuid4().hex[:12]
    driver = Path(native_driver()["command"])
    source_hashes = {
        str(path.relative_to(ROOT)): sha(path)
        for directory, pattern in (("computer_use_engine", "*.py"), ("os_adapter/macos/Sources", "*.swift"))
        for path in sorted((ROOT / directory).rglob(pattern))
    }
    for path in (FIXTURE, ROOT / "tests/fixtures/qt_toggle_activation_probe.py", Path(__file__)):
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
        "variant": variant,
        "collect_semantic_failures": collect_semantic_failures,
        "window_title": title,
        "commands": [],
        "semantic_failures": [],
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
                "--variant",
                variant,
                cwd=ROOT,
                env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=errors,
                stderr=errors,
            )
            initial = await state(folder, lambda value: value["visible"], process=child, seconds=10)
            assert initial["pid"] == child.pid and initial["platform"] == "cocoa", initial
            assert initial["variant"] == variant, initial
            report["initial"] = initial
            # Identity selection uses only published public application choices.
            choices = await computer.list_apps()
            report["application_choices"] = choices
            matching = [choice for choice in choices if choice.is_running and choice.id not in previous_running]
            assert len(matching) == 1, [dict(choice) for choice in choices]
            app = computer.app(matching[0].id)
            report["selected_application"] = dict(matching[0])
            observation = await app.read(view="both")
            assert title in observation.text, "New application must expose the owned fixture window before input"
            save_observation(folder, "before", observation)

            async def activate(route, field, label):
                element = await app.element(label)
                before = await state(folder, process=child)
                assert before["forms"][route]["checked"][field] is False, before
                start, first_event = time.monotonic(), len(events)
                command = {"route": route, "field": field, "label": label, "before": before}
                report["commands"].append(command)
                try:
                    result = await (element.click() if route == "normal" else element.perform_action("AXPress"))
                    command["result"] = result
                except Exception as error:
                    command["error"] = {"type": type(error).__name__, "detail": str(error)}
                    raise
                finally:
                    command["elapsed_seconds"] = time.monotonic() - start
                    command["native_effects"] = [
                        event["value"]
                        for event in events[first_event:]
                        if event.get("kind") == "backend_diagnostic"
                        and isinstance(event.get("value"), dict)
                        and event["value"].get("kind") == "input_activity"
                        and event["value"].get("phase") == "willCall"
                    ]
                after = await state(folder, lambda value: value["forms"][route]["checked"][field], process=child)
                command["after"] = after
                counts = after["forms"][route]["counts"][field]
                assert counts["toggled"] == 1 and counts["clicked"] in (0, 1), after
                assert after["guard"] == "unchanged", after
                for other_route in ("normal", "explicit"):
                    for other_field in ("recording", "muted"):
                        if (other_route, other_field) != (route, field):
                            for key in ("checked", "model"):
                                assert (
                                    after["forms"][other_route][key][other_field]
                                    == before["forms"][other_route][key][other_field]
                                ), after
                if route == "normal":
                    matches = counts["clicked"] == 1 and after["forms"][route]["model"][field] is True
                    command["activation_semantics_match"] = matches
                    if not matches:
                        report["semantic_failures"].append({
                            "route": route, "field": field, "clicked": counts["clicked"],
                            "model": after["forms"][route]["model"][field],
                        })
                        # Diagnostic continuation uses different controls. It
                        # never retries the failed click or changes its score.
                        if not collect_semantic_failures:
                            raise AssertionError(f"Normal click did not activate the model: {after}")
                else:
                    # Explicit native action exposes its actual provider semantics;
                    # do not force a state-only assumption if this Qt version differs.
                    assert after["forms"][route]["model"][field] is bool(counts["clicked"]), after
                    command["explicit_action_semantics"] = "activated" if counts["clicked"] else "state_only"
                assert result.status in ("dispatched", "verified"), result
                save_observation(folder, route + "-" + field + "-after", result.observation)

            for route, label in (("normal", "Normal click"), ("explicit", "Explicit action")):
                await activate(route, "recording", label + " checkbox")
                await activate(route, "muted", label + " radio on")
            before_save = await state(folder, process=child)
            save_button = await app.element("Save and reopen")
            result = await save_button.click()
            report["save_result"] = result
            current = await state(
                folder, lambda value: value["save_clicks"] == 1 and value["generation"] == 2, process=child
            )
            report["final"] = current
            assert current["guard"] == "unchanged"
            for route in ("normal", "explicit"):
                for field in ("recording", "muted"):
                    expected = before_save["forms"][route]["model"][field]
                    assert current["saved"][route][field] is expected
                    assert current["forms"][route]["checked"][field] is expected
            if not report["semantic_failures"]:
                assert current["saved"]["normal"] == {"recording": True, "muted": True}
            assert json.loads((folder / "saved-settings.json").read_text()) == current["saved"]
            save_observation(folder, "reopened", result.observation)
            # Input diagnostics must agree with the default no-global grant.
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
            if report["semantic_failures"]:
                raise AssertionError(f"Normal click semantic failures: {report['semantic_failures']}")
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
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--variant", default="standard",
        choices=("standard", "custom-classes", "custom-names", "custom-classes-names"),
    )
    parser.add_argument(
        "--collect-semantic-failures", action="store_true",
        help="Record normal-click semantic failures, inspect independent controls and saved state, then fail",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = args.output or Path(tempfile.mkdtemp(prefix="qt-cocoa-activation-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(verify(
            folder.resolve(), args.python.absolute(), allow_global_input=args.allow_global_input,
            variant=args.variant, collect_semantic_failures=args.collect_semantic_failures,
        ))
    finally:
        LOG.info("Qt Cocoa activation evidence: %s", folder)


if __name__ == "__main__":
    main()
