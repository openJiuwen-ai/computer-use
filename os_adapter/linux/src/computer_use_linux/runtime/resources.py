# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Installation and read-only resource checks for the protocol 6 driver.

The v6 check uses the same lazy native resources as its driver. This is not an input
acceptance test: advertised XTEST availability does not prove target delivery.
"""

import ctypes
import ctypes.util
import os
import threading
import time

from computer_use_linux.contracts.protocol import Failure, Request


def protocol_version(value):
    # Exact types preserve scalar validation and identity contracts.
    # pylint: disable-next=unidiomatic-typecheck
    if type(value) is not int or value != 6:
        raise ValueError("This Linux driver requires protocol 6; upgrade the client.")
    return value


def check_installation(version=6, *, installed=False):
    """Check imports/libraries without connecting to a desktop or AT-SPI bus."""
    protocol_version(version)
    from computer_use_linux.objects.native_accessibility import NativeAccessibility

    # Construction loads exactly the GI namespaces used by v6; the bus and
    # event listener stay lazy. Do not read .atspi or call applications here.
    ax = NativeAccessibility()
    ax.close()
    for name, fallback in (("X11", "libX11.so.6"), ("xkbcommon", "libxkbcommon.so.0")):
        ctypes.CDLL(ctypes.util.find_library(name) or fallback)
    if installed:
        from PIL import Image  # noqa: F401
        from Xlib import display  # noqa: F401 - installed Python dependency


def check_permissions(version=6):
    """Report independent observation and input resources.

    ready means this unlocked session has a usable observation channel, not
    that all methods or modes are available.
    """
    protocol_version(version)
    status = dict(
        platform="linux",
        protocolVersion=version,
        sessionType=os.environ.get("XDG_SESSION_TYPE", "unknown"),
        accessibility=False,
        screenCapture=False,
        input=False,
        ready=False,
        details=[],
    )
    backend = None
    try:
        from computer_use_linux.runtime.native_backend import NativeBackend as Backend

        backend = Backend("exclusive", threading.Event())
        status["sessionType"] = backend.desktop.session_type
        status["details"].extend(backend.desktop.notes)
        backend.ensure_unlocked()
        # AX may be unavailable while X11 remains usable. Run independent
        # checks with separate bounded budgets; a slow AX provider must not
        # consume the screenshot probe's time. Cancellation remains shared.
        try:
            ctx = Request(time.monotonic() + 3, backend.stopped)
            backend.ax.begin(ctx)
            root = backend.ax.call(backend.ax.atspi.get_desktop, 0)
            ctx.check()
            if root is None:
                raise Failure("AT-SPI desktop root is unavailable.", "observation_unavailable")
            status["accessibility"] = True
        except Exception as error:
            status["details"].append("Accessibility unavailable: " + str(error))
        if backend.desktop.can_capture:
            try:
                ctx = Request(time.monotonic() + 3, backend.stopped)
                backend.desktop.capture(None, backend.registry.desktop_scope, ctx)
                ctx.check()
                status["screenCapture"] = True
            except Exception as error:
                status["details"].append("Screenshot unavailable: " + str(error))
        # Do not recheck an expired resource deadline after the independent
        # probes. A cancelled session, unlike a single failed resource, stops
        # the whole check.
        Request(time.monotonic() + 1, backend.stopped).check()
        backend.ensure_unlocked()
        status["input"] = bool(backend.desktop.can_input)
        status["ready"] = status["accessibility"] or status["screenCapture"]
        status["details"].append(
            "ready describes available observation resources only; input reports "
            "the native event route, not delivery to a specific control."
        )
        if not status["accessibility"]:
            status["details"].append(
                "No accessibility tree is available; use observed window/desktop images "
                "when capture and native input are available."
            )
        if not status["input"]:
            status["details"].append(
                "Synthetic keyboard/mouse input is unavailable; supported native "
                "element actions may remain available when accessibility is reachable."
            )
    except Exception as error:
        status["ready"], status["input"] = False, False
        status["details"].append(str(error))
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception as error:
                status["ready"], status["input"] = False, False
                status["details"].append("Resource cleanup failed: " + str(error))
    return status
