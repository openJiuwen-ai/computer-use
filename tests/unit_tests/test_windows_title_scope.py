"""The Windows exact-title selector discovers metadata before acquiring content."""

import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use import ComputerUseError
from computer_use.client.computer import computer_for_controller
from computer_use.client.factory import session_controller
from computer_use.interaction.query.instances.scope import adopt, allowed, resolve
from computer_use.interaction.read.acquisition.components.support import ObservationGap
from computer_use.interaction.read.views.collection import CollectionPlan
from computer_use.interaction.read.views.pipeline import InterfaceReadPipeline, WindowPart, member_target
from computer_use.interaction.read.views.publication import capture_header
from computer_use.interaction.read.views.related import RelatedRead
from computer_use.interaction.requests import ExecuteAction
from computer_use.interaction.write.request.model import ObservedAction
from computer_use.interaction.write.request.observation import WriteWithObservation
from computer_use.native.contracts import Binding, HostPolicy, Intent
from computer_use.native.query.path import TargetQuery
from computer_use.native.transport import ConnectionPort
from computer_use.protocol.directory import decode_directory
from computer_use.protocol.execution import decode_inspect_query
from computer_use.protocol.observation.adapters.wire import decode_query_facts
from computer_use.session.interfaces import DeliveredWindowContext, LookupContext, WindowIdentity
from computer_use.session.observation import CapturedTextDocument, lazy_observation
from computer_use.session.observation_data import retain_observation_data
from unit_tests.test_application_window_lifetimes import directory, ref, setup, surface
from unit_tests.test_metadata_value_pool import payload, scalar

TITLE = "Exact Windows fixture"


def evidence(*windows, complete=True):
    return {
        "session": "session", "platform": "windows", "stopReasons": [] if complete else ["caption query gap"],
        "windowTitle": {"title": TITLE, "complete": complete},
        "applications": [{"id": "editor", "displayName": "Editor", "isRunning": True,
                          "windows": [{"selector": f"owner:{key}", "window": key} for key in windows]}] if windows else [],
    }


def fixture(windows, *, complete=True):
    a = {**surface("a"), "title": TITLE}
    peer = surface("b")
    popup = {**surface("popup", parent="a"), "modal": True}
    controller, prior = setup(a, peer, popup)
    controller.transport.platform = "windows"
    controller.transport.capabilities = ("nativeWindowTitleSelection", "nativeReadIsolation", "nativeStrictWindowRead")
    facts = {**directory(a, peer, popup, focus="b"), "platform": "windows"}
    controller.reader.directory._query = AsyncMock(side_effect=[evidence(*windows, complete=complete), facts])
    controller.interfaces.read = AsyncMock(return_value="scoped observation")
    return controller, prior, facts


def test_title_discovery_uses_bounded_native_directory_operations():
    target = {"session": "session", "instance": None, "rootWindow": None, "surface": None, "node": None, "capture": None}
    window = {**surface("a"), "title": TITLE}
    facts = {**directory(window), "platform": "windows"}
    connection = SimpleNamespace(
        protocol_version=6, platform="windows", session="session", session_identity=(1, "session"),
        capabilities=("nativeDirectory", "nativeWindowTitleSelection", "nativeReadIsolation", "nativeStrictWindowRead"),
        connect=AsyncMock(return_value={"session": "session"}),
        request=AsyncMock(side_effect=[
            {"operation": "caption", "target": target}, {**evidence("a"), "kind": "applications"},
            {"cleanup": "complete"}, {"operation": "identity", "target": target}, facts, {"cleanup": "complete"},
        ]),
    )
    controller = session_controller(ConnectionPort(connection, HostPolicy()))
    controller.interfaces.read = AsyncMock(return_value="scoped observation")
    app = computer_for_controller(controller).app(window_title=TITLE)

    assert app.read(view="tree") == "scoped observation"
    calls = [call.args for call in connection.request.await_args_list]
    assert [method for method, _, _ in calls] == ["begin", "inspect", "finish"] * 2
    assert [body["query"] for method, body, _ in calls if method == "inspect"] == [
        {"kind": "applications", "windowTitle": TITLE},
        {"kind": "surfaces", "application": "editor", "includeAccessibility": False},
    ]
    assert all(0 < budget["timeoutMs"] <= 30000 for _, _, budget in calls)
    assert controller.interfaces.read.await_args.args[0].identity == WindowIdentity.of(window)
    assert not controller.port.calls.retired


