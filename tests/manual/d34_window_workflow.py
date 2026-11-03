# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""D34 acceptance with independent AppKit callbacks and the real SDK/MCP runtime.

Build without opening any window:
  .venv/bin/python tests/manual/d34_window_workflow.py --build-only
Run only while the shared desktop is available for the disposable test windows:
  .venv/bin/python tests/manual/d34_window_workflow.py --driver /absolute/driver

Commands never retry failed writes. Polling only reads the fixture's independent
callback journal. JSON setup commands simulate an external user's focus changes;
all measured input uses public SDK calls. The MCP phase uses Runtime.execute and
its real presentation/delivery boundary, not an installed host integration.
"""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import logging
import math
import os
import plistlib
import re
import signal
import struct
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

import computer_use
from artifact_json import encode_artifact
from computer_use import Computer, ComputerUseError
from computer_use.diagnostics import set_diagnostic_handler
from computer_use.paths import native_driver
from computer_use_mcp.runtime import Runtime
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
NAME = "D34WindowProbe"
LOG = logging.getLogger(__name__)
WINDOW_REF = re.compile(r"computer\.window\((\d+)\)")
QUOTED = r'"(?:[^"\\]|\\.)*"'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fingerprint():
    """Fingerprint executed Python and native source, without generated builds."""
    files = set()
    for root, suffixes in (
        (ROOT / "computer_use_engine", {".py", ".pyi"}),
        (ROOT / "interface_adapter/mcp", {".py"}),
        (ROOT / "os_adapter/macos/Sources", {".swift", ".h", ".c", ".m", ".mm"}),
    ):
        files.update(path for path in root.rglob("*") if path.is_file() and path.suffix in suffixes)
    files.update((Path(__file__).resolve(), ROOT / "tests/fixtures/D34WindowProbe.swift"))
    package = ROOT / "os_adapter/macos/Package.swift"
    if package.is_file():
        files.add(package)
    manifest = {str(path.relative_to(ROOT)): digest(path) for path in sorted(files)}
    combined = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return {"sha256": combined, "files": manifest}


def actual_modules():
    return {
        name: str(Path(module.__file__).resolve())
        for name, module in sorted(sys.modules.items())
        if (name in {"computer_use", "computer_use_mcp"} or name.startswith(("computer_use.", "computer_use_mcp.")))
        and getattr(module, "__file__", None)
    }


def png_size(png):
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n", "Expected PNG screenshot"
    return struct.unpack(">II", png[16:24])


def refs(text):
    return set(map(int, WINDOW_REF.findall(text)))


def window_with_content(text, content):
    """Choose the public Window section containing independently known content."""
    headers = list(re.finditer(r"(?m)^Window (\d+):", text))
    matches = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        if content in text[header.end() : end]:
            matches.append(int(header[1]))
    assert len(matches) == 1, (content, matches, text)
    return matches[0]


def current_window(text):
    matches = re.findall(r"(?m)^Window (\d+):[^\n]*\bcurrent\b", text)
    assert len(matches) == 1, (matches, text)
    return int(matches[0])


def released(value):
    return not value["keysHeld"] and not value["buttonsHeld"] and not value["modifierHeld"]


def error_evidence(error):
    if isinstance(error, ComputerUseError):
        return {
            name: getattr(error, name)
            for name in (
                "code",
                "effect",
                "target",
                "detail",
                "next",
                "impact",
                "window_lifecycle",
            )
        }
    return {"type": type(error).__name__, "detail": str(error)}


def build_fixture(folder, bundle):
    app = folder / (NAME + ".app")
    binary = app / "Contents/MacOS" / NAME
    binary.parent.mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": bundle,
                "CFBundleName": NAME,
                "CFBundleExecutable": NAME,
                "CFBundlePackageType": "APPL",
                "NSHighResolutionCapable": True,
            }
        )
    )
    subprocess.run(
        [
            "swiftc",
            "-swift-version",
            "6",
            "-parse-as-library",
            str(ROOT / "tests/fixtures/D34WindowProbe.swift"),
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["codesign", "--force", "--sign", "-", str(app)], check=True, capture_output=True, text=True)
    return app


async def state(folder):
    for _ in range(100):
        try:
            return json.loads((folder / "state.json").read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            await asyncio.sleep(0.05)
    raise AssertionError("Fixture did not publish state")


async def effect(folder, predicate):
    for _ in range(100):
        current = await state(folder)
        if predicate(current) and released(current):
            assert all(doc["guard"] == "Guard unchanged" for doc in current["documents"].values()), current
            return current
        await asyncio.sleep(0.05)
    raise AssertionError({"missing_callback_or_unreleased_input": current})


async def command(folder, action, name="A"):
    current = await state(folder)
    sequence = current["sequence"] + 1
    temporary = folder / "command.tmp"
    temporary.write_text(json.dumps({"sequence": sequence, "action": action, "name": name}))
    temporary.replace(folder / "command.json")

    def completed(item):
        if item["sequence"] != sequence:
            return False
        if action in {"focus", "weak"}:
            doc = item["documents"]["Weak" if action == "weak" else name]
            return item["keyWindow"] == doc["window"] and item["frontmostPID"] == item["pid"]
        if action == "show-sheet":
            sheet, owner = item["documents"]["Sheet"], item["documents"][name]
            return (
                sheet["visible"]
                and sheet["sheetParent"] == owner["window"]
                and owner["attachedSheet"] == sheet["window"]
            )
        if action == "dismiss-sheet":
            sheet = item["documents"]["Sheet"]
            return item["sheetOwner"] is None and not sheet["visible"] and sheet["sheetParent"] == -1
        return True

    value = await effect(folder, completed)
    assert value["commandError"] is None, value
    return value


def save_observation(folder, label, observation):
    (folder / (label + ".txt")).write_text(observation.text)
    if observation.screenshot:
        (folder / (label + ".png")).write_bytes(observation.screenshot)


def window_point(observation, doc, key="countPoint"):
    width, height = png_size(observation.screenshot)
    _, _, native_width, native_height = doc["frame"]
    x, y = doc[key]
    return x * width / native_width, y * height / native_height


def assert_window_image(observation, doc):
    """Prove actual pixels belong to this document, independently of SDK text."""
    x, y = window_point(observation, doc, "markerPoint")
    with Image.open(io.BytesIO(observation.screenshot)) as image:
        pixel = image.convert("RGB").getpixel((round(x), round(y)))
    # Display colour management may convert sRGB to the captured profile. The
    # fixture's five markers differ by >100 in at least one channel, so these
    # bounded tolerance boxes cannot confuse one document with another.
    assert all(abs(actual - expected) <= 32 for actual, expected in zip(pixel, doc["markerRGB"])), (
        "Screenshot belongs to another window or coordinate space",
        pixel,
        doc["markerRGB"],
    )
    palette = {
        "A": (220, 30, 40),
        "B": (20, 70, 220),
        "Child": (20, 180, 70),
        "Sheet": (230, 140, 20),
        "Weak": (160, 40, 200),
    }
    distances = sorted((math.dist(pixel, colour), name) for name, colour in palette.items())
    assert list(palette[distances[0][1]]) == doc["markerRGB"] and distances[0][0] < distances[1][0], distances
    return {
        "native_window": doc["window"],
        "expected_srgb": doc["markerRGB"],
        "captured_rgb": pixel,
        "nearest_document": distances[0][1],
        "palette_distances": {name: distance for distance, name in distances},
    }


def desktop_point(text, png, ref, doc, pointer_hit, fixture_pid):
    """Use only actually emitted PNG geometry and public catalogue coordinates."""
    dimensions = png_size(png)
    header = re.search(r"(?m)^Screenshot: desktop (\d+)x(\d+) px; origin=top-left$", text)
    assert header and tuple(map(int, header.groups())) == dimensions, (dimensions, text)
    assert "Window catalogue truncated:" not in text, text
    pattern = re.compile(rf"^  ({QUOTED}) — ({QUOTED}|<untitled>) \(([^)]+)\)(?: computer\.window\((\d+)\))?.*$")
    windows = []
    for line in text.splitlines():
        match = pattern.fullmatch(line)
        if match:
            rect = tuple(map(float, match[3].split(",")))
            assert len(rect) == 4 and all(math.isfinite(v) for v in rect) and min(rect[2:]) > 0
            windows.append((int(match[4]) if match[4] else None, rect, line))
    selected = [(index, rect, line) for index, (item, rect, line) in enumerate(windows) if item == ref]
    assert len(selected) == 1, (ref, text)
    index, (x, y, width, height), line = selected[0]
    _, _, native_width, native_height = doc["frame"]
    point = x + doc["countPoint"][0] * width / native_width, y + doc["countPoint"][1] * height / native_height
    assert 0 <= point[0] < dimensions[0] and 0 <= point[1] < dimensions[1], point
    overlapping = [
        other for _, (ox, oy, ow, oh), other in windows[:index] if ox <= point[0] < ox + ow and oy <= point[1] < oy + oh
    ]
    # Catalogue rectangles include transparent, mouse-ignoring overlays. Use
    # independent live system hit evidence, never an application-name exception.
    assert pointer_hit["window"] == doc["window"], ("Owned point covered", overlapping, pointer_hit)
    assert pointer_hit["axStatus"] == 0 and pointer_hit["axPID"] == fixture_pid, pointer_hit
    return point, {
        "png_size": dimensions,
        "window": line,
        "point": point,
        "overlapping_catalogue_rows": overlapping,
        "system_hit": pointer_hit,
    }


async def verify(folder, options, fixture, bundle):
    driver = Path(options.driver or native_driver()["command"]).resolve()
    report = {
        "passed": False,
        "sdk": computer_use.__file__,
        "driver": str(driver),
        "driver_sha256": digest(driver),
        "source_before": source_fingerprint(),
        "actual_modules_before": actual_modules(),
        "checks": [],
        "actions": [],
        "limitations": [
            "MCP Runtime boundary; installed-host integration not exercised",
            "Weak AX fixture suppresses children; complete absence of an AX window is not simulated",
            "Cross-process helper windows and native window-number reuse are not simulated",
        ],
    }
    journal = (folder / "diagnostics.jsonl").open("w")
    computer, runtime, initial = None, None, None

    def diagnostic(value):
        journal.write(json.dumps(value, ensure_ascii=False, default=encode_artifact) + "\n")
        journal.flush()

    def new_computer():
        value = Computer(allow_global_input=True)
        value._controller.transport.driver = {"command": str(driver), "args": []}
        return value

    async def action(label, operation, predicate):
        before = await state(folder)
        receipt = await asyncio.to_thread(operation)
        current = await effect(folder, predicate)
        report["actions"].append({"label": label, "receipt": dict(receipt), "before": before, "after": current})
        return receipt

    async def read(label, ui, **kwargs):
        observation = await asyncio.to_thread(ui.read, **kwargs)
        save_observation(folder, label, observation)
        return observation

    try:
        assert all(Path(path).is_relative_to(ROOT) for path in report["actual_modules_before"].values()), (
            "SDK/MCP must run from this source checkout",
            report["actual_modules_before"],
        )
        await asyncio.to_thread(subprocess.run, ["open", str(fixture)], check=True)
        initial = await state(folder)
        report["initial"] = initial
        assert released(initial), initial
        await command(folder, "focus", "A")
        computer = new_computer()
        set_diagnostic_handler(diagnostic)
        apps = await asyncio.to_thread(computer.list_apps)
        choices = [app for app in apps if app.display_name == NAME and app.is_running]
        assert len(choices) == 1, [(app.id, app.display_name) for app in apps]
        selector_app, numeric_app = computer.app(bundle), computer.app(choices[0].id)
        desktop = await read("01-desktop", computer, view="both")
        desktop_refs = set()
        for line in desktop.text.splitlines():
            if f'"{NAME}" — ' in line:
                desktop_refs |= refs(line)
        assert len(desktop_refs) == 2, desktop.text
        before_handles = await state(folder)
        # Materialize handles before any App observation; selection is read-only.
        handles = {ref: computer.window(ref) for ref in desktop_refs}
        early_aliases = {ref: numeric_app.window(ref) for ref in desktop_refs}
        assert (await state(folder))["keyWindow"] == before_handles["keyWindow"]
        selector_observation = await read("02-selector-app", selector_app, view="both")
        numeric_observation = await read("03-numeric-app", numeric_app, view="tree")
        assert desktop_refs <= refs(selector_observation.text) and desktop_refs <= refs(numeric_observation.text), (
            selector_observation.text,
            numeric_observation.text,
            desktop.text,
        )
        report["additional_AX_refs"] = {
            "selector_app": sorted(refs(selector_observation.text) - desktop_refs),
            "numeric_app": sorted(refs(numeric_observation.text) - desktop_refs),
        }
        named = {}
        for ref, window in handles.items():
            observation = await read(f"04-window-{ref}", window, view="both")
            names = [name for name in ("A", "B") if f"Document {name}" in observation.text]
            assert len(names) == 1, observation.text
            named[names[0]] = (ref, window)
            report.setdefault("image_markers", []).append(
                assert_window_image(observation, (await state(folder))["documents"][names[0]])
            )
            for label, alias in (("numeric", early_aliases[ref]), ("selector", selector_app.window(ref))):
                other = await read(f"05-{label}-{ref}", alias, view="both")
                assert f"Document {names[0]}" in other.text, other.text
                report["image_markers"].append(assert_window_image(other, (await state(folder))["documents"][names[0]]))
        assert set(named) == {"A", "B"}
        report["identity"] = {name: ref for name, (ref, _) in named.items()}
        report["checks"].append("same-title windows: desktop, selector App, numeric App share refs and fixed targets")
        a_ref, a = named["A"]
        b_ref, b = named["B"]
        a_field = await asyncio.to_thread(a.element, [{"identifier": "d34-input-A"}])
        await command(folder, "focus", "A")
        await read("06-delivered-A", selector_app, view="tree")
        await command(folder, "focus", "B")
        await action(
            "App context stays A after external focus B",
            lambda: selector_app.type_text("Pinned-A"),
            lambda value: value["documents"]["A"]["field"] == "Pinned-A" and value["documents"]["B"]["field"] == "",
        )
        await command(folder, "focus", "B")
        await read("07-delivered-B", selector_app, view="tree")
        await action(
            "old element stays A after App observes B",
            lambda: a_field.type_text("Original-A", replace=True),
            lambda value: value["documents"]["A"]["field"] == "Original-A" and value["documents"]["B"]["field"] == "",
        )
        a_image = await read("08-retained-A-image", a, view="both")
        report["image_markers"].append(assert_window_image(a_image, (await state(folder))["documents"]["A"]))
        await command(folder, "focus", "B")
        await read("09-newer-B-image", selector_app, view="both")
        point = window_point(a_image, (await state(folder))["documents"]["A"])
        await action(
            "fixed Window screenshot remains A",
            lambda: a.click(at=point),
            lambda value: value["documents"]["A"]["count"] == 1 and value["documents"]["B"]["count"] == 0,
        )
        opened = await action(
            "open untitled independent window",
            lambda: a.element("Open independent window").click(),
            lambda value: value["opens"] == 1,
        )
        assert opened.observation is not None and "Child input" in opened.observation.text, opened
        save_observation(folder, "10-post-open", opened.observation)
        assert opened.observation.screenshot is not None, (
            "New independent window must be visually observable in receipt"
        )
        report["image_markers"].append(
            assert_window_image(opened.observation, (await state(folder))["documents"]["Child"])
        )
        child_ref = window_with_content(opened.observation.text, "Child input")
        assert child_ref not in desktop_refs, opened.observation.text
        report["identity"]["Child"] = child_ref
        child_point = window_point(opened.observation, (await state(folder))["documents"]["Child"])
        await action(
            "original selector App uses newly delivered Child image without another read",
            lambda: selector_app.click(at=child_point),
            lambda value: (
                value["documents"]["Child"]["count"] == 1
                and value["documents"]["A"]["count"] == 1
                and value["documents"]["B"]["count"] == 0
            ),
        )
        # The next model operation can address the delivered new window directly.
        child_field = await asyncio.to_thread(selector_app.element, [{"identifier": "d34-input-Child"}])
        await action(
            "post-action Child element available",
            lambda: child_field.type_text("Child-value", replace=True),
            lambda value: value["documents"]["Child"]["field"] == "Child-value",
        )
        child = computer.window(child_ref)
        assert "Document A" in (await read("11-original-A", a, view="tree")).text
        child_image = await read("12-child", child, view="both")
        assert "Document Child" in child_image.text
        report["image_markers"].append(assert_window_image(child_image, (await state(folder))["documents"]["Child"]))
        closer = await asyncio.to_thread(a.element, "Close this window")
        closed = await action(
            "close original while other windows survive",
            lambda: closer.click(),
            lambda value: value["documents"]["A"]["closed"],
        )
        report["closed_receipt"] = dict(closed)
        assert closed.window_lifecycle == "closed", closed
        for label, operation in (
            ("closed Window", lambda: a.read(view="tree")),
            ("closed Element", lambda: a_field.type_text("MUST NOT LAND")),
            ("closed screenshot", lambda: a.click(at=point)),
        ):
            try:
                result = await asyncio.to_thread(operation)
            except ComputerUseError as error:
                assert error.code == "stale_target" and error.effect == "none", error
                report["checks"].append({label: error_evidence(error)})
            else:
                raise AssertionError((label, "should be stale, not retargeted", result))
        survivors = await read("13-surviving-App", selector_app, view="tree")
        assert {b_ref, child_ref} <= refs(survivors.text) and a_ref not in refs(survivors.text), survivors.text
        await command(folder, "focus", "B")
        sheet_button = await asyncio.to_thread(b.element, "Open sheet")
        sheet_receipt = await action(
            "open confirmed sheet", lambda: sheet_button.click(), lambda value: value["sheetOwner"] == "B"
        )
        assert sheet_receipt.observation is not None and "Sheet input" in sheet_receipt.observation.text, sheet_receipt
        save_observation(folder, "14-sheet", sheet_receipt.observation)
        sheet_ref = window_with_content(sheet_receipt.observation.text, "Sheet input")
        assert sheet_ref not in {a_ref, b_ref, child_ref}, sheet_receipt.observation.text
        report["identity"]["Sheet"] = sheet_ref
        sheet = computer.window(sheet_ref)
        b_with_sheet = await read("14b-fixed-B-with-sheet", b, view="both")
        assert "Sheet input" in b_with_sheet.text, b_with_sheet.text
        await command(folder, "focus", "Child")
        unrelated_app_read = await read("14c-App-observes-Child", selector_app, view="tree")
        assert "Child input" in unrelated_app_read.text, unrelated_app_read.text
        await action(
            "fixed B keyboard retains its delivered sheet after App observes Child",
            lambda: b.type_text("Pinned-Sheet"),
            lambda value: (
                value["documents"]["Sheet"]["field"] == "Pinned-Sheet"
                and value["documents"]["B"]["field"] == ""
                and value["documents"]["Child"]["field"] == "Child-value"
            ),
        )
        sheet_field = await asyncio.to_thread(sheet.element, [{"identifier": "d34-input-Sheet"}])
        await action(
            "sheet exact input",
            lambda: sheet_field.type_text("Sheet-value", replace=True),
            lambda value: value["documents"]["Sheet"]["field"] == "Sheet-value",
        )
        dismiss = await asyncio.to_thread(b.element, "Dismiss sheet")
        await action("dismiss sheet", lambda: dismiss.click(), lambda value: value["sheetOwner"] is None)
        await action(
            "original B continues after its scoped sheet dismissal without an extra read",
            lambda: b.type_text("B-after-sheet"),
            lambda value: (
                value["documents"]["B"]["field"] == "B-after-sheet"
                and value["documents"]["Sheet"]["field"] == "Sheet-value"
                and value["documents"]["Child"]["field"] == "Child-value"
            ),
        )
        before_weak = refs((await read("15-before-weak", selector_app, view="tree")).text)
        await command(folder, "weak")
        weak_catalogue = await read("16-weak-catalogue", selector_app, view="tree")
        weak_ref = current_window(weak_catalogue.text)
        assert weak_ref not in before_weak, weak_catalogue.text
        report["identity"]["Weak"] = weak_ref
        weak = computer.window(weak_ref)
        weak_image = await read("17-weak-image", weak, view="both")
        report["image_markers"].append(assert_window_image(weak_image, (await state(folder))["documents"]["Weak"]))
        assert "Weak input" not in weak_image.text, "Fixture did not actually suppress AX children"
        weak_point = window_point(weak_image, (await state(folder))["documents"]["Weak"])
        await action(
            "weak AX window screenshot click",
            lambda: weak.click(at=weak_point),
            lambda value: value["documents"]["Weak"]["count"] == 1,
        )
        report["checks"].append("weak AX retained a selectable untitled window, screenshot and coordinate input")
        # End direct SDK use before installing MCP's process-global delivery guard.
        await asyncio.to_thread(computer.close)
        computer = None
        runtime = Runtime(diagnostic, computer_factory=new_computer)
        execution = 0

        async def mcp(label, code):
            nonlocal execution
            execution += 1
            result = await runtime.execute(
                {
                    "code": code,
                    "executionId": f"d34-{execution}",
                    "history": {"resetText": False, "resetImages": False, "incremental": False},
                }
            )
            texts, images = [], []
            for content in result["content"]:
                if content["type"] == "text":
                    texts.append(content["text"])
                elif content["type"] == "image":
                    image = base64.b64decode(content["data"])
                    images.append(image)
                    (folder / f"{label}-{len(images)}.png").write_bytes(image)
            text = "\n".join(texts)
            (folder / (label + ".txt")).write_text(text)
            assert not result.get("isError"), text
            return text, images

        # The fresh MCP session discovers its own IDs. No private ref registry is used.
        desktop_text, images = await mcp("18-mcp-desktop", "shot = computer.read(view='both')")
        assert len(images) == 1, len(images)
        fixture_rows = [line for line in desktop_text.splitlines() if f'"{NAME}" — <untitled>' in line]
        # Weak was explicitly made frontmost above; catalogue preserves front-to-back order.
        assert fixture_rows and "<untitled>" in fixture_rows[0], fixture_rows
        mcp_weak_ref = next(iter(refs(fixture_rows[0])))
        report["mcp_identity"] = {"Weak": mcp_weak_ref}
        public_text, window_images = await mcp(
            "19-mcp-window", f"weak = computer.window({mcp_weak_ref})\nweak_shot = weak.read(view='both')"
        )
        assert "Weak input" not in public_text and "Document Child" not in public_text, public_text
        assert len(window_images) == 1, len(window_images)
        report["image_markers"].append(
            assert_window_image(
                SimpleNamespace(screenshot=window_images[0]), (await state(folder))["documents"]["Weak"]
            )
        )
        # Re-deliver desktop after the window capture; the same public object must
        # receive resized text and coordinates, for both automatic and explicit output.
        desktop_text, images = await mcp(
            "20-mcp-desktop-delivered",
            "shot = computer.read(view='both')\nprint(shot.text)\nprint_image(shot.screenshot)",
        )
        assert len(images) == 1, len(images)
        raw_dimensions = png_size(runtime.task_namespace["shot"].screenshot)
        delivered_dimensions = png_size(images[0])
        assert max(delivered_dimensions) <= 2048 and delivered_dimensions != raw_dimensions, (
            "Resized-image acceptance requires a desktop larger than the MCP cap",
            raw_dimensions,
            delivered_dimensions,
        )
        current = await command(folder, "pointer-check", "Weak")
        point, geometry = desktop_point(
            desktop_text, images[0], mcp_weak_ref, current["documents"]["Weak"], current["pointerHit"], current["pid"]
        )
        report["mcp_geometry"] = {"raw": raw_dimensions, "delivered": delivered_dimensions, **geometry}
        await mcp("21-mcp-desktop-click", f"receipt = computer.click(at={point!r})")
        final = await effect(folder, lambda value: value["documents"]["Weak"]["count"] == 2)
        assert final["documents"]["A"]["count"] == 1 and final["documents"]["B"]["count"] == 0, final
        assert final["documents"]["Child"]["count"] == 1, final
        assert final["documents"]["Child"]["field"] == "Child-value", final
        assert final["documents"]["B"]["field"] == "B-after-sheet", final
        report["checks"].append(
            "actual MCP resized PNG, public rectangles, delivered coordinate binding and native callback agree"
        )
        report["passed"] = True
    except Exception as error:
        report["error"] = error_evidence(error)
        raise
    finally:
        report["source_after"] = source_fingerprint()
        report["actual_modules_after"] = actual_modules()
        report["driver_sha256_after"] = digest(driver)
        report["source_unchanged"] = report["source_before"] == report["source_after"]
        report["driver_unchanged"] = report["driver_sha256"] == report["driver_sha256_after"]
        if not report["source_unchanged"] or not report["driver_unchanged"]:
            report["passed"] = False
            report["validation_invalidated"] = (
                "Source or driver changed during the run; this is not fixed-version evidence"
            )
        if initial is not None:
            report["final"] = await state(folder)
        (folder / "result.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=encode_artifact) + "\n"
        )
        if runtime is not None:
            await runtime.close()
        if computer is not None:
            await asyncio.to_thread(computer.close)
        set_diagnostic_handler(None)
        journal.close()
        if initial is not None:
            try:
                os.kill(initial["pid"], signal.SIGTERM)
            except ProcessLookupError:
                pass
    assert report["passed"], report.get("validation_invalidated", report.get("error"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--driver", type=Path)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    folder = Path(tempfile.mkdtemp(prefix="d34-window-", dir=ROOT / ".artifacts"))
    try:
        bundle = "local.computer-use.d34." + uuid.uuid4().hex
        fixture = build_fixture(folder, bundle)
        if not args.build_only:
            asyncio.run(verify(folder, args, fixture, bundle))
    finally:
        LOG.info("D34 evidence: %s", folder)


if __name__ == "__main__":
    main()
