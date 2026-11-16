# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Probe the current Linux session without starting the native driver."""

import ctypes
import os
import sys

DESKTOP_UNAVAILABLE = 3


def _display_reachable(*, wayland: bool) -> bool:
    roundtrip = None
    try:
        if wayland:
            library = ctypes.CDLL("libwayland-client.so.0")
            connect, disconnect = library.wl_display_connect, library.wl_display_disconnect
            roundtrip = library.wl_display_roundtrip
            roundtrip.argtypes = [ctypes.c_void_p]
            roundtrip.restype = ctypes.c_int
        else:
            library = ctypes.CDLL("libX11.so.6")
            connect, disconnect = library.XOpenDisplay, library.XCloseDisplay
    except (OSError, AttributeError):
        return False
    connect.argtypes = [ctypes.c_char_p]
    connect.restype = ctypes.c_void_p
    disconnect.argtypes = [ctypes.c_void_p]
    disconnect.restype = None if wayland else ctypes.c_int
    display = connect(None)
    if not display:
        return False
    try:
        return roundtrip is None or roundtrip(display) >= 0
    finally:
        disconnect(display)


def _accessibility_reachable() -> bool:
    address = os.environ.get("AT_SPI_BUS_ADDRESS")
    if not address and not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return False
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        return False

    no_start = Gio.DBusCallFlags.NO_AUTO_START
    try:
        if not address:
            session = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            address, = session.call_sync(
                "org.a11y.Bus", "/org/a11y/bus", "org.a11y.Bus", "GetAddress",
                None, GLib.VariantType.new("(s)"), no_start, 500, None,
            ).unpack()
        flags = Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        bus = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
        try:
            # A D-Bus session or an empty AT-SPI registry can also exist on a headless server.
            count, = bus.call_sync(
                "org.a11y.atspi.Registry", "/org/a11y/atspi/accessible/root", "org.freedesktop.DBus.Properties", "Get",
                GLib.Variant("(ss)", ("org.a11y.atspi.Accessible", "ChildCount")),
                GLib.VariantType.new("(v)"), no_start, 500, None,
            ).unpack()
            return count > 0
        finally:
            bus.close_sync(None)
    except (GLib.Error, ValueError):
        return False


def desktop_available() -> bool:
    if os.environ.get("DISPLAY") and _display_reachable(wayland=False):
        return True
    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))
    if wayland and _display_reachable(wayland=True):
        return True
    return _accessibility_reachable()


if __name__ == "__main__":
    sys.exit(0 if desktop_available() else DESKTOP_UNAVAILABLE)