def test_title_discovery_is_lazy_and_retains_exact_scope_after_caption_change():
    controller, prior, facts = fixture(("a",))
    app = computer_for_controller(controller).app(window_title=TITLE)
    controller.reader.directory._query.assert_not_called()
    assert app.read(view="both") == "scoped observation"
    scopes = controller.interfaces.read.await_args_list
    owner = scopes[0].args[0]
    assert owner.identity.window == "a"
    assert set(allowed(owner)) == {"a"}
    assert [entry.native_window for entry in capture_header(controller, owner, facts, parts=()).windows] == ["a"]
    assert controller.reader.directory._query.call_args_list[0].args[0] == {"kind": "applications", "windowTitle": TITLE}

    child = WindowIdentity.of(facts["surfaces"][2])
    owner._instance.window_contexts[owner.identity] = DeliveredWindowContext(child, LookupContext(child), 1)
    assert owner._input_context == owner.identity and owner._lookup_context.window == owner.identity
    owner._instance.refs["88"] = {
        "binding": Binding("session", child.instance, child.window, child.surface, "popup-control"),
        "window_identity": child,
    }
    with pytest.raises(ComputerUseError) as popup:
        app.element(88)
    assert popup.value.code == "stale_target"
    owner._instance.refs["89"] = {
        "binding": Binding("session", owner.identity.instance, "a", owner.identity.surface, "outside-proxy"),
        "window_identity": owner.identity,
        "native_window_outside": True,
    }
    with pytest.raises(ComputerUseError) as outside_proxy:
        app.element(89)
    assert outside_proxy.value.code == "stale_target"

    changed = {**facts, "surfaces": [{**facts["surfaces"][0], "title": "Renamed"}, *facts["surfaces"][1:]]}
    adopt(controller, prior, changed)
    assert app.read() == "scoped observation"
    assert controller.interfaces.read.await_args.args[0].identity == owner.identity
    assert controller.reader.directory._query.await_count == 2
    with pytest.raises(ComputerUseError) as rejected:
        app.read(window=ref(prior, "b"))
    assert rejected.value.code == "stale_target"

    ended = {**changed, "surfaces": changed["surfaces"][1:], "endedWindows": [changed["surfaces"][0]]}
    adopt(controller, prior, ended)
    with pytest.raises(ComputerUseError) as stale:
        app.read()
    assert stale.value.code == "stale_target"
    assert controller.reader.directory._query.await_count == 2


@pytest.mark.parametrize(("windows", "complete", "code"), [
    (("a", "b"), True, "ambiguous_target"), (("a", "b"), False, "ambiguous_target"),
    ((), True, "element_not_found"), ((), False, "observation_incomplete"),
    (("a",), False, "observation_incomplete"),
])
def test_title_discovery_never_expands_content_without_a_unique_complete_match(windows, complete, code):
    controller, _, _ = fixture(windows, complete=complete)
    app = computer_for_controller(controller).app(window_title=TITLE)
    with pytest.raises(ComputerUseError) as rejected:
        app.read()
    assert rejected.value.code == code
    controller.interfaces.read.assert_not_awaited()
    assert controller.reader.directory._query.await_count == 1


def test_title_read_has_no_popup_or_desktop_pixels_when_the_window_image_fails():
    controller, _, facts = fixture(("a",))
    facts["surfaces"].append({
        **surface("unowned-popup"),
        "content": {"kind": "popup", "host": None, "evidence": "unclassified"},
    })
    app = computer_for_controller(controller).app(window_title=TITLE)
    app.read()
    owner = controller.interfaces.read.await_args.args[0]
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(
        member_target(owner._instance, facts["surfaces"][0]),
        gaps=(ObservationGap("image", "observation_unavailable", "window pixels unavailable"),),
    ))
    controller.desktop_reader.read = AsyncMock()
    result = asyncio.run(pipeline._collect(owner, "both", False, None, facts, None, None, None))
    assert result.screenshot is None and "window pixels unavailable" in result.text
    assert "Showing the visible desktop" not in result.text
    pipeline.member.assert_awaited_once()
    assert pipeline.member.await_args.args[1]["window"] == "a"
    controller.desktop_reader.read.assert_not_awaited()


def test_title_observation_keeps_exact_window_context_with_two_modal_children():
    controller, _, facts = fixture(("a",))
    facts["surfaces"].append({**surface("other-popup", parent="a"), "modal": True})
    app = computer_for_controller(controller).app(window_title=TITLE)
    app.read()
    owner = controller.interfaces.read.await_args.args[0]
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(
        member_target(owner._instance, facts["surfaces"][0]),
        gaps=(ObservationGap("image", "observation_unavailable", "fixture"),),
    ))

    result = asyncio.run(pipeline._collect(owner, "image", False, None, facts, None, None, None))

    assert owner._context == "a"
    assert "Current input window is not uniquely confirmed" not in result.text
    pipeline.member.assert_awaited_once()
    assert pipeline.member.await_args.args[1]["window"] == "a"
    ordinary = CollectionPlan("a", "image")
    ordinary.update(facts, {item["window"]: item for item in facts["surfaces"]})
    assert ordinary.selection_error is not None and ordinary.selection_error.code == "ambiguous_target"


