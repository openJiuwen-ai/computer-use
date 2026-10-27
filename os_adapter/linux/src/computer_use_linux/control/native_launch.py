"""One retained Gio installation launch and its direct process receipt.

GSpawn's PID callback identifies the process Gio created. It does not identify
an arbitrary descendant, an existing process receiving a forwarded request, or
a D-Bus/terminal application's final instance. Those need separate providers.
"""

from __future__ import annotations

import os
import time

from computer_use_linux.contracts.protocol import Failure, fields, require, token
from computer_use_linux.windowing.process_identity import process_birth


def process_launched(pid, birth, filename):
    """Gio's helper marker becomes the initial environment only after exec.

    Keep only the two provenance keys, never log the process environment. This
    confirms this launch's exec handoff, not permanent executable identity or
    the eventual destination of a single-instance forwarding request.
    """
    if process_birth(pid) != birth:
        return False
    try:
        with open(f"/proc/{pid}/environ", "rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            return False
        fields_value = {}
        for entry in data.split(b"\x00"):
            name, separator, value = entry.partition(b"=")
            if separator and name in (b"GIO_LAUNCHED_DESKTOP_FILE", b"GIO_LAUNCHED_DESKTOP_FILE_PID"):
                if name in fields_value:
                    return False
                fields_value[name] = value
        return (
            fields_value.get(b"GIO_LAUNCHED_DESKTOP_FILE_PID") == str(pid).encode("ascii")
            and fields_value.get(b"GIO_LAUNCHED_DESKTOP_FILE") == os.fsencode(filename)
            and process_birth(pid) == birth
        )
    except OSError:
        return False


class NativeLaunch:
    def __init__(self, backend, directory):
        self.b, self.directory = backend, directory
        self.children = {}

    def child(self, pid):
        # DO_NOT_REAP_CHILD keeps an exited child's PID reserved until the
        # native watch collects it; the first birth sample cannot see reuse.
        birth = process_birth(pid)
        g_lib = self.b.ax.glib

        def exited(child_pid, status, *_):
            self.children.pop(child_pid, None)
            g_lib.spawn_close_pid(child_pid)

        self.children[pid] = g_lib.child_watch_add(pid, exited)
        return birth

    def close(self):
        g_lib_local = self.b.ax.glib
        for pid, watch in list(self.children.items()):
            g_lib_local.source_remove(watch)
            try:
                os.waitpid(pid, os.WNOHANG)
            except OSError:
                pass
            g_lib_local.spawn_close_pid(pid)
        self.children.clear()
        # Live applications outlive the driver. Its process exit reparents
        # them normally; closing this connection never terminates an app.

    def installation(self, target):
        record = self.directory.installations.get(target["application"])
        require(
            record is not None,
            "The observed installation is unavailable; read the application directory again.",
            "stale_target",
        )
        return record

    def inspect(self, operation, target, ctx):
        require(
            operation.writable
            and not operation.closed
            and not operation.dispatched
            and operation.target == target
            and operation.intent == dict(kind="launch", parameters={}),
            "Launch observation requires the original installation operation.",
            "stale_target",
        )
        ctx.check()
        record = self.installation(target)
        record.validate(ctx)
        identifier_local = operation.contract("launch_application", target, (), installation=record)
        available = "yes" if record.available else "no"
        return dict(
            kind="launch",
            binding=dict(target),
            observed=token("observation"),
            targetLabel=record.label or record.identifier,
            primitives=[
                dict(
                    id=identifier_local,
                    unit="launch_application",
                    channel="object",
                    supported=available,
                    readiness=available,
                    pending=[],
                    backgroundPreparation="no",
                )
            ],
        )

    def execute(self, operation, command, ctx):
        fields(command, ("kind", "primitiveId", "target", "foreground"))
        require(
            command["kind"] == "launch_application" and command["foreground"] is False,
            "Launching an installation cannot request foreground input.",
        )
        contract = operation.contracts.get(command["primitiveId"])
        require(
            contract is not None
            and contract["unit"] == "launch_application"
            and command["target"] == operation.target == contract["target"]
            and operation.intent == dict(kind="launch", parameters={}),
            "The launch contract expired or changed its installation.",
            "stale_target",
        )
        target, record = contract["target"], contract["installation"]
        dispatch, confirmation, error_code, application = "notDispatched", "unavailable", None, None
        detail = "Application launch was not dispatched."
        try:
            ctx.check()
            self.b.ensure_unlocked()
            require(self.installation(target) is record, "The observed installation record expired.", "stale_target")
            native = record.validate(ctx)
            running = self.directory.running_installation(record, ctx)
            require(
                len(running) <= 1,
                "Several confirmed running instances match this installation; select one.",
                "ambiguous_target",
            )
            if running:
                application, confirmation = running[0], "confirmed"
                detail = "The confirmed instance is already running; no launch or activation was sent."
            else:
                gio, glib = self.b.ax.gio, self.b.ax.glib
                context = gio.AppLaunchContext.new()
                self.b.desktop.configure_launch(context)
                # Do not inherit the driver's lifetime/protocol resources or a
                # stale request to activate a user's current desktop window.
                for key in (
                    "COMPUTER_USE_LIFETIME_FD",
                    "COMPUTER_USE_PREVIEW_FD",
                    "DESKTOP_STARTUP_ID",
                    "XDG_ACTIVATION_TOKEN",
                    "GIO_LAUNCHED_DESKTOP_FILE_PID",
                ):
                    context.unsetenv(key)
                receipts = []

                def received(app, pid, *_):
                    receipts.append((pid, self.child(pid)))

                with open(os.devnull, "r+b") as stream:
                    self.b.ensure_unlocked()
                    record.validate(ctx)
                    ctx.sent()
                    operation.dispatched, dispatch = True, "unknown"
                    accepted = native.launch_uris_as_manager_with_fds(
                        [],
                        context,
                        glib.SpawnFlags.SEARCH_PATH | glib.SpawnFlags.DO_NOT_REAP_CHILD,
                        None,
                        None,
                        received,
                        None,
                        stream.fileno(),
                        stream.fileno(),
                        stream.fileno(),
                    )
                require(
                    accepted is True,
                    "Gio did not confirm launch acceptance; inspect before continuing.",
                    "execution_unknown",
                )
                require(
                    len(receipts) == 1 and receipts[0][1],
                    "Gio accepted launch without one live direct-process receipt; no window was guessed.",
                    "execution_unknown",
                )
                pid, birth = receipts[0]
                deadline = min(ctx.deadline, time.monotonic() + 1.0)
                while not process_launched(pid, birth, record.path):
                    ctx.check()
                    require(
                        process_birth(pid) == birth and time.monotonic() < deadline,
                        (
                            "The launched process did not confirm exec into this desktop entry; no other "
                            "process was "
                            "selected."
                        ),
                        "execution_unknown",
                    )
                    ctx.pause(0.01)
                application = self.directory.register_launch(record, pid, birth, ctx)
                dispatch, confirmation = "dispatched", "confirmed"
                detail = "The direct application process was launched; window discovery is a separate observation."
            ctx.check()
        except (Failure, self.b.ax.glib.Error, OSError) as error:
            detail = str(error)
            error_code = (
                error.code
                if isinstance(error, Failure)
                else "input_unavailable"
                if dispatch == "notDispatched"
                else "execution_unknown"
            )
        result = dict(
            target=dict(target),
            dispatch=dispatch,
            detail=detail,
            preparation=[],
            confirmation=confirmation,
            cleanup="complete",
            errorCode=error_code,
            retryReason=None,
            context=None,
        )
        if application is not None:
            result["application"] = application
        return result
