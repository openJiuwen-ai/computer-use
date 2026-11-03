# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Exercise one owned TextEdit document with an installed SDK; never quit the app.

Run with an installed venv's `python -I` after building its native resources.
The only document created is a UUID-named .txt under this checkout's .artifacts.
All target selection and editing use public SDK calls and captured observations.
Private transport access is used only to close the SDK process in finally.
If selection, editing, saving or closure is uncertain, stop without replay or
attempting app-wide cleanup. The owned document may remain open in that case.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import checkout_root, native_driver

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
TEXTEDIT = Path("/System/Applications/TextEdit.app")
CLOSE = re.compile(r"^\s*(\d+) close button(?:\s|$)")


def encoded(value):
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    raise TypeError(f"Unsupported artifact type: {type(value).__name__}")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=encoded) + "\n")


def failure(error):
    if isinstance(error, ComputerUseError):
        return {key: getattr(error, key) for key in (
            "code", "effect", "target", "detail", "next", "impact", "observation",
            "observation_status", "observation_error", "window_lifecycle", "choices",
        )}
    return {"type": type(error).__name__, "detail": str(error)}


async def captured_header(observation):
    """Read already-captured public details; this does not re-observe the desktop."""
    content, cursor = "", None
    prefix = "Captured interface metadata: "
    for _ in range(16):
        page = await observation.read_details(cursor=cursor, length=16384)
        content += page.text
        if prefix in content:
            try:
                value, _ = json.JSONDecoder().raw_decode(content.split(prefix, 1)[1])
            except json.JSONDecodeError:
                pass
            else:
                return value
        cursor = page.next
        if cursor is None:
            return None
    raise RuntimeError("Captured root metadata exceeds this harness's bounded detail budget; no input sent")


def own_window(windows, filename):
    titles = {filename.name, filename.stem}
    matches = [window for window in windows if window["title"] in titles]
    if len(matches) > 1:
        raise RuntimeError("More than one published window has the owned document title; no input sent")
    return matches[0] if matches else None


async def select_owned_window(app, filename, folder, report):
    for attempt in range(8):
        observation = await app.read(view="tree", full=True)
        (folder / f"selection-{attempt}.txt").write_text(observation.text)
        header = await captured_header(observation)
        if header is not None:
            selected = own_window(header["windows"], filename)
            if selected is not None:
                window = app.window(selected["ref"])
                observed = await window.read(view="tree", full=True)
                selected_header = await captured_header(observed)
                if selected_header is None or selected_header["scope"] != "window":
                    raise RuntimeError("Owned Window read did not retain captured scope metadata; no input sent")
                if selected_header["scope_root"] != selected["ref"]:
                    raise RuntimeError("Owned Window read changed its published window identity; no input sent")
                confirmed = own_window(selected_header["windows"], filename)
                if confirmed is None or confirmed["ref"] != selected["ref"]:
                    raise RuntimeError("Owned Window identity/title was not confirmed; no input sent")
                report["window_proof"] = {
                    "title": selected["title"], "window_ref": selected["ref"],
                    "application": header["application"], "source": "captured public interface metadata",
                }
                return window, observed
        await asyncio.sleep(0.25)
    raise RuntimeError("No uniquely confirmed owned TextEdit window was observed; no input sent")


