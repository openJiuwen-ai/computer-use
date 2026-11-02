# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Compare row presentation changes with original field/record continuity.

Default mode requires that the same field remain usable across row presentation
changes and subsequent reads. --expect-rejection reproduces the old driver's
row-label rejection, including its partial first input and refused later calls.
--build-only compiles the disposable app and read-only AX helper without opening UI.
The fixture separately publishes its model truth and AX identifier policy; an
arbitrary application's accessibilityIdentifier is not assumed to be a data key.
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

import computer_use
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.errors import ComputerUseError
from computer_use.paths import native_driver

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
IDENTITY_SOURCES = (
    "os_adapter/macos/Sources/Objects/ElementIdentity.swift",
    "os_adapter/macos/Sources/Objects/AXIdentity.swift",
    "os_adapter/macos/Sources/Objects/ApplicationElements.swift",
    "os_adapter/macos/Sources/Objects/ObservedElements.swift",
    "os_adapter/macos/Sources/Objects/NativeElementInspection.swift",
    "os_adapter/macos/Sources/Control/NativeCellEditorState.swift",
    "os_adapter/macos/Sources/Control/NativeCellEditorHandoff.swift",
    "tests/fixtures/RowIdentityProbe.swift",
    "tests/fixtures/RowIdentityAXProbe.swift",
    "tests/manual/row_identity_workflow.py",
)


def build(folder, bundle):
    app = folder / "RowIdentityProbe.app"
    binary = app / "Contents/MacOS/RowIdentityProbe"
    binary.parent.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": bundle,
                "CFBundleExecutable": "RowIdentityProbe",
                "CFBundleName": "Row identity probe",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "1",
                "NSHighResolutionCapable": True,
            }
        )
    )
    probe = folder / "row-identity-ax-probe"
    for source, output in (("RowIdentityProbe", binary), ("RowIdentityAXProbe", probe)):
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
    return app, probe


async def state(folder):
    for _ in range(100):
        try:
            value = json.loads((folder / "state.json").read_text())
            assert value["other"] == "unchanged", value
            return value
        except FileNotFoundError:
            await asyncio.sleep(0.05)
    raise TimeoutError("Row fixture did not publish state")


async def wait_state(folder, predicate):
    for _ in range(100):
        value = await state(folder)
        if predicate(value):
            return value
        await asyncio.sleep(0.05)
    raise AssertionError(value)


async def command(folder, kind, **parameters):
    identifier = uuid.uuid4().hex
    (folder / "command.json").write_text(json.dumps({"id": identifier, "kind": kind, **parameters}))
    return await wait_state(folder, lambda value: value["commandId"] == identifier)


async def configure(folder, *, derived=True, publishing="record"):
    return await command(
        folder,
        "configure",
        dataKey="A",
        publishing=publishing,
        derivedDescription=derived,
        value="seed",
    )


async def outcome(operation):
    try:
        return {"receipt": dict(await operation)}
    except ComputerUseError as error:
        return {"error": {"code": error.code, "effect": error.effect, "detail": error.detail}}


def create_computer(driver):
    computer = Computer()
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    return computer


class AXProbe:
    def __init__(self, process):
        self.process = process

    async def sample(self, *, capture=False):
        self.process.stdin.write(b"capture\n" if capture else b"sample\n")
        await self.process.stdin.drain()
        raw = await asyncio.wait_for(self.process.stdout.readline(), timeout=10)
        if not raw:
            raise RuntimeError("Read-only AX helper stopped before publishing its sample")
        return json.loads(raw)

    async def close(self):
        self.process.stdin.close()
        await self.process.wait()


async def retained_field(computer, bundle, folder, name):
    app = computer.app(bundle)
    observation = await app.read()
    (folder / (name + "-before.txt")).write_text(observation.text)
    field = await app.element([{"identifier": "row-field-control"}])
    return app, field


