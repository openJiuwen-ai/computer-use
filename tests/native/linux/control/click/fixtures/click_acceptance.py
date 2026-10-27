"""Real GTK/Qt click v1 acceptance on an explicitly owned X11 display.

Run with the installed editable SDK/driver, never by modifying PYTHONPATH.
CU_CLICK_ISOLATED_DISPLAY must equal DISPLAY; the caller starts and cleans its
own Xvfb, WM and session bus. Fixture arrangements do not synthesize input.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from copy import deepcopy
from pathlib import Path

from computer_use import ClickOption, ComputerUseError
from computer_use.client.factory import nativeComputer
from computer_use.diagnostics import setDiagnosticHandler
from computer_use.native.connection import Connection
from computer_use.native.contracts import HostPolicy
from computer_use.native.transport import ConnectionPort
from computer_use_native_tools.process import launch_logged_process

TITLE = "Computer Use v5 Element Pointer "


async def run(toolkit, output):
    assert sys.platform == "linux"
    assert os.environ.get("DISPLAY") == os.environ.get("CU_CLICK_ISOLATED_DISPLAY")
    assert os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")
    output.mkdir(parents=True, exist_ok=False)
    fixture_source = Path(__file__).resolve().parents[2] / "fixtures/v5_element_pointer_fixture.py"
    state_path = output / "fixture-state.json"
    fixture = None
    desktop = None
    report = dict(passed=False, toolkit=toolkit, display=os.environ["DISPLAY"], steps=[], manifest={})
    rpc, connections, command_number = [], [], 0
    native_diagnostics = []
    click_diagnostics = []

    def diagnostic(event):
        if event.get("kind") in ("click_selection", "click_decision"):
            click_diagnostics.append(event)
        value = event.get("value")
        if (
            event.get("kind") == "backend_diagnostic"
            and isinstance(value, dict)
            and value.get("kind") == "native_click_timing"
        ):
            native_diagnostics.append(value)

    def property(window, name):
        value = window.get_full_property(desktop.intern_atom(name), X.AnyPropertyType)
        return [] if value is None else value.value

    def snapshot():
        active, pointer = property(xroot, "_NET_ACTIVE_WINDOW"), xroot.query_pointer()
        focus = desktop.get_input_focus().focus
        return dict(
            active=int(active[0]) if (len(active) > 0) else None,
            keyboard=getattr(focus, "id", focus),
            pointer=[pointer.root_x, pointer.root_y],
            mask=pointer.mask,
        )

    def state():
        return json.loads(state_path.read_text())

    async def wait(predicate, seconds=8):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            if fixture.poll() is not None:
                raise AssertionError("Fixture exited; see fixture.log")
            if state_path.exists() and predicate(state()):
                return state()
            await asyncio.sleep(0.025)
        raise AssertionError(
            "Fixture state did not settle: " + (state_path.read_text() if state_path.exists() else "none")
        )

    async def arrange(kind, **values):
        nonlocal command_number
        command_number += 1
        fixture.stdin.write(json.dumps(dict(kind=kind, id=command_number, **values)) + "\n")
        fixture.stdin.flush()
        return await wait(lambda s: s["command"] == command_number)

    async def connect(assist, label):
        connection = Connection(
            lambda: None,
            dict(
                command="/usr/bin/env",
                args=[
                    "COMPUTER_USE_DESKTOP=shared",
                    "COMPUTER_USE_FOREGROUND_ASSIST=" + ("1" if assist else "0"),
                    "COMPUTER_USE_TRACE_NATIVE_INPUT=1",
                    sys.executable,
                    "-m",
                    "computer_use_linux",
                ],
                timeoutMs=15000,
            ),
        )
        connections.append(connection)
        request = connection.request

        async def traced(method, params, options=None):
            row = dict(phase=phase, method=method, params=deepcopy(params), before=snapshot())
            rpc.append(row)
            try:
                value = await request(method, params, options)
                if method != "capture":
                    row["result"] = value
                else:
                    row["result"] = {key: value[key] for key in ("kind", "binding", "coordinates") if key in value}
                return value
            except BaseException as error:
                row["error"] = repr(error)
                raise
            finally:
                row["after"] = snapshot()

        connection.request = traced
        computer = nativeComputer(ConnectionPort(connection, HostPolicy("shared", assist)))
        apps = [app for app in await computer.listApps() if app.isRunning and "fixture" in app.displayName.lower()]
        (output / (label + "-apps.json")).write_text(json.dumps([dict(id=a.id, name=a.displayName) for a in apps]))
        assert apps, "Owned fixture absent from directory"
        app = computer.app(apps[0].id)
        observed = await app.read(view="tree")
        (output / (label + "-catalogue.txt")).write_text(observed.text)
        if "Pointer canvas A" not in observed.text:
            match = re.search(r'(\d+)\s+"' + re.escape(TITLE + "A") + '"', observed.text)
            assert match, observed.text
            app = app.window(int(match[1]))
        observed = await app.read(view="both", full=True)
        (output / (label + "-before.txt")).write_text(observed.text)
        if observed.screenshot is not None:
            (output / (label + "-before.png")).write_bytes(observed.screenshot)
        canvas = await app.element([{"name": "Pointer canvas A"}])
        button = await app.element([{"name": "Semantic button A"}])
        assert "nativeClickPipelineV1" in connection.capabilities
        return app, canvas, button

    def execute_rows(start):
        return [row for row in rpc[start:] if row["method"] == "execute"]

    def assert_effects_stay_owned(current):
        other = current["windows"]["b"]
        assert not other["presses"] and not other["releases"] and other["semanticActions"] == 0, other
        assert all(not w["pressed"] for w in current["windows"].values()), current

    async def accepted(name, operation, expected, *, preparation=False, unit="pointer_sequence"):
        nonlocal phase
        phase = name
        before, start = state(), len(rpc)
        result = await operation()
        await wait(lambda s: expected(before, s))
        await asyncio.sleep(0.1)
        current = state()
        assert expected(before, current), current
        assert_effects_stay_owned(current)
        rows = execute_rows(start)
        assert len(rows) == 1 and rows[0]["params"]["command"]["kind"] == unit, rows
        assert rows[0]["params"]["command"]["foreground"] is preparation, rows
        receipt = rows[0]["result"]
        assert receipt["dispatch"] == "dispatched" and receipt["cleanup"] == "complete", receipt
        assert bool(receipt["click"]["preparationEvidence"]) is preparation, receipt
        assert receipt["click"]["continuation"]["kind"] == "observe", receipt
        assert any(r["method"] == "begin" and r["params"].get("clickContract") == "v1" for r in rpc[start:])
        report["steps"].append(
            dict(
                name=name,
                status=result.status,
                before=before,
                after=current,
                native=receipt,
                desktopBefore=rows[0]["before"],
                desktopAfter=snapshot(),
            )
        )

    try:
        fixture = launch_logged_process(
            [sys.executable, str(fixture_source), toolkit, str(state_path), "--click-v1"],
            output / "fixture.log",
            stdin=subprocess.PIPE,
            text=True,
            env={
                **os.environ,
                "GDK_BACKEND": "x11",
                "QT_QPA_PLATFORM": "xcb",
                "GTK_MODULES": "gail:atk-bridge",
                "NO_AT_BRIDGE": "0",
                "QT_ACCESSIBILITY": "1",
                "QT_LINUX_ACCESSIBILITY_ALWAYS_ON": "1",
            },
        )
        setDiagnosticHandler(diagnostic)
        phase = "startup"
        from Xlib import X
        from Xlib.display import Display

        desktop = Display()
        xroot = desktop.screen().root
        import computer_use
        import computer_use_linux
        import computer_use_linux.control.click.pipeline as click_module

        for name, module in [("sdk", computer_use), ("driver", computer_use_linux), ("click", click_module)]:
            path = Path(module.__file__).resolve()
            report["manifest"][name] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        report["manifest"]["python"] = sys.version
        report["manifest"]["pythonExecutable"] = sys.executable
        report["manifest"]["fixture"] = dict(
            path=str(fixture_source), sha256=hashlib.sha256(fixture_source.read_bytes()).hexdigest()
        )
        report["manifest"]["acceptance"] = dict(
            path=__file__, sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        )
        await wait(lambda s: s["reason"] == "ready")
        await arrange("spread")
        app, canvas, button = await connect(False, "shared")
        phase = "visible-background"
        await accepted(
            phase, canvas.click, lambda a, b: len(b["windows"]["a"]["presses"]) == len(a["windows"]["a"]["presses"]) + 1
        )
        await arrange("cover")
        phase = "covered-shared-refused"
        before, start = state(), len(rpc)
        try:
            await canvas.click()
        except ComputerUseError as error:
            assert error.effect == "none", repr(error)
        else:
            raise AssertionError("Covered shared click unexpectedly dispatched")
        assert state()["windows"]["a"]["presses"] == before["windows"]["a"]["presses"]
        assert not execute_rows(start), execute_rows(start)
        report["steps"].append(dict(name=phase, status="refused-before-input"))
        await accepted(
            "covered-semantic-action",
            button.click,
            lambda a, b: b["windows"]["a"]["semanticActions"] == a["windows"]["a"]["semanticActions"] + 1,
            unit="native_action",
        )
        assisted, assisted_canvas, _ = await connect(True, "assisted")
        await arrange("cover")
        await accepted(
            "covered-assisted-single-execute",
            assisted_canvas.click,
            lambda a, b: len(b["windows"]["a"]["presses"]) == len(a["windows"]["a"]["presses"]) + 1,
            preparation=True,
        )
        await arrange("spread")
        await accepted(
            "right-click-background",
            lambda: assisted_canvas.click(ClickOption(button="right")),
            lambda a, b: b["windows"]["a"]["presses"] == a["windows"]["a"]["presses"] + [3],
        )
        await arrange("spread")
        await accepted(
            "double-click-background",
            lambda: assisted_canvas.click(ClickOption(count=2)),
            lambda a, b: b["windows"]["a"]["presses"] == a["windows"]["a"]["presses"] + [1, 1],
        )
        await arrange("spread")
        phase = "captured-point-background"
        observed = await assisted.read(view="image")
        (output / "captured-point-before.png").write_bytes(observed.screenshot)
        captured = next(row["result"] for row in reversed(rpc) if row["method"] == "capture")
        a, b, c, d, tx, ty = captured["coordinates"]["toNative"]
        assert b == c == 0 and a > 0 and d > 0, captured
        x, y, width, height = state()["windows"]["a"]["canvasRect"]
        point = ((x + width / 2 - tx) / a, (y + height / 2 - ty) / d)
        await accepted(
            phase,
            lambda: assisted.click(at=point),
            lambda a, b: b["windows"]["a"]["presses"] == a["windows"]["a"]["presses"] + [1],
        )
        image_target = report["steps"][-1]["native"]["target"]
        assert image_target["node"] is None and image_target["capture"] is not None, image_target
        await arrange("spread")
        await arrange("armMoveAfterPress")
        phase = "double-click-target-moves-after-first-press"
        before, start = state(), len(rpc)
        try:
            await assisted_canvas.click(ClickOption(count=2))
        except ComputerUseError as error:
            assert error.effect == "partial", repr(error)
        else:
            raise AssertionError("Changed geometry did not stop the second press")
        await wait(lambda s: s["windows"]["a"]["movesAfterPress"] == before["windows"]["a"]["movesAfterPress"] + 1)
        await asyncio.sleep(0.15)
        current = state()
        assert current["windows"]["a"]["presses"] == before["windows"]["a"]["presses"] + [1]
        assert_effects_stay_owned(current)
        rows = execute_rows(start)
        assert len(rows) == 1, rows
        receipt = rows[0]["result"]
        assert receipt["dispatch"] == "partial" and receipt["cleanup"] == "complete", receipt
        assert receipt["click"]["transportEvidence"]["submitted"] == 3, receipt
        assert receipt["click"]["transportEvidence"]["expected"] == 5, receipt
        assert receipt["click"]["continuation"]["kind"] == "observe", receipt
        report["steps"].append(dict(name=phase, status="partial-no-replay", native=receipt, after=current))
        assert snapshot()["mask"] & (X.Button1Mask | X.Button2Mask | X.Button3Mask) == 0
        report["passed"] = True
    except BaseException as error:
        report["error"] = repr(error)
        import traceback

        report["traceback"] = traceback.format_exc()
    finally:
        for connection in connections:
            connection.close()
        for connection in connections:
            await connection.waitClosed()
        setDiagnosticHandler(None)
        if fixture is not None and fixture.poll() is None:
            fixture.stdin.close()
            try:
                fixture.wait(4)
            except subprocess.TimeoutExpired:
                fixture.terminate()
                fixture.wait(4)
        report["cleanup"] = dict(
            fixtureReturncode=fixture.returncode if fixture is not None else None, connectionsClosed=True
        )
        expected = {
            (row["params"]["operation"], row["params"]["step"])
            for row in rpc
            if row["method"] == "execute" and "result" in row
        }
        measured = {(row["operation"], row["step"]) for row in native_diagnostics}
        gated = {(row["operation"], row["step"]) for row in click_diagnostics if row["kind"] == "click_decision"}
        report["diagnostics"] = dict(
            expectedExecutions=len(expected),
            measuredExecutions=len(measured),
            matched=expected == measured,
            gateMatched=expected == gated,
            selectionCount=sum(row["kind"] == "click_selection" for row in click_diagnostics),
            allStagesTimed=all(
                row["stages"]
                and all(
                    isinstance(stage["durationMs"], (int, float)) and stage["durationMs"] >= 0
                    for stage in row["stages"]
                )
                for row in native_diagnostics
            ),
        )
        if report["passed"] and not all(
            report["diagnostics"][key] for key in ("matched", "gateMatched", "allStagesTimed")
        ):
            report.update(passed=False, error="Native timing projection did not cover the executed steps.")
        (output / "native-diagnostics.json").write_text(json.dumps(native_diagnostics, ensure_ascii=False, indent=2))
        (output / "click-diagnostics.json").write_text(
            json.dumps(click_diagnostics, ensure_ascii=False, indent=2, default=repr)
        )
        (output / "rpc.json").write_text(json.dumps(rpc, ensure_ascii=False, indent=2, default=repr))
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=repr))
        if desktop is not None:
            desktop.close()
    print(json.dumps(dict(passed=report["passed"], report=str(output / "report.json"))))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run(sys.argv[1], Path(sys.argv[2]).resolve())))
