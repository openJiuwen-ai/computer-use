"""Linux launch contracts exercised through the production native pipeline.

Only the Gio and process providers are substituted; these tests launch no apps.
The production directory, execution operations, schema and Server remain real.
"""

import io
import json
import os
import shlex
import sys
import time
import unittest
from configparser import ConfigParser
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import mock_open, patch

from linux_native_provider import Backend, NativeError

from backends.linux.test_linux_native import context, scope
from computer_use.interaction.read.acquisition.directory.service import select_application
from computer_use.protocol.directory import decodeDirectory
from computer_use.protocol.execution import decodeExecutionResult
from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.control.native_launch import process_launched as native_process_launched
from computer_use_linux.objects.registry import Window
from computer_use_linux.runtime.native import NativeExecution
from computer_use_linux.runtime.server import Server
from computer_use_linux.windowing.native_installation import NativeInstallation


class DesktopRecipe:
    def __init__(self, owner, filename, values=None):
        self.owner, self.filename = owner, str(filename)
        self.values = values or {}

    def get_id(self):
        return self.values.get("ID", "test-launch.desktop")

    def get_filename(self):
        return self.filename

    def get_display_name(self):
        return self.values.get("Name", "Native launch fixture")

    def get_executable(self):
        return self.values.get("Executable", str(self.owner.program))

    def get_commandline(self):
        return self.values.get("Executable", shlex.quote(str(self.owner.program)))

    def get_boolean(self, key):
        return self.values.get(key, "false").lower() == "true"

    def get_startup_wm_class(self):
        return self.values.get("StartupWMClass")

    def launch_uris_as_manager_with_fds(
        self, uris, launch_context, flags, child_setup, setup_data, callback, callback_data, stdin, stdout, stderr
    ):
        owner = self.owner
        owner.launch_calls.append((self, uris, launch_context, flags))
        owner.assertEqual(uris, [])
        owner.assertIsNone(child_setup)
        owner.assertIsNone(setup_data)
        owner.assertEqual(
            flags, owner.backend.ax.GLib.SpawnFlags.SEARCH_PATH | owner.backend.ax.GLib.SpawnFlags.DO_NOT_REAP_CHILD
        )
        owner.assertEqual(stdin, stdout)
        owner.assertEqual(stdout, stderr)
        owner.assertEqual(os.fstat(stdout).st_rdev, os.stat(os.devnull).st_rdev)
        owner.assertTrue(owner.driver.current.dispatched, "The requested-input barrier must precede the native call")
        owner.assertEqual(owner.active_context.effect, "possible")
        for pid in owner.callback_pids:
            callback(self, pid, callback_data)
        owner.after_launch()
        return owner.launch_result


