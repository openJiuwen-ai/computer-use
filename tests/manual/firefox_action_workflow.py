# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Opt-in Firefox Downloads directory-picker comparison using an owned profile.

Supply an existing official Firefox.app; this script never downloads software or
accepts an agreement. Each run sends one Choose… click/AXPress, observes its real
effect, and cancels only its own picker. It never selects a directory or replays
an unconfirmed action. focus-action may prepare foreground through the SDK.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import plistlib
import re
import signal
import subprocess
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path

from artifact_json import encode_artifact

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import state
from web_action_workflow import build, timestamp, wait_for

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
PREFERENCES = {
    "browser.shell.checkDefaultBrowser": False,
    "browser.aboutwelcome.enabled": False,
    "browser.startup.homepage_override.mstone": "ignore",
    "browser.startup.page": 0,
    "app.update.auto": False,
    "datareporting.policy.dataSubmissionEnabled": False,
    "termsofuse.bypassNotification": True,
}
PREFERENCE_SOURCE = (
    "https://github.com/mozilla-firefox/firefox/blob/9378fbc58660847fbc02c3486a0ecd6049978e25/"
    "browser/app/profile/firefox.js#L1966"
)


def running_firefox():
    processes = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True, check=True).stdout
    return [
        int(pid)
        for line in processes.splitlines()
        if len(parts := line.strip().split(None, 1)) == 2
        for pid, executable in [parts]
        if Path(executable).name.lower() == "firefox"
    ]


def button_reference(text, label):
    matches = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        direct = re.fullmatch(r"\s*(\d+) button(?: \([^\n)]*\))* " + re.escape(label) + r"(?:,.*)?", line)
        if direct:
            matches.append((int(direct[1]), line))
        if index and "Elements [button]:" in lines[index - 1]:
            for part in line.strip().split(" | "):
                compact = re.fullmatch(r"(\d+) " + re.escape(label) + r"(?:,.*)?", part)
                if compact:
                    matches.append((int(compact[1]), line))
    if len(matches) != 1:
        raise ValueError(f"Expected one observed button {label!r}, got {matches}")
    return matches[0]


def download_value(text):
    matches = re.findall(r"(?m)^\s*\d+ text field[^\n]*\bSave files to,.*?Value: ([^\n]+)$", text)
    if len(matches) != 1:
        raise ValueError(f"Expected one observed Downloads value, got {matches}")
    return matches[0]


def picker_visible(text):
    if not re.search(r"(?m)^\s*\d+ (?:sheet|dialog) (?:open|Open)\b", text):
        return False
    try:
        button_reference(text, "Cancel")
        button_reference(text, "Open")
        return True
    except ValueError:
        return False


async def outcome(operation):
    try:
        return {"receipt": dict(await asyncio.to_thread(operation)), "returned_at": timestamp()}
    except ComputerUseError as error:
        return {
            "error": {"code": error.code, "effect": error.effect, "detail": error.detail},
            "returned_at": timestamp(),
        }


async def observe(app, folder, name):
    observation = await asyncio.to_thread(app.read, view="both", full=True)
    (folder / (name + ".txt")).write_text(observation.text)
    if observation.screenshot is not None:
        (folder / (name + ".png")).write_bytes(observation.screenshot)
    return observation.text


