# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Diagnostic only: one WK insert with native_text_edit excluded locally.

The real SDK pipeline retains every other candidate, preparation, budget,
permission check and native dispatch guard. Global input is always disabled.
This is not acceptance of the unmodified public SDK route selection.
"""

import argparse
import asyncio
import importlib
import json
import logging
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from enum import Enum
from pathlib import Path
from uuid import uuid4

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from keyboard_window_command_workflow import build_owned
from qt_cocoa_activation_workflow import ROOT, save_observation, sha, state
from web_text_workflow import INSERTED, NAME, assert_original_document, input_effect

LOG = logging.getLogger(__name__)


def encode(value):
    return value.value if isinstance(value, Enum) else encode_artifact(value)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encode) + "\n")


@contextmanager
def exclude_selected_text(binding, records):
    """Patch only this process and this original target's insertion candidates."""
    candidates = importlib.import_module("computer_use.interaction.write.routes.selection.candidates")
    entry = importlib.import_module("computer_use.interaction.write.routes.selection.passes.enumerate_intent_units")
    original, original_entry = candidates.enumerate_intent_units, entry.enumerate_intent_units
    original_phases = candidates.CANDIDATE_PHASES

    def filtered(request, *, origin):
        value = original(request)
        if request.intent.kind != "insert" or request.facts.binding != binding:
            return value
        assert request.policy is None or request.policy.allow_global_input is False
        kept = tuple(item for item in value.primitives if item.unit != "native_text_edit")
        records.append(
            {
                "origin": origin,
                "intent": request.intent.kind,
                "binding": asdict(binding),
                "needs_editing_evidence": request.needs_editing_evidence,
                "before": [asdict(item) for item in value.primitives],
                "excluded": [asdict(item) for item in value.primitives if item.unit == "native_text_edit"],
                "after": [asdict(item) for item in kept],
            }
        )
        return replace(value, primitives=kept)

    def direct(request):
        return filtered(request, origin="pure_resolver")

    def pipeline(request):
        return filtered(request, origin="selection_pipeline")

    try:
        candidates.enumerate_intent_units = direct
        entry.enumerate_intent_units = pipeline
        candidates.CANDIDATE_PHASES = tuple(
            (name, direct if phase is original else phase, before, after, reasons)
            for name, phase, before, after, reasons in original_phases
        )
        yield
    finally:
        candidates.enumerate_intent_units = original
        entry.enumerate_intent_units = original_entry
        candidates.CANDIDATE_PHASES = original_phases


