# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Check borrowed scroll permissions against a real owned NSScrollView viewport.

Run after computer-use-build. Use --case page with a retained --driver for the
formal baseline/current comparison; no failed scroll is replayed in a test case.
Other cases verify wheel, aliases and actual receiver/relationship changes.
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
import uuid
from dataclasses import asdict
from pathlib import Path

from artifact_json import encode_artifact

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver
from input_receiver_workflow import build_fixture, state
from pointer_focus_workflow import require_window_directed_transport
from write_stage_workflow import wait_state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
CASES = (
    "page",
    "wheel",
    "owner_disables",
    "region_disables",
    "bar_disables",
    "page_disables",
    "relation_changes",
    "modal",
    "region_alias_disabled",
    "bar_alias_disabled",
    "page_alias_disabled",
    "wheel_hit_alias_disabled",
)
SOURCES = (
    "os_adapter/macos/Sources/Control/InputContext/PrimitiveDeclarations.swift",
    "os_adapter/macos/Sources/Control/NativeScrollUnit.swift",
    "os_adapter/macos/Sources/Windowing/NativeScopeResolver.swift",
    "computer_use_engine/interaction/write/resolution/scroll.py",
    "computer_use_engine/interaction/write/routes/selection/admission.py",
    "tests/native/macos/Control/NativeScrollOwnerPermissionTests.swift",
    "tests/fixtures/ScrollOwnerPermissionProbe.swift",
    "tests/fixtures/ScrollOwnerPermissionAXProbe.swift",
    "tests/manual/scroll_owner_permission_workflow.py",
)


def native_evidence(helper, pid):
    result = subprocess.run([str(helper), str(pid)], check=True, capture_output=True, text=True, timeout=20)
    value = json.loads(result.stdout)
    assert value["queryOnly"] is True and value["pid"] == pid
    return value


def require_provider(value, mode):
    objects = value["objects"]
    for suffix in ("owner", "area", "bar", "page"):
        assert "scroll-permission-" + suffix in objects, ("missing native provider object", suffix, value)
    owner, area, bar, page = [objects["scroll-permission-" + suffix] for suffix in ("owner", "area", "bar", "page")]
    assert owner["AXRole"]["value"] == "AXTextField" and area["AXRole"]["value"] == "AXScrollArea"
    assert bar["AXRole"]["value"] == "AXScrollBar" and page["AXRole"]["value"] == "AXButton"
    assert page["AXSubrole"]["value"] == "AXIncrementPage" and "AXPress" in page["actions"]["names"]
    assert area["AXVerticalScrollBar"] == ["scroll-permission-bar"]
    assert "scroll-permission-page" in bar["AXChildren"]
    assert page["AXParent"] == ["scroll-permission-bar"]
    windows = [entry["windowHashes"] for entry in (owner, area, bar, page)]
    assert windows[0] and all(entry == windows[0] for entry in windows), "all actual objects must belong to one window"
    expected = {
        "owner": mode == "owner_disables",
        "area": mode != "region_alias_disabled",
        "bar": mode != "bar_alias_disabled",
        "page": mode != "page_alias_disabled",
    }
    for suffix, enabled in expected.items():
        raw = objects["scroll-permission-" + suffix]["AXEnabled"]
        assert raw["status"] == 0 and raw["value"] is enabled, (suffix, raw, enabled)


