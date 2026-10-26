"""Verify the selected session and accessibility bus before libatspi init."""

import os

from computer_use_linux.contracts.protocol import Failure


def connect_accessibility_bus(Gio, GLib):
    """Fail before libatspi's fatal initialization on a missing/stale session bus.

    Resolve through the selected session, not an X root property left by another
    desktop. Respect an explicitly supplied accessibility bus, but verify it too.
    """
    try:
        address = os.environ.get("AT_SPI_BUS_ADDRESS")
        if not address:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            address = bus.call_sync(
                "org.a11y.Bus",
                "/org/a11y/bus",
                "org.a11y.Bus",
                "GetAddress",
                None,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            ).unpack()[0]
        if not address:
            raise ValueError("The session did not provide an accessibility bus.")
        connection = Gio.DBusConnection.new_for_address_sync(
            address,
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,
            None,
            None,
        )
        try:
            connection.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetId",
                None,
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
        finally:
            connection.close_sync(None)
        os.environ["AT_SPI_BUS_ADDRESS"] = address
    except (GLib.Error, ValueError) as error:
        raise Failure(
            f"Cannot connect to the selected desktop's accessibility bus: {error}",
            "observation_unavailable",
            next="Start from a terminal in the target desktop session, or regenerate its SSH launcher.",
        ) from error
