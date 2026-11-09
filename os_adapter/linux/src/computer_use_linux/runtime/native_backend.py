# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""v6 platform resources for native AT-SPI and desktop operations."""

from computer_use_linux.contracts.protocol import desktop_lease, require
from computer_use_linux.objects.native_accessibility import NativeAccessibility
from computer_use_linux.objects.registry import Registry
from computer_use_linux.windowing.desktop import connect_desktop, coordination_identity, ensure_unlocked


class NativeBackend:
    def __init__(self, allow_global_input, stopped):
        require(allow_global_input is True or allow_global_input is False, "Global input permission must be a boolean.")
        self.allow_global_input, self.stopped = allow_global_input, stopped
        self.registry = Registry()
        self.clipboard = None
        self.ax = NativeAccessibility()
        try:
            self.desktop = connect_desktop(self.ax, self.registry.retire, allow_global_input=allow_global_input)
        except Exception:
            self.ax.close()
            raise

    def installed(self):
        return [app for app in self.ax.gio.AppInfo.get_all() if app.get_id() and app.should_show()]

    def ensure_unlocked(self):
        ensure_unlocked(self.ax)

    def coordinate(self, ctx):
        return desktop_lease(ctx, coordination_identity(self.desktop))

    def get_clipboard(self):
        require(
            getattr(self.desktop, "session_type", None) == "x11",
            "This desktop has no implemented clipboard paste route.",
            "unsupported_operation",
        )
        if self.clipboard is None:
            from computer_use_linux.control.native_clipboard import NativeClipboard

            self.clipboard = NativeClipboard()
        return self.clipboard

    def pump(self):
        self.desktop.pump()
        self.ax.pump()
        if self.clipboard is not None:
            self.clipboard.pump()

    def close(self):
        try:
            if self.clipboard is not None:
                self.clipboard.close()
        finally:
            try:
                self.desktop.close()
            finally:
                self.ax.close()
