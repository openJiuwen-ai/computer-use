"""Application startup preserves an exact installation across public observations."""

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from computer_use.client.computer import App, computer_for_controller
from computer_use.client.factory import session_controller
from computer_use.errors import ComputerUseError
from computer_use.interaction.query.instances.scope import resolve
from computer_use.interaction.read.acquisition.directory.service import NativeDirectory
from computer_use.interaction.write.pipeline import WritePipeline
from computer_use.native.contracts import Binding
from computer_use.protocol.directory import decode_directory
from computer_use.session import operations
from test_d34_read_contracts import directory, window
from test_observation_budget_delivery import Port

PATH = "/outside/Fixture.app"
INSTALLATION = dict(id="installed", displayName="Fixture", isRunning=False, identifier="fixture.app", path=PATH)
RUNNING = {**INSTALLATION, "id": "application", "isRunning": True}


def applications(*entries, **extra):
    return dict(session="session", applications=list(entries), stopReasons=[], **extra)


def port():
    result = Port(SimpleNamespace(now=100.0), expired_at="none")
    result.connection.capabilities.add("nativeApplicationPath")
    result.connect = AsyncMock(return_value=Binding("session", None, None, None))
    return result


def test_cold_absolute_selector_uses_exact_native_lookup():
    native = port()
    capabilities = native.connection.capabilities
    native.connection.capabilities = ()

    async def connect(_):
        native.connection.capabilities = capabilities
        return Binding("session", None, None, None)

    native.connect = connect
    native.directory = AsyncMock(return_value=applications(INSTALLATION, path=PATH))

    result = asyncio.run(NativeDirectory(native).applications(selector=PATH))

    assert result["applications"] == [INSTALLATION]
    assert native.directory.await_args.args[2] == {"kind": "applications", "path": PATH}


def test_absolute_startup_uses_the_installation_from_the_first_lookup(monkeypatch):
    controller = session_controller(port())
    choices = directory(window("one"), window("two"))
    controller.reader.directory.applications = AsyncMock(return_value=applications(INSTALLATION, path=PATH))
    # If the path (e.g. a symlink) changed after discovery, only the original
    # installation identity may reach the native launch unit.
    async def collect(read, query, **_):
        if query["kind"] == "surfaces":
            return choices
        return applications({**INSTALLATION, "id": "replacement"}, path=PATH)

    controller.reader.directory.collect = AsyncMock(side_effect=collect)
    controller.reader._startup_directory = AsyncMock(return_value=(RUNNING, choices))
    dispatch = AsyncMock(return_value=dict(dispatch="dispatched", detail="Started.", application=RUNNING))
    monkeypatch.setattr(WritePipeline, "run_in_call", dispatch)

    app = App(controller, {"selector": PATH}, _internal=True)
    result, _ = asyncio.run(resolve(controller, app, "tree"))

    assert result is choices
    assert dispatch.await_args.args[1].application == "installed"
    controller.reader.directory.applications.assert_awaited_once_with(selector=PATH)
    assert all(call.args[1]["kind"] != "applications" for call in controller.reader.directory.collect.await_args_list)


@pytest.mark.parametrize("effect", ["possible", "none"])
def test_observation_rechecks_an_uncertain_launch_without_repeating_it(monkeypatch, effect):
    controller = session_controller(port())
    choices = directory(window("one"), window("two"))

    async def collect(read, query, **_):
        if query["kind"] == "surfaces":
            assert query["application"] == "application"
            return choices
        if query.get("installation") == "installed":
            return applications(*running, installation="installed")
        return applications(INSTALLATION)

    running = []
    controller.reader.directory.applications = AsyncMock(return_value=applications(INSTALLATION))
    controller.reader.directory.collect = AsyncMock(side_effect=collect)
    refused = ComputerUseError(
        dict(code="execution_failed", target="application", effect=effect, detail="launch failed")
    )
    dispatch = AsyncMock(side_effect=refused)
    monkeypatch.setattr(WritePipeline, "run_in_call", dispatch)

    async def observe_twice():
        first = App(controller, {"selector": "Fixture"}, _internal=True)
        with pytest.raises(ComputerUseError) as initial:
            await resolve(controller, first, "tree")
        assert initial.value is refused
        # The next observation may use a newly constructed SDK wrapper.
        later = App(controller, {"selector": "Fixture"}, _internal=True)
        with pytest.raises(ComputerUseError) as pending:
            await resolve(controller, later, "tree")
        assert pending.value.code == ("observation_incomplete" if effect == "possible" else "execution_failed")
        assert dispatch.await_count == (1 if effect == "possible" else 2)
        if effect == "possible":
            running.append(RUNNING)
            recovered, _ = await resolve(controller, later, "tree")
            assert recovered is choices
            assert controller.state(later).application == "application"
            dispatch.assert_awaited_once()

    asyncio.run(observe_twice())


