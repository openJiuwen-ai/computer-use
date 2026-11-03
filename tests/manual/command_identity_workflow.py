# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Probe standard NSButton AXPress when only its title/description changes.

Default mode records the current label-identity rejection. --expect-compatible
requires repeated clicks and reconciliation to retain the original handle.
--build-only compiles the fixture and read-only helper without opening UI.
All callbacks are reversible counters in a disposable window. The fixture uses
AppKit's standard AXPress, with no custom accessibility action implementation.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import plistlib
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

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
SOURCES = (
    "os_adapter/macos/Sources/Objects/ElementIdentity.swift",
    "os_adapter/macos/Sources/Objects/NativeElementInspection.swift",
    "os_adapter/macos/Sources/Objects/ApplicationElements.swift",
    "os_adapter/macos/Sources/Objects/ObservedElements.swift",
    "os_adapter/macos/Sources/Control/NativeActionUnit.swift",
    "tests/fixtures/CommandIdentityProbe.swift",
    "tests/fixtures/CommandIdentityAXProbe.swift",
    "tests/manual/command_identity_workflow.py",
)


def build(folder, bundle):
    app = folder / "CommandIdentityProbe.app"
    binary = app / "Contents/MacOS/CommandIdentityProbe"
    binary.parent.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": bundle,
                "CFBundleExecutable": "CommandIdentityProbe",
                "CFBundleName": "Command identity probe",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
                "NSHighResolutionCapable": True,
            }
        )
    )
    helper = folder / "command-identity-ax-probe"
    for source, output in (("CommandIdentityProbe", binary), ("CommandIdentityAXProbe", helper)):
        subprocess.run(
            [
                "swiftc",
                "-swift-version",
                "6",
                "-warnings-as-errors",
                "-parse-as-library",
                str(ROOT / "tests/fixtures" / (source + ".swift")),
                "-o",
                str(output),
            ],
            check=True,
        )
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True)
    return app, helper


async def state(folder, predicate=lambda _: True):
    for _ in range(100):
        try:
            value = json.loads((folder / "state.json").read_text())
            assert value["otherClicks"] == 0, value
            if predicate(value):
                return value
        except FileNotFoundError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError("Command fixture did not publish the expected state")


async def command(folder, kind, **parameters):
    identifier = uuid.uuid4().hex
    (folder / "command.json").write_text(json.dumps({"id": identifier, "kind": kind, **parameters}))
    return await state(folder, lambda value: value["commandId"] == identifier)


async def outcome(operation):
    try:
        return {"receipt": dict(await operation)}
    except ComputerUseError as error:
        return {"error": {"code": error.code, "effect": error.effect, "detail": error.detail}}


class AXProbe:
    def __init__(self, process):
        self.process = process

    async def sample(self, *, capture=False):
        self.process.stdin.write(b"capture\n" if capture else b"sample\n")
        await self.process.stdin.drain()
        raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=10)
        if not raw:
            raise RuntimeError("Read-only AX helper stopped")
        return json.loads(raw)

    async def close(self):
        self.process.stdin.close()
        await self.process.wait()


def same_native(before, after, raw):
    assert before["buttonObjectID"] == after["buttonObjectID"], (before, after)
    assert raw["trusted"] and raw["sameButton"] and raw["sameWindow"] and raw["sameParent"], raw
    assert raw["button"]["AXRole"]["value"] == "AXButton", raw
    assert "AXPress" in raw["button"]["actions"]["names"], raw


