# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve one logical instance, then retain only confirmed member windows."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from computer_use.diagnostics import local_failure
from computer_use.interaction.query.instances.membership import ancestor, automatic_root, membership_evidence
from computer_use.interaction.read.model import WindowSelectionRequired
from computer_use.native.contracts import Knowledge
from computer_use.native.query.directory import SurfaceReadProfile, select_application, selected_window, surfaces_query
from computer_use.native.scopes import (
    FixedWindowInvalidated,
    FixedWindowUnconfirmed,
    RunningApplication,
    StartingApplication,
    WorkingWindow,
)
from computer_use.session.interfaces import SurfaceOwner, WindowIdentity, WindowLifetime
from computer_use.session.state import AppState

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.read.model import AppObservation, MenuAppObservation
    from computer_use.interaction.records import UIHandle
    from computer_use.protocol.records import NativeDirectory as DirectoryFacts
    from computer_use.protocol.records import SurfaceInfo
    from computer_use.session.interfaces import InstanceState


def validate_session(controller: SessionController, ui: UIHandle) -> None:
    """Check the handle's session and fixed instance/window identities.

    Args:
        controller: Session owning the native connection and handle bindings.
        ui: App/Window handle whose identities are being checked.
    """
    if controller.port.calls.retired:
        raise local_failure(
            "native session is retired after unconfirmed cleanup",
            "reset the session and observe again; never replay the previous input",
            "stale_target",
        )
    # The pipeline shares retained bindings with their owning service.
    # pylint: disable-next=protected-access
    anchor = ui._instance.anchor
    if anchor is not None:
        controller.validate_element(anchor.root)
        require_identity(ui, WindowIdentity(anchor.root.root_window, anchor.root.surface, anchor.root.instance))
        identity = getattr(ui, "_window_identity", None)
        if identity is not None:
            # pylint: disable-next=protected-access
            require_identity(ui, WindowIdentity(ui._anchor, identity.surface, identity.instance))
    else:
        running = controller.state(ui).running
        if running is not None and running.session != controller.transport.session:
            raise local_failure("the application belongs to an expired session", "obtain a new App", "stale_target")


def require_identity(ui: UIHandle, identity: WindowIdentity, *, current: bool = False) -> None:
    """Retained unknown identities may be refreshed, but cannot authorize input."""
    # pylint: disable-next=protected-access
    evidence = ui._instance.evidence.get(identity)
    if evidence is None or evidence.lifetime is WindowLifetime.RETIRED:
        raise local_failure(
            "the fixed Window identity no longer exists",
            "read the App and select a new Window reference",
            "stale_target",
        )
    # pylint: disable-next=protected-access
    current_item = ui._instance.members.get(identity.window)
    if current_item is not None and WindowIdentity.of(current_item) != identity:
        raise local_failure(
            "the fixed Window identity changed", "read the App and select a new Window reference", "stale_target"
        )
    if current and evidence.membership is not Knowledge.YES:
        unknown = evidence.membership is Knowledge.UNKNOWN
        raise local_failure(
            "the fixed Window membership is not confirmed in the current directory"
            if unknown
            else "the fixed Window is outside this App instance",
            "read this Window again to refresh its evidence" if unknown else "discover the intended App instance",
            "observation_incomplete" if unknown else "stale_target",
        )


def retain_fixed_failure(
    instance: InstanceState, selection: WorkingWindow, error: FixedWindowInvalidated | FixedWindowUnconfirmed
) -> None:
    """Consume only evidence for this exact fixed root, including before first read."""
    if error.binding != selection.root:
        return
    instance.anchor = selection
    identity = WindowIdentity(error.binding.root_window, error.binding.surface, error.binding.instance)
    if isinstance(error, FixedWindowInvalidated):
        instance.retire(identity)
    else:
        instance.unconfirm(identity)


