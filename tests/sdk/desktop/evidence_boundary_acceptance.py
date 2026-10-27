"""Opt-in native acceptance for evidence-scoped input and recoverable windows.

Run ``COMPUTER_USE_DESKTOP=shared COMPUTER_USE_FOREGROUND_ASSIST=0 uv run python
tests/sdk/desktop/evidence_boundary_acceptance.py`` against the current driver.
``--build-only`` compiles the fixture without launching it or sending input.

All tested input uses the public SDK. A one-response port fault removes an
owned window from an explicitly partial catalogue; it never changes native UI
objects or synthesizes input/read results. Native control values and action
counts, screenshots, passive desktop samples and source fingerprints are saved.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import plistlib
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

import computer_use
from computer_use import Computer, ComputerUseError
from computer_use._process import resolve_executable
from computer_use.client.host import setDiagnosticHandler
from computer_use.paths import nativeDriver

ROOT = Path(__file__).resolve().parents[3]


def fingerprint(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def build(folder: Path) -> tuple[Path, str, Path]:
    """Compile and sign an isolated app; do not launch it."""
    name = f"EvidenceBoundaryProbe-{folder.name}"
    bundle = folder / f"{name}.app"
    executable = bundle / "Contents/MacOS/probe"
    executable.parent.mkdir(parents=True)
    identifier = f"local.computer-use.evidence-boundary-{folder.name}"
    subprocess.run(
        [
            resolve_executable("swiftc"),
            "-swift-version",
            "6",
            str(ROOT / "tests/fixtures/EvidenceBoundaryProbe.swift"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
    )
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": identifier,
                "CFBundleName": name,
                "CFBundleExecutable": "probe",
                "CFBundlePackageType": "APPL",
            }
        )
    )
    subprocess.run(
        [resolve_executable("codesign"), "--force", "--sign", "-", str(bundle)], check=True, capture_output=True
    )
    monitor = folder / "desktop-monitor"
    subprocess.run(
        [resolve_executable("swiftc"), str(ROOT / "tests/fixtures/DesktopStateProbe.swift"), "-o", str(monitor)],
        check=True,
        capture_output=True,
    )
    return bundle, name, monitor


@contextmanager
def omit_once(port, application: str, window: str, evidence: dict[str, Any]):
    """Fault only a real catalogue response; restore the native port afterward."""
    original = port.directory

    async def partial(operation, target, query, timeout_ms):
        result = await original(operation, target, query, timeout_ms)
        if query.get("kind") == "surfaces" and query.get("application") == application and not evidence.get("injected"):
            items = [item for item in result["surfaces"] if item["window"] == window]
            assert len(items) == 1, (window, result)
            changed = deepcopy(result)
            changed["surfaces"] = [item for item in changed["surfaces"] if item["window"] != window]
            changed["stopReasons"] = [*changed["stopReasons"], "test: transient member collection timeout"]
            evidence.update(
                injected=True,
                omitted=items[0],
                originalCount=len(result["surfaces"]),
                deliveredCount=len(changed["surfaces"]),
                stopReasons=changed["stopReasons"],
            )
            return changed
        return result

    port.directory = partial
    try:
        yield
    finally:
        port.directory = original


async def main(*, build_only: bool = False) -> None:
    assert sys.platform == "darwin", "This opt-in fixture exercises AppKit."
    assert os.environ.get("COMPUTER_USE_DESKTOP", "shared") == "shared"
    assert os.environ.get("COMPUTER_USE_FOREGROUND_ASSIST", "0") == "0"
    folder = ROOT / ".artifacts/evidence-boundary-acceptance" / str(time.time_ns())
    bundle, name, monitor = build(folder)
    if build_only:
        print(json.dumps({"buildOnly": True, "bundle": str(bundle), "folder": str(folder)}))
        return

    assert Path(computer_use.__file__).resolve() == ROOT / "computer_use_engine/__init__.py", computer_use.__file__
    driver = Path(nativeDriver()["command"])
    source_paths = [
        path
        for path in (ROOT / "computer_use_engine").rglob("*")
        if path.suffix in (".py", ".swift", ".cs") and ".build" not in path.parts and ".venv" not in path.parts
    ]
    source_paths.extend([Path(__file__), ROOT / "tests/fixtures/EvidenceBoundaryProbe.swift"])
    source_before = {str(path.relative_to(ROOT)): fingerprint(path)["sha256"] for path in source_paths}
    manifest: dict[str, Any] = {
        "head": subprocess.check_output(
            [
                resolve_executable(
                    "git",
                    cwd=ROOT,
                ),
                "rev-parse",
                "HEAD",
            ],
            cwd=ROOT,
            text=True,
        ).strip(),
        "gitStatus": subprocess.check_output(
            [
                resolve_executable(
                    "git",
                    cwd=ROOT,
                ),
                "status",
                "--porcelain=v1",
            ],
            cwd=ROOT,
            text=True,
        ),
        "sourceBefore": source_before,
        "driver": fingerprint(driver),
        "fixture": fingerprint(bundle / "Contents/MacOS/probe"),
        "python": sys.executable,
        "sdkModule": str(Path(computer_use.__file__).resolve()),
        "desktopMode": "shared",
        "foregroundAssist": False,
    }
    computer = Computer()
    report: dict[str, Any] = {"passed": False, "folder": str(folder), "cases": {}}
    samples: list[dict[str, Any]] = []
    commands: list[dict[str, Any]] = []
    sampler = None
    sampling = None

    def diagnostic(event: dict[str, Any]) -> None:
        with (folder / "commands.jsonl").open("a") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        if event.get("kind") == "connection":
            manifest["actualConnection"] = event
        if event.get("kind") == "command_start":
            commands.append(event)

    setDiagnosticHandler(diagnostic)

    async def state(**expected) -> dict[str, Any]:
        value = None
        for _ in range(80):
            try:
                value = json.loads((folder / "state.json").read_text())
                if all(value.get(key) == item for key, item in expected.items()):
                    return value
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.05)
        raise AssertionError((value, expected))

    async def observe(ui, label, view="both"):
        observed = await ui.read(full=True, view=view)
        (folder / f"{label}.txt").write_text(observed.text)
        if observed.screenshot is not None:
            (folder / f"{label}.png").write_bytes(observed.screenshot)
        return observed

    def unchanged(before, after):
        for key in ("input", "adjacent", "independent", "child", "submits", "childSubmits"):
            assert before[key] == after[key], (key, before, after)

    async def refused(label, action, *, codes):
        before = await state()
        try:
            await action()
        except ComputerUseError as error:
            assert error.effect == "none" and error.code in codes, error
            await asyncio.sleep(0.15)
            after = await state()
            unchanged(before, after)
            result = {
                "code": error.code,
                "effect": error.effect,
                "detail": str(error),
                "before": before,
                "after": after,
            }
            report["cases"][label] = result
            return result
        raise AssertionError(f"{label}: operation unexpectedly succeeded")

    async def sample():
        assert sampler is not None and sampler.stdout is not None
        while line := await sampler.stdout.readline():
            value = json.loads(line)
            value["receivedAt"] = time.monotonic()
            samples.append(value)

    def dialog_of(app):
        matches = [
            ref
            for ref, key in app._instance.windows.items()
            if key in app._instance.members and app._instance.members[key].get("modal") is True
        ]
        assert len(matches) == 1, matches
        return app.window(matches[0])

    try:
        subprocess.run([resolve_executable("open"), "-g", str(bundle)], check=True, capture_output=True)
        await state(labelCount=320, sheet=False)
        sampler = await asyncio.create_subprocess_exec(
            str(monitor), stdout=asyncio.subprocess.PIPE, env={**os.environ, "COMPUTER_USE_TEST_MONITOR_SECONDS": "300"}
        )
        sampling = asyncio.create_task(sample())
        await asyncio.sleep(0.15)
        report["before"] = await state()
        entries = await computer.listApps()
        app = computer.app(next(item.id for item in entries if item.displayName == f"{name} — Evidence target"))
        other = computer.app(next(item.id for item in entries if item.displayName == f"{name} — Evidence independent"))
        await observe(app, "00-target")
        container = await app.element([{"identifier": "evidence-container"}])
        field = await app.element([{"identifier": "evidence-input"}])
        peer = await app.element([{"identifier": "evidence-adjacent"}])
        assert container._snapshot["container"], container._snapshot
        # Independent raw read proves the wide child relation really exists in
        # native accessibility, regardless of presentation compaction.
        port = computer._controller.port
        binding = container._snapshot["binding"]
        async with port.calls.open(binding, 30000) as lease:
            facts = await port.nodes(lease.token, lease.target, ["AXRole", "AXIdentifier"], 512, 1, lease.remaining())
        (folder / "wide-native-facts.json").write_text(json.dumps(facts, indent=2, ensure_ascii=False))
        native_labels = [
            node
            for node in facts["nodes"]
            if str(node["values"].get("AXIdentifier", "")).startswith("evidence-static-")
        ]
        children = next(
            item for item in facts["relations"] if item["source"] == binding.node and item["property"] == "AXChildren"
        )
        assert children["total"] == 321 and len(native_labels) > 0, children

        await field.focus()
        first_command = len(commands)
        await container.typeText("ACCEPTED 中文🙂")
        await container.pressKey("Return")
        queries = [event["params"] for event in commands[first_command:] if event["method"] == "inspect"]
        focus_queries = [item for item in queries if item["query"].get("currentReceiver") is True]
        assert len(focus_queries) == 2, focus_queries
        container_scans = [
            item
            for item in queries
            if item["target"].get("node") == binding.node and item["query"]["kind"] in ("nodes", "relation")
        ]
        assert not container_scans, container_scans
        good = await state(input="ACCEPTED 中文🙂", submits=1, adjacent="GUARD", independent="OTHER-WINDOW")
        report["cases"]["wideContainerInputAndSubmit"] = {
            "nativeChildren": children["total"],
            "labelsInFirstPage": len(native_labels),
            "fixtureLabels": good["labelCount"],
            "currentReceiverQueries": focus_queries,
            "containerGraphQueries": len(container_scans),
            "state": good,
        }
        await observe(app, "01-wide-input-submitted")

        await peer.focus()
        await state(adjacentResponder=True)
        await refused(
            "focusInSiblingContainer",
            lambda: container.typeText("MUST-NOT-APPEAR"),
            codes={"input_unavailable", "observation_incomplete"},
        )
        await observe(other, "02-independent")
        independent = await other.element([{"identifier": "evidence-independent"}])
        await independent.focus()
        await state(keyWindow="Evidence independent")
        await refused(
            "focusInIndependentWindow",
            lambda: container.pressKey("Return"),
            codes={"input_unavailable", "observation_incomplete"},
        )
        # Rejection must leave the addressed object usable after focus returns.
        await field.focus()
        await field.pressKey("super+Right")
        await container.typeText("!")
        await container.pressKey("Return")
        report["cases"]["receiverRecovers"] = await state(
            input="ACCEPTED 中文🙂!", submits=2, adjacent="GUARD", independent="OTHER-WINDOW"
        )

        await (await app.element("Open owned dialog")).click()
        await state(sheet=True, generation=1)
        await observe(app, "03-owned-dialog")
        dialog = dialog_of(app)
        await observe(dialog, "04-fixed-dialog")
        child = await dialog.element([{"identifier": "evidence-child"}])
        identity = repr(dialog._window_identity)
        fault: dict[str, Any] = {}
        with omit_once(port, app._instance.anchor.application, dialog._anchor, fault):
            await refused(
                "partialWindowIsUnknown", lambda: dialog.read(full=True, view="image"), codes={"observation_incomplete"}
            )
        assert fault.get("injected"), fault
        report["cases"]["partialWindowIsUnknown"]["fault"] = fault
        assert (await state())["sheet"], "The test must not physically close the omitted window."
        # No App reread or replacement Window handle is allowed here.
        recovered = await observe(dialog, "05-same-dialog-recovered")
        assert recovered.screenshot is not None
        assert repr(dialog._window_identity) == identity
        await child.typeText("RECOVERED CHILD 中文🙂", replace=True)
        await child.pressKey("Return")
        report["cases"]["sameWindowRecovers"] = await state(
            child="RECOVERED CHILD 中文🙂",
            childSubmits=1,
            sheet=True,
            input="ACCEPTED 中文🙂!",
            adjacent="GUARD",
            independent="OTHER-WINDOW",
        )
        await observe(dialog, "06-recovered-child-submitted")
        await (await dialog.element("Close owned dialog")).click()
        await state(sheet=False)
        # AppKit sheet dismissal is asynchronous. Prove actual disappearance
        # independently before testing the saved handle's lifecycle response.
        for _ in range(30):
            closed_directory = await computer._controller.reader.directory.surfaces(app._instance.anchor.application)
            if not closed_directory["stopReasons"] and not any(
                item["window"] == dialog._anchor for item in closed_directory["surfaces"]
            ):
                break
            await asyncio.sleep(0.1)
        assert not any(item["window"] == dialog._anchor for item in closed_directory["surfaces"]), closed_directory
        (folder / "closed-directory.json").write_text(json.dumps(closed_directory, indent=2, ensure_ascii=False))
        await refused("closedWindowRejected", lambda: dialog.read(view="image"), codes={"stale_target"})
        await (await app.element("Open owned dialog")).click()
        await state(sheet=True, generation=2, child="child-2")
        await observe(app, "07-same-title-new-dialog")
        replacement = dialog_of(app)
        assert replacement._window_identity != dialog._window_identity
        await refused("oldWindowCannotRetarget", lambda: dialog.pressKey("Return"), codes={"stale_target"})
        await refused(
            "oldElementCannotRetarget", lambda: child.typeText("MUST-NOT-REPLACE", replace=True), codes={"stale_target"}
        )
        new_child = await replacement.element([{"identifier": "evidence-child"}])
        await new_child.typeText("NEW CHILD", replace=True)
        await new_child.pressKey("Return")
        report["cases"]["replacementExplicitlySelected"] = await state(
            child="NEW CHILD", childSubmits=2, input="ACCEPTED 中文🙂!", adjacent="GUARD", independent="OTHER-WINDOW"
        )
        await observe(replacement, "08-replacement-result")
        report["functionalPassed"] = True
    except BaseException as error:
        report["error"] = repr(error)
        raise
    finally:
        manifest["driverHello"] = computer._controller.transport._hello
        computer._controller.transport.close()
        await computer._controller.transport.waitClosed()
        if sampler is not None:
            if sampler.returncode is None:
                sampler.terminate()
            await sampler.wait()
        if sampling is not None:
            await sampling
        (folder / "desktop-state.jsonl").write_text("".join(json.dumps(item) + "\n" for item in samples))
        report["desktop"] = {
            "samples": len(samples),
            "changes": {
                name: sum(any(b[key] != a[key] for key in keys) for a, b in zip(samples, samples[1:]))
                for name, keys in {
                    "foreground": ("pid",),
                    "pointer": ("x", "y"),
                    "modifiers": ("flags",),
                    "clipboard": ("clipboard",),
                }.items()
            },
        }
        setDiagnosticHandler(None)
        pid_file = folder / "fixture.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text())
            try:
                os.kill(pid, signal.SIGTERM)
                for _ in range(40):
                    await asyncio.sleep(0.05)
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.kill(pid, 0)
                report["fixtureTerminated"] = False
            except ProcessLookupError:
                report["fixtureTerminated"] = True
        manifest["loadedModules"] = {
            name: fingerprint(Path(module.__file__))
            for name, module in sorted(sys.modules.items())
            if (name == "computer_use" or name.startswith("computer_use."))
            and getattr(module, "__file__", None)
            and Path(module.__file__).is_file()
        }
        manifest["sourceAfter"] = {str(path.relative_to(ROOT)): fingerprint(path)["sha256"] for path in source_paths}
        manifest["sourceUnchangedDuringRun"] = manifest["sourceBefore"] == manifest["sourceAfter"]
        manifest["driverAfter"] = fingerprint(driver)
        report["passed"] = (
            report.get("functionalPassed") is True
            and len(samples) > 1
            and not any(report["desktop"]["changes"].values())
            and manifest["sourceUnchangedDuringRun"]
            and manifest["driver"] == manifest["driverAfter"]
            and report.get("fixtureTerminated") is True
        )
        (folder / "source-manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(json.dumps({"passed": report["passed"], "folder": str(folder), "error": report.get("error")}), flush=True)
    assert report["passed"], report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true")
    asyncio.run(main(build_only=parser.parse_args().build_only))
