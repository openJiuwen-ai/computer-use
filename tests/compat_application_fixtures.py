# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Synthetic app bindings and window catalogues for read scope tests."""

from types import SimpleNamespace

from computer_use.client.computer import App
from computer_use.client.factory import session_controller
from computer_use.interaction.query.instances.scope import adopt, display_selection
from computer_use.native.scopes import RunningApplication
from computer_use.session.state import AppState


def surface(key, *, scope=None, parent=None, accessible=True, parent_state=None):
    return dict(
        window=key,
        instance=f"owner:{scope or key}",
        surface=f"surface:{scope or key}",
        node=f"node:{key}" if accessible else None,
        title="same title",
        kind="window",
        parent=parent,
        parentState=parent_state or ("known" if parent else "none"),
        modal=False,
        nativeWindow=True,
        accessibility=accessible,
    )


def directory(*items, focus="a", partial=False, application="editor", ended=()):
    return dict(
        kind="surfaces",
        session="session",
        platform="macos",
        application=application,
        observed=dict(id="directory", startedAt=1, completedAt=2),
        surfaces=list(items),
        focusedWindow=focus,
        stopReasons=["incomplete"] if partial else [],
        rootDiscoveryComplete=not partial,
        endedWindows=[{key: item[key] for key in ("window", "instance", "surface")} for item in ended],
        provider=dict(status="alreadyEnabled", attribute=None, writeStatus=None),
    )


def setup(*items):
    port = SimpleNamespace(
        connection=SimpleNamespace(
            session="session", platform="macos", capabilities=(), session_identity=(1, "session")
        ),
        calls=SimpleNamespace(retired=False),
    )
    controller = session_controller(port)
    app = App(
        controller,
        dict(
            selector="editor",
            _nativeState=AppState("editor", running=RunningApplication("editor", "Editor", "session")),
        ),
        _internal=True,
    )
    facts = directory(*(items or (surface("a"), surface("b"))))
    adopt(controller, app, facts)
    app._instance.anchor = display_selection(controller, app._instance, facts)
    return controller, app


def ref(app, key):
    return next(number for number, window in app._instance.windows.items() if window == key)