async def text_case(folder, bundle, probe, journal, name, *, derived, expect_rejection, driver):
    before = await configure(folder, derived=derived)
    computer = create_computer(driver)
    try:
        app, field = await retained_field(computer, bundle, folder, name)
        binding = asdict(field._snapshot["binding"])
        raw_before = await probe.sample(capture=True)
        first = await outcome(field.type_text("abcd"))
        await asyncio.sleep(0.1)
        after = await state(folder)
        raw_after = await probe.sample()
        # Keep using the original Element after reconciliation, not a fresh ref.
        observation = await app.read()
        (folder / (name + "-after-input.txt")).write_text(observation.text)
        assert asdict(field._snapshot["binding"]) == binding
        raw_after_read = await probe.sample()
        tab = await outcome(field.press_key("Tab"))
        await asyncio.sleep(0.05)
        after_tab = await state(folder)
        await command(folder, "focus")
        following = await outcome(field.type_text("Z"))
        await asyncio.sleep(0.05)
        final = await state(folder)
        assert before["dataKey"] == final["dataKey"] == "A"
        assert before["fieldObjectID"] == final["fieldObjectID"]
        assert before["rowObjectID"] == final["rowObjectID"]
        assert final["valueWrites"] == 0, final
        if raw_before["trusted"]:
            assert raw_before["sameField"] and raw_before["sameRow"], raw_before
            assert raw_after["sameField"] and raw_after["sameRow"], raw_after
            assert raw_after_read["sameField"] and raw_after_read["sameRow"], raw_after_read
        if not expect_rejection:
            assert "receipt" in first and after["value"] == "seedabcd", (first, after)
            assert "receipt" in tab and after_tab["tabKeys"] == 1, (tab, after_tab)
            assert not after_tab["fieldFocused"], after_tab
            assert "receipt" in following and final["value"] == "seedabcdZ", (following, final)
        else:
            assert "error" in first and first["error"]["effect"] == "partial", (first, after)
            assert after["callbacks"] > 0 and after["value"] != "seedabcd", (first, after)
            assert "error" in tab and after_tab["tabKeys"] == 0, (tab, after_tab)
            assert "error" in following and final["value"] == after["value"], (following, final)
        return {
            "name": name,
            "native_binding": binding,
            "original_binding_after_read": asdict(field._snapshot["binding"]),
            "before": before,
            "first_input": first,
            "after_input": after,
            "tab": tab,
            "after_tab": after_tab,
            "next_input": following,
            "final": final,
            "ax_before": raw_before,
            "ax_after": raw_after,
            "ax_after_read": raw_after_read,
        }
    finally:
        journal.flush()
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()


async def guard_case(folder, bundle, probe, name, *, mutation, driver):
    before = await configure(folder, derived=False)
    computer = create_computer(driver)
    try:
        _, field = await retained_field(computer, bundle, folder, name)
        raw_before = await probe.sample(capture=True)
        if mutation == "replace":
            changed = await command(folder, "replace")
            changed = await wait_state(folder, lambda value: not value["oldRowAlive"] and not value["oldFieldAlive"])
        elif mutation == "cell_position":
            changed = await command(folder, "cell_position", rowIndex=1)
        else:
            changed = await command(folder, "reuse", dataKey="B", value="other-record")
        action = await outcome(field.type_text("X"))
        await asyncio.sleep(0.1)
        after = await state(folder)
        raw_after = await probe.sample()
        assert "error" in action, action
        assert action["error"]["effect"] == "none", action
        assert after["callbacks"] == changed["callbacks"] == 0, (changed, after)
        assert after["value"] == changed["value"] and after["valueWrites"] == 0, (action, after)
        if mutation == "replace":
            assert before["fieldObjectID"] != after["fieldObjectID"], (before, after)
            assert not after["oldRowAlive"] and not after["oldFieldAlive"], after
        else:
            assert before["fieldObjectID"] == after["fieldObjectID"], (before, after)
            assert before["rowObjectID"] == after["rowObjectID"], (before, after)
        if mutation == "cell_position":
            assert before["cellRowIndexRange"] == {"location": 0, "length": 1}, before
            assert after["cellRowIndexRange"] == {"location": 1, "length": 1}, after
        return {
            "name": name,
            "before": before,
            "changed": changed,
            "action": action,
            "after": after,
            "ax_before": raw_before,
            "ax_after": raw_after,
        }
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()