async def run(options, folder):
    if os.environ.get("PYTHONPATH") or os.environ.get("PYTHONHOME"):
        raise RuntimeError("Run with env -u PYTHONPATH -u PYTHONHOME so SDK child processes also use the installed package")
    sdk_file = Path(computer_use.__file__).resolve()
    if checkout_root() is not None or "site-packages" not in sdk_file.parts:
        raise RuntimeError(f"This harness requires an installed SDK, not an editable checkout: {sdk_file}")
    if not TEXTEDIT.is_dir():
        raise RuntimeError(f"TextEdit was not found at the explicit application path: {TEXTEDIT}")
    driver = Path(native_driver()["command"]).resolve()
    if not driver.is_file():
        raise RuntimeError(f"Build this installed SDK's driver before opening TextEdit: {driver}")
    identity = uuid.uuid4().hex[:16]
    filename = folder / f"SDKGA-{identity}.txt"
    seed = f"sdkga:{identity}"
    replacement = f"SDK GA 文本替换 {identity} 🙂"
    expected = replacement + "\n"
    filename.write_text(seed, encoding="utf-8")
    computer = Computer(allow_global_input=False)
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    manifest = sdk_file.parent / "_resources/manifest.json"
    report = {
        "passed": False, "sdk": str(sdk_file), "interpreter": sys.executable,
        "driver": str(driver), "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "native_resource_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
        "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "allow_global_input": False, "owned_file": str(filename), "seed": seed,
        "replacement": replacement, "expected_saved_text": expected, "actions": [],
        "cleanup": "no app-wide cleanup; only close the proven owned root after verified save",
    }

    def record(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=encoded) + "\n")
        journal.flush()

    async def action(name, operation):
        entry = {"name": name}
        report["actions"].append(entry)
        start = time.monotonic()
        try:
            receipt = await operation
            entry["receipt"] = receipt
            return receipt
        except BaseException as error:
            entry["error"] = failure(error)
            raise
        finally:
            entry["elapsed_seconds"] = time.monotonic() - start

    set_diagnostic_handler(record)
    try:
        before = await computer.list_apps()
        report["apps_before"] = before
        await asyncio.to_thread(
            subprocess.run, ["open", "-g", "-a", str(TEXTEDIT), str(filename)], check=True, timeout=15
        )
        report["opened_owned_file"] = True
        before_ids = {item.id for item in before if item.is_running}
        selected = None
        for _ in range(12):
            apps = await computer.list_apps()
            report["apps_after_open"] = apps
            named = [item for item in apps if item.is_running and item.display_name == options.app_name]
            new = [item for item in apps if item.is_running and item.id not in before_ids]
            candidates = named if named else new
            if len(candidates) == 1:
                selected = candidates[0]
                report["application_choice_basis"] = "exact published display name" if named else "unique new running app"
                break
            await asyncio.sleep(0.25)
        if selected is None:
            raise RuntimeError("No unique published application choice; inspect apps_after_open, no input sent")
        report["application_choice"] = selected
        window, observation = await select_owned_window(computer.app(selected.id), filename, folder, report)
        (folder / "owned-before.txt").write_text(observation.text)
        close_refs = [int(match[1]) for line in observation.text.splitlines() if (match := CLOSE.match(line))]
        if len(close_refs) != 1:
            raise RuntimeError("A unique close-button reference was not observed in the owned root; no input sent")
        close_button = await window.element(close_refs[0])
        field = await window.element([{"role": "textArea"}])
        initial = await field.read_text(length=4096)
        report["body_before"] = initial
        if initial.text != seed or initial.next is not None:
            raise RuntimeError("The observed body does not exactly match the owned-file seed; no input sent")
        # Deliberately no model-side focus, read, lookup or receiver preparation
        # between replacement and Return on the same retained Element.
        await action("replace", field.type_text(replacement, replace=True))
        await action("same_element_return", field.press_key("Return"))
        current = await field.read_text(length=4096)
        report["body_after_return"] = current
        if current.text != expected or current.next is not None:
            raise RuntimeError("The retained body's exact replacement + Return effect was not confirmed")
        await action("same_element_save", field.press_key("super+s"))
        for _ in range(50):
            saved = filename.read_text(encoding="utf-8")
            if saved == expected:
                break
            await asyncio.sleep(0.1)
        report["saved_text"] = saved
        if saved != expected:
            raise RuntimeError("The owned file did not contain the exact expected saved text; do not close or replay")
        report["save_verified"] = True
        receipt = await action("close_owned_root", close_button.click())
        closed = receipt.window_lifecycle == "closed"
        for attempt in range(4):
            if closed:
                break
            try:
                remaining = await window.read(view="tree")
                (folder / f"after-close-{attempt}.txt").write_text(remaining.text)
            except ComputerUseError as error:
                report["after_close_read_error"] = failure(error)
                # A generic stale handle is not proof that a window closed.
                # Accept only the SDK's explicit complete-directory absence
                # evidence; unknown lifecycle stays a failed acceptance.
                closed = (
                    error.code == "stale_target" and error.effect == "none"
                    and "fixed working window is absent from the complete current directory" in error.detail
                )
                break
            await asyncio.sleep(0.1)
        report["owned_root_closed"] = closed
        if not closed:
            raise RuntimeError("Close was sent once, but original-root closure was not confirmed; no replay")
        report["passed"] = True
    except BaseException as error:
        report["error"] = failure(error)
        raise
    finally:
        if filename.exists():
            report["final_owned_file_sha256"] = hashlib.sha256(filename.read_bytes()).hexdigest()
        try:
            write_json(folder / "result.json", report)
        finally:
            try:
                computer._controller.transport.close()
                await computer._controller.transport.wait_closed()
            finally:
                set_diagnostic_handler(None)
                journal.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-name", default="TextEdit", help="Exact list_apps display name; used only to inspect roots")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = Path(tempfile.mkdtemp(prefix="textedit-sdk-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(run(options, folder))
    finally:
        LOG.info("TextEdit SDK evidence: %s", folder)


if __name__ == "__main__":
    main()
