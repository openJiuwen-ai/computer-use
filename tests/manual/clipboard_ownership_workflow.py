# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Verify one paste preserves a later application's clipboard ownership.

Run after rebuilding: uv run python tests/manual/clipboard_ownership_workflow.py
The fixture consumes the SDK's temporary text with its normal paste callback,
then writes its own new clipboard value. After verifying the SDK preserved that
owner, separate fixture cleanup restores its in-memory original only while the
fixture still owns the exact clipboard generation and unique value. External
clipboard changes are preserved. Original clipboard bytes never enter artifacts.
Global input is denied; one addressed paste is called, without caller retries.
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
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


async def snapshot(ui, folder, name):
    observation = await asyncio.to_thread(ui.read, view="both")
    (folder / f"{name}.txt").write_text(observation.text)
    assert observation.screenshot, "Expected an observed fixture window"
    (folder / f"{name}.png").write_bytes(observation.screenshot)


async def cleanup_fixture_clipboard(folder):
    """Ask the fixture resource owner to clean up; this is not an SDK action."""
    identity = uuid.uuid4().hex
    pending = folder / "command.pending.json"
    pending.write_text(json.dumps({"id": identity, "kind": "cleanupClipboard"}))
    pending.replace(folder / "command.json")
    for _ in range(100):
        current = await state(folder)
        cleanup = current["cleanup"]
        if cleanup["commandId"] == identity and cleanup["status"] != "notRequested":
            return cleanup, current
        await asyncio.sleep(0.05)
    raise TimeoutError("The fixture did not acknowledge its independent clipboard cleanup")


