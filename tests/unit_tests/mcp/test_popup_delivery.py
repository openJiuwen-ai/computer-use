# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""One public click must deliver a late popup through the real MCP worker."""

import base64
import inspect
import os
import re
import sys
from datetime import timedelta
from io import BytesIO
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from PIL import Image


def popup_fixture(failure=None, *, capture_parent=False, transient=False, image_action_chain=False):
    """Replace native I/O only; retain public SDK, read, numbering and delivery paths."""
    import base64
    from contextlib import nullcontext
    from io import BytesIO
    from types import SimpleNamespace

    from computer_use.client.computer import App
    from computer_use.client.factory import session_controller
    from computer_use.errors import ComputerUseError
    from computer_use.native.contracts import Binding, Cleanup, OpenedOperation
    from computer_use.native.lifecycle import CallLifecycle
    from computer_use.native.scopes import RunningApplication
    from computer_use.protocol.captures import decode_capture
    from computer_use.protocol.observation import Action, NativeNode, NativeObservation, Relation
    from computer_use.protocol.observation.adapters.records import DriverObservationReply, DriverReadMetadata
    from computer_use.session.observation_data import observation_data
    from computer_use.session.state import AppState
    from PIL import Image

    def surface(key, parent=None, version=0):
        return {
            "window": key,
            "instance": "instance:" + key + (f":{version}" if version else ""),
            "surface": "surface:" + key,
            "node": "node:" + key,
            "title": "Accessibility" if key == "A" else "Open application",
            "kind": "sheet" if parent else "window",
            "parent": parent,
            "parentState": "known" if parent else "none",
            "modal": parent is not None,
            "nativeWindow": True,
            "accessibility": True,
        }

    class Port:
        def __init__(self):
            self.connection = SimpleNamespace(
                session="session",
                session_identity=(1, "session"),
                platform="macos",
                capabilities={"nativeCapture"},
                retain_session=lambda identity: nullcontext(),
            )
            self.capabilities = self.connection.capabilities
            self.calls = CallLifecycle(self)
            self.trace = []
            self.suffix_budgets = []
            self.clicked = self.popup_visible = False
            self.popup_read = False
            self.opened = self.finished = 0
            self.partial = self.fail_next_write = False
            self.version = 0
            self.writes = 0

        async def connect(self, timeout):
            return Binding("session", None, None, None)

        async def begin_read(self, target, timeout):
            self.calls.check_begin("read", "read", target)
            self.opened += 1
            return OpenedOperation(str(self.opened), target)

        async def finish(self, token):
            self.finished += 1
            return Cleanup.COMPLETE

        async def directory(self, token, target, query, timeout):
            self.trace.append(("directory", self.popup_visible))
            if self.clicked:
                self.suffix_budgets.append(("directory", timeout))
            if failure == "directory" and self.popup_read:
                raise TimeoutError("Optional popup directory recheck failed")
            tick = 10 * len(self.trace) if transient else 1
            partial = self.partial if transient or image_action_chain else self.popup_visible
            popup = surface("Sheet", None if transient else "A", self.version)
            return {
                "kind": "surfaces",
                "session": "session",
                "application": "application",
                "platform": "macos",
                "observed": {"id": f"directory:{tick}", "startedAt": tick, "completedAt": tick + 1},
                "surfaces": [surface("A"), *([popup] if self.popup_visible else [])],
                "focusedWindow": "Sheet" if self.popup_visible else "A",
                "stopReasons": ["unrelated AXError -25202"] if partial else [],
                "rootDiscoveryComplete": not partial,
                "provider": {"status": "notRequested", "attribute": None, "writeStatus": None},
            }

        async def nodes(self, token, target, fields, max_nodes, max_depth, timeout, **options):
            self.trace.append(("tree", target.root_window))
            if self.clicked:
                self.suffix_budgets.append(("tree:" + target.root_window, timeout))
            key = target.root_window
            if key == "A":
                # The first suffix directory still contains only the parent.
                # The popup appears while its perfectly usable AX tree is read.
                if not transient and not image_action_chain:
                    self.popup_visible = self.clicked
                children = {
                    "add": NativeNode(role="button", name="Add application", actions=(Action("press", "AXPress"),)),
                    "unchanged": NativeNode(role="button", name="Unchanged parent control"),
                }
            else:
                self.popup_read = True
                if failure in ("tree", "both"):
                    raise ComputerUseError(
                        {
                            "code": "observation_incomplete",
                            "effect": "none",
                            "target": "popup AX",
                            "detail": "Popup AX is not ready",
                            "next": "use the captured popup image",
                        }
                    )
                children = {
                    "search": NativeNode(role="text_field", name="Search applications"),
                    "cancel": NativeNode(
                        role="button", name="Cancel file selection", actions=(Action("press", "AXPress"),)
                    ),
                }
            root = "node:" + key
            native = NativeObservation(
                "macos",
                "session",
                "tree:" + key,
                (1, 2),
                (root,),
                {
                    root: NativeNode(
                        role="window",
                        name="Accessibility" if key == "A" else "Open application",
                        relations={"children": Relation("children", tuple(children), total=len(children))},
                    ),
                    **children,
                },
            )
            return DriverObservationReply(native, DriverReadMetadata("surface:" + key, None, {}, {}, (), {}))

        async def capture(self, token, target, max_dimension, timeout):
            self.trace.append(("image", target.root_window))
            self.suffix_budgets.append(("image", timeout))
            assert capture_parent or target.root_window == "Sheet", "A tree-only click must not capture the unchanged parent"
            if failure in ("image", "both"):
                raise TimeoutError("Popup screenshot unavailable")
            buffer = BytesIO()
            color = "#22bb44"
            if image_action_chain:
                if self.writes == 0:
                    color = "#eeeeee"
                elif self.writes > 2:
                    color = "#aa1199"
            with Image.new("RGB", (20, 10), color) as image:
                image.save(buffer, format="PNG")
            png = decode_capture(
                {
                    "id": "popup-capture" if target.root_window == "Sheet" else "parent-capture",
                    "kind": "window",
                    "scope": target.surface,
                    "startedAt": 1,
                    "completedAt": 2,
                    "width": 20,
                    "height": 10,
                    "png": base64.b64encode(buffer.getvalue()).decode(),
                },
                "window",
            )
            return {
                "image": png,
                "observed": {},
                "coordinates": {"nativeSpace": "screen", "toNative": (1, 0, 0, 1, 0, 0)},
            }

    port = Port()
    controller = session_controller(port)
    app = App(
        controller,
        {
            "selector": "application",
            "_nativeState": AppState("application", running=RunningApplication("application", "Fixture", "session")),
        },
        _internal=True,
    )

    async def dispatch(intent, target, *args, **kwargs):
        port.trace.append(("write", target.root_window, target.node))
        if image_action_chain:
            assert target.root_window == "A"
            port.writes += 1
            port.partial = True
            return {"target": "Fixture window", "status": "dispatched", "detail": "FIXTURE_DISPATCHED"}
        if transient:
            assert target.root_window == "A" and target.node == "add"
            if port.fail_next_write:
                raise ComputerUseError({
                    "code": "input_unavailable", "effect": "none", "target": "Add application",
                    "detail": "Write was refused", "next": "observe the current window",
                })
            port.writes += 1
            port.popup_visible = not port.popup_visible
            detail = "POPUP_OPEN_DISPATCHED" if port.popup_visible else "POPUP_DISMISS_DISPATCHED"
            return {"target": "Add application", "status": "dispatched", "detail": detail}
        assert not port.clicked, "Observation recovery must never replay input"
        assert target.root_window == "A" and target.node == "add"
        port.clicked = True
        return {"target": "Add application", "status": "dispatched", "detail": "ADD_DISPATCHED_ONCE"}

    controller.engine.execute = dispatch
    controller.engine.execute_in_call = dispatch
    return SimpleNamespace(app=app, port=port, controller=controller, observation_data=observation_data)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, "tree", "image", "both", "directory"])
