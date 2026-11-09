# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One fresh Cocoa date diagnostic; never a passing acceptance substitute.

Compare the unchanged nonactivating fixture, normal window show, or one public
SDK element/text-point click before the same single replace/Return chain. The
text point comes from an independent fixture oracle and the delivered image's
actual mapping; it is diagnostic data, not a proposed SDK dependency. Optional
native AX probes only read the owned date object's parameterized text bounds.
No active-window setter, synthetic event helper or input retry is used.
Run only with desktop validation authorization. Each invocation owns its child.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import computer_use
from artifact_json import captured_interface_metadata, encode_artifact
from computer_use import Computer
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from computer_use.protocol.captures import capture_metadata
from qt_cocoa_activation_workflow import DEFAULT_PYTHON, ROOT, save_observation, sha, state

FIXTURE = ROOT / "tests/fixtures/qt_cocoa_date_probe.py"
AX_PROBE_SOURCE = ROOT / "tests/fixtures/qt_cocoa_date_ax_probe.swift"
DEFAULT_AX_PROBE = ROOT / ".artifacts/qt-cocoa-date-ax-probe"


def delivered_text_point(app, window, native_point):
    """Invert the retained capture transform, then apply its delivered scaling."""
    identity = app._controller.interfaces.windows.resolve(window, app._controller.transport.session).identity
    image = app._instance.window_images[identity]
    surface = identity.surface
    piece = next(item for item in image.pieces if item.image.target.surface == surface)
    a, b, c, d, tx, ty = piece.image.coordinates["toNative"]
    determinant = a * d - b * c
    assert determinant != 0, "The delivered capture has no invertible native mapping"
    dx, dy = native_point[0] - tx, native_point[1] - ty
    source = ((d * dx - c * dy) / determinant, (-b * dx + a * dy) / determinant)
    metadata = capture_metadata(piece.image.image)
    assert all(0 <= value < metadata[axis] for value, axis in zip(source, ("width", "height")))
    x, y, width, height = piece.box
    canvas = (x + source[0] * width / metadata["width"], y + source[1] * height / metadata["height"])
    point = canvas
    if image.delivered_coordinates is not None:
        mapping = image.delivered_coordinates
        point = tuple(
            v * delivered / retained
            for v, delivered, retained in zip(canvas, mapping.delivered_size, mapping.source_size)
        )
    hit, mapped = image.hit(point)
    assert hit is piece and all(abs(left - right) < 1e-6 for left, right in zip(mapped, source))
    return point, {
        "native_point": native_point,
        "to_native": list(piece.image.coordinates["toNative"]),
        "capture_pixels": [metadata["width"], metadata["height"]],
        "capture_point": source,
        "piece_box": piece.box,
        "canvas_point": canvas,
        "delivered_point": point,
    }


