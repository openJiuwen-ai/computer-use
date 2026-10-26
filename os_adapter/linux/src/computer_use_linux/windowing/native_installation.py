"""Retained desktop recipes; GLib resolves static identity, Gio launches Exec.

The record binds a catalogue entry to one concrete file, not to a later desktop
ID lookup. This is pre-dispatch identity checking, not an atomic exec facility.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from dataclasses import dataclass, field

from computer_use_linux.contracts.protocol import Failure

MAX_DESKTOP_BYTES = 1024 * 1024


def window_class_keys(values):
    """Exact X11/desktop-entry declarations, not process-origin evidence."""
    return tuple(
        dict.fromkeys(
            "x11.wm-class:" + value
            for value in values
            if isinstance(value, str) and value.strip() and "\x00" not in value and len(value) <= 4000
        )
    )


def _check(ctx):
    if ctx is not None:
        ctx.check()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class DesktopFile:
    path: str
    identity: tuple
    digest: str


def _desktop_file(filename, ctx):
    """Bounded read with descriptor and pathname checks on both sides."""
    _check(ctx)
    try:
        path = os.path.realpath(filename, strict=True)
        expected = os.stat(path)
        if not stat.S_ISREG(expected.st_mode):
            raise Failure("Installed desktop entry is not a regular file.", "observation_incomplete", filename)
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            if _identity(before) != _identity(expected):
                raise Failure("Installed desktop entry changed before reading.", "stale_target", filename)
            if not stat.S_ISREG(before.st_mode):
                raise Failure("Installed desktop entry is not a regular file.", "observation_incomplete", filename)
            if before.st_size > MAX_DESKTOP_BYTES:
                raise Failure(
                    "Installed desktop entry exceeds the 1 MiB read budget.", "observation_incomplete", filename
                )
            digest, size = hashlib.sha256(), 0
            while True:
                _check(ctx)
                chunk = os.read(fd, min(65536, MAX_DESKTOP_BYTES + 1 - size))
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DESKTOP_BYTES:
                    raise Failure(
                        "Installed desktop entry exceeds the 1 MiB read budget.", "observation_incomplete", filename
                    )
                digest.update(chunk)
            if (
                _identity(before) != _identity(os.fstat(fd))
                or _identity(before) != _identity(os.stat(filename))
                or path != os.path.realpath(filename, strict=True)
            ):
                raise Failure("Installed desktop entry changed while being read.", "stale_target", filename)
            _check(ctx)
            return DesktopFile(path, _identity(before), digest.hexdigest())
        finally:
            os.close(fd)
    except (OSError, ValueError) as error:
        raise Failure(
            "Cannot read the installed desktop entry: " + str(error), "observation_incomplete", filename
        ) from error


def _executable(value):
    """Resolve the static executable without choosing or executing its arguments."""
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if os.path.isabs(value):
        candidate = value
    elif "/" not in value:
        # Gio may chdir to desktop Path= before resolving Exec. A relative
        # PATH component can select another file than shutil.which sees here.
        if any(not os.path.isabs(part) for part in os.environ.get("PATH", os.defpath).split(os.pathsep)):
            return None
        candidate = shutil.which(value)
        # A relative PATH component depends on the launcher's working directory.
        if candidate is None or not os.path.isabs(candidate):
            return None
    else:
        return None
    try:
        path = os.path.realpath(candidate, strict=True)
        info = os.stat(candidate)
        if not stat.S_ISREG(info.st_mode) or not os.access(candidate, os.X_OK):
            return None
        return path, _identity(info)
    except (OSError, ValueError):
        return None


@dataclass(frozen=True)
class NativeInstallation:
    identifier: str
    filename: str | None
    desktop: DesktopFile | None
    label: str
    executable: str | None
    executable_path: str | None
    executable_identity: tuple | None
    dbus_activatable: bool | None
    terminal: bool | None
    available: bool
    reason: str | None
    native: object = field(compare=False, repr=False)
    startup_wm_class: str | None = None

    @property
    def path(self):
        return self.desktop.path if self.desktop is not None else None

    @property
    def key(self):
        # Two recipes for the same executable remain different installations.
        # A new executable resolution must never replace a retained record under
        # an old directory token, even when the desktop recipe is unchanged.
        return (
            self.identifier,
            self.filename,
            self.desktop,
            self.executable,
            self.executable_path,
            self.executable_identity,
            self.dbus_activatable,
            self.terminal,
            self.startup_wm_class,
        )

    @property
    def identity(self):
        return self.key

    @classmethod
    def capture(cls, app, Gio, ctx=None, *, GLib):
        _check(ctx)
        identifier = app.get_id()
        if not isinstance(identifier, str) or not identifier.strip():
            raise Failure("Installed application has no usable desktop entry ID.", "observation_incomplete")
        label = app.get_display_name() or identifier
        filename = app.get_filename() if hasattr(app, "get_filename") else None
        if not isinstance(filename, str) or not os.path.isabs(filename) or "\x00" in filename:
            executable = app.get_executable()
            return cls(
                identifier,
                None,
                None,
                label,
                executable,
                None,
                None,
                None,
                None,
                False,
                "No concrete absolute desktop file is available.",
                app,
            )
        before = _desktop_file(filename, ctx)
        native = Gio.DesktopAppInfo.new_from_filename(before.path)
        if native is None:
            if before != _desktop_file(filename, ctx):
                raise Failure("Installed desktop entry changed during Gio parsing.", "stale_target", filename)
            return cls(
                identifier,
                filename,
                before,
                label,
                None,
                None,
                None,
                None,
                None,
                False,
                "Gio cannot load this desktop entry.",
                app,
            )
        label = native.get_display_name() or identifier
        # get_executable() is only a debug label: Gio's binary_from_exec
        # stops at the first space and preserves quotes. Use GLib's tokenizer
        # for a static first argument; Gio alone expands fields and launches.
        executable, parse_reason = None, None
        commandline = native.get_commandline()
        try:
            parsed, argv = GLib.shell_parse_argv(commandline or "")
            if parsed and argv and isinstance(argv[0], str):
                executable = argv[0]
            if executable is None or "%" in executable:
                parse_reason = "The executable argument is not a confirmed static desktop entry."
        except GLib.Error:
            parse_reason = "GLib could not parse the desktop command identity."
        dbus, terminal = native.get_boolean("DBusActivatable"), native.get_boolean("Terminal")
        startup_class = native.get_startup_wm_class() if hasattr(native, "get_startup_wm_class") else None
        if not window_class_keys((startup_class,)):
            startup_class = None
        if type(dbus) is not bool or type(terminal) is not bool:
            raise Failure("Desktop launch declarations are unavailable.", "observation_incomplete", filename)
        resolved = _executable(executable) if parse_reason is None else None
        if before != _desktop_file(filename, ctx):
            raise Failure("Installed desktop entry changed during Gio parsing.", "stale_target", filename)
        reason = (
            "D-Bus activation has no exact instance provider yet."
            if dbus
            else "Terminal launch has no exact instance provider yet."
            if terminal
            else parse_reason
            if parse_reason
            else "The declared executable cannot be resolved to a regular executable file."
            if resolved is None
            else "Gio does not expose the managed launch API."
            if not hasattr(native, "launch_uris_as_manager_with_fds")
            else None
        )
        return cls(
            identifier,
            filename,
            before,
            label,
            executable,
            resolved[0] if resolved else None,
            resolved[1] if resolved else None,
            dbus,
            terminal,
            reason is None,
            reason,
            native,
            startup_class,
        )

    def validate(self, ctx=None):
        """Return the retained Gio recipe after rechecking its native inputs."""
        _check(ctx)
        if not self.available:
            raise Failure(self.reason or "This installation cannot be launched.", "mode_unsupported", self.identifier)
        try:
            current = _desktop_file(self.filename, ctx)
        except Failure as error:
            if error.code in ("cancelled", "deadline_exceeded"):
                raise
            raise Failure(
                "The observed desktop entry is no longer available: " + str(error), "stale_target", self.identifier
            ) from error
        if current != self.desktop:
            raise Failure("The observed desktop entry has changed.", "stale_target", self.identifier)
        if _executable(self.executable) != (self.executable_path, self.executable_identity):
            raise Failure(
                "The observed executable entry has changed or is unavailable.", "stale_target", self.identifier
            )
        _check(ctx)
        return self.native
