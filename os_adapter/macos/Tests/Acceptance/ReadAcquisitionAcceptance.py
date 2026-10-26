"""Opt-in AX acquisition acceptance through a fresh source SDK/driver process.

Build tests/fixtures/AXReadProbe.swift as a disposable .app executable and
AXReadFaults.c as a dylib. Pass those paths and a freshly built --driver.
Only read queries are issued; faults affect the test driver, never the provider.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import computer_use
from computer_use import Computer

ROOT = Path(__file__).resolve().parents[4]


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")


async def case(options, mode):
    folder = options.output / mode
    folder.mkdir(parents=True, exist_ok=True)
    previous = {key: os.environ.get(key) for key in ("DYLD_INSERT_LIBRARIES", "AX_READ_FAULT")}
    os.environ["AX_READ_FAULT"] = mode
    if mode != "none":
        os.environ["DYLD_INSERT_LIBRARIES"] = str(options.fault_library)
    computer = Computer()
    computer._controller.transport.driver = {"command": str(options.driver), "args": []}
    connection = computer._controller.transport
    original = connection.request
    captured = []

    async def traced(method, params, request_options):
        value = await original(method, params, request_options)
        if method == "inspect" and value.get("kind") == "observation":
            captured.append(value)
        if method == "inspect" and value.get("kind") == "surfaces":
            save(folder / "surfaces.json", value)
        return value

    connection.request = traced
    report = {"mode": mode, "passed": False}
    try:
        app = computer.app("local.computer-use.ax-read-probe")
        observation = await app.read(full=True)
        if "Select an application instance" in observation.text:
            choices = [
                item for item in await computer.listApps() if item.displayName == "AX Read Probe — AX Read Probe"
            ]
            assert len(choices) == 1, [item.displayName for item in await computer.listApps()]
            app = computer.app(choices[0].id)
            observation = await app.read(full=True)
        (folder / "app.txt").write_text(observation.text)
        assert "Enumerated control" in observation.text
        assert "Attached audit popover" in observation.text, "surface after child 128 was not discovered"
        if mode == "post-count":
            # A partial list cannot prove a global name match is unique. Use
            # exact references already returned by the successful observation.
            button_ref = re.search(r"(?m)^\s*(\d+) button Enumerated control(?:,|$)", observation.text)
            canvas_ref = re.search(r"(?m)^\s*(\d+) container ID: ax-read-canvas(?:,|$)", observation.text)
            assert button_ref and canvas_ref
            button = await app.element(int(button_ref[1]))
            canvas_element = await app.element(int(canvas_ref[1]))
        else:
            button = await app.element([dict(role="button", name="Enumerated control")])
            canvas_element = await app.element([dict(identifier="ax-read-canvas")])
        binding = button._snapshot["binding"]
        port = computer._controller.port
        async with port.calls.open(binding, 20000) as read:
            fields = await port.query_nodes(
                read.token,
                binding,
                ["AXRole", "AXTitle", "AXIdentifier", "AXEnabled", "AXParent", "AXWindow"],
                2,
                1,
                read.remaining(),
                advertised_fields=["AXEnabled"],
                follow_relations=["AXParent"],
            )
            save(folder / "button.json", captured[-1])
            row = next(row for row in fields["nodes"] if row["node"] == binding.node)
            assert row["values"]["AXRole"] == "AXButton" and row["values"]["AXTitle"] == "Enumerated control"
            assert row["values"]["AXEnabled"] is True
            if mode == "batch":
                native = next(node for node in captured[-1]["nodes"] if node["id"] == binding.node)
                prop = next(p for p in native["properties"] if p["key"] == ["macos.ax", "AXTitle"])
                assert prop["read"]["api"] == "AXUIElementCopyAttributeValue", (
                    "fault did not exercise individual fallback"
                )
            if mode == "catalogue":
                assert row["fieldStatus"]["AXEnabled"]["state"] == "error"
                parent = next(
                    edge
                    for edge in fields["relations"]
                    if edge["source"] == binding.node and edge["property"] == "AXParent"
                )
                assert parent["targets"] and parent["state"] == "partial", (
                    "catalogue failure suppressed the relation payload"
                )
            canvas = canvas_element._snapshot["binding"]
            window = replace(binding, node=row["values"]["AXWindow"]["node"])
            children = await port.query_nodes(
                read.token, canvas, ["AXRole", "AXIdentifier", "AXChildren"], 1, 0, read.remaining()
            )
            save(folder / "extra-attributes.json", captured[-1])
            canvas_row = next(row for row in children["nodes"] if row["node"] == canvas.node)
            assert canvas_row["values"]["AXIdentifier"] == "ax-read-canvas"
            assert len(canvas_row["values"]["AXChildren"]) == 132, (
                "an explicit field outside the default profile lost data"
            )
            close = await port.query_relation(read.token, window, "AXCloseButton", None, 2, read.remaining())
            save(folder / "extra-relation.json", captured[-1])
            assert close["relation"]["targets"], "an explicit single-object relation was not read"
            items = await port.query_relation(read.token, canvas, "AXChildren", None, 4, read.remaining())
            save(folder / "children.json", captured[-1])
            edge = items["relation"]
            assert len(edge["targets"]) == 4 and edge["total"] == 132
            if mode in ("count", "range"):
                assert (
                    edge["nativeAPI"] == "AXUIElementCopyAttributeValue"
                    if "nativeAPI" in edge
                    else "individual getter used" in edge["detail"]
                )
            if mode == "post-count":
                assert edge["state"] == "partial" and edge.get("cursor") is None
            else:
                assert edge.get("cursor"), "a stable unread suffix lost its continuation"
        report.update(passed=True, driverPid=connection._child.pid, capturedGraphs=len(captured))
    except Exception as error:
        report["error"] = repr(error)
        raise
    finally:
        connection.close()
        await connection.waitClosed()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        save(folder / "report.json", report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


async def run(options):
    assert Path(computer_use.__file__).resolve() == ROOT / "computer_use_engine/__init__.py"
    options.output.mkdir(parents=True, exist_ok=True)
    sources = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "computer_use_engine").rglob("*")
        if p.suffix in (".swift", ".py") and ".build" not in p.parts
    }
    save(
        options.output / "version.json",
        {
            "sdk": computer_use.__file__,
            "driver": str(options.driver),
            "driverSha256": hashlib.sha256(options.driver.read_bytes()).hexdigest(),
            "sources": sources,
            "faultLibrarySha256": hashlib.sha256(options.fault_library.read_bytes()).hexdigest(),
            "desktop": os.environ.get("COMPUTER_USE_DESKTOP", "shared"),
        },
    )
    state = options.output / "fixture.json"
    state.unlink(missing_ok=True)
    modes = ("none", "batch", "count", "range", "catalogue", "post-count") if options.case == "all" else (options.case,)
    with (options.output / "fixture.log").open("w") as log:
        fixture = subprocess.Popen([str(options.fixture), str(state)], stdout=log, stderr=subprocess.STDOUT)
        reports = []
        try:
            for _ in range(100):
                if state.exists():
                    break
                if fixture.poll() is not None:
                    raise AssertionError("fixture exited before reporting its window")
                await asyncio.sleep(0.05)
            assert state.exists()
            for mode in modes:
                reports.append(await case(options, mode))
            assert json.loads(state.read_text())["actions"] == 0, "read acquisition performed an action"
        finally:
            fixture.terminate()
            try:
                fixture.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fixture.kill()
                fixture.wait()
            save(
                options.output / "report.json",
                {
                    "passed": len(reports) == len(modes) and all(item["passed"] for item in reports),
                    "requestedCases": modes,
                    "cases": reports,
                    "fixtureCleaned": fixture.poll() is not None,
                    "sourceDrift": [
                        name
                        for name, sha in sources.items()
                        if not (ROOT / name).exists() or hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha
                    ],
                },
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("driver", "fixture", "fault-library", "output"):
        parser.add_argument("--" + flag, type=lambda value: Path(value).resolve(), required=True)
    parser.add_argument(
        "--case", choices=("all", "none", "batch", "count", "range", "catalogue", "post-count"), default="all"
    )
    asyncio.run(run(parser.parse_args()))