async def test_one_public_click_delivers_late_popup_without_model_reread(tmp_path, failure):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp"],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "COMPUTER_USE_LOG_DIR": str(tmp_path)},
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as client,
    ):
        await client.initialize()

        async def call(code):
            result = await client.call_tool("computer_use", {"code": code})
            text = "\n".join(block.text for block in result.content if block.type == "text")
            assert not result.isError, text
            return result, text

        _, initial = await call(
            inspect.getsource(popup_fixture) + f"\nglobals().update(vars(popup_fixture({failure!r})))\napp.read()"
        )
        assert "Unchanged parent control" in initial
        match = re.search(r"(\d+) button Add application", initial)
        assert match is not None, initial
        # No explicit read, private collector call, or manually published observation.
        result, text = await call(f"port.trace.clear()\nclicked = app.element({match[1]}).click()")
        assert text.count("Action [dispatched]") == 1, text
        assert "ADD_DISPATCHED_ONCE" in text
        assert "Open application" in text
        assert "Unchanged parent control" not in text  # The parent retains its delivered diff baseline.
        assert "unrelated AXError -25202" in text
        if failure == "directory":
            assert "Optional popup directory recheck failed" in text
        assert ("Search applications" in text) == (failure not in ("tree", "both")), text
        images = [block for block in result.content if block.type == "image"]
        assert len(images) == (0 if failure in ("image", "both") else 1)
        if images:
            with Image.open(BytesIO(base64.b64decode(images[0].data))) as image:
                assert image.size == (20, 10)
                assert image.getpixel((10, 5))[:3] == (34, 187, 68)

        # Inspect acknowledgements only after the previous tool result reached
        # the client. Capturing a component must not itself count as delivery.
        await call(
            "assert [entry for entry in port.trace if entry[0] == 'write'] == [('write', 'A', 'add')], port.trace\n"
            "assert port.trace.count(('tree', 'A')) == 1, port.trace\n"
            "assert port.trace.count(('tree', 'Sheet')) == 1, port.trace\n"
            "assert port.trace.index(('image', 'Sheet')) < port.trace.index(('tree', 'Sheet')), port.trace\n"
            "assert ('directory', False) in port.trace and ('directory', True) in port.trace, port.trace\n"
            "assert port.opened == port.finished, (port.opened, port.finished)\n"
            "assert all(0 < ms <= 5000 for _, ms in port.suffix_budgets), port.suffix_budgets\n"
            "assert all(ms <= 2000 for component, ms in port.suffix_budgets if component == 'tree:Sheet')\n"
            "assert clicked.status == 'dispatched', clicked\n"
            "assert clicked.observation_status == 'partial', clicked.observation_status\n"
            "coverage = {item['window']: item for item in observation_data(clicked.observation)['window_coverage']}\n"
            "assert coverage['Sheet']['requested'] == ('image', 'tree'), coverage\n"
            f"assert coverage['Sheet']['delivered'] == {tuple(c for c in ('image', 'tree') if failure not in (c, 'both'))!r}, coverage\n"
            "assert set(coverage['Sheet']['included']) == set(coverage['Sheet']['delivered']), coverage\n"
            "assert coverage['A']['delivered'] == ('tree',), coverage\n"
        )
        if failure not in ("tree", "both"):
            popup_ref = re.search(r"(\d+) [^\n]*Search applications", text)
            assert popup_ref is not None, text
            await call(
                f"search = app.element({popup_ref[1]})\n"
                "assert search._snapshot['binding'].root_window == 'Sheet'\n"
                "assert search._snapshot['binding'].node == 'search'\n"
            )