@pytest.mark.parametrize("evidence", ["exact", "partial", "omitted", "other", "collision", "old-driver"])
def test_fresh_text_app_after_positive_process_end_preserves_old_handles(monkeypatch, evidence):
    monkeypatch.setattr(operations, "_guard", None)
    native = port()
    if evidence != "old-driver":
        native.connection.capabilities.add("nativeApplicationLifetime")
    controller = session_controller(native)
    old = {**RUNNING, "id": "instance:original"}
    new = {**RUNNING, "id": "instance:replacement"}
    peer = {**RUNNING, "id": "instance:peer"}
    phase = "initial"
    current = directory(window("A"), focused="A")
    queries = []

    async def catalogue(_token, _target, query, _timeout):
        queries.append(query)
        if query["kind"] == "applications":
            if phase == "initial":
                rows = applications(old)
            elif phase == "ended" and evidence == "collision" and not query.get("includeEnded"):
                rows = applications(new, peer)
            else:
                rows = applications(INSTALLATION if phase == "ended" else new)
            if query.get("includeEnded"):
                rows["endedApplications"] = (
                    [peer["id"]] if evidence == "other"
                    else [] if evidence == "omitted"
                    else [old["id"]]
                )
                if evidence == "partial":
                    rows["stopReasons"] = ["An unrelated installation scan was incomplete"]
            return decode_directory({
                "kind": "applications", "platform": "macos", "observed": current["observed"], **rows,
            })
        if query["kind"] == "roots":
            return {"kind": "roots", "session": "session", "platform": "macos", "application": query["application"],
                    "roots": [], "stopReasons": [], "observed": current["observed"]}
        assert query["kind"] == "surfaces"
        if phase != "initial" and query["application"] == old["id"]:
            raise ComputerUseError({
                "code": "stale_target", "target": "application", "effect": "none", "detail": "application instance expired",
            })
        return {**current, "application": query["application"]}

    async def launch(_intent, target, *_args, **_options):
        nonlocal phase
        assert phase == "ended" and target.application == INSTALLATION["id"]
        phase = "relaunched"
        return {"dispatch": "dispatched", "detail": "Started.", "application": new}

    native.directory = catalogue
    controller.reader._startup_directory = AsyncMock(
        return_value=(new, {**current, "application": new["id"]})
    )
    dispatch = AsyncMock(side_effect=launch)
    monkeypatch.setattr(WritePipeline, "run_in_call", dispatch)
    with computer_for_controller(controller) as computer:
        original = computer.app("Fixture")
        observed = original.read()
        assert "Search" in observed.text
        ref = int(re.search(r"(\d+) (?:text field|text-field) .*Search", observed.text)[1])
        original_element = original.element(ref)
        old_window = next(iter(original._instance.windows))

        phase = "ended"
        assert not computer.list_apps()[0].is_running
        assert queries[-1].get("includeEnded", False) == (evidence != "old-driver")
        with pytest.raises(ComputerUseError, match="expired"):
            original.read()

        replacement = computer.app("Fixture")
        if evidence in ("omitted", "other", "old-driver"):
            assert replacement is original
            dispatch.assert_not_awaited()
            return
        assert replacement is not original
        if evidence == "collision":
            with pytest.raises(ComputerUseError) as rejected:
                replacement.read()
            assert rejected.value.code == "ambiguous_target"
            dispatch.assert_not_awaited()
            return

        assert "Search" in replacement.read().text
        assert controller.state(replacement).application == new["id"]
        assert controller.interfaces.windows.application_for_selector("session", "Fixture") is replacement._instance
        dispatch.assert_awaited_once()
        previous = len(queries)
        for action in (original.read, original_element.click, lambda: original.read(window=old_window)):
            with pytest.raises(ComputerUseError) as rejected:
                action()
            assert rejected.value.code == "stale_target"
        assert len(queries) == previous
        assert controller.state(original).application == old["id"]


def test_ended_application_wire_never_conflicts_with_live_rows():
    old = {**RUNNING, "id": "instance:original"}
    current = directory()
    wire = {
        "kind": "applications", "platform": "macos", "observed": current["observed"],
        **applications(old, endedApplications=[old["id"]]),
    }
    with pytest.raises(ValueError, match="conflicting ended application lifetime"):
        decode_directory(wire)
