# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Diagnose fixed window-directed pointer delivery to an owned custom NSView.

This deliberately bypasses SDK candidate selection, but compiles and calls the
existing native preparation, gesture, dispatch and release implementation. It is
primitive evidence, never a passing SDK-chain result. Computer(False) is used
only to observe the disposable PointerButtonProbe. No global input is granted.
Build-only never launches a fixture or sends input; each live case sends once.
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
from pointer_button_workflow import CASES, expected_callbacks, snapshot

ROOT = Path(__file__).resolve().parents[2]
LOG = logging.getLogger(__name__)
PROBE_SOURCE = ROOT / "tests/fixtures/AddressedPointerMechanismProbe.swift"


def build_probe(folder):
    """Freeze source files before an isolated compile; do not touch native .build."""
    folder.mkdir(parents=True, exist_ok=True)
    sources = [
        path for path in sorted((ROOT / "os_adapter/macos/Sources").rglob("*.swift")) if path.name != "main.swift"
    ]
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
    for source in sources:
        assert manifest[str(source.relative_to(ROOT))] == hashlib.sha256(source.read_bytes()).hexdigest(), (
            "Native source changed during probe snapshot; build from a stable checkout",
            source,
        )
    binary = folder / "AddressedPointerMechanismProbe"
    command = ["swiftc", "-swift-version", "6", "-parse-as-library", *copies, "-o", str(binary)]
    (folder / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (folder / "build-command.json").write_text(json.dumps(command, indent=2) + "\n")
    with (folder / "build.log").open("w") as output:
        result = subprocess.run(command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Native probe compile failed ({result.returncode}); see {folder / 'build.log'}")
    (folder / "build-result.json").write_text(
        json.dumps(
            {
                "binary": str(binary),
                "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "scope": "standalone diagnostic; unchanged production sources except replacement main",
            },
            indent=2,
        )
        + "\n"
    )
    return binary


async def invoke_probe(command, stream):
    """Let the probe drain its existing paired release before forced shutdown."""
    process = await asyncio.create_subprocess_exec(*command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    termination = None
    try:
        await asyncio.wait_for(process.wait(), timeout=25)
    except TimeoutError:
        termination = "deadline; requested native cancellation and paired cleanup"
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            termination = "unresponsive after cancellation; forced termination, cleanup unknown"
            process.kill()
            await process.wait()
    return process.returncode, termination


async def verify(folder, selected, preparation, binary, driver_option):
    bundle = "local.computer-use.pointer-buttons." + uuid.uuid4().hex
    driver = Path(driver_option or native_driver()["command"]).resolve()
    computer = Computer(allow_global_input=False)
    computer._controller.transport.driver = {"command": str(driver), "args": []}
    journal = (folder / "sdk-observation-diagnostics.jsonl").open("w", encoding="utf-8")
    initial = None
    report = {
        "passed": False,
        "scope": "primitive_diagnostic_not_sdk_acceptance",
        "allow_global_input": False,
        "selected_delivery": "surface_pointer",
        "preparation": preparation,
        "sdk": computer_use.__file__,
        "observation_driver": str(driver),
        "observation_driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
        "probe": str(binary),
        "probe_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "probe_source_manifest": str(binary.parent / "source-manifest.json"),
        "fixture_source_sha256": hashlib.sha256(
            (ROOT / "tests/fixtures/PointerButtonProbe.swift").read_bytes()
        ).hexdigest(),
        "runner_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": [],
    }

    def record(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=str) + "\n")
        journal.flush()

    set_diagnostic_handler(record)
    try:
        await asyncio.to_thread(build_fixture, folder, bundle, "PointerButtonProbe")
        initial = await state(folder)
        report["initial"] = initial
        app = computer.app(bundle)
        for case in selected:
            await snapshot(app, folder, f"{case}-before")
            before = await state(folder)
            assert not any(before["heldButtons"].values()), before["heldButtons"]
            image = app._image
            root = computer._controller.state(app).selection.root
            pieces = [p for p in image.pieces if p.image.target.surface == root.surface]
            assert len(pieces) == 1, "The exact original root must own one screenshot piece"
            piece = pieces[0]
            x, y, width, height = piece.box
            image_point = (
                x + before["targetPoint"][0] * width / before["width"],
                y + before["targetPoint"][1] * height / before["height"],
            )
            assert image.hit(image_point)[0] is piece
            # Use the original screenshot's logical window basis. The native
            # probe independently validates exact CGWindow/PID and current frame.
            local_point = (
                (image_point[0] - x) * before["width"] / width,
                (image_point[1] - y) * before["height"] / height,
            )
            button, count = CASES[case]
            output = folder / f"{case}-native.json"
            command = [
                str(binary),
                "--pid",
                str(before["pid"]),
                "--window",
                str(before["window"]),
                "--bundle",
                bundle,
                "--x",
                str(local_point[0]),
                "--y",
                str(local_point[1]),
                "--width",
                str(before["width"]),
                "--height",
                str(before["height"]),
                "--button",
                button,
                "--count",
                str(count),
                "--preparation",
                preparation,
                "--output",
                str(output),
            ]
            row = {
                "case": case,
                "before": before,
                "image_point": image_point,
                "window_local_point": local_point,
                "button": button,
                "count": count,
                "caller_invocations": 1,
                "command": command,
            }
            report["cases"].append(row)
            started = time.monotonic()
            # One native invocation only. A failure never causes automatic
            # foreground/global fallback or a second pointer sequence.
            with (folder / f"{case}-probe.log").open("w") as stream:
                returncode, termination = await invoke_probe(command, stream)
            row["seconds"] = time.monotonic() - started
            row["returncode"] = returncode
            row["termination"] = termination
            assert output.is_file(), "Probe returned no receipt; dispatch/cleanup remain unknown"
            native = json.loads(output.read_text())
            row["native"] = native
            for _ in range(60):
                after = await state(folder)
                if not native["passed"] or len(after["callbacks"]) >= len(before["callbacks"]) + 2 * count:
                    break
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.15)
            after = await state(folder)
            row["after"] = after
            callbacks = after["callbacks"][len(before["callbacks"]) :]
            row["callback_delta"] = callbacks
            assert termination is None and returncode == 0 and native["passed"], native
            assert native["selected_delivery"] == "surface_pointer" and native["global_input_allowed"] is False
            assert native["receiver_qualified"] is False, "The probe must exercise the excluded custom receiver"
            receiver = native["receiver_evidence"]
            assert receiver["role"] == "AXGroup" and receiver["sameWindow"] is True, receiver
            assert receiver["queryStatus"] == 0 and receiver["actions"] == [], receiver
            assert native["dispatch"] == "dispatched" and native["cleanup"] == "complete", native
            assert [(c["callback"], c["button"], c["clickCount"]) for c in callbacks] == expected_callbacks(
                button, count
            )
            assert all(c["window"] == before["window"] for c in callbacks), callbacks
            input_delta = after["inputEvents"][len(before["inputEvents"]) :]
            row["event_delta"] = input_delta
            assert len(input_delta) == 2 * count and all(e["kind"] == "mouse" for e in input_delta), input_delta
            assert [(e["button"], e["clickCount"]) for e in input_delta] == [
                (number, click) for _, number, click in expected_callbacks(button, count)
            ], input_delta
            for name in (
                "guardCallbacks",
                "guardText",
                "cursor",
                "heldButtons",
                "clipboardDigest",
                "clipboardChangeCount",
            ):
                assert after[name] == before[name], (name, before[name], after[name])
            assert before["cursor"] is not None
            impact = native["impact"]
            assert impact["coverage"] == "complete" and impact["omittedEffects"] == 0, impact
            invoked = [e for e in impact["effects"] if e["state"] != "notInvoked"]
            assert not any(e["transport"] == "globalHID" for e in invoked), impact
            mouse = [e for e in invoked if e["device"] == "mouse"]
            assert mouse and all(e["transport"] == "process" and e["state"] == "invoked" for e in mouse), impact
            assert any(e["stage"] == "input" for e in mouse), impact
            row["foreground_changed"] = after["frontmostPID"] != before["frontmostPID"]
            await snapshot(app, folder, f"{case}-after")
            row["callback_verified"] = True
        report["passed"] = True
    except BaseException as error:
        report["failure"] = {"type": type(error).__name__, "detail": str(error)}
        raise
    finally:
        try:
            if initial is not None:
                report["final"] = await state(folder)
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    parser.add_argument("--preparation", choices=("background", "foreground"), default="background")
    parser.add_argument("--probe", type=Path, help="An independently built probe; its sibling manifest is retained")
    parser.add_argument("--driver", help="Driver used only for SDK observation; the probe sends the pointer input")
    parser.add_argument("--build-only", action="store_true", help="Compile the probe without UI or native execution")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    (ROOT / ".artifacts").mkdir(exist_ok=True)
    if args.build_only and args.probe is not None:
        parser.error("--build-only compiles current source and cannot use --probe")
    if args.probe is None:
        output = Path(tempfile.mkdtemp(prefix="pointer-mechanism-build-", dir=ROOT / ".artifacts"))
        binary = build_probe(output)
        LOG.info("Fixed-route native probe: %s", binary)
    else:
        binary = args.probe.resolve(strict=True)
        assert (binary.parent / "source-manifest.json").is_file(), "Retain the probe's source manifest beside it"
    if args.build_only:
        return
    folder = Path(tempfile.mkdtemp(prefix="pointer-mechanism-", dir=ROOT / ".artifacts"))
    try:
        asyncio.run(verify(folder, CASES if args.case == "all" else [args.case], args.preparation, binary, args.driver))
    finally:
        LOG.info("Primitive diagnosis evidence (not SDK acceptance): %s", folder)


if __name__ == "__main__":
    main()