async def verify(folder, options):
    browser = options.browser.resolve()
    executable = browser / "Contents/MacOS/firefox"
    info = plistlib.loads((browser / "Contents/Info.plist").read_bytes())
    driver = Path(options.driver or native_driver()["command"]).resolve()
    sources = [
        Path(__file__),
        ROOT / "tests/fixtures/WebActionProbe.swift",
        ROOT / "tests/manual/web_action_workflow.py",
        ROOT / "os_adapter/macos/Sources/Control/NativeActionUnit.swift",
        ROOT / "os_adapter/macos/Sources/Control/NativeActionDocumentPolicy.swift",
    ]
    result = {
        "operation": options.operation,
        "browser": str(browser),
        "browser_version": info.get("CFBundleShortVersionString"),
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "sdk": computer_use.__file__,
        "source_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "profile_setup": {
            "preferences": PREFERENCES,
            "source": PREFERENCE_SOURCE,
            "scope": "Owned temporary profile; local-build notification test setting only.",
        },
    }
    firefox = sentinel = computer = None
    console = (folder / "firefox-console.log").open("w")
    journal = (folder / "diagnostics.jsonl").open("w")

    def record(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        existing = await asyncio.to_thread(running_firefox)
        result["existing_main_firefox"] = existing
        if existing or not executable.is_file():
            raise RuntimeError("Setup requires an existing Firefox.app executable and no running main Firefox process")
        profile = folder / "profile"
        profile.mkdir()
        (profile / "user.js").write_text(
            "".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n" for k, v in PREFERENCES.items())
        )
        firefox = await asyncio.to_thread(
            subprocess.Popen,
            [str(executable), "--no-remote", "-profile", str(profile), "about:preferences#downloads"],
            stdout=console,
            stderr=console,
        )
        result["owned_firefox_pid"] = firefox.pid
        computer = Computer()
        computer._controller.transport.driver = {"command": str(driver), "args": []}
        app = computer.app(info["CFBundleIdentifier"])
        for attempt in range(40):
            try:
                text = await observe(app, folder, "setup-last")
                chosen, line = button_reference(text, "Choose…")
                original_download = download_value(text)
                break
            except (ValueError, ComputerUseError):
                if attempt == 39 or firefox.poll() is not None:
                    raise
                await asyncio.sleep(0.25)
        target = await asyncio.to_thread(app.element, chosen)
        result["target"] = {"reference": chosen, "line": line, "binding": asdict(target._snapshot["binding"])}
        result["downloads_before"] = original_download
        sentinel_folder = folder / "sentinel"
        sentinel_app = await asyncio.to_thread(build, sentinel_folder, "local.firefox-sentinel." + uuid.uuid4().hex)
        launcher = await asyncio.create_subprocess_exec("open", str(sentinel_app), "--args", "--sentinel")
        if await launcher.wait():
            raise RuntimeError("Owned foreground sentinel failed to launch")
        sentinel = await state(sentinel_folder)
        before = await wait_for(sentinel_folder, lambda s: s["frontmostPID"] == s["pid"] and s["active"])
        assert await asyncio.to_thread(running_firefox) == [firefox.pid], "Another main Firefox process appeared"
        text = await observe(app, folder, "before")
        assert not picker_visible(text) and "standard window Settings" in text and "minimized" not in text, text
        assert download_value(text) == original_download
        before = await state(sentinel_folder)
        assert before["frontmostPID"] == before["pid"] and before["active"], before
        assert before["other"] == "unchanged" and all(before[k] == 0 for k in ("keys", "keyUps", "mouseEvents")), before
        result["before"] = {"time": timestamp(), "sentinel": before}
        result["setup_ready"] = True
        if options.operation == "focus-action":
            result["focus"] = {"started_at": timestamp(), **await outcome(lambda: target.focus())}
            result["after_focus"] = {"time": timestamp(), "sentinel": await state(sentinel_folder)}
            result["focus"]["foreground_changed"] = (
                before["frontmostPID"] != result["after_focus"]["sentinel"]["frontmostPID"]
            )
        result["action"] = {
            "started_at": timestamp(),
            **await outcome(
                lambda: target.click() if options.operation == "click" else target.perform_action("AXPress")
            ),
        }
        for name, delay in (("early", 0.15), ("late", 1.0)):
            await asyncio.sleep(delay)
            text = await observe(app, folder, name)
            result[name] = {
                "time": timestamp(),
                "picker_visible": picker_visible(text),
                "sentinel": await state(sentinel_folder),
            }
        result["application_effect"] = "confirmed" if result["late"]["picker_visible"] else "unconfirmed"
        if result["late"]["picker_visible"]:
            cancel, cancel_line = button_reference(text, "Cancel")
            result["cancel"] = {
                "observed_line": cancel_line,
                **await outcome(lambda: app.element(cancel).perform_action("AXPress")),
            }
        text = await observe(app, folder, "after-cancel")
        assert not picker_visible(text), "The owned picker remains open; no cancellation is replayed"
        result["downloads_after"] = download_value(text)
        assert result["downloads_after"] == original_download
        final = await state(sentinel_folder)
        result["sentinel_final"] = final
        assert all(final[k] == before[k] for k in ("other", "keys", "keyUps", "mouseEvents")), final
        result["complete"] = True
    except Exception as error:
        result["failure"] = {"type": type(error).__name__, "detail": str(error), "time": timestamp()}
        raise
    finally:
        cleanup = []
        if computer is not None:
            try:
                await asyncio.to_thread(computer.close)
            except (OSError, RuntimeError) as error:
                cleanup.append({"component": "transport", "error": str(error)})
        for label, pid in (
            ("sentinel", sentinel["pid"] if sentinel else None),
            ("firefox", firefox.pid if firefox else None),
        ):
            if pid is not None and (label != "firefox" or firefox.poll() is None):
                try:
                    os.kill(pid, signal.SIGTERM)
                    cleanup.append({"component": label, "pid": pid, "terminated": True})
                except ProcessLookupError:
                    pass
        if firefox is not None:
            try:
                await asyncio.to_thread(firefox.wait, timeout=10)
            except subprocess.TimeoutExpired:
                firefox.kill()
                await asyncio.to_thread(firefox.wait)
        result["cleanup"] = cleanup
        result["source_drift"] = [
            str(p) for p in sources if hashlib.sha256(p.read_bytes()).hexdigest() != result["source_sha256"][str(p)]
        ]
        set_diagnostic_handler(None)
        console.close()
        journal.close()
        (folder / "result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--operation", choices=("click", "action", "focus-action"), default="action")
    options = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="firefox-action-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, options))
    finally:
        LOG.info("Firefox action evidence: %s", folder)


if __name__ == "__main__":
    main()
