# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Isolate AppKit menu callbacks caused by direct, read-only AX queries.

Launches one disposable fixture and a separate native helper. No SDK instance,
accessibility action, attribute setter, activation request or input is sent.
Launching the fixture may bring its own window forward. This diagnostic records
provider behavior; it does not predeclare that menu delegate callbacks are input.
"""

import asyncio
import hashlib
import json
import logging
import subprocess
import tempfile
import uuid
from pathlib import Path

from document_action_workflow import cleanup_fixture
from input_receiver_workflow import state
from keyboard_window_command_workflow import build_owned, launch

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def verify(folder):
    bundle = "local.computer-use.menu-read-query." + uuid.uuid4().hex
    app = await asyncio.to_thread(build_owned, folder, bundle, "KeyboardWindowCommandProbe")
    helper = folder / "menu-read-ax-probe"
    source = ROOT / "tests/fixtures/MenuReadAXProbe.swift"
    await asyncio.to_thread(
        subprocess.run,
        ["swiftc", "-swift-version", "6", "-warnings-as-errors", "-parse-as-library", str(source), "-o", str(helper)],
        check=True,
    )
    report = {
        "sdk_constructed": False,
        "helper": str(helper),
        "helper_sha256": digest(helper),
        "source_sha256": {
            str(path): digest(path)
            for path in (source, ROOT / "tests/fixtures/KeyboardWindowCommandProbe.swift", Path(__file__).resolve())
        },
    }
    owned = None
    try:
        await launch(app, arguments=("--menu-probe",))
        owned = await state(folder)
        report["before"] = owned
        probe = await asyncio.create_subprocess_exec(
            str(helper), str(owned["pid"]), str(folder), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        output, error = await probe.communicate()
        (folder / "probe-output.json").write_bytes(output)
        report["probe_exit"] = probe.returncode
        report["probe_stderr"] = error.decode()
        report["probe"] = json.loads(output)
        report["after"] = await state(folder)
        initial_menu = report["probe"]["initial"]["menu"]
        final_menu = report["probe"]["final"]["menu"]
        report["provider_lifecycle"] = {
            "delegate_opens_delta": final_menu["opens"] - initial_menu["opens"],
            "delegate_closes_delta": final_menu["closes"] - initial_menu["closes"],
            "needs_updates_delta": final_menu["needsUpdates"] - initial_menu["needsUpdates"],
            "tracking_begins_delta": final_menu["trackingBegins"] - initial_menu["trackingBegins"],
            "tracking_ends_delta": final_menu["trackingEnds"] - initial_menu["trackingEnds"],
            "notification_tracking_after": final_menu["notificationTracking"],
            "events": final_menu["lifecycle"],
        }
        assert probe.returncode == 0, report
        assert report["after"]["delegateActions"] == report["after"]["documentActions"] == 0, report
        assert report["after"]["text"] == "KEYBOARD-SEED" and report["after"]["guard"] == "GUARD", report
        report["query_completed_without_command_callback"] = True
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        report["cleanup"] = await cleanup_fixture(
            "target", folder, "KeyboardWindowCommandProbe", owned["pid"] if owned else None
        )
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="menu-read-query-", dir=output))
    try:
        asyncio.run(verify(folder))
    finally:
        LOG.info("Direct AX menu-read evidence: %s", folder)


if __name__ == "__main__":
    main()