def test_scope_managed_read_defers_display_without_duplicate_parent_projection(monkeypatch):
    """Keep numbering's preview, but defer final display work until popup acquisition."""
    from computer_use.interaction.read.presentation.pipeline import PrepareSnapshotPipeline

    fixture = popup_fixture()
    initial = fixture.app.read()
    add = re.search(r"(\d+) button Add application", initial.text)
    assert add is not None
    original = PrepareSnapshotPipeline.run

    def traced(pipeline, request, *args, **kwargs):
        fixture.port.trace.append(("prepare", request.snapshot.native.roots, request.public_api))
        return original(pipeline, request, *args, **kwargs)

    monkeypatch.setattr(PrepareSnapshotPipeline, "run", traced)
    fixture.port.trace.clear()
    clicked = fixture.app.element(int(add[1])).click()
    assert "Search applications" in clicked.observation.text
    assert "Unchanged parent control" in clicked.observation.text
    trace = fixture.port.trace
    # Each body needs numbering's preview and one final public projection.
    # The inner app reader must not add another projection before the popup.
    for root in ("node:A", "node:Sheet"):
        assert trace.count(("prepare", (root,), False)) == 1, trace
        assert trace.count(("prepare", (root,), True)) == 1, trace
    last_capture = max(index for index, entry in enumerate(trace) if entry[0] in ("tree", "image"))
    first_projection = next(index for index, entry in enumerate(trace) if entry[0] == "prepare" and entry[2])
    assert first_projection > last_capture, trace


