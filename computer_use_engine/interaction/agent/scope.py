# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Resolve an optional window parameter once, without creating public handles.

A scope exists for one request. Identity, delivered context and images belong
to the application session; garbage collection of this object changes none of
them. Elements returned by scoped lookup continue to belong to the public App.
"""

from computer_use.diagnostics import local_failure
from computer_use.interaction.query.instances.scope import adopt, require_identity, validate_session
from computer_use.native.query.directory import select_application, select_surface
from computer_use.native.scopes import RunningApplication
from computer_use.session.interfaces import LookupContext, WindowIdentity
from computer_use.session.state import AppState


class RequestScope:
    """Internal, immutable window selection plus request-local collection state."""

    def __init__(self, controller, app, entry):
        self._app = app
        self._instance = app._instance
        self._anchor = entry.identity.window
        self._context, self._image = None, None
        self._lookup_scopes, self._menu_only = (), False
        selection = controller.reader._selection(
            entry.application, entry.name, entry.directory, self._anchor,
        )
        self._window_identity = selection.root
        state = AppState(
            entry.application,
            running=RunningApplication(entry.application, entry.name, entry.session),
            selection=selection,
            refs=self._instance.refs,
            directory=self._instance.directory,
            windows={str(k): v for k, v in self._instance.windows.items()},
        )
        controller.register(self, {"selector": entry.application, "_nativeState": state})

    @property
    def identity(self):
        binding = self._window_identity
        return WindowIdentity(binding.root_window, binding.surface, binding.instance)

    @property
    def _input_context(self):
        context = self._instance.window_contexts.get(self.identity)
        return context.input if context is not None else None

    @property
    def _lookup_context(self):
        context = self._instance.window_contexts.get(self.identity)
        return context.lookup if context is not None else LookupContext(self.identity)


def window_scope(controller, app, ref):
    """Bind a known application/window pair, without querying or changing focus."""
    validate_session(controller, app)
    running = controller.state(app).running
    application = running.application if running is not None else app._instance.selector
    entry = controller.interfaces.windows.resolve(ref, controller.transport.session, application=application)
    instance = controller.interfaces.windows.application(entry.session, entry.application, entry.name)
    app._instance = instance
    if instance.directory is None:
        adopt(controller, app, entry.directory)
    require_identity(app, entry.identity)
    return RequestScope(controller, app, entry)


async def request_scope(controller, app, ref):
    if ref is None:
        return app
    validate_session(controller, app)
    state = controller.state(app)
    if state.running is None and app._instance.session is None:
        # Resolve selector aliases by native application identity. Selecting an
        # existing window never launches a second application or reads a tree.
        selector = app._instance.selector
        applications = (
            await controller.reader.directory.applications(selector=selector)
            if selector.startswith("/")
            else await controller.reader.directory.applications()
        )
        application = select_application(applications, selector)
        if not application["isRunning"]:
            raise local_failure("the selected window's application is no longer running", "stale_target")
        state.running = RunningApplication(application["id"], application["displayName"], applications["session"])
    return window_scope(controller, app, ref)


def lookup_window(ui):
    """Use the fixed window's confirmed modal receiver, never a blocked parent.

    Reuse the native directory's existing modal selection contract. Unknown
    ancestry cannot admit an unrelated window, and independent nonmodal views
    continue to participate in the ordinary scoped lookup.
    """
    from computer_use.interaction.query.instances.scope import allowed

    permitted = allowed(ui)
    if ui._anchor not in permitted:
        return ui._anchor
    target = select_surface({**ui._instance.directory, "surfaces": list(permitted.values())}, ui._anchor)
    return next(key for key, item in permitted.items() if item["surface"] == target.surface)
