# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Select an owned row via SDK, then diagnose one fixed native element click.

The second operation deliberately bypasses candidate selection, while retaining
the real original element and calling the existing native delivery/release unit.
It is primitive evidence, not a successful public SDK re-click. Build-only never
launches a fixture or sends input. No global input is granted.
"""

import argparse
import asyncio
import hashlib
import json
import logging
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import computer_use
from addressed_pointer_mechanism_workflow import invoke_probe
from artifact_json import encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from document_action_workflow import cleanup_fixture
from input_receiver_workflow import build_fixture, state
from row_selection_workflow import actual_effects, beta_row_reference, row_click_delivery, wait_state

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
PROBE_SOURCE = ROOT / "tests/fixtures/ElementRowPointerMechanismProbe.swift"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_probe(folder):
    """Compile frozen native sources with a separate entry point and output."""
    sources = [p for p in sorted((ROOT / "os_adapter/macos/Sources").rglob("*.swift")) if p.name != "main.swift"]
    sources.append(PROBE_SOURCE)
    manifest, copies = {}, []
    for source in sources:
        relative = source.relative_to(ROOT)
        payload = source.read_bytes()
        manifest[str(relative)] = hashlib.sha256(payload).hexdigest()
        copy = folder / "source" / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(payload)
        copies.append(str(copy))
    assert all(manifest[str(p.relative_to(ROOT))] == digest(p) for p in sources), "Source changed during snapshot"
    binary = folder / "ElementRowPointerMechanismProbe"
    command = ["swiftc", "-swift-version", "6", "-parse-as-library", *copies, "-o", str(binary)]
    (folder / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (folder / "build-command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (folder / "build.log").open("w") as stream:
        result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Native probe compile failed ({result.returncode}); see {folder / 'build.log'}")
    (folder / "build-result.json").write_text(json.dumps({
        "binary": str(binary), "sha256": digest(binary),
        "scope": "standalone diagnostic; unchanged production sources except replacement main",
    }, indent=2) + "\n")
    return binary


async def observe(app, folder, name):
    observation = await app.read(view="both", full=True)
    (folder / f"{name}.txt").write_text(observation.text)
    if observation.screenshot:
        (folder / f"{name}.png").write_bytes(observation.screenshot)
    return observation


async def verify(folder, binary, driver_option):
    bundle = "local.computer-use.row-selection." + uuid.uuid4().hex
    driver = Path(driver_option or native_driver()["command"]).resolve()
    computer = Computer(allow_global_input=False)
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    journal = (folder / "sdk-diagnostics.jsonl").open("w")
    report = {
        "passed": False, "scope": "primitive_diagnostic_not_sdk_acceptance", "allow_global_input": False,
        "sdk": computer_use.__file__, "selection_driver": str(driver), "selection_driver_sha256": digest(driver),
        "probe": str(binary), "probe_sha256": digest(binary),
        "probe_source_manifest": str(binary.parent / "source-manifest.json"),
        "fixture_source_sha256": digest(ROOT / "tests/fixtures/RowSelectionProbe.swift"),
        "runner_source_sha256": digest(Path(__file__)),
        "row_workflow_source_sha256": digest(ROOT / "tests/manual/row_selection_workflow.py"),
        "public_selection_calls": 0, "native_pointer_calls": 0,
    }
    initial = None

    def record(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(build_fixture, folder, bundle, "RowSelectionProbe")
        initial = await wait_state(folder, lambda value: all(row["objectID"] is not None for row in value["rows"]))
        report["initial"] = initial
        assert initial["selected"] == [0]
        app = computer.app(bundle)
        observed = await observe(app, folder, "before")
        ref, report["row_selection_source"] = beta_row_reference(observed.text)
        row = await app.element(ref)
        report["public_selection_calls"] += 1
        receipt = await row.click()
        report["selection_receipt"] = dict(receipt)
        before = await wait_state(folder, lambda value: value["selected"] == [1])
        report["before_probe"] = before
        effects = actual_effects(receipt)
        assert receipt["status"] == "verified"
        assert len(receipt.impact.attempts) == 1
        assert receipt.impact.attempts[0].route == "macos.axSelection.replaceRows.v1"
        assert not any(e.device in ("mouse", "keyboard") and e.state != "notInvoked" for e in effects)
        assert before["inputs"] == initial["inputs"] and before["mouseDowns"] == initial["mouseDowns"]
        await observe(app, folder, "selected-before-probe")

        # No second SDK click and no coordinate/capture target: this independent
        # probe retains the exact ordinary AXRow inside the original AX window.
        output = folder / "native.json"
        command = [
            str(binary), "--pid", str(before["pid"]), "--window", str(before["window"]), "--bundle", bundle,
            "--row-index", "1", "--output", str(output),
        ]
        report["command"] = command
        report["native_pointer_calls"] += 1
        started = time.monotonic()
        with (folder / "probe.log").open("w") as stream:
            code, termination = await invoke_probe(command, stream)
        report["probe_seconds"] = time.monotonic() - started
        report["probe_returncode"], report["probe_termination"] = code, termination
        assert output.is_file(), "Probe returned no receipt; dispatch/cleanup remain unknown"
        native = json.loads(output.read_text())
        report["native"] = native
        if native["passed"]:
            after = await wait_state(folder, lambda value: (
                len(value["mouseDowns"]) > len(before["mouseDowns"])
                and len(value["mouseTracking"]) >= len(before["mouseTracking"]) + 2
            ))
        else:
            await asyncio.sleep(0.15)
            after = await state(folder)
        report["after_probe"] = after
        await observe(app, folder, "after-probe")
        assert termination is None and code == 0 and native["passed"], native
        assert native["global_input_allowed"] is False and native["selected_delivery"] == "surface_pointer"
        assert native["target_kind"] == "element" and native["receiver_qualified"] is False
        assert native["dispatch"] == "dispatched" and native["cleanup"] == "complete"
        assert native["before_geometry"]["basis"] == "exactHit"
        assert native["after_preparation_geometry"]["basis"] == "exactHit"
        guards_before, guards_after = native["input_guards_before"], native["input_guards_after"]
        assert guards_before["cursor"] is not None and guards_before["held_buttons"] == 0
        for key in ("cursor", "held_buttons", "clipboard_change_count"):
            assert guards_before[key] == guards_after[key], (key, guards_before, guards_after)
        report.update(row_click_delivery(before, after, initial["window"]))
        for name in ("pid", "window", "windowObjectID", "tableObjectID", "rows", "guard", "cursor"):
            assert after[name] == before[name] == initial[name], (name, initial[name], before[name], after[name])
        assert initial["cursor"] is not None
        assert after["selected"] == [1] and after["summary"] == "Selected: Beta record"
        impact = native["impact"]
        assert impact["coverage"] == "complete" and impact["omittedEffects"] == 0
        invoked = [effect for effect in impact["effects"] if effect["state"] != "notInvoked"]
        assert not any(effect["transport"] == "globalHID" for effect in invoked)
        mouse = [effect for effect in invoked if effect["device"] == "mouse"]
        assert mouse and all(effect["transport"] == "process" and effect["state"] == "invoked" for effect in mouse)
        assert any(effect["stage"] == "input" for effect in mouse)
        assert any(effect["stage"] == "cleanup" for effect in mouse)
        report["foreground_changed"] = after["frontmostPID"] != before["frontmostPID"]
        report["callback_verified"] = True
        report["passed"] = True
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        try:
            computer._controller.transport.close()
            await computer._controller.transport.wait_closed()
        finally:
            set_diagnostic_handler(None)
            journal.close()
            if initial is not None:
                try:
                    report["final_fixture_state"] = await state(folder)
                except (OSError, ValueError, RuntimeError) as error:
                    report["final_fixture_state_error"] = str(error)
            report["cleanup"] = await cleanup_fixture(
                "owned-table", folder, "RowSelectionProbe", initial["pid"] if initial else None
            )
            (folder / "result.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", type=Path)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    if args.build_only and args.probe:
        parser.error("--build-only compiles current source; do not pass --probe")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    if args.probe is None:
        folder = Path(tempfile.mkdtemp(prefix="row-pointer-mechanism-build-", dir=ROOT / ".artifacts"))
        binary = build_probe(folder)
        LOG.info("Independent row pointer probe: %s", binary)
    else:
        binary = args.probe.resolve(strict=True)
        assert (binary.parent / "source-manifest.json").is_file(), "Retain the source manifest beside the probe"
    if args.build_only:
        return
    folder = Path(tempfile.mkdtemp(prefix="row-pointer-mechanism-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, binary, args.driver))
    finally:
        LOG.info("Element pointer diagnostic evidence (not SDK acceptance): %s", folder)


if __name__ == "__main__":
    main()