@unittest.skipUnless(
    sys.platform == "linux",
    "Linux launch identity uses real POSIX executable and desktop-file contracts; protocol simulations remain portable",
)
class LinuxNativeLaunchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.program = self.root / "program"
        self.program.write_text("#!/bin/sh\nexit 0\n")
        self.program.chmod(0o755)
        self.filename = self.root / "launch.desktop"
        self.write_recipe()
        self.backend = Backend()
        self.backend.desktop.items = []
        self.backend.ax.applications = lambda notes=None: []
        self.backend.desktop.configure_launch = self.configure_launch
        self.backend.installed = lambda: [DesktopRecipe(self, self.filename)]
        self.watchers, self.removed_watches, self.closed_pids = {}, [], []
        self.next_watch = 1000
        self.backend.ax.GLib = NS(
            Error=NativeError,
            SpawnFlags=NS(SEARCH_PATH=4, DO_NOT_REAP_CHILD=2),
            shell_parse_argv=lambda value: (True, shlex.split(value)),
            child_watch_add=self.child_watch,
            source_remove=self.remove_watch,
            spawn_close_pid=self.closed_pids.append,
        )
        self.backend.ax.Gio = NS(
            DesktopAppInfo=NS(new_from_filename=self.load_recipe), AppLaunchContext=NS(new=self.launch_context)
        )
        self.launch_calls, self.contexts, self.configured = [], [], []
        self.callback_pids = [101]
        self.births = {101: "generation-101"}
        self.launch_result = True
        self.after_launch = lambda: None
        self.active_context = None
        for module in ("control.native_launch", "windowing.native_directory"):
            mocked = patch(
                "computer_use_linux." + module + ".process_birth", side_effect=lambda pid: self.births.get(pid, "")
            )
            mocked.start()
            self.addCleanup(mocked.stop)
        self.handoff_checker = lambda pid, birth, filename: (
            self.births.get(pid) == birth and filename == str(self.filename.resolve())
        )
        handoff_patch = patch(
            "computer_use_linux.control.native_launch.process_launched",
            side_effect=lambda *args: self.handoff_checker(*args),
        )
        self.handoff_mock = handoff_patch.start()
        self.addCleanup(handoff_patch.stop)
        wait_patch = patch("computer_use_linux.control.native_launch.os.waitpid", side_effect=ChildProcessError())
        self.wait_mock = wait_patch.start()
        self.addCleanup(wait_patch.stop)
        self.driver = NativeExecution(self.backend, "session")
        self.addCleanup(lambda: self.driver.close())
        self.session = "session"
        self.operation = None
        self.installation = self.catalogue_installation()
        self.target = dict(session=self.session, application=self.installation["id"])

    def child_watch(self, pid, callback):
        self.next_watch += 1
        self.watchers[self.next_watch] = (pid, callback)
        return self.next_watch

    def remove_watch(self, watch):
        self.removed_watches.append(watch)
        self.watchers.pop(watch, None)

    def write_recipe(self, **changes):
        values = dict(Type="Application", Name="Native launch fixture", Executable=str(self.program))
        values.update(changes)
        self.filename.write_text("[Desktop Entry]\n" + "".join(f"{k}={v}\n" for k, v in values.items()))

    def load_recipe(self, filename):
        config = ConfigParser(interpolation=None)
        config.optionxform = str
        config.read(filename)
        return DesktopRecipe(self, filename, dict(config["Desktop Entry"]))

    def launch_context(self):
        values = {}
        result = NS(
            setenv=lambda k, v: values.update({k: v}), unsetenv=lambda k: values.update({k: None}), values=values
        )
        self.contexts.append(result)
        return result

    def configure_launch(self, value):
        self.configured.append(value)
        value.setenv("FIXTURE_DESKTOP_SESSION", "the-selected-session")

    def directory(self, kind="applications", application=None):
        target = scope(session=self.session)
        operation = self.driver.perform(
            "begin", dict(mode="read", target=target, intent=dict(kind="read", parameters={})), context()
        )["operation"]
        try:
            query = dict(kind=kind)
            if application is not None:
                query["application"] = application
            return decodeDirectory(
                self.driver.perform("inspect", dict(operation=operation, target=target, query=query), context())
            )
        finally:
            self.driver.finish(operation)

    def catalogue_installation(self):
        return next(a for a in self.directory()["applications"] if not a["isRunning"])

    def test_window_class_facts_match_an_existing_process_without_claiming_launch_provenance(self):
        self.write_recipe(StartupWMClass="Fixture.Window")
        window = Window(
            77,
            "Working document",
            "different-native-name",
            201,
            (0, 0, 400, 300),
            process_start="generation-201",
            wm_class=("instance-name", "Fixture.Window"),
        )
        self.backend.desktop.items = [window]
        observed = self.directory()
        running = next(app for app in observed["applications"] if app["isRunning"])
        installation = next(app for app in observed["applications"] if not app["isRunning"])
        self.assertEqual(running["matchKeys"], ["x11.wm-class:Fixture.Window", "x11.wm-class:instance-name"])
        self.assertEqual(installation["matchKeys"], ["x11.wm-class:Fixture.Window"])
        self.assertEqual(select_application(observed, "Native launch fixture"), running)
        self.assertIsNone(running["identifier"])
        self.assertIsNone(running["path"])
        self.assertFalse(self.driver.directory.launched)
        self.assertFalse(self.launch_calls)
        window.wm_class = ("a-different-class",)
        updated = self.directory()
        self.assertFalse(select_application(updated, "Native launch fixture")["isRunning"])
        self.assertEqual(select_application(updated, running["id"])["id"], running["id"])

    def begin(self, target=None, *, intent=None, mode="write", ctx=None):
        reply = self.driver.perform(
            "begin",
            dict(mode=mode, target=target or self.target, intent=intent or dict(kind="launch", parameters={})),
            ctx or context(),
        )
        self.operation = reply["operation"]
        return reply

    def inspect(self, target=None, query=None):
        return decodeExecutionResult(
            "inspect",
            self.driver.perform(
                "inspect",
                dict(operation=self.operation, target=target or self.target, query=query or dict(kind="launch")),
                context(),
            ),
        )

    def prepare(self):
        self.begin()
        return self.command(self.inspect())

    def command(self, facts):
        return dict(
            kind="launch_application",
            primitiveId=facts["primitives"][0]["id"],
            target=deepcopy(self.target),
            foreground=False,
        )

    def send(self, command, *, step=1, ctx=None):
        self.active_context = ctx or context()
        return decodeExecutionResult(
            "execute",
            self.driver.perform(
                "execute", dict(operation=self.operation, step=step, command=command), self.active_context
            ),
        )

    def finish(self):
        value = self.driver.finish(self.operation)
        self.assertEqual(value, dict(cleanup="complete"))
        self.assertEqual(self.backend.leases, 0)
        return value

    def register(self, pid=101, birth="generation-101"):
        self.births[pid] = birth
        record = self.driver.directory.installations[self.target["application"]]
        return self.driver.directory.register_launch(record, pid, birth, context())

    def test_application_only_launch_needs_no_window_ax_or_input_provider(self):
        def forbidden(*args, **kwargs):
            self.fail("Application launch must not require accessibility or a window")

        self.backend.ax.begin = forbidden
        self.backend.ax.applications = forbidden
        self.backend.desktop.windows = forbidden
        opened = self.begin()
        self.assertEqual(opened["target"], self.target)
        self.assertEqual(self.backend.leases, 1)
        facts = self.inspect()
        self.assertEqual(facts["binding"], self.target)
        self.assertNotIn("input", facts)
        self.assertEqual(len(facts["primitives"]), 1)
        primitive = facts["primitives"][0]
        self.assertEqual((primitive["unit"], primitive["channel"]), ("launch_application", "object"))
        self.assertEqual((primitive["readiness"], primitive["pending"]), ("yes", []))
        reply = self.send(self.command(facts))
        self.assertEqual(reply["target"], self.target)
        self.assertEqual((reply["dispatch"], reply["confirmation"]), ("dispatched", "confirmed"))
        self.assertTrue(reply["application"]["isRunning"])
        self.assertNotEqual(reply["application"]["id"], self.target["application"])
        self.assertEqual(len(self.launch_calls), 1)
        self.assertEqual(reply["preparation"], [])
        self.assertIsNone(reply["context"])
        self.finish()

    def test_malformed_and_cross_scope_begin_rejected_before_work(self):
        cases = [
            (self.target, dict(kind="read", parameters={}), "read"),
            (self.target, dict(kind="key", parameters=dict(key="Return")), "write"),
            (self.target, dict(kind="launch", parameters=dict(path="/arbitrary")), "write"),
            ({**self.target, "node": "foreign"}, dict(kind="launch", parameters={}), "write"),
            (scope(), dict(kind="launch", parameters={}), "write"),
            ({**self.target, "session": "another-session"}, dict(kind="launch", parameters={}), "write"),
            ({**self.target, "application": "unobserved"}, dict(kind="launch", parameters={}), "write"),
        ]
        for target, intent, mode in cases:
            with self.subTest(target=target, intent=intent, mode=mode), self.assertRaises(Failure):
                self.begin(target, intent=intent, mode=mode)
            self.assertIsNone(self.driver.current)
            self.assertEqual(self.backend.leases, 0)
        self.assertFalse(self.launch_calls)

    def test_launch_cannot_inspect_nodes_input_context_or_other_installation(self):
        self.begin()
        for query in (
            dict(kind="nodes", fields=["name"], maxNodes=1, maxDepth=0),
            dict(kind="inputContext", within=[]),
            dict(kind="applications"),
            dict(kind="launch", within=[]),
        ):
            with self.subTest(query=query), self.assertRaises(Failure):
                self.inspect(query=query)
        with self.assertRaises(Failure):
            self.inspect(target={**self.target, "application": "different"})
        self.assertFalse(self.launch_calls)
        self.finish()

    def test_foreground_arbitrary_command_and_foreign_contract_never_launch(self):
        command = self.prepare()
        for index, change in enumerate(
            (
                dict(foreground=True),
                dict(kind="key_sequence"),
                dict(primitiveId="unobserved"),
                dict(target={**self.target, "application": "different"}),
                dict(text="smuggled"),
            ),
            1,
        ):
            with self.subTest(change=change):
                try:
                    result = self.send({**command, **change}, step=index)
                except Failure as error:
                    self.assertEqual(error.effect, "none")
                else:
                    self.assertEqual(result["dispatch"], "notDispatched")
                    self.assertIsNotNone(result["errorCode"])
        self.assertFalse(self.launch_calls)
        self.finish()

    def test_desktop_change_after_inspect_fails_without_dispatch(self):
        command = self.prepare()
        self.write_recipe(Name="A different observed recipe")
        reply = self.send(command)
        self.assertEqual((reply["dispatch"], reply["errorCode"]), ("notDispatched", "stale_target"))
        self.assertFalse(self.launch_calls)
        self.assertEqual(self.active_context.effect, "none")
        self.finish()

    def test_executable_replacement_and_rediscovery_cannot_revive_old_installation_id(self):
        old = deepcopy(self.target)
        replacement = self.root / "replacement"
        replacement.write_bytes(self.program.read_bytes())
        replacement.chmod(0o755)
        replacement.replace(self.program)
        refreshed = self.catalogue_installation()
        self.assertNotEqual(refreshed["id"], old["application"])
        self.begin(old)
        with self.assertRaises(Failure) as caught:
            self.inspect(target=old)
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertFalse(self.launch_calls)
        self.finish()

    def test_unique_registered_running_instance_is_reused_without_native_launch(self):
        running = self.register()
        command = self.prepare()
        reply = self.send(command)
        self.assertEqual((reply["dispatch"], reply["confirmation"]), ("notDispatched", "confirmed"))
        self.assertEqual(reply["application"]["id"], running["id"])
        self.assertEqual(self.active_context.effect, "none")
        self.assertFalse(self.launch_calls)
        self.assertFalse(self.contexts)
        self.finish()

    def test_another_desktop_recipe_using_same_executable_is_not_reused(self):
        other = self.root / "other.desktop"
        other.write_text(
            f"[Desktop Entry]\nType=Application\nID=other.desktop\nName=Other recipe\nExecutable={self.program}\n"
        )
        record = NativeInstallation.capture(
            self.load_recipe(str(other)), self.backend.ax.Gio, context(), GLib=self.backend.ax.GLib
        )
        self.births[202] = "generation-202"
        foreign = self.driver.directory.register_launch(record, 202, "generation-202", context())
        reply = self.send(self.prepare())
        self.assertEqual(reply["dispatch"], "dispatched")
        self.assertNotEqual(reply["application"]["id"], foreign["id"])
        self.assertEqual(len(self.launch_calls), 1)
        self.finish()

    def test_multiple_registered_instances_are_ambiguous_and_not_launched(self):
        self.register()
        self.register(102, "generation-102")
        reply = self.send(self.prepare())
        self.assertEqual((reply["dispatch"], reply["errorCode"]), ("notDispatched", "ambiguous_target"))
        self.assertFalse(self.launch_calls)
        self.finish()

    def test_running_receipt_retained_without_window_then_matches_exact_process_window(self):
        reply = self.send(self.prepare())
        app = reply["application"]
        self.finish()
        before = self.directory("surfaces", app["id"])
        self.assertEqual(before["surfaces"], [])
        self.assertIn(app["id"], [a["id"] for a in self.directory()["applications"] if a["isRunning"]])
        self.backend.desktop.items = [
            Window(
                21, "First", "Any display name", 101, (0, 0, 320, 200), process_start="generation-101", accessible=None
            )
        ]
        after = self.directory("surfaces", app["id"])
        self.assertEqual(len(after["surfaces"]), 1)
        self.assertIsNone(after["surfaces"][0]["node"])
        self.assertEqual(after["application"], app["id"])
        self.assertEqual(len(self.launch_calls), 1)

    def test_pid_reuse_does_not_keep_old_running_receipt_alive(self):
        running = self.register()
        self.births[101] = "reused-generation"
        directory = self.directory()
        self.assertNotIn(running["id"], [a["id"] for a in directory["applications"]])
        with self.assertRaises(Failure) as caught:
            self.directory("surfaces", running["id"])
        self.assertEqual(caught.exception.code, "stale_target")
        self.assertFalse(self.launch_calls)

    def test_no_pid_false_or_multiple_callback_receipt_is_unknown_without_replay(self):
        for pids, accepted in (([], True), ([101], False), ([101, 102], True)):
            with self.subTest(pids=pids, accepted=accepted):
                self.driver.directory.launched.clear()
                self.callback_pids, self.launch_result = pids, accepted
                self.births[102] = "generation-102"
                command = self.prepare()
                before = len(self.launch_calls)
                reply = self.send(command)
                self.assertEqual(reply["dispatch"], "unknown")
                self.assertIsNotNone(reply["errorCode"])
                self.assertNotIn("application", reply)
                self.assertEqual(self.active_context.effect, "possible")
                self.assertEqual(len(self.launch_calls), before + 1)
                with self.assertRaises(Failure):
                    self.send(command, step=2)
                self.finish()
                self.assertEqual(self.send(command), reply)
                self.assertEqual(len(self.launch_calls), before + 1)

    def test_native_exception_after_callback_keeps_unknown_effect_and_cached_result(self):
        def fail():
            raise NativeError("Native launch disconnected after accepting the request")

        self.after_launch = fail
        command = self.prepare()
        reply = self.send(command)
        self.assertEqual(reply["dispatch"], "unknown")
        self.assertIsNotNone(reply["errorCode"])
        self.assertNotIn("application", reply)
        self.assertEqual(self.send(command), reply)
        self.finish()
        self.assertEqual(self.send(command), reply)
        self.assertEqual(len(self.launch_calls), 1)

    def test_process_disappearing_after_callback_cannot_return_confirmed_receipt(self):
        self.after_launch = lambda: self.births.clear()
        reply = self.send(self.prepare())
        self.assertEqual(reply["dispatch"], "unknown")
        self.assertIsNotNone(reply["errorCode"])
        self.assertNotIn("application", reply)
        self.finish()

    def test_step_replay_after_finish_ignores_later_installation_change(self):
        command = self.prepare()
        reply = self.send(command)
        self.finish()
        self.filename.unlink()
        self.assertEqual(self.send(command), reply)
        self.assertEqual(len(self.launch_calls), 1)
        for candidate, step in (({**command, "foreground": True}, 1), (command, 2)):
            with self.assertRaises(Failure):
                self.send(candidate, step=step)
        self.assertEqual(len(self.launch_calls), 1)

    def test_cancellation_before_and_after_native_launch_has_distinct_effects(self):
        command = self.prepare()
        cancelled = context()
        cancelled.cancelled.set()
        try:
            before = self.send(command, ctx=cancelled)
        except Failure as error:
            self.assertEqual((error.code, error.effect), ("cancelled", "none"))
        else:
            self.assertEqual((before["errorCode"], before["dispatch"]), ("cancelled", "notDispatched"))
        self.assertFalse(self.launch_calls)
        self.finish()
        command = self.prepare()
        after = context()
        self.after_launch = after.cancelled.set
        reply = self.send(command, ctx=after)
        self.assertEqual((reply["errorCode"], reply["dispatch"]), ("cancelled", "unknown"))
        self.assertEqual(after.effect, "possible")
        self.finish()
        self.assertEqual(len(self.launch_calls), 1)

    def test_expiry_and_finish_release_lease_exactly_once(self):
        self.begin()
        self.assertEqual(self.backend.leases, 1)
        self.driver.current.deadline = 0
        self.driver.expire()
        self.assertIsNone(self.driver.current)
        self.assertEqual(self.backend.leases, 0)
        self.finish()
        self.finish()
        self.assertFalse(self.launch_calls)

    def test_session_environment_and_protocol_fds_use_native_launcher_contract(self):
        reply = self.send(self.prepare())
        self.assertEqual(reply["dispatch"], "dispatched")
        self.assertEqual(len(self.configured), 1)
        self.assertIs(self.configured[0], self.contexts[0])
        self.assertEqual(self.contexts[0].values["FIXTURE_DESKTOP_SESSION"], "the-selected-session")
        for key in (
            "COMPUTER_USE_LIFETIME_FD",
            "COMPUTER_USE_PREVIEW_FD",
            "DESKTOP_STARTUP_ID",
            "XDG_ACTIVATION_TOKEN",
            "GIO_LAUNCHED_DESKTOP_FILE_PID",
        ):
            self.assertIn(key, self.contexts[0].values)
            self.assertIsNone(self.contexts[0].values[key])
        self.finish()

    def test_catalogue_refresh_preserves_the_original_live_contract(self):
        command = self.prepare()
        record = self.driver.directory.installations[self.target["application"]]
        # A separate read call normally cannot run while this write owns the
        # lease. Exercise the collector refresh directly as a lifetime test.
        directory = self.driver.directory.applications(context())
        self.assertIn(self.target["application"], [a["id"] for a in directory["applications"]])
        self.assertIs(self.driver.directory.installations[self.target["application"]], record)
        self.assertEqual(self.send(command)["dispatch"], "dispatched")
        self.assertEqual(len(self.launch_calls), 1)
        self.finish()

    def test_lock_after_inspect_stops_before_any_launch(self):
        command = self.prepare()
        self.backend.locked = True
        reply = self.send(command)
        self.assertEqual((reply["dispatch"], reply["errorCode"]), ("notDispatched", "input_unavailable"))
        self.assertEqual(self.active_context.effect, "none")
        self.assertFalse(self.launch_calls)
        self.finish()

    def test_unsupported_recipe_is_visible_but_never_dispatches(self):
        self.write_recipe(DBusActivatable="true")
        self.installation = self.catalogue_installation()
        self.target = dict(session=self.session, application=self.installation["id"])
        self.begin()
        with self.assertRaises(Failure) as caught:
            self.inspect()
        self.assertEqual(caught.exception.code, "mode_unsupported")
        self.assertIn("D-Bus", str(caught.exception))
        self.assertFalse(self.launch_calls)
        self.finish()

    def test_process_generation_change_after_callback_is_not_a_receipt(self):
        self.after_launch = lambda: self.births.update({101: "reused-generation"})
        reply = self.send(self.prepare())
        self.assertEqual(reply["dispatch"], "unknown")
        self.assertEqual(reply["errorCode"], "execution_unknown")
        self.assertNotIn("application", reply)
        self.finish()

    def test_deadline_after_native_launch_keeps_unknown_effect(self):
        command = self.prepare()
        expired = context()
        self.after_launch = lambda: setattr(expired, "deadline", 0)
        reply = self.send(command, ctx=expired)
        self.assertEqual((reply["errorCode"], reply["dispatch"]), ("deadline_exceeded", "unknown"))
        self.assertEqual(expired.effect, "possible")
        self.assertEqual(len(self.launch_calls), 1)
        self.finish()

    def test_handoff_waits_readonly_for_markers_before_confirming(self):
        states = iter([False, False, True])
        self.handoff_checker = lambda *_: next(states)
        reply = self.send(self.prepare())
        self.assertEqual((reply["dispatch"], reply["confirmation"]), ("dispatched", "confirmed"))
        self.assertEqual(self.handoff_mock.call_count, 3)
        self.handoff_mock.assert_called_with(101, "generation-101", str(self.filename.resolve()))
        self.assertEqual(len(self.launch_calls), 1)
        self.finish()

    def test_handoff_absent_has_finite_wait_and_no_replay(self):
        command = self.prepare()
        clock = [time.monotonic()]
        started = clock[0]
        pending = context(5)
        pauses = []

        def advance(seconds):
            pauses.append(seconds)
            clock[0] += 0.3

        pending.pause = advance
        self.handoff_checker = lambda *_: False
        with patch("computer_use_linux.control.native_launch.time", NS(monotonic=lambda: clock[0])):
            reply = self.send(command, ctx=pending)
        self.assertEqual((reply["dispatch"], reply["errorCode"]), ("unknown", "execution_unknown"))
        self.assertNotIn("application", reply)
        self.assertEqual(pending.effect, "possible")
        self.assertGreaterEqual(clock[0] - started, 1)
        self.assertLess(clock[0] - started, 1.5)
        self.assertTrue(pauses)
        self.assertTrue(all(0 < pause <= 0.02 for pause in pauses))
        with self.assertRaises(Failure):
            self.send(command, step=2)
        self.finish()
        self.assertEqual(self.send(command), reply)
        self.assertEqual(len(self.launch_calls), 1)

    def test_cancellation_during_handoff_never_confirms_or_replays(self):
        command = self.prepare()
        pending = context()

        def checking(*_):
            pending.cancelled.set()
            return False

        self.handoff_checker = checking
        reply = self.send(command, ctx=pending)
        self.assertEqual((reply["dispatch"], reply["errorCode"]), ("unknown", "cancelled"))
        self.assertNotIn("application", reply)
        self.finish()
        self.assertEqual(self.send(command), reply)
        self.assertEqual(len(self.launch_calls), 1)

    def test_child_watch_reaps_exit_once_without_terminating_application(self):
        self.send(self.prepare())
        self.finish()
        watch = self.driver.launch.children[101]
        pid, callback = self.watchers.pop(watch)
        callback(pid, 0)
        self.assertEqual(self.driver.launch.children, {})
        self.assertEqual(self.closed_pids, [101])
        self.driver.close()
        self.assertEqual(self.closed_pids, [101])
        self.assertFalse(self.removed_watches)
        self.wait_mock.assert_not_called()

    def test_driver_close_removes_live_child_watch_without_killing_app(self):
        self.send(self.prepare())
        watch = self.driver.launch.children[101]
        with patch(
            "computer_use_linux.control.native_launch.os.kill", side_effect=AssertionError("Do not kill the app")
        ):
            self.driver.close()
            self.driver.close()
        self.assertEqual(self.driver.launch.children, {})
        self.assertEqual(self.removed_watches, [watch])
        self.assertEqual(self.closed_pids, [101])
        self.wait_mock.assert_called_once_with(101, os.WNOHANG)
        self.assertEqual(self.births[101], "generation-101")
        self.assertEqual(self.backend.leases, 0)

    def test_real_server_rpc_and_step_dedup_preserve_unknown_result(self):
        self.driver.close()
        output = io.StringIO()
        server = Server(lambda *_: self.backend, output=output)
        server.execute(dict(version=6, id=0, method="hello", params=dict(desktopMode="shared")), context())
        self.driver = server.native
        self.session = server.session
        self.installation = self.catalogue_installation()
        self.target = dict(session=self.session, application=self.installation["id"])
        command = self.prepare()
        self.callback_pids = []

        def rpc(id, command=command):
            self.active_context = context()
            frame = dict(
                version=6,
                session=self.session,
                id=id,
                timeoutMs=1000,
                method="execute",
                params=dict(operation=self.operation, step=1, command=command),
            )
            server.execute(frame, self.active_context)
            return json.loads(output.getvalue().splitlines()[-1])

        first = rpc(1)
        self.assertEqual(first["result"]["dispatch"], "unknown")
        self.assertEqual(rpc(1)["result"], first["result"])
        self.assertEqual(rpc(2)["result"], first["result"])
        self.finish()
        self.assertEqual(rpc(3)["result"], first["result"])
        self.assertEqual(rpc(3, {**command, "foreground": True})["error"]["code"], "request_conflict")
        server.cache.clear()
        self.assertEqual(rpc(1)["error"]["code"], "request_conflict")
        self.assertEqual(len(self.launch_calls), 1)


