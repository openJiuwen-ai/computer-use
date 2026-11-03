# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify ordinary AppKit switch/radio activation and persistence with no global input.

Each control receives one public SDK call. The independent target/action callback,
model, saved file and reopened control are the oracle; no action is retried.
--require-background additionally requires the foreground process to remain unchanged.
Run only during authorized desktop acceptance, after rebuilding the source driver.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import signal
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state
from qt_cocoa_activation_workflow import save_observation

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


async def wait_state(folder, predicate):
    for _ in range(120):
        current = await state(folder)
        if current.get("failure"):
            raise AssertionError(current["failure"])
        if predicate(current):
            return current
        await asyncio.sleep(0.05)
    raise AssertionError(f"Fixture did not reach expected state: {current}")


def check_desktop(before, after, require_background):
    baseline = before["desktop"]
    samples = [sample for sample in after["desktopSamples"] if sample["sequence"] > baseline["sequence"]]
    assert samples, "No desktop samples were collected during the command"
    assert baseline["cursor"] is not None, "Missing cursor evidence cannot prove preservation"
    for sample in samples:
        for key in ("cursor", "modifiers", "heldButtons", "clipboardChangeCount"):
            assert sample[key] == baseline[key], (key, baseline, sample)
    assert after["clipboardDigest"] == before["clipboardDigest"]
    assert after["guard"] == before["guard"] == "unchanged"
    changed = any(sample["frontmostPID"] != baseline["frontmostPID"] for sample in samples)
    if require_background:
        assert baseline["frontmostPID"] != before["pid"], "The strict-background initial state was not established"
        assert not changed, "The foreground process changed during strict-background acceptance"
    return {"foreground_changed": changed, "samples": len(samples)}


async def verify(folder, driver_option, require_background):
    bundle = "local.computer-use.appkit-toggle." + uuid.uuid4().hex
    driver = Path(driver_option or native_driver()["command"]).resolve()
    computer = Computer(allow_global_input=False)
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    initial, events = None, []
    report = {
        "passed": False, "allow_global_input": False, "require_background": require_background,
        "sdk": computer_use.__file__, "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "commands": [],
    }
    sources = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory, pattern in (("computer_use_engine", "*.py"), ("os_adapter/macos/Sources", "*.swift"))
        for path in sorted((ROOT / directory).rglob(pattern))
    }
    for path in (
        Path(__file__), ROOT / "tests/fixtures/AppKitToggleActivationProbe.swift",
        ROOT / "tests/manual/input_receiver_workflow.py", ROOT / "tests/manual/qt_cocoa_activation_workflow.py",
    ):
        sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "source-manifest.json").write_text(json.dumps(sources, indent=2) + "\n")
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")

    def record(event):
        events.append(event)
        journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(build_fixture, folder, bundle, "AppKitToggleActivationProbe")
        initial = await state(folder)
        report["initial"] = initial
        app = computer.app(bundle)
        observation = await app.read(view="both")
        assert initial["title"] in observation.text
        save_observation(folder, "before", observation)

        async def command(name, operation, expected):
            before = await state(folder)
            assert not any(before["desktop"]["heldButtons"].values()), before["desktop"]
            row = {"name": name, "before": before, "sdk_calls": 1}
            report["commands"].append(row)
            started, first_event = time.monotonic(), len(events)
            try:
                receipt = await operation()
                row["receipt"] = receipt
                await wait_state(folder, expected)
                await asyncio.sleep(0.15)
                after = await state(folder)
                row["after"] = after
                row["desktop_result"] = check_desktop(before, after, require_background)
                assert receipt.status in ("dispatched", "verified"), receipt
                assert receipt.impact is not None and receipt.impact.global_input_allowed is False
                assert receipt.impact.attempts and all(
                    attempt.cleanup == "complete" for attempt in receipt.impact.attempts
                ), receipt.impact
                save_observation(folder, name, receipt.observation)
                return before, after
            except BaseException as error:
                row["error"] = {"type": type(error).__name__, "detail": str(error)}
                row["after_failure"] = await state(folder)
                raise
            finally:
                row["seconds"] = time.monotonic() - started
                row["native_effects"] = [
                    event["value"] for event in events[first_event:]
                    if event.get("kind") == "backend_diagnostic" and isinstance(event.get("value"), dict)
                    and event["value"].get("kind") == "input_activity"
                    and event["value"].get("phase") == "willCall"
                ]

        for route, label in (("normal", "Normal click"), ("explicit", "Explicit action")):
            for field, suffix in (("recording", "switch"), ("muted", "radio on")):
                element = await app.element(label + " " + suffix)
                operation = element.click if route == "normal" else lambda: element.perform_action("AXPress")
                before, after = await command(
                    route + "-" + field, operation,
                    lambda value: value["forms"][route]["counts"][field] >= 1,
                )
                form = after["forms"][route]
                assert before["forms"][route]["counts"][field] == 0
                assert form["counts"][field] == 1 and form["model"][field] is True, form
                assert form["checked"][field] is True, form
                for other_route in ("normal", "explicit"):
                    for other_field in ("recording", "muted"):
                        if (other_route, other_field) != (route, field):
                            for key in ("checked", "model", "counts"):
                                assert after["forms"][other_route][key][other_field] == before["forms"][other_route][key][other_field]
                assert all(name == "NSButton" for name in form["classes"].values()), form["classes"]
        save = await app.element("Save and reopen")
        before, final = await command(
            "save-and-reopen", save.click,
            lambda value: value["saveClicks"] == 1 and value["generation"] == 2,
        )
        report["final"] = final
        expected = {route: {"recording": True, "muted": True} for route in ("normal", "explicit")}
        assert final["saved"] == expected
        assert json.loads((folder / "saved-settings.json").read_text()) == expected
        for route in expected:
            assert final["forms"][route]["checked"] == expected[route]
            assert final["forms"][route]["model"] == expected[route]
            assert final["forms"][route]["counts"] == {"recording": 0, "muted": 0}
            assert final["beforeReopen"][route]["counts"] == {"recording": 1, "muted": 1}
        all_effects = [effect for row in report["commands"] for effect in row["native_effects"]]
        assert not any(effect.get("transport") == "globalHID" for effect in all_effects), all_effects
        report["passed"] = True
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        try:
            if initial is not None:
                report["final_state"] = await state(folder)
            computer._controller.transport.close()
            await computer._controller.transport.wait_closed()
        finally:
            set_diagnostic_handler(None)
            journal.close()
            if initial is not None:
                try:
                    os.kill(initial["pid"], signal.SIGTERM)
                    report["fixture_termination_requested"] = True
                except ProcessLookupError:
                    report["fixture_already_exited"] = True
            (folder / "result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver")
    parser.add_argument("--require-background", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = args.output or Path(tempfile.mkdtemp(prefix="appkit-toggle-activation-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(verify(folder.resolve(), args.driver, args.require_background))
    finally:
        LOG.info("AppKit toggle activation evidence: %s", folder)


if __name__ == "__main__":
    main()