async def case(folder, bundle, driver, probe, name, *, label_policy, compatible, mutation=None, read_first=False):
    before = await command(folder, "configure", labelPolicy=label_policy)
    computer = Computer()
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    result = {"name": name, "before": before}
    try:
        app = computer.app(bundle)
        observation = await app.read()
        (folder / (name + "-before.txt")).write_text(observation.text)
        button = await app.element([{"identifier": "command-control"}])
        binding = asdict(button._snapshot["binding"])
        result["binding"] = binding
        result["ax_before"] = await probe.sample(capture=True)
        if mutation:
            if mutation == "label":
                changed = await command(folder, "label", title="External title", description="External description")
            else:
                changed = await command(folder, mutation)
            if mutation == "replace":
                changed = await state(folder, lambda value: not value["oldButtonAlive"])
            result["changed"] = changed
        first = await outcome(button.click())
        await asyncio.sleep(0.1)
        after = await state(folder)
        raw_after = await probe.sample()
        result.update(first=first, after_first=after, ax_after_first=raw_after)
        if mutation in {"identity", "replace"}:
            assert "error" in first and first["error"]["effect"] == "none", first
            assert after["count"] == 0, after
            if mutation == "replace":
                assert before["buttonObjectID"] != after["buttonObjectID"], (before, after)
                assert raw_after["sameButton"] is False, raw_after
                assert raw_after["originalButton"]["AXRole"]["status"] == -25202, raw_after
            else:
                assert before["identifier"] != after["identifier"], (before, after)
                same_native(before, after, raw_after)
            return result

        same_native(before, after, raw_after)
        assert before["identifier"] == after["identifier"], (before, after)
        if mutation == "label" and not compatible:
            assert "error" in first and first["error"]["effect"] == "none", first
            assert after["count"] == 0, after
        else:
            assert "receipt" in first and after["count"] == 1, (first, after)
        if read_first:
            observation = await app.read()
            (folder / (name + "-after-first.txt")).write_text(observation.text)
        second = await outcome(button.click())
        await asyncio.sleep(0.1)
        final = await state(folder)
        raw_final = await probe.sample()
        same_native(before, final, raw_final)
        assert asdict(button._snapshot["binding"]) == binding
        if compatible:
            assert "receipt" in second and final["count"] == 2, (second, final)
        else:
            assert "error" in second and second["error"]["effect"] == "none", second
            assert final["count"] == after["count"], (after, final)
        result.update(second=second, final=final, ax_final=raw_final, binding_after=asdict(button._snapshot["binding"]))
        return result
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()


async def verify(folder, bundle, app, helper, driver, expect_compatible):
    opener = await asyncio.create_subprocess_exec("open", "-g", str(app))
    if await opener.wait():
        raise RuntimeError("Failed to open the disposable command fixture")
    initial = await state(folder)
    process = await asyncio.create_subprocess_exec(
        str(helper), str(initial["pid"]), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
    )
    probe = AXProbe(process)
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    events = []
    report = {
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
        "expect_compatible": expect_compatible,
        "contract": "A retained native button is distinct from an unobservable immutable logical command.",
        "cases": [],
    }

    def record(value):
        events.append(value)
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        matrix = [
            ("fixed-control", "fixed", True, None, True),
            ("title-self-change", "title", expect_compatible, None, False),
            ("description-self-change", "description", expect_compatible, None, False),
            ("both-self-change-then-read", "both", expect_compatible, None, True),
            ("external-label-change", "fixed", expect_compatible, "label", True),
            ("identifier-changed", "fixed", False, "identity", False),
            ("native-button-replaced", "fixed", False, "replace", False),
        ]
        for name, policy, compatible, mutation, read_first in matrix:
            report["cases"].append(
                await case(
                    folder,
                    bundle,
                    driver,
                    probe,
                    name,
                    label_policy=policy,
                    compatible=compatible,
                    mutation=mutation,
                    read_first=read_first,
                )
            )
        effects = [
            value["value"]
            for value in events
            if value.get("kind") == "backend_diagnostic"
            and isinstance(value.get("value"), dict)
            and value["value"].get("kind") == "input_activity"
            and value["value"].get("phase") == "willCall"
        ]
        assert any(value.get("mechanism") == "AXPress" for value in effects), effects
        assert all(value.get("device") not in {"mouse", "keyboard"} for value in effects), effects
        report["effects"] = effects
        report["oracle_passed"] = True
    finally:
        report["source_drift"] = [
            name
            for name, digest in report["source_sha256"].items()
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
        ]
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n")
        set_diagnostic_handler(None)
        journal.close()
        await probe.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--expect-compatible", action="store_true")
    parser.add_argument("--driver", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="command-identity-", dir=output))
    bundle = "local.computer-use.command-identity." + uuid.uuid4().hex
    driver = Path(args.driver or native_driver()["command"]).resolve()
    try:
        app, helper = build(folder, bundle)
        if not args.build_only:
            asyncio.run(verify(folder, bundle, app, helper, driver, args.expect_compatible))
    finally:
        LOG.info("Command identity evidence: %s", folder)


if __name__ == "__main__":
    main()