def test_title_details_keep_only_the_selected_window_metadata():
    controller, _, facts = fixture(("a",))
    app = computer_for_controller(controller).app(window_title=TITLE)
    app.read()
    owner = controller.interfaces.read.await_args.args[0]
    target = member_target(owner._instance, facts["surfaces"][0])
    applications = {"applications": [
        {"id": "editor", "displayName": "Editor", "windows": [{"window": "a"}, {"window": "b"}]},
        {"id": "outside-app", "displayName": "Private other App"},
    ]}
    pipeline = InterfaceReadPipeline(controller)
    pipeline.member = AsyncMock(return_value=WindowPart(
        target, gaps=(ObservationGap("image", "observation_unavailable", "fixture"),),
        source=SimpleNamespace(target=target, applications=applications, source_directory=facts, directory=facts,
                               scope_confirmed=True, context_notes=()),
    ))
    result = asyncio.run(pipeline._collect(owner, "both", True, None, facts, None, None, None))
    assert "computer.app(" not in result.text and "existing App handle" in result.text
    prefix = "Captured interface metadata: "
    header = json.loads(next(line[len(prefix):] for line in result.read_details(length=16384).text.splitlines()
                             if line.startswith(prefix)))
    assert header["reported_input"] is None and header["input_state"] == "outside"
    heading, = header["windows"]
    for captured in (header["directory"], heading["source_directory"], heading["rechecked_directory"]):
        assert [item["window"] for item in captured["surfaces"]] == ["a"]
        assert captured.get("focusedWindow") is None
    selected_app, = heading["source_applications"]["applications"]
    assert selected_app["id"] == "editor" and selected_app["windows"] == [{"window": "a"}]
    assert [item["window"] for item in owner._instance.directory["surfaces"]] == ["a", "b", "popup"]


def test_title_surface_read_requests_uia_root_for_the_bound_window_only():
    controller, _, facts = fixture(("a",))
    app = computer_for_controller(controller).app(window_title=TITLE)
    app.read()
    owner = controller.interfaces.read.await_args.args[0]
    controller.reader.directory._query = AsyncMock(return_value=facts)
    returned, _ = asyncio.run(resolve(controller, owner, "both"))
    controller.reader.directory._query.assert_awaited_once_with(
        {"kind": "surfaces", "application": "editor", "rootWindow": "a"}, 30000
    )
    assert returned is facts and {row["window"] for row in returned["surfaces"]} == {"a", "b", "popup"}


@pytest.mark.parametrize("action", ["element", "key", "close"])
@pytest.mark.parametrize("scope", ["exact_title", "ordinary_window", "ordinary_app"])
def test_public_windows_write_refresh_preserves_the_owner_scope(action, scope):
    controller, ordinary, facts = fixture(("a",))
    controller.port.platform = "windows"
    controller.port.calls.limit = lambda _: nullcontext()
    title = computer_for_controller(controller).app(window_title=TITLE)
    assert title.read(view="tree") == "scoped observation"
    owner = controller.interfaces.read.await_args.args[0]
    identity = owner.identity
    owner._instance.refs["22"] = {
        "binding": Binding("session", identity.instance, identity.window, identity.surface, "element:a"),
        "window_identity": identity,
    }
    owner._instance.input_context = identity
    owner._instance.observed.add(identity.window)
    app = title if scope == "exact_title" else ordinary
    window = ref(ordinary, "a") if scope != "ordinary_app" else None
    assert {item["window"] for item in facts["surfaces"]} == {"a", "b", "popup"}

    stop = RuntimeError("Stopped at pre-write directory refresh")
    query = controller.reader.directory._query = AsyncMock(side_effect=stop)
    with pytest.raises(RuntimeError, match="Stopped at pre-write directory refresh"):
        if action == "element":
            app.element(22, window=window).type_text("synthetic")
        elif action == "key":
            app.press_key("Return", window=window)
        else:
            app.close_window(ref(ordinary, "a"))

    expected = {"kind": "surfaces", "application": "editor"}
    if scope == "exact_title":
        expected["rootWindow"] = "a"
    query.assert_awaited_once_with(expected, 30000)