async def diagnose(folder, python, mechanism, ax_probe):
    if sys.platform != "darwin":
        raise RuntimeError("This diagnostic requires macOS Cocoa")
    title = "CU Qt Date Diagnostic " + uuid4().hex[:12]
    driver = Path(native_driver()["command"])
    report = {
        "diagnostic_only": True,
        "mechanism": mechanism,
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sdk": computer_use.__file__,
        "sdk_python": sys.executable,
        "fixture_python": str(python),
        "driver": str(driver),
        "driver_sha256": sha(driver),
        "allow_global_input": False,
        "window_title": title,
        "steps": [],
        "source_hashes": {
            str(path.relative_to(ROOT)): sha(path)
            for directory, pattern in (("computer_use_engine", "*.py"), ("os_adapter/macos/Sources", "*.swift"))
            for path in sorted((ROOT / directory).rglob(pattern))
        },
    }
    for path in (FIXTURE, Path(__file__)):
        report["source_hashes"][str(path.relative_to(ROOT))] = sha(path)
    if mechanism in ("ax-bounds", "text-point-click"):
        if not ax_probe.is_file():
            raise FileNotFoundError(f"Compile the read-only AX probe first: {ax_probe}")
        report["ax_probe"] = {"path": str(ax_probe), "sha256": sha(ax_probe)}
        report["source_hashes"][str(AX_PROBE_SOURCE.relative_to(ROOT))] = sha(AX_PROBE_SOURCE)
    computer = Computer()
    child = None
    events = []
    with (folder / "fixture-stderr.log").open("w") as errors, (folder / "diagnostics.jsonl").open("w") as journal:

        def record(event):
            events.append(event)
            journal.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            journal.flush()

        set_diagnostic_handler(record)
        try:
            previous = {choice.id for choice in await asyncio.to_thread(computer.list_apps) if choice.is_running}
            child = await asyncio.create_subprocess_exec(
                str(python),
                str(FIXTURE),
                "--output",
                str(folder),
                "--title",
                title,
                *(["--normal-show"] if mechanism == "normal-show" else []),
                cwd=ROOT,
                env={**os.environ, "QT_QPA_PLATFORM": "cocoa"},
                stdin=asyncio.subprocess.DEVNULL,
                stdout=errors,
                stderr=errors,
            )
            report["initial"] = await state(folder, lambda value: value["visible"], process=child, seconds=10)
            choices = await asyncio.to_thread(computer.list_apps)
            matching = [choice for choice in choices if choice.is_running and choice.id not in previous]
            assert len(matching) == 1, [dict(choice) for choice in choices]
            report["selected_application"] = dict(matching[0])
            app = computer.app(matching[0].id)
            observation = await asyncio.to_thread(app.read, view="both")
            assert title in observation.text, "Only the owned diagnostic window may receive input"
            save_observation(folder, "before", observation)
            field = await asyncio.to_thread(app.element, "Meeting date")

            if mechanism in ("ax-bounds", "text-point-click"):
                probe = await asyncio.create_subprocess_exec(
                    str(ax_probe),
                    "--pid",
                    str(child.pid),
                    "--window-title",
                    title,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    output, error = await asyncio.wait_for(probe.communicate(), 10)
                except TimeoutError:
                    probe.kill()
                    await probe.wait()
                    raise RuntimeError("The read-only AX probe exceeded its bounded query deadline") from None
                (folder / "native-ax-bounds.json").write_bytes(output)
                (folder / "native-ax-bounds.stderr").write_bytes(error)
                report["native_ax_probe"] = {
                    "returncode": probe.returncode,
                    "stderr": error.decode(errors="replace"),
                    "result": json.loads(output) if output.strip() else None,
                }
                if mechanism == "ax-bounds":
                    return

            async def step(name, call):
                before = await state(folder, process=child)
                item = {"name": name, "before": before}
                report["steps"].append(item)
                start = time.monotonic()
                try:
                    result = await asyncio.to_thread(call)
                    item["result"] = result
                    save_observation(folder, name, result.observation)
                    return True
                except Exception as error:
                    item["error"] = {
                        "type": type(error).__name__,
                        "detail": str(error),
                        "code": getattr(error, "code", None),
                        "effect": getattr(error, "effect", None),
                        "impact": getattr(error, "impact", None),
                    }
                    return False
                finally:
                    item["elapsed_seconds"] = time.monotonic() - start
                    # Observe asynchronous provider events; never resend input.
                    await asyncio.sleep(0.15)
                    item["after"] = await state(folder, process=child)

            if mechanism == "element-click" and not await step("click", field.click):
                return
            if mechanism == "text-point-click":
                catalogue = captured_interface_metadata(observation)
                candidates = [item for item in catalogue["windows"] if item["title"] == title]
                assert len(candidates) == 1, catalogue
                reference = candidates[0]["ref"]
                window = reference
                image_observation = await asyncio.to_thread(app.read, view="image", window=window)
                assert image_observation.screenshot, "Coordinate input requires delivered window pixels"
                save_observation(folder, "text-point-window-before", image_observation)
                oracle = (await state(folder, process=child))["text_geometry"]
                assert oracle["visible"] and oracle["coordinate_space"] == "qt_global_logical_desktop", oracle
                px, py = oracle["point"]
                x, y, width, height = oracle["text_rect"]
                assert x < px < x + width and y < py < y + height, oracle
                native_element = (report["native_ax_probe"].get("result") or {}).get("element", {})
                position, size = native_element.get("position", {}), native_element.get("size", {})
                if position.get("status") == 0 and size.get("status") == 0:
                    origin, extent = position["value"], size["value"]
                    assert origin["x"] <= px < origin["x"] + extent["width"], oracle
                    assert origin["y"] <= py < origin["y"] + extent["height"], oracle
                point, mapping = delivered_text_point(app, window, oracle["point"])
                report["text_point_oracle"] = oracle
                report["text_point_mapping"] = mapping
                report["window_reference"] = reference
                if not await step("text_point_click", lambda: app.click(at=point, window=window)):
                    return
                click = report["steps"][-1]
                for name in ("value", "text", "model", "date_changes", "submissions", "guard"):
                    assert click["after"][name] == click["before"][name], f"Preparation click changed {name}"
                focus = click["after"]["focus_state"]
                if not focus["date_has_focus"] or not focus["cocoa"].get("first_responder_is_view"):
                    report["chain_stopped"] = "text-point click did not establish the original date receiver"
                    return
            if not await step("replace", lambda: field.type_text("2026/11/13", replace=True)):
                return
            current = await state(folder, process=child)
            if current["value"] != "2026/11/13" or current["submissions"]:
                report["chain_stopped"] = "replacement did not establish the requested unsubmitted value"
                return
            await step("return", lambda: field.press_key("Return"))
        finally:
            try:
                if child is not None and child.returncode is None:
                    report["final"] = await state(folder, process=child)
                report["native_effects"] = [
                    event["value"]
                    for event in events
                    if event.get("kind") == "backend_diagnostic"
                    and isinstance(event.get("value"), dict)
                    and event["value"].get("kind") == "input_activity"
                    and event["value"].get("phase") == "willCall"
                ]
                assert not any(event.get("transport") == "globalHID" for event in report["native_effects"])
            finally:
                try:
                    await asyncio.to_thread(computer.close)
                finally:
                    set_diagnostic_handler(None)
                    if child is not None:
                        if child.returncode is None:
                            child.terminate()
                            try:
                                await asyncio.wait_for(child.wait(), 3)
                            except TimeoutError:
                                child.kill()
                                await child.wait()
                        report["fixture_exit_code"] = child.returncode
                    (folder / "result.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
                    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument(
        "--mechanism",
        choices=("baseline", "normal-show", "element-click", "ax-bounds", "text-point-click"),
        required=True,
    )
    parser.add_argument("--ax-probe", type=Path, default=DEFAULT_AX_PROBE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    folder = args.output or Path(tempfile.mkdtemp(prefix="qt-date-diagnostic-", dir=ROOT / ".artifacts"))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(diagnose(folder.resolve(), args.python.absolute(), args.mechanism, args.ax_probe.absolute()))
    finally:
        sys.stdout.write(f"Qt date diagnostic ({args.mechanism}): {folder}\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