async def presentation_case(folder, bundle, probe, name, *, reuse, expect_rejection, driver):
    before = await configure(folder, publishing="static" if reuse else "record")
    computer = create_computer(driver)
    try:
        _, field = await retained_field(computer, bundle, folder, name)
        raw_before = await probe.sample(capture=True)
        changed = (
            await command(folder, "reuse", dataKey="B", value="other-record")
            if reuse
            else await command(folder, "description", text="Updated row presentation before input")
        )
        action = await outcome(field.type_text("X"))
        await asyncio.sleep(0.1)
        after = await state(folder)
        raw_after = await probe.sample()
        assert before["rowObjectID"] == after["rowObjectID"], (before, after)
        assert before["fieldObjectID"] == after["fieldObjectID"], (before, after)
        assert before["rowDescription"] != changed["rowDescription"], (before, changed)
        assert after["valueWrites"] == 0, after
        if expect_rejection:
            assert "error" in action and action["error"]["effect"] == "none", action
            assert after["callbacks"] == 0 and after["value"] == changed["value"], (action, after)
        else:
            assert "receipt" in action and after["value"] == changed["value"] + "X", (action, after)
            assert after["callbacks"] > 0, after
        assert before["dataKey"] == "A" and after["dataKey"] == ("B" if reuse else "A"), (before, after)
        if raw_before["trusted"]:
            assert raw_after["sameField"] and raw_after["sameRow"], raw_after
        return {
            "name": name,
            "business_identity": (
                "No general business-record key is available: the same native field now displays fixture record B."
                if reuse
                else "Only the row presentation changed; fixture record A and all native objects remain the same."
            ),
            "before": before,
            "changed": changed,
            "action": action,
            "after": after,
            "ax_before": raw_before,
            "ax_after": raw_after,
        }
    finally:
        computer._controller.transport.close()
        await computer._controller.transport.wait_closed()


async def verify(folder, bundle, app, helper, expect_rejection, driver=None):
    opener = await asyncio.create_subprocess_exec("open", "-g", str(app))
    if await opener.wait():
        raise RuntimeError("Failed to open the disposable row fixture")
    initial = await state(folder)
    process = await asyncio.create_subprocess_exec(
        str(helper), str(initial["pid"]), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
    )
    probe = AXProbe(process)
    journal = (folder / "diagnostics.jsonl").open("w", encoding="utf-8")
    driver = Path(driver if driver is not None else native_driver()["command"]).resolve()
    report = {
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in IDENTITY_SOURCES},
        "expect_rejection": expect_rejection,
        "provider_contract": {
            "record": "This fixture publishes its model dataKey as the row accessibilityIdentifier.",
            "static": "The row test identifier is static while its model dataKey can change.",
            "limitation": "An arbitrary accessibilityIdentifier is not assumed to identify business data.",
        },
        "cases": [],
    }

    def record(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        report["cases"].append(
            await text_case(
                folder,
                bundle,
                probe,
                journal,
                "fixed-description-control",
                derived=False,
                expect_rejection=False,
                driver=driver,
            )
        )
        report["cases"].append(
            await text_case(
                folder,
                bundle,
                probe,
                journal,
                "derived-description",
                derived=True,
                expect_rejection=expect_rejection,
                driver=driver,
            )
        )
        before = await configure(folder, derived=True)
        raw_before = await probe.sample(capture=True)
        await command(folder, "native_insert", text="abcd")
        after = await wait_state(folder, lambda value: value["value"] == "seedabcd" and value["callbacks"] > 0)
        raw_after = await probe.sample()
        assert before["fieldObjectID"] == after["fieldObjectID"] and before["dataKey"] == after["dataKey"]
        report["cases"].append(
            {
                "name": "direct-native-input",
                "before": before,
                "after": after,
                "ax_before": raw_before,
                "ax_after": raw_after,
            }
        )
        for name, reuse in [
            ("description-before-operation", False),
            ("static-identifier-view-reused", True),
        ]:
            report["cases"].append(
                await presentation_case(
                    folder, bundle, probe, name, reuse=reuse, expect_rejection=expect_rejection, driver=driver
                )
            )
        for name, mutation in [
            ("record-key-changed", "reuse"),
            ("cell-position-changed", "cell_position"),
            ("native-object-replaced", "replace"),
        ]:
            report["cases"].append(await guard_case(folder, bundle, probe, name, mutation=mutation, driver=driver))
        report["oracle_passed"] = True
    finally:
        report["source_drift"] = [
            name
            for name, digest in report["source_sha256"].items()
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest
        ]
        (folder / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
        set_diagnostic_handler(None)
        journal.close()
        await probe.close()
        os.kill(initial["pid"], signal.SIGTERM)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--expect-rejection", action="store_true")
    parser.add_argument("--driver", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    output = ROOT / ".artifacts"
    output.mkdir(exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="row-identity-", dir=output))
    bundle = "local.computer-use.row-identity." + uuid.uuid4().hex
    try:
        app, helper = build(folder, bundle)
        if not args.build_only:
            asyncio.run(verify(folder, bundle, app, helper, args.expect_rejection, args.driver))
    finally:
        LOG.info("Row identity evidence: %s", folder)


if __name__ == "__main__":
    main()