@pytest.mark.anyio
@pytest.mark.parametrize("case", [
    "dismissed", "partial", "printed", "image", "explicit", "full", "refused", "reappeared", "replaced",
])
async def test_same_call_transient_window_delivery(tmp_path, case):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "computer_use_mcp"],
        cwd=Path(__file__).resolve().parents[3],
        env={**os.environ, "COMPUTER_USE_LOG_DIR": str(tmp_path)},
    )
    async with stdio_client(params) as streams, ClientSession(*streams) as client:
        await client.initialize()

        async def call(code):
            reply = await client.call_tool("computer_use", {"code": code})
            text = "\n".join(block.text for block in reply.content if block.type == "text")
            assert not reply.isError, text
            return reply, text

        _, initial = await call(
            inspect.getsource(popup_fixture) + "\nglobals().update(vars(popup_fixture(transient=True)))\napp.read()"
        )
        add = re.search(r"(\d+) button Add application", initial)
        assert add is not None
        before_close = {
            "partial": "port.partial = True",
            "printed": "print(opened.observation)",
            "image": "print_image(opened.observation.screenshot)",
            "explicit": "app.read(view='both', window=popup)",
            "full": "app.read(view='both', window=popup, full=True)",
        }.get(case, "")
        after_close = {
            "refused": (
                "from computer_use.errors import ComputerUseError\n"
                "port.fail_next_write = True\ntry: button.click()\nexcept ComputerUseError: pass"
            ),
            "reappeared": "button.click()",
            "replaced": "port.version = 2\nbutton.click()",
        }.get(case, "")
        reply, text = await call(
            f"button = app.element({add[1]})\n"
            "opened = button.click()\n"
            "popup = next(ref for ref, key in app._instance.windows.items() if key == 'Sheet')\n"
            f"{before_close}\n"
            "closed = button.click()\n"
            f"{after_close}"
        )
        images = [block for block in reply.content if block.type == "image"]
        assert text.index("POPUP_OPEN_DISPATCHED") < text.index("POPUP_DISMISS_DISPATCHED")
        if case == "dismissed":
            assert not images, text
            assert "Search applications" not in text
            note = "absent from the latest complete application observation"
            assert text.index("POPUP_OPEN_DISPATCHED") < text.index(note) < text.index("POPUP_DISMISS_DISPATCHED")
            await call(
                "from computer_use.session.interfaces import WindowLifetime\n"
                "coverage = observation_data(opened.observation)['window_coverage']\n"
                "assert [row['delivered'] for row in coverage] == [()], coverage\n"
                "assert opened.observation.screenshot is not None\n"
                "assert 'Search applications' in opened.observation.text\n"
                "assert any(item.identity.window == 'Sheet' and item.lifetime is WindowLifetime.RETAINED "
                "for item in app._instance.evidence.values())"
            )
        else:
            assert images, text
            assert "Search applications" in text
            assert "absent from the latest complete application observation" not in text