async def verify(folder, app_path, *, foreground_diagnostic=False):
    title = "CU Web Key Diagnostic " + uuid4().hex[:12]
    driver = Path(native_driver()["command"])
    binary = app_path / "Contents/MacOS" / NAME
    manifest = {
        str(path.relative_to(ROOT)): sha(path)
        for directory, pattern in (
            ("computer_use_engine", "*.py"),
            ("os_adapter/macos/Sources", "*.swift"),
        )
        for path in sorted((ROOT / directory).rglob(pattern))
    }
    for relative in (
        "tests/fixtures/WKWebTextProbe.swift",
        "tests/manual/web_text_keyboard_diagnostic.py",
        "tests/manual/web_text_workflow.py",
        "tests/manual/keyboard_window_command_workflow.py",
        "tests/manual/qt_cocoa_activation_workflow.py",
        "tests/manual/artifact_json.py",
    ):
        manifest[relative] = sha(ROOT / relative)
    write_json(folder / "source-manifest.json", manifest)
    report = {
        "diagnostic_only": True,
        "public_default_acceptance": False,
        "diagnostic_change": "Exclude native_text_edit only for this original binding's insert candidates",
        "allow_global_input": False,
        "foreground_diagnostic": foreground_diagnostic,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sdk": computer_use.__file__,
        "sdk_python": sys.executable,
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "fixture_sha256": sha(binary),
        "window_title": title,
        "filtered_candidates": [],
        "commands": [],
        "passed": False,
        "outcome": "incomplete",
    }
    computer, child, events = Computer(allow_global_input=False), None, []
    with (folder / "fixture-stderr.log").open("w") as errors, (folder / "diagnostics.jsonl").open("w") as journal:

        def record(event):
            events.append(event)
            journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        def activities():
            return [
                event["value"]
                for event in events
                if event.get("kind") == "backend_diagnostic"
                and isinstance(event.get("value"), dict)
                and event["value"].get("kind") == "input_activity"
            ]

        set_diagnostic_handler(record)
        try:
            previous = {choice.id for choice in await asyncio.to_thread(computer.list_apps) if choice.is_running}
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
            matching = [
                item for item in choices if item.is_running and item.id not in previous and item.display_name == NAME
            ]
            assert len(matching) == 1, [dict(item) for item in choices if item.id not in previous]
            app = computer.app(matching[0].id)
            report["selected_application"] = dict(matching[0])
            observation = await asyncio.to_thread(app.read, view="both", allow_foreground=foreground_diagnostic)
            assert title in observation.text
            save_observation(folder, "before", observation)
            report["setup_observation_preparation"] = observation.preparation
            field = await asyncio.to_thread(app.element, "Web callback input")
            binding = field._snapshot["binding"]  # Identity restriction for the diagnostic patch only.
            report["original_binding"] = asdict(binding)
            command = {"name": "insert", "before": await state(folder, process=child)}
            report["commands"].append(command)
            started = time.monotonic()
            try:
                with exclude_selected_text(binding, report["filtered_candidates"]):
                    result = await asyncio.to_thread(field.type_text, INSERTED)
                command["result"] = result
                assert result.status in ("dispatched", "verified"), result
                save_observation(folder, "insert", result.observation)
            except Exception as error:
                command["error"] = {
                    "type": type(error).__name__,
                    "code": getattr(error, "code", None),
                    "detail": str(error),
                    "effect": getattr(error, "effect", None),
                    "impact": getattr(error, "impact", None),
                    "observation_status": getattr(error, "observation_status", None),
                }
                raise
            finally:
                command["elapsed_seconds"] = time.monotonic() - started
                command["returned_state"] = await state(folder, process=child)
            current = await state(
                folder,
                lambda value: input_effect(value, command["before"], INSERTED),
                process=child,
            )
            command["after"] = current
            assert_original_document(current, initial)
            assert current["page"]["submissions"] == [] and current["page"]["savedModel"] == ""
            assert any(item["excluded"] for item in report["filtered_candidates"]), "No candidate was excluded"
            assert not any(
                item.get("transport") == "globalHID" and item.get("phase") == "willCall" for item in activities()
            )
            report["passed"], report["outcome"] = True, "diagnostic_insert_passed"
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
                report["native_activity"] = activities()
                report["unexpected_global_input"] = any(
                    item.get("transport") == "globalHID" and item.get("phase") == "willCall" for item in activities()
                )
                if child is not None:
                    try:
                        if (folder / "state.json").exists():
                            report["final"] = json.loads((folder / "state.json").read_text())
                    except (OSError, ValueError) as error:
                        report["final_state_error"] = str(error)
                    if child.returncode is None:
                        child.terminate()
                        try:
                            await asyncio.wait_for(child.wait(), 3)
                        except TimeoutError:
                            child.kill()
                            await child.wait()
                    report["fixture_exit_code"] = child.returncode
                    report["fixture_terminated"] = child.returncode is not None
                report["source_drift"] = [name for name, digest in manifest.items() if sha(ROOT / name) != digest]
                if report["source_drift"]:
                    report["passed"], report["outcome"] = False, "source_drift"
                write_json(folder / "result.json", report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--foreground-diagnostic", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This fixture requires macOS WebKit")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = (
        args.output or Path(tempfile.mkdtemp(prefix="web-keyboard-diagnostic-", dir=ROOT / ".artifacts"))
    ).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        app = build_owned(folder, "local.computer-use.web-text." + uuid4().hex, NAME)
        if args.build_only:
            LOG.info("Built without launching: %s", app)
        else:
            asyncio.run(verify(folder, app, foreground_diagnostic=args.foreground_diagnostic))
    finally:
        LOG.info("DIAGNOSTIC ONLY Web keyboard evidence: %s", folder)


if __name__ == "__main__":
    main()