def test_title_scope_failure_exposes_only_the_ended_target_evidence():
    controller, prior, facts = fixture(("a",))
    app = computer_for_controller(controller).app(window_title=TITLE)
    app.read()
    controller.interfaces.read = type(controller.interfaces).read.__get__(controller.interfaces)
    ended = {**facts, "surfaces": facts["surfaces"][1:2], "endedWindows": [facts["surfaces"][0]]}
    controller.reader.directory._query = AsyncMock(return_value=ended)
    with pytest.raises(ComputerUseError) as stale:
        app.read()
    assert stale.value.code == "stale_target" and stale.value.effect == "none"
    assert stale.value.directory["surfaces"] == []
    assert [item["window"] for item in stale.value.directory["endedWindows"]] == ["a"]
    assert "focusedWindow" not in stale.value.directory
    assert [item["window"] for item in prior._instance.directory["surfaces"]] == ["b"]


def test_title_write_suffix_retains_fixed_scope_and_does_not_expand_related_windows():
    controller, _, facts = fixture(("a",))
    app = computer_for_controller(controller).app(window_title=TITLE)
    app.read()
    owner = controller.interfaces.read.await_args.args[0]
    controller.transport.retain_session = lambda _: nullcontext()
    controller.port.calls.limit = lambda _: nullcontext()
    result = lazy_observation(CapturedTextDocument("The fixed window"))
    retain_observation_data(result, (), (), directory=facts)
    controller.interfaces.read.reset_mock(return_value=True)
    controller.interfaces.read.return_value = result
    target = owner._window_identity
    request = ExecuteAction(owner, Intent("click", {}), target)
    related = RelatedRead(target, (owner.identity,))
    suffix = WriteWithObservation(controller, SimpleNamespace(contract=SimpleNamespace(effects=frozenset())))
    outcome = asyncio.run(suffix.observe(
        request, ObservedAction({"status": "dispatched"}), "dispatched", (1, "session"), related,
    ))
    controller.interfaces.read.assert_awaited_once_with(owner, "tree", False, None)
    assert outcome.observation is result and outcome.choices == ()


def test_caption_wire_evidence_is_scoped_to_the_windows_query():
    query = {"kind": "applications", "windowTitle": TITLE}
    assert decode_inspect_query(query) == query
    value = {**evidence(), "kind": "applications", "scope": "runningWindows",
             "observed": {"id": "observation", "startedAt": 1, "completedAt": 2}}
    assert decode_directory(value) == value
    with pytest.raises(ValueError, match="caption catalogue"):
        decode_directory({**value, "platform": "macos"})
    nodes = {"kind": "nodes", "maxNodes": 4, "maxDepth": 3, "fields": ["Name"], "strictWindow": True}
    assert decode_inspect_query(nodes) == nodes


def test_strict_observation_decodes_an_outside_declaration_without_a_body():
    raw = payload(platform="windows")
    raw["nodes"][0].update(
        source="windows.uia", nativeType="IUIAutomationElement",
        properties=[{"key": ["windows.uia", "ControlType"], "value": scalar("ControlType.Window")}],
    )
    raw["nodes"].append({
        "id": "outside", "source": "windows.uia", "kind": "element", "nativeType": "IUIAutomationElement",
        "properties": None, "read": {"complete": False, "issues": [{"reason": "outside_window_scope"}]},
    })
    facts = decode_query_facts(raw)
    assert facts["outsideWindowNodes"] == ["outside"]
    assert [node["node"] for node in facts["nodes"]] == ["root"]


def test_strict_path_uses_inside_match_without_reading_the_outside_declaration():
    async def query_nodes(_operation, target, _fields, _max_nodes, _depth, _timeout, **options):
        assert target.node == "root"
        strict = options.get("strict_window", False)
        values = {"root": ("ControlType.Window", "Fixture"), "inside": ("ControlType.Button", "Target")}
        if not strict:
            values["outside"] = ("ControlType.Button", "Target")
        return {
            "session": "session", "platform": "windows", "surface": "surface", "roots": ["root"],
            "observed": {"id": "observation"}, "outsideWindowNodes": ["outside"] if strict else [],
            "nodes": [{"node": key, "values": {"ControlType": role, "Name": name},
                       "requestedFields": ["ControlType", "Name"], "fieldStatus": {},
                       "actions": {"state": "complete", "items": []}}
                      for key, (role, name) in values.items()],
            "relations": [{"source": key, "property": "RawViewChildren", "certainty": "confirmed", "state": "complete",
                           "targets": ["outside", "inside"] if key == "root" else []} for key in values],
        }

    port = SimpleNamespace(platform="windows", capabilities=(), query_nodes=query_nodes)
    root = Binding("session", "instance", "window", "surface", "root")
    strict = TargetQuery(port, "read", root, lambda: 1000, strict_window=True)
    assert asyncio.run(strict.resolve(["Target"])).node == "inside"
    ordinary = TargetQuery(port, "read", root, lambda: 1000)
    with pytest.raises(ComputerUseError) as ambiguous:
        asyncio.run(ordinary.resolve(["Target"]))
    assert ambiguous.value.code == "ambiguous_target"