def adopt(
    controller: SessionController, ui: UIHandle, directory: DirectoryFacts, selection: WorkingWindow
) -> InstanceState:
    """Record a confirmed instance directory and update its member-window owners.

    Args:
        controller: Session providing handle state and native window selection.
        ui: App handle whose shared instance state receives the directory.
        directory: Current window catalogue for the selected application.
        selection: Confirmed working instance and its fixed root window.

    Returns:
        Updated shared instance state, including public window references.
    """
    # pylint: disable-next=protected-access
    instance = ui._instance
    if instance.anchor is not None and instance.anchor.root != selection.root:
        raise local_failure("the fixed application instance changed identity", "discover a new App", "stale_target")
    if instance.anchor is not None:
        require_identity(
            ui, WindowIdentity(selection.root.root_window, selection.root.surface, selection.root.instance)
        )
    # Even callers holding the old selection must establish its identity in
    # this directory. Only explicit fixed-root evidence can change lifetime.
    try:
        # pylint: disable-next=protected-access
        controller.reader._selection(
            selection.application, selection.name, directory, selection.root.root_window, previous=selection
        )
    except (FixedWindowInvalidated, FixedWindowUnconfirmed) as error:
        retain_fixed_failure(instance, selection, error)
        raise
    instance.anchor = selection
    instance.reconcile(directory, membership_evidence(directory, selection.root.root_window))
    aliases = controller.reader.window_aliases(directory)
    instance.windows = {int(ref): window for ref, window in aliases.items() if window in instance.members}
    state = controller.state(ui)
    state.selection, state.running, state.directory = (
        selection,
        RunningApplication(selection.application, selection.name, directory["session"]),
        directory,
    )
    state.refs = instance.refs
    for window, item in instance.members.items():
        identity = WindowIdentity.of(item)
        if window not in instance.owners or instance.owner_identities.get(window) != identity:
            # pylint: disable-next=protected-access
            selected = controller.reader._selection(selection.application, selection.name, directory, window)
            native_state = AppState(
                state.selector,
                selection=selected,
                running=state.running,
                directory=directory,
                refs=instance.refs,
                windows=dict(aliases),
            )
            instance.owners[window] = SurfaceOwner(controller, native_state)
            instance.owner_identities[window] = identity
            instance.observed.discard(window)
        else:
            native_state = controller.state(instance.owners[window])
            native_state.directory, native_state.windows = directory, dict(aliases)
    return instance


async def refresh(controller: SessionController, ui: UIHandle, *, accessibility: bool = True) -> DirectoryFacts:
    """Refresh a selected instance's directory and revalidate the UI view.

    Args:
        controller: Session providing native directory discovery.
        ui: App/Window handle identifying the selected instance.
        accessibility: Request accessibility roots along with the window directory.

    Returns:
        Current directory after member-window state has been updated.
    """
    validate_session(controller, ui)
    # pylint: disable-next=protected-access
    instance = ui._instance
    if instance.anchor is None:
        raise local_failure(
            "this UI has no observed application instance",
            "call App.read() and choose an instance if requested",
            "stale_target",
        )
    try:
        with controller.port.calls.claim(30000) as remaining:
            # pylint: disable-next=protected-access
            selected, directory = await controller.reader._discover(
                instance.anchor, remaining, accessibility=accessibility
            )
    except (FixedWindowInvalidated, FixedWindowUnconfirmed) as error:
        retain_fixed_failure(instance, instance.anchor, error)
        raise
    # pylint: disable-next=protected-access
    adopt(controller, ui if ui._anchor is None else cast("WindowHandle", ui)._app, directory, selected)
    validate_session(controller, ui)
    identity = getattr(ui, "_window_identity", None)
    if identity is not None:
        # pylint: disable-next=protected-access
        require_identity(ui, WindowIdentity(ui._anchor, identity.surface, identity.instance), current=True)
    return directory


