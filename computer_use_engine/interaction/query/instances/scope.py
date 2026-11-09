# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Application discovery and independently retained fixed Window lifetimes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from computer_use.diagnostics import local_failure
from computer_use.interaction.query.instances.membership import ancestor, interaction_contains
from computer_use.interaction.read.model import WindowSelectionRequired
from computer_use.native.contracts import Knowledge
from computer_use.native.query.directory import SurfaceReadProfile, select_application, surfaces_query
from computer_use.native.scopes import (
    FixedWindowInvalidated,
    FixedWindowUnconfirmed,
    RunningApplication,
    StartingApplication,
)
from computer_use.session.interfaces import (
    SurfaceOwner,
    WindowIdentity,
    WindowLifetime,
)
from computer_use.session.state import AppState
from computer_use.session.surface_content import content_host, content_kind, display_host, transient_content

if TYPE_CHECKING:
    from computer_use.interaction.controller import SessionController
    from computer_use.interaction.records import UIHandle
    from computer_use.native.scopes import WorkingWindow
    from computer_use.session.interfaces import InstanceState


def validate_session(controller: SessionController, ui: UIHandle) -> None:
    """Validate application session and this Window, never another display primary."""
    if controller.port.calls.retired:
        raise local_failure(
            "native session is retired after unconfirmed cleanup",
            "stale_target",
        )
    instance = ui._instance
    session = instance.session
    running = controller.state(ui).running
    if session is None and running is not None:
        session = running.session
    if session is not None and session != controller.transport.session:
        raise local_failure("the application belongs to an expired session", "stale_target")
    identity = getattr(ui, "_window_identity", None)
    if identity is not None:
        controller.validate_element(identity)
        require_identity(ui, WindowIdentity(ui._anchor, identity.surface, identity.instance))


def require_identity(ui: UIHandle, identity: WindowIdentity, *, current: bool = False) -> None:
    """Retained unknown identities can refresh; retired windows never come back."""
    evidence = ui._instance.evidence.get(identity)
    if evidence is None or evidence.lifetime is WindowLifetime.RETIRED:
        raise local_failure(
            "the fixed Window identity no longer exists",
            "stale_target",
        )
    item = ui._instance.members.get(identity.window)
    if item is not None and WindowIdentity.of(item) != identity:
        raise local_failure("the fixed Window identity changed", "stale_target")
    if current and evidence.membership is not Knowledge.YES:
        raise local_failure(
            "the fixed Window is not confirmed in the current application directory",
            "observation_incomplete",
        )


def retain_fixed_failure(instance: InstanceState, selection: WorkingWindow, error) -> None:
    """Retire or suspend just the exact fixed window named by native evidence."""
    if error.binding != selection.root:
        return
    identity = WindowIdentity(error.binding.root_window, error.binding.surface, error.binding.instance)
    if isinstance(error, FixedWindowInvalidated):
        instance.retire(identity)
    else:
        instance.unconfirm(identity)
    if instance.anchor is not None and instance.anchor.root == selection.root:
        instance.anchor = None


def display_selection(controller, instance, directory):
    """Choose an observation primary without granting a keyboard input target."""
    sources = {item["window"]: item for item in directory["surfaces"]}
    entries = {key: item for key, item in sources.items()
               if not transient_content(item) and content_host(item) not in sources}
    if not entries:
        # A menu-only/status-item application still has observable content.
        # Its heading remains a menu; choosing it does not promote a document.
        entries = {key: item for key, item in sources.items() if content_host(item) not in sources}
    if working := {key: item for key, item in entries.items() if content_kind(item) != "application"}:
        entries = working
    classified = {key: item for key, item in entries.items() if content_kind(item) == "window"}
    if classified:
        entries = classified
    focused = display_host(sources, directory.get("focusedWindow"))
    if focused in entries:
        key = focused
    elif len(entries) == 1:
        key = next(iter(entries))
    elif len(roots := [key for key, item in entries.items() if item.get("parentState") == "none"]) == 1 and all(
        ancestor(directory, key, roots[0]) for key in entries
    ):
        # An attached panel does not turn its sole confirmed document family
        # into several independent App observation choices.
        key = roots[0]
    elif len(readable := {key: item for key, item in entries.items() if item.get("node") is not None}) == 1 and (
        instance.anchor is None
        or entries.get(instance.anchor.root.root_window, {}).get("node") is None
    ):
        # An App observation may discover a new usable form while the hidden
        # launcher remains a native-only row. Historical display preference is
        # not a fixed Window binding and must not force that obsolete root.
        key = next(iter(readable))
    elif instance.anchor is not None and instance.anchor.root.root_window in entries:
        previous = entries[instance.anchor.root.root_window]
        key = (
            previous["window"]
            if WindowIdentity.of(previous)
            == WindowIdentity(
                instance.anchor.root.root_window, instance.anchor.root.surface, instance.anchor.root.instance
            )
            else None
        )
    else:
        key = None
    if key is None:
        return None
    return controller.reader._selection(directory["application"], instance.name, directory, key)