async def verify(folder, cases, selected_driver):
    require_window_directed_transport()
    helper = folder / "ScrollOwnerPermissionAXProbe"
    await asyncio.to_thread(
        subprocess.run,
        ["swiftc", "-swift-version", "6", "-parse-as-library",
         str(ROOT / "tests/fixtures/ScrollOwnerPermissionAXProbe.swift"), "-o", str(helper)],
        check=True,
    )
    bundle = "local.computer-use.scroll-owner." + uuid.uuid4().hex
    await asyncio.to_thread(build_fixture, folder, bundle, "ScrollOwnerPermissionProbe")
    initial = await state(folder)
    computer = Computer()
    if selected_driver is not None:
        computer._controller.transport.driver = {"command": str(selected_driver), "args": []}
    driver = Path(selected_driver if selected_driver is not None else native_driver()["command"])
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    events = []
    report = {
        "sdk": computer_use.__file__, "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
        "cases": [], "passed": False,
    }

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        app = computer.app(bundle)
        for name in cases:
            command = uuid.uuid4().hex
            temporary = folder / ("command-" + command + ".json")
            temporary.write_text(json.dumps({"id": command, "mode": name}))
            temporary.replace(folder / "command.json")
            before = await wait_state(folder, lambda value, command=command: value["lastCommand"] == command)
            native_before = await asyncio.to_thread(native_evidence, helper, initial["pid"])
            (folder / (name + "-native-before.json")).write_text(json.dumps(native_before, indent=2) + "\n")
            require_provider(native_before, name)
            observed = await app.read(view="both", full=True)
            (folder / (name + "-before.txt")).write_text(observed.text)
            if observed.screenshot is not None:
                (folder / (name + "-before.png")).write_bytes(observed.screenshot)
            label = {
                "region_alias_disabled": "Borrowed scroll viewport",
                "bar_alias_disabled": "Borrowed vertical scroll bar",
                "page_alias_disabled": "Page down",
            }.get(name, "Scroll content anchor")
            target = await app.element(label)
            wheel = name in ("wheel", "wheel_hit_alias_disabled")
            pages = 0.5 if wheel else 1 if name == "page" or name.endswith("alias_disabled") else 3
            start = len(events)
            error = None
            try:
                receipt = dict(await target.scroll("down", pages=pages))
            except ComputerUseError as caught:
                error = caught
                receipt = {"code": caught.code, "effect": caught.effect, "detail": caught.detail}
            await asyncio.sleep(0.1)
            after = await state(folder)
            effects = [
                item["value"] for item in events[start:]
                if item.get("kind") == "backend_diagnostic" and isinstance(item.get("value"), dict)
                and item["value"].get("kind") == "input_activity"
                and item["value"].get("phase") == "willCall"
            ]
            row = {
                "case": name, "binding": asdict(target._snapshot["binding"]), "pages": pages,
                "before": before, "result": receipt, "after": after, "native_before": native_before,
                "native_after": await asyncio.to_thread(native_evidence, helper, initial["pid"]), "effects": effects,
            }
            report["cases"].append(row)
            assert after["owner"] == before["owner"] and after["other"] == before["other"]
            assert after["keyEvents"] == 0 and not any(item in (1, 2, 3, 4) for item in after["mouseEvents"])
            assert after["frontmostPID"] == before["frontmostPID"], "owned scrolling must preserve system foreground"
            assert after["cursor"] == before["cursor"], "window-directed scrolling must preserve the system cursor"
            if name in ("page", "owner_disables"):
                expected_pages = 1 if name == "page" else 3
                assert error is None, (name, receipt)
                assert after["pageActions"] == expected_pages and after["offset"] == 240 * expected_pages
                assert 22 not in after["mouseEvents"], "native pages must not be replaced with wheel input"
                if name == "owner_disables":
                    assert before["ownerEnabled"] is True and after["ownerEnabled"] is False
            elif name == "wheel":
                assert error is None and after["offset"] > 10 and after["pageActions"] == 0, (name, receipt)
                assert 22 in after["mouseEvents"], "fractional scrolling must actually reach the owned viewport"
            elif name.endswith("alias_disabled"):
                assert error is not None and error.code == "input_unavailable", (name, receipt)
                assert after["pageActions"] == 0 and after["offset"] == 0 and 22 not in after["mouseEvents"]
            else:
                assert error is not None and error.effect == "partial", (name, receipt)
                if name in ("region_disables", "bar_disables", "page_disables"):
                    assert error.code == "input_unavailable" and "scrolling control is disabled" in error.detail
                    assert after[{"region_disables": "regionEnabled", "bar_disables": "barEnabled",
                                  "page_disables": "pageEnabled"}[name]] is False
                elif name == "relation_changes":
                    assert error.code == "input_unavailable" and "scroll bar left its region" in error.detail
                    assert after["conflictingBar"] is True
                else:
                    assert name == "modal" and error.code == "blocked_by_modal" and after["modal"] is True
                assert after["pageActions"] == 1 and after["offset"] == 240, (name, after)
                assert 22 not in after["mouseEvents"], "a partial page action cannot be replayed through wheel"
        report["passed"] = True
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()
        set_diagnostic_handler(None)
        journal.close()
        os.kill(initial["pid"], signal.SIGTERM)
        for _ in range(100):
            try:
                os.kill(initial["pid"], 0)
            except ProcessLookupError:
                report["owned_fixture_terminated"] = True
                break
            await asyncio.sleep(0.02)
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", nargs="+", choices=CASES, default=CASES)
    parser.add_argument("--driver", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = Path(tempfile.mkdtemp(prefix="scroll-owner-permission-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, args.case, args.driver))
    finally:
        LOG.info("Scroll owner evidence: %s", folder)


if __name__ == "__main__":
    main()
