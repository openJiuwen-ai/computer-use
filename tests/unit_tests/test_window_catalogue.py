"""Discover exact visible-window references without opening either app body."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from computer_use.client.computer import computer_for_controller
from computer_use.client.factory import session_controller
from computer_use.errors import ComputerUseError
from computer_use.native.contracts import Binding
from computer_use.protocol.directory import decode_directory
from test_d34_read_contracts import directory, window
from test_observation_budget_delivery import Port


def surface(window_id, title):
    return {
        **window(window_id, title=title), "node": None, "parentState": "unknown",
        "modal": None, "nativeWindow": True, "accessibility": False,
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_public_window_catalogue_keeps_same_name_instances_and_scopes_the_next_read(reverse):
    native = Port(SimpleNamespace(now=100.0), expired_at="none")
    native.capabilities.add("nativeWindowCatalogue")
    native.connect = AsyncMock(return_value=Binding("session", None, None, None))
    controller = session_controller(native)
    owned = directory(window("owned"), window("sibling"), focused="sibling")
    peer = surface("peer", "")
    target = surface("owned", "project.mlt")
    calls = []
    rows = [
        {"selector": "instance:peer", "window": peer, "appIsForeground": False},
        {"selector": "instance:owned", "window": target, "appIsForeground": True},
    ]
    if reverse:
        rows.reverse()

    async def discover(_token, binding, query, _timeout):
        calls.append(query)
        if query == {"kind": "applications"}:
            return decode_directory({
                "kind": "applications", "platform": "macos", "session": "session",
                "observed": owned["observed"], "stopReasons": [],
                "applications": [
                    dict(id=item["selector"], displayName="Fixture", isRunning=True, path=None, identifier=None)
                    for item in rows
                ],
            })
        if query == {"kind": "windowCatalogue", "appName": "Fixture"}:
            assert binding == Binding("session", None, None, None)
            return decode_directory({
                "kind": "windowCatalogue", "platform": "macos", "session": "session",
                "observed": owned["observed"], "appName": "Fixture",
                "stopReasons": [],
                "windows": rows,
            })
        assert query["kind"] == "surfaces"
        assert query["application"] == "instance:owned"
        return {**owned, "application": query["application"]}

    native.directory = discover
    controller.desktop_reader.read = AsyncMock(side_effect=AssertionError("must not capture desktop pixels"))
    with computer_for_controller(controller) as computer:
        apps = computer.list_apps()
        catalogue = computer.list_windows(app_name="Fixture")
        assert len(catalogue.windows) == 2 and catalogue.stop_reasons == ()
        assert [row.app_id for row in catalogue.windows] == [app.id for app in apps]
        new = next(row for row in catalogue.windows if row.app_is_foreground)
        old = next(row for row in catalogue.windows if not row.app_is_foreground)
        assert old.app_id != new.app_id and old.window_ref != new.window_ref
        assert old.title is None and old.app_is_foreground is False
        assert new.title == "project.mlt" and new.app_is_foreground is True
        assert calls == [{"kind": "applications"}, {"kind": "windowCatalogue", "appName": "Fixture"}]
        assert not native.queries
        assert len(native.finished) == 2
        app = computer.app(new.app_id)
        assert "Search" in app.read(window=new.window_ref).text
        assert native.queries and all(query[0].root_window == "owned" for query in native.queries)
        current_directory = app._instance.directory
        current_members = dict(app._instance.members)
        assert set(current_members) == {"owned", "sibling"}
        assert computer.list_windows(app_name="Fixture").windows == catalogue.windows
        assert app._instance.directory is current_directory
        assert app._instance.members == current_members
        controller.desktop_reader.read.assert_not_awaited()


@pytest.mark.parametrize("platform", ["macos", "windows", "linux"])
def test_public_window_catalogue_reports_unsupported_without_fallback(platform):
    native = Port(SimpleNamespace(now=100.0), expired_at="none")
    native.connection.platform = platform
    if platform != "macos":
        native.capabilities.add("nativeWindowCatalogue")
    native.connect = AsyncMock(return_value=Binding("session", None, None, None))
    native.directory = AsyncMock()
    controller = session_controller(native)
    with computer_for_controller(controller) as computer:
        with pytest.raises(ComputerUseError) as failure:
            computer.list_windows(app_name="Fixture")
        assert failure.value.code == "unsupported_operation"
    native.directory.assert_not_awaited()
    assert not native.queries