async def verify(folder, driver_option):
    bundle = "local.computer-use.clipboard-ownership." + uuid.uuid4().hex
    payload = "One addressed paste 精确 " + uuid.uuid4().hex
    driver = Path(driver_option or native_driver()["command"]).resolve()
    computer = Computer(allow_global_input=False)
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    events, initial = [], None
    report = {
        "passed": False,
        "sdk_passed": False,
        "allow_global_input": False,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "request": {"kind": "element.paste", "text": payload, "caller_invocations": 0},
        "fixture_cleanup": {"status": "notRequested"},
    }
    sources = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory, suffix in (("computer_use_engine", ".py"), ("os_adapter/macos/Sources", ".swift"))
        for path in sorted((ROOT / directory).rglob("*" + suffix))
    }
    for path in (Path(__file__), ROOT / "tests/fixtures/ClipboardOwnershipProbe.swift"):
        sources[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (folder / "source-manifest.json").write_text(json.dumps(sources, indent=2) + "\n")

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(build_fixture, folder, bundle, "ClipboardOwnershipProbe")
        initial = await state(folder)
        report["initial"] = initial
        app = computer.app(bundle)
        await snapshot(app, folder, "before")
        editor = await asyncio.to_thread(app.element, "Ownership paste editor")
        before = await state(folder)
        report["before"] = before
        original = before["clipboardSnapshot"]
        assert original["status"] == "captured", "Full original clipboard snapshot is required before mutation"
        assert original["generation"] == before["clipboardChangeCount"], "Clipboard changed since fixture setup"
        assert original["digest"] == before["clipboardDigest"], "Clipboard representations changed since setup"
        assert before["editor"] == "" and before["pastes"] == [], before
        assert before["newOwnerValue"] != payload
        assert not any(before["heldButtons"].values()), before["heldButtons"]
        start, started = len(events), time.monotonic()
        receipt, caught = None, None
        report["request"]["caller_invocations"] = 1
        try:
            receipt = await asyncio.to_thread(editor.paste, payload)
            report["receipt"] = dict(receipt)
        except ComputerUseError as error:
            caught = error
            report["error"] = {
                "code": error.code,
                "effect": error.effect,
                "detail": error.detail,
                "impact": error.impact,
            }
        report["seconds"] = time.monotonic() - started
        # Observations only: never retry the paste or repair its effects here.
        for _ in range(60):
            after = await state(folder)
            if caught is not None or after["pastes"]:
                break
            await asyncio.sleep(0.05)
        # Check after the SDK's clipboard cleanup and queued callbacks settle.
        await asyncio.sleep(0.2)
        after = await state(folder)
        report["after"] = after
        report["input_activity"] = [
            item["value"]
            for item in events[start:]
            if item.get("kind") == "backend_diagnostic"
            and isinstance(item.get("value"), dict)
            and item["value"].get("kind") == "input_activity"
        ]
        if caught is not None:
            raise caught
        assert after["editor"] == payload, after
        assert len(after["pastes"]) == 1, after["pastes"]
        callback = after["pastes"][0]
        assert callback["received"] == payload and callback["editorAfterPaste"] == payload, callback
        assert callback["ownershipWriteSucceeded"] and callback["window"] == before["window"], callback
        assert callback["ownedGeneration"] > callback["incomingGeneration"], callback
        assert after["clipboardMatchesNewOwner"], after
        assert after["clipboardChangeCount"] == callback["generationAfterWrite"], after
        assert after["clipboardDigest"] != before["clipboardDigest"], after
        for name in (
            "guardText",
            "guardPasteCallbacks",
            "guardTextChanges",
            "guardMouseCallbacks",
            "cursor",
            "heldButtons",
        ):
            assert after[name] == before[name], (name, before[name], after[name])
        assert before["cursor"] is not None, "Missing system cursor evidence cannot prove preservation"
        input_delta = after["inputEvents"][len(before["inputEvents"]) :]
        report["input_event_delta"] = input_delta
        assert not any(event["kind"] == "mouse" for event in input_delta), input_delta
        key_down = [event for event in input_delta if event["type"] == 10]
        key_up = [event for event in input_delta if event["type"] == 11]
        assert len(key_down) == len(key_up) == 1, input_delta
        assert key_down[0]["code"] == key_up[0]["code"] == 9, input_delta  # Physical V.
        assert key_down[0]["flags"] & (1 << 20), input_delta  # NSEvent command modifier.
        assert all(event["window"] == before["window"] for event in key_down + key_up), input_delta
        impact = receipt.impact
        assert impact is not None and impact.global_input_allowed is False and impact.attempts, impact
        actuals = [attempt.actual for attempt in impact.attempts]
        assert all(actual is not None and actual.coverage == "complete" for actual in actuals), impact
        assert all(actual.omitted_effects == 0 for actual in actuals), impact
        restorations = [actual.clipboard_restoration for actual in actuals if actual.clipboard_restoration != "notUsed"]
        assert restorations == ["ownershipLost"], impact
        effects = [effect for actual in actuals for effect in actual.effects]
        invoked = [effect for effect in effects if effect.state != "notInvoked"]
        assert not any(effect.transport == "globalHID" for effect in invoked), impact
        keyboard = [effect for effect in invoked if effect.device == "keyboard"]
        assert keyboard and all(effect.transport == "process" and effect.state == "invoked" for effect in keyboard), (
            impact
        )
        clipboard = [effect for effect in invoked if effect.device == "clipboard"]
        assert clipboard and all(effect.stage == "preparation" and effect.state == "invoked" for effect in clipboard), (
            impact
        )
        assert all(attempt.cleanup == "complete" for attempt in impact.attempts), impact
        sent = [
            attempt for attempt in impact.attempts if not attempt.preparation_only and attempt.dispatch == "dispatched"
        ]
        assert len(sent) == 1 and sent[0].route == "clipboard_paste", impact
        assert not any(attempt.dispatch in ("partial", "unknown") for attempt in impact.attempts), impact
        report["foreground_changed"] = after["frontmostPID"] != before["frontmostPID"]
        await snapshot(app, folder, "after")
        report["sdk_passed"] = True
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        cleanup_error = None
        try:
            if initial is not None:
                # Keep post-SDK evidence frozen before requesting any fixture
                # mutation. Failure cleanup never changes the SDK verdict.
                report["final_before_fixture_cleanup"] = await state(folder)
                cleanup, final = await cleanup_fixture_clipboard(folder)
                report["fixture_cleanup"] = cleanup
                report["final_after_fixture_cleanup"] = final
                if cleanup["status"] == "restored":
                    assert cleanup["restoredDigest"] == cleanup["originalDigest"], cleanup
                elif cleanup["status"] not in ("ownershipLost", "notOwned"):
                    raise RuntimeError("Fixture clipboard cleanup failed: " + str(cleanup))
                if report["sdk_passed"] and cleanup["status"] == "notOwned":
                    raise RuntimeError("Verified application ownership unexpectedly missing during cleanup")
            report["passed"] = report["sdk_passed"]
        except BaseException as error:
            cleanup_error = error
            report["fixture_cleanup_error"] = {"type": type(error).__name__, "detail": str(error)}
        finally:
            try:
                (folder / "result.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
                )
            finally:
                try:
                    await asyncio.to_thread(computer.close)
                finally:
                    set_diagnostic_handler(None)
                    journal.close()
                    if initial is not None:
                        try:
                            os.kill(initial["pid"], signal.SIGTERM)
                        except ProcessLookupError:
                            pass
        if cleanup_error is not None and report["sdk_passed"]:
            raise cleanup_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", help="Rebuilt driver path; defaults to this checkout's driver")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="clipboard-ownership-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, args.driver))
    finally:
        LOG.info("Clipboard ownership evidence: %s", folder)


if __name__ == "__main__":
    main()