class LinuxLaunchMarkerTests(unittest.TestCase):
    @staticmethod
    def marker(payload, *, births=("generation", "generation")):
        # Call the real process_launched function; only /proc I/O is substituted.
        opened = mock_open(read_data=payload)
        with (
            patch("computer_use_linux.control.native_launch.process_birth", side_effect=births),
            patch("computer_use_linux.control.native_launch.open", opened, create=True),
        ):
            result = native_process_launched(101, "generation", "/observed/app.desktop")
        return result, opened

    def test_only_exact_file_and_pid_after_exec_confirm_handoff(self):
        data = (
            b"SECRET=not-for-output\0GIO_LAUNCHED_DESKTOP_FILE=/observed/app.desktop\0"
            b"GIO_LAUNCHED_DESKTOP_FILE_PID=101\0"
        )
        result, opened = self.marker(data)
        self.assertTrue(result)
        opened.assert_called_once_with("/proc/101/environ", "rb")
        opened().read.assert_called_once_with(1024 * 1024 + 1)

    def test_missing_wrong_and_duplicate_provenance_markers_are_not_confirmed(self):
        good_file = b"GIO_LAUNCHED_DESKTOP_FILE=/observed/app.desktop\0"
        good_pid = b"GIO_LAUNCHED_DESKTOP_FILE_PID=101\0"
        for data in (
            b"",
            good_file,
            good_pid,
            good_file + b"GIO_LAUNCHED_DESKTOP_FILE_PID=102\0",
            b"GIO_LAUNCHED_DESKTOP_FILE=/other/app.desktop\0" + good_pid,
            good_file + good_pid + good_pid,
            good_file + good_pid + good_file,
        ):
            with self.subTest(data=data):
                self.assertFalse(self.marker(data)[0])

    def test_process_reuse_before_or_during_environment_read_is_not_confirmed(self):
        data = b"GIO_LAUNCHED_DESKTOP_FILE=/observed/app.desktop\0GIO_LAUNCHED_DESKTOP_FILE_PID=101\0"
        result, opened = self.marker(data, births=("another-generation",))
        self.assertFalse(result)
        opened.assert_not_called()
        self.assertFalse(self.marker(data, births=("generation", "another-generation"))[0])

    def test_environment_budget_and_unavailable_proc_remain_unknown(self):
        self.assertFalse(self.marker(b"x" * (1024 * 1024 + 1))[0])
        with (
            patch("computer_use_linux.control.native_launch.process_birth", return_value="generation"),
            patch(
                "computer_use_linux.control.native_launch.open", side_effect=PermissionError("unavailable"), create=True
            ),
        ):
            self.assertFalse(native_process_launched(101, "generation", "/observed/app.desktop"))


if __name__ == "__main__":
    unittest.main()
