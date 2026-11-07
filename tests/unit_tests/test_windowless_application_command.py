"""An observed application menu can address a windowless Command shortcut."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from computer_use.errors import ComputerUseError
from computer_use.interaction.agent.pipeline import AgentInterfacePipeline
from computer_use.interaction.requests import ExecuteAction
from computer_use.native.contracts import Binding, Intent
from computer_use.native.scopes import RunningApplication
from computer_use.pipeline.core import Control
from computer_use.session.interfaces import LookupContext, SurfaceOwner, WindowIdentity
from computer_use.session.state import AppState
from unit_tests.test_d34_read_contracts import directory, setup


def test_delivered_windowless_menu_addresses_one_application_command():
    controller, app, instance = setup(directory())
    controller.transport.capabilities = {"nativeApplicationCommand"}
    running = RunningApplication("application", "Fixture", "session")
    controller.state(app).running = running
    target = Binding("session", "application-instance", None, "menu", "menu-root")
    source = AppState("Fixture", running=running, query_target=target, query_scope_kind="applicationMenu")
    owner = SurfaceOwner(controller, source)
    write = AsyncMock()
    api = AgentInterfacePipeline(controller, write, None)
    control = Control()

    def key(value="super+o", **options):
        return asyncio.run(api.keyboard(app, "press_key", {"key": value}, control, **options))

    # A sampled or retained owner alone cannot change the input destination.
    app._lookup_scopes = (owner,)
    with pytest.raises(ComputerUseError, match="no delivered input context"):
        key()
    instance.lookup_context = LookupContext(None, menu_only=True, menus=(owner,))
    assert key() is write.arun.return_value
    write.arun.assert_awaited_once_with(
        ExecuteAction(app, Intent("key", {"key": "super+o"}), Binding("session", "application-instance", None, "menu")),
        control,
    )

    for invocation in (
        lambda: key("Tab"),
        lambda: key(global_input=True),
        lambda: asyncio.run(api.keyboard(app, "type_text", {"text": "o"}, control)),
    ):
        with pytest.raises(ComputerUseError, match="no delivered input context"):
            invocation()
    source.menu_window_identity = WindowIdentity("window", "window-surface", "window-instance")
    with pytest.raises(ComputerUseError, match="no delivered input context"):
        key()
    source.menu_window_identity = None
    source.running = RunningApplication("replacement-process", "Fixture", "session")
    with pytest.raises(ComputerUseError, match="no delivered input context"):
        key()
    source.running = running
    instance.delivered_lookup_windows.add(WindowIdentity("old-window", "window-surface", "window-instance"))
    with pytest.raises(ComputerUseError, match="no delivered input context"):
        key()
    assert write.arun.await_count == 1