async def resolve(
    controller: SessionController, ui: UIHandle, view: str
) -> tuple[DirectoryFacts, AppObservation | MenuAppObservation | Mapping[str, Any] | None]:
    """Resolve an App's instance or return its available window choices.

    Args:
        controller: Session providing discovery, startup, and handle state.
        ui: App/Window handle whose instance is to be resolved.
        view: Requested view; image-only reads use window-directory discovery.

    Returns:
        The window directory and any observation already produced during startup.
        The directory can contain several unselected root-window choices.
    """
    validate_session(controller, ui)
    # pylint: disable-next=protected-access
    instance, state = ui._instance, controller.state(ui)
    if instance.anchor is not None:
        return await refresh(controller, ui, accessibility=view != "image"), None
    # pylint: disable-next=protected-access
    if ui._anchor is not None:
        raise local_failure("Window has no live instance", "obtain a new App and Window", "stale_target")
    if state.selection is not None:
        controller.validate_element(state.selection.root)
        try:
            with controller.port.calls.claim(30000) as remaining:
                # pylint: disable-next=protected-access
                selected, directory = await controller.reader._discover(
                    state.selection, remaining, accessibility=view != "image"
                )
        except (FixedWindowInvalidated, FixedWindowUnconfirmed) as error:
            retain_fixed_failure(instance, state.selection, error)
            raise
        adopt(controller, ui, directory, selected)
        return directory, None
    if isinstance(state.running, StartingApplication):
        return await observe_startup(controller, ui, state.running, view)
    if state.running is not None:
        if state.running.session != controller.transport.session:
            raise local_failure("the application belongs to an expired session", "obtain a new App", "stale_target")
        application = dict(id=state.running.application, displayName=state.running.name, isRunning=True)
    else:
        applications = await controller.reader.directory.applications()
        application = select_application(applications, instance.selector)
    if not application["isRunning"]:
        return await observe_startup(controller, ui, instance.selector, view)
    # pylint: disable-next=protected-access
    directory = await controller.reader.directory._query(
        surfaces_query(
            application["id"],
            profile=SurfaceReadProfile.VISUAL if view == "image" else SurfaceReadProfile.ACCESSIBILITY,
            capabilities=controller.transport.capabilities,
        ),
        30000,
    )
    instance.directory = directory
    chosen = selected_window(application, instance.selector) if application.get("windows") else None
    if chosen is None and (candidate := automatic_root(directory)) is not None:
        chosen = candidate["window"]
    state.running = RunningApplication(application["id"], application["displayName"], directory["session"])
    if chosen is None:
        return directory, None
    # pylint: disable-next=protected-access
    selected = controller.reader._selection(application["id"], application["displayName"], directory, chosen)
    adopt(controller, ui, directory, selected)
    return directory, None


async def observe_startup(
    controller: SessionController, ui: UIHandle, selector: str | RunningApplication, view: str
) -> tuple[DirectoryFacts, AppObservation | MenuAppObservation | Mapping[str, Any] | None]:
    """Observe startup while retaining launch and instance identity in handle state.

    Args:
        controller: Session providing application startup and observation.
        ui: App handle receiving the running application and selected instance.
        selector: Application selector or retained startup/running scope.
        view: Tree, image, or combined view requested by the caller.

    Returns:
        The discovered directory and either its observation or launch receipt.
    """
    state = controller.state(ui)

    def remember(scope: RunningApplication | WorkingWindow) -> None:
        """Retain each confirmed startup scope as soon as the reader reports it."""
        state.running = (
            RunningApplication(scope.application, scope.name, scope.root.session)
            if isinstance(scope, WorkingWindow)
            else scope
        )
        if isinstance(scope, WorkingWindow):
            state.selection = scope

    try:
        observed = await controller.reader.read(selector, view=view, overview=False, on_scope=remember)
    except WindowSelectionRequired as choices:
        state.running = RunningApplication(choices.application, choices.display_name, choices.directory["session"])
        return choices.directory, choices.launch
    if observed.selection is not None:
        adopt(controller, ui, observed.directory, observed.selection)
    else:
        # pylint: disable-next=protected-access
        ui._instance.directory = observed.directory
    return observed.directory, observed


def allowed(ui: UIHandle) -> dict[str, SurfaceInfo]:
    """Return the confirmed member windows visible through this App/Window scope."""
    # pylint: disable-next=protected-access
    instance = ui._instance
    return {
        key: item
        for key, item in instance.members.items()
        # pylint: disable-next=protected-access
        if ui._anchor is None or ancestor(cast("DirectoryFacts", instance.directory), key, ui._anchor)
    }


if TYPE_CHECKING:
    from typing import Any, Mapping
if TYPE_CHECKING:
    from computer_use.interaction.records import WindowHandle