def adopt(controller, ui, directory, selection=None):
    """Reconcile one application catalogue; a display selection is not its identity."""
    state = controller.state(ui)
    name = selection.name if selection is not None else state.name or ui._instance.name or str(state.selector)
    instance = controller.interfaces.windows.application(directory["session"], directory["application"], name)
    if ui._instance is not instance:
        ui._instance = instance
    aliases = controller.interfaces.windows.register_directory(directory, name)
    instance.windows = {int(ref): key for ref, key in aliases.items() if key in instance.members}
    state.running = RunningApplication(directory["application"], name, directory["session"])
    state.directory, state.refs, state.windows = directory, instance.refs, aliases
    if ui._anchor is None:
        state.selection = None
        controller.interfaces.windows.remember_application_selector(state.selector, instance)
        if selection is not None:
            instance.anchor = selection
    else:
        fixed = state.selection
        try:
            state.selection = controller.reader._selection(
                directory["application"], name, directory, ui._anchor, previous=fixed
            )
        except (FixedWindowInvalidated, FixedWindowUnconfirmed) as error:
            if fixed is not None:
                retain_fixed_failure(instance, fixed, error)
            raise
        require_identity(
            ui, WindowIdentity(ui._anchor, state.selection.root.surface, state.selection.root.instance), current=True
        )
    for key, item in instance.members.items():
        identity = WindowIdentity.of(item)
        if instance.owner_identities.get(key) != identity:
            selected = controller.reader._selection(directory["application"], name, directory, key)
            native_state = AppState(
                instance.selector,
                selection=selected,
                running=state.running,
                directory=directory,
                refs=instance.refs,
                windows=dict(aliases),
            )
            instance.owners[key] = SurfaceOwner(controller, native_state)
            instance.owner_identities[key] = identity
            instance.observed.discard(key)
        else:
            native_state = controller.state(instance.owners[key])
            native_state.directory, native_state.windows = directory, dict(aliases)
    return instance


async def refresh(controller, ui, *, accessibility=True):
    """Refresh application facts without changing the delivered input context."""
    validate_session(controller, ui)
    state = controller.state(ui)
    instance = ui._instance
    running = state.running
    if running is None and instance.session is not None:
        running = RunningApplication(instance.selector, instance.name, instance.session)
    if running is None:
        raise local_failure("this UI has no observed application", "stale_target")
    directory = await controller.reader.directory._query(
        surfaces_query(
            running.application,
            profile=SurfaceReadProfile.ACCESSIBILITY if accessibility else SurfaceReadProfile.VISUAL,
            capabilities=controller.transport.capabilities,
        ),
        30000,
    )
    adopt(controller, ui, directory)
    validate_session(controller, ui)
    return directory


async def resolve(controller, ui, view):
    """Discover windows, select an observation primary, and retain exact targets."""
    validate_session(controller, ui)
    state = controller.state(ui)
    if isinstance(state.running, StartingApplication):
        return await observe_startup(controller, ui, state.running, view)
    if state.running is not None or ui._instance.session is not None:
        directory = await refresh(controller, ui, accessibility=view != "image")
    else:
        selector = ui._instance.selector
        if controller.reader.pending_launch(selector) is not None:
            return await observe_startup(controller, ui, selector, view)
        applications = (
            await controller.reader.directory.applications(selector=selector)
            if selector.startswith("/")
            else await controller.reader.directory.applications()
        )
        application = select_application(applications, selector)
        if not application["isRunning"]:
            return await observe_startup(controller, ui, selector, view, source_applications=applications)
        state.running = RunningApplication(application["id"], application["displayName"], applications["session"])
        directory = await refresh(controller, ui, accessibility=view != "image")
    if ui._anchor is None:
        ui._instance.anchor = display_selection(controller, ui._instance, directory)
    return directory, None


async def observe_startup(controller, ui, selector, view, *, source_applications=None):
    """Preserve the existing single-launch contract, then publish application scope."""
    state = controller.state(ui)

    def remember(scope):
        state.running = (
            scope
            if isinstance(scope, RunningApplication)
            else RunningApplication(scope.application, scope.name, scope.root.session)
        )

    try:
        observed = await controller.reader.read(
            selector, view=view, overview=False, on_scope=remember, source_applications=source_applications
        )
    except WindowSelectionRequired as choices:
        state.running = RunningApplication(choices.application, choices.display_name, choices.directory["session"])
        adopt(controller, ui, choices.directory)
        ui._instance.anchor = display_selection(controller, ui._instance, choices.directory)
        return choices.directory, choices.launch
    adopt(controller, ui, observed.directory, observed.selection)
    ui._instance.anchor = display_selection(controller, ui._instance, observed.directory)
    return observed.directory, observed


def allowed(ui):
    """Application catalogue, or one fixed Window and confirmed descendants."""
    instance = ui._instance
    return {
        key: item
        for key, item in instance.members.items()
        if ui._anchor is None or interaction_contains(instance.directory, key, ui._anchor)
    }
