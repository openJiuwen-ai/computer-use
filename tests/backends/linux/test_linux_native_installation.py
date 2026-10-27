"""Desktop installation identity; no application is launched by these tests."""

import os
import shlex
import sys
import unittest
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch

from computer_use_linux.contracts.protocol import Failure
from computer_use_linux.windowing.native_installation import MAX_DESKTOP_BYTES, NativeInstallation


class DesktopApp:
    def __init__(self, filename, identifier="example.desktop", *, executable=None, label="Catalogue label"):
        self.filename, self.identifier = filename, identifier
        self.executable, self.label = executable, label
        self.settings = {}

    def get_id(self):
        return self.identifier

    def get_filename(self):
        return self.filename

    def get_display_name(self):
        return self.label

    def get_executable(self):
        return self.executable

    def get_boolean(self, key):
        return self.settings.get(key, "false").lower() == "true"

    def get_commandline(self):
        return self.settings.get("Command", shlex.quote(self.executable))

    def launch_uris_as_manager_with_fds(self, *args):
        raise AssertionError("Identity tests must not launch an application")


@unittest.skipUnless(
    sys.platform == "linux",
    "Linux installation filesystem contracts require POSIX modes, nonblocking opens and FIFO semantics",
)
class LinuxNativeInstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.program = self.root / "program"
        self.program.write_text("#!/bin/sh\nexit 0\n")
        self.program.chmod(0o755)
        self.filename = self.root / "example.desktop"
        self.write()
        self.loads = []
        self.before_load = lambda: None
        self.Gio = NS(DesktopAppInfo=NS(new_from_filename=self.load))
        self.GLib = NS(shell_parse_argv=lambda value: (True, shlex.split(value)), Error=ValueError)

    def write(self, path=None, **values):
        fields = dict(Type="Application", Name="Fresh label", Executable=str(self.program))
        fields.update(values)
        # Executable is a fake-provider field; production never parses desktop text.
        (path or self.filename).write_text(
            "[Desktop Entry]\n" + "".join(f"{key}={value}\n" for key, value in fields.items())
        )

    def load(self, filename):
        self.before_load()
        self.loads.append(filename)
        config = ConfigParser(interpolation=None)
        config.optionxform = str
        config.read(filename)
        values = config["Desktop Entry"]
        result = DesktopApp(
            filename, identifier="filename-derived.desktop", executable=values["Executable"], label=values["Name"]
        )
        result.settings = dict(values)
        return result

    def capture(self, path=None, identifier="example.desktop", ctx=None):
        app = DesktopApp(str(path or self.filename), identifier)
        return NativeInstallation.capture(app, self.Gio, ctx, GLib=self.GLib)

    def failure(self, code, callback):
        with self.assertRaises(Failure) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(caught.exception.effect, "none")
        return str(caught.exception)

    def test_quoted_executable_with_spaces_uses_glib_not_debug_label(self):
        target = self.root / "program with spaces"
        target.write_bytes(self.program.read_bytes())
        target.chmod(0o755)
        self.write(Executable='"' + str(target).split(" ")[0], Command='"' + str(target) + '" --open %U')
        item = self.capture()
        self.assertTrue(item.available)
        self.assertEqual(item.executable_path, str(target.resolve()))
        self.assertIs(item.validate(), item.native)

    def test_dynamic_executable_and_malformed_command_remain_unavailable(self):
        for command in ("%c --arg", '"unterminated', '"/tmp/with%%percent" %U'):
            with self.subTest(command=command):
                self.write(Command=command)
                item = self.capture()
                self.assertFalse(item.available)
                self.failure("mode_unsupported", item.validate)

    def test_capture_reloads_exact_file_and_keeps_original_id(self):
        item = self.capture()
        self.assertTrue(item.available)
        self.assertEqual(item.identifier, "example.desktop")
        self.assertEqual(item.label, "Fresh label")
        self.assertEqual(item.path, str(self.filename.resolve()))
        self.assertEqual(item.executable_path, str(self.program.resolve()))
        self.assertEqual(self.loads, [item.path])
        self.assertIs(item.validate(), item.native)
        self.assertEqual(self.loads, [item.path], "Validation cannot reload by desktop ID")
        self.assertEqual(item.identity, item.key)
        self.assertIsInstance(hash(item.key), int)

    def test_same_file_recapture_has_stable_key(self):
        self.assertEqual(self.capture().key, self.capture().key)

    def test_same_executable_different_recipes_stay_distinct(self):
        other = self.root / "other.desktop"
        self.write(other, Name="Alternate recipe", Arguments="--different")
        one, two = self.capture(), self.capture(other, "other.desktop")
        self.assertEqual(one.executable_path, two.executable_path)
        self.assertNotEqual(one.key, two.key)

    def test_same_id_shadow_entry_cannot_redirect_record(self):
        item = self.capture()
        shadow = self.root / "shadow.desktop"
        self.write(shadow, Name="Override")
        replacement = self.capture(shadow)
        self.assertEqual(item.identifier, replacement.identifier)
        self.assertNotEqual(item.key, replacement.key)
        self.assertIs(item.validate(), item.native)
        self.assertEqual(item.path, str(self.filename.resolve()))

    def test_atomic_desktop_replacement_invalidates_even_identical_content(self):
        item = self.capture()
        replacement = self.root / "replacement"
        replacement.write_bytes(self.filename.read_bytes())
        replacement.replace(self.filename)
        self.failure("stale_target", item.validate)

    def test_desktop_content_change_invalidates(self):
        item = self.capture()
        self.write(Name="Other label")
        self.failure("stale_target", item.validate)

    def test_desktop_removed_invalidates(self):
        item = self.capture()
        self.filename.unlink()
        self.failure("stale_target", item.validate)

    def test_desktop_symlink_is_supported_but_retargeting_invalidates(self):
        link = self.root / "linked.desktop"
        link.symlink_to(self.filename)
        item = self.capture(link)
        self.assertEqual(item.filename, str(link))
        self.assertEqual(item.path, str(self.filename.resolve()))
        other = self.root / "other.desktop"
        self.write(other)
        link.unlink()
        link.symlink_to(other)
        self.failure("stale_target", item.validate)

    def test_change_during_gio_parse_is_not_captured_as_consistent(self):
        self.before_load = lambda: self.write(Name="Changed during parse")
        self.failure("stale_target", self.capture)

    def test_executable_replacement_invalidates(self):
        item = self.capture()
        replacement = self.root / "replacement"
        replacement.write_bytes(self.program.read_bytes())
        replacement.chmod(0o755)
        replacement.replace(self.program)
        self.failure("stale_target", item.validate)

    def test_recapture_after_executable_replacement_has_a_new_key(self):
        original = self.capture()
        replacement = self.root / "replacement"
        replacement.write_bytes(self.program.read_bytes())
        replacement.chmod(0o755)
        replacement.replace(self.program)
        current = self.capture()
        self.assertEqual(original.desktop, current.desktop)
        self.assertEqual(original.executable_path, current.executable_path)
        self.assertNotEqual(original.key, current.key)
        self.failure("stale_target", original.validate)
        self.assertIs(current.validate(), current.native)

    def test_executable_symlink_retargeting_invalidates(self):
        link = self.root / "launcher"
        link.symlink_to(self.program)
        self.write(Executable=str(link))
        item = self.capture()
        replacement = self.root / "second-program"
        replacement.write_bytes(self.program.read_bytes())
        replacement.chmod(0o755)
        link.unlink()
        link.symlink_to(replacement)
        self.failure("stale_target", item.validate)

    def test_relative_path_search_match_is_not_a_launchable_entry(self):
        self.write(Executable="program")
        with patch("computer_use_linux.windowing.native_installation.shutil.which", return_value="./program"):
            item = self.capture()
        self.assertFalse(item.available)
        self.failure("mode_unsupported", item.validate)

    def test_relative_path_component_cannot_be_hidden_by_later_absolute_match(self):
        self.write(Executable="program")
        for relative in (".", "", "relative-bin"):
            with self.subTest(relative=relative), patch.dict(os.environ, PATH=relative + os.pathsep + str(self.root)):
                # The relative directory may miss in this process but contain
                # a different executable under the desktop entry's Path=.
                with patch(
                    "computer_use_linux.windowing.native_installation.shutil.which", return_value=str(self.program)
                ):
                    item = self.capture()
                self.assertFalse(item.available)
                self.failure("mode_unsupported", item.validate)

    def test_executable_no_longer_executable_invalidates(self):
        item = self.capture()
        self.program.chmod(0o644)
        self.failure("stale_target", item.validate)

    def test_absolute_path_and_path_program_supported(self):
        self.write(Executable="program")
        with patch.dict(os.environ, PATH=str(self.root)):
            item = self.capture()
            self.assertTrue(item.available)
            self.assertEqual(item.executable_path, str(self.program.resolve()))
            self.assertIs(item.validate(), item.native)

    def test_path_resolution_change_invalidates(self):
        self.write(Executable="program")
        other = self.root / "bin"
        other.mkdir()
        second = other / "program"
        second.write_bytes(self.program.read_bytes())
        second.chmod(0o755)
        with patch.dict(os.environ, PATH=str(self.root)):
            item = self.capture()
        with patch.dict(os.environ, PATH=str(other)):
            self.failure("stale_target", item.validate)

    def test_recapture_after_path_resolution_changes_has_a_new_key(self):
        self.write(Executable="program")
        other = self.root / "bin"
        other.mkdir()
        second = other / "program"
        second.write_bytes(self.program.read_bytes())
        second.chmod(0o755)
        with patch.dict(os.environ, PATH=str(self.root)):
            original = self.capture()
        with patch.dict(os.environ, PATH=str(other)):
            current = self.capture()
            self.failure("stale_target", original.validate)
            self.assertIs(current.validate(), current.native)
        self.assertEqual(original.desktop, current.desktop)
        self.assertEqual(original.executable, current.executable)
        self.assertNotEqual(original.executable_path, current.executable_path)
        self.assertNotEqual(original.key, current.key)

    def test_unknown_and_relative_entries_remain_visible_but_unavailable(self):
        for value in ("missing-unresolvable-program", "./program", str(self.root / "missing")):
            with self.subTest(value=value):
                self.write(Executable=value)
                item = self.capture()
                self.assertFalse(item.available)
                self.assertEqual(item.label, "Fresh label")
                self.failure("mode_unsupported", item.validate)

    def test_filename_missing_has_stable_unlaunchable_identity(self):
        app = DesktopApp(None, executable="unknown-program")
        one = NativeInstallation.capture(app, self.Gio, GLib=self.GLib)
        two = NativeInstallation.capture(app, self.Gio, GLib=self.GLib)
        self.assertFalse(one.available)
        self.assertEqual(one.key, two.key)
        self.assertEqual(one.executable, "unknown-program")
        self.assertIsNone(one.path)
        self.assertEqual(self.loads, [])
        self.failure("mode_unsupported", one.validate)

    def test_dbus_and_terminal_declarations_are_retained_without_false_launch_support(self):
        for field in ("DBusActivatable", "Terminal"):
            with self.subTest(field=field):
                self.write(**{field: "true"})
                item = self.capture()
                self.assertFalse(item.available)
                self.assertEqual(item.dbus_activatable, field == "DBusActivatable")
                self.assertEqual(item.terminal, field == "Terminal")
                self.assertEqual(item.executable_path, str(self.program.resolve()))
                self.failure("mode_unsupported", item.validate)

    def test_non_regular_desktop_is_rejected_without_blocking_on_fifo(self):
        self.filename.unlink()
        os.mkfifo(self.filename)
        self.assertIn("regular file", self.failure("observation_incomplete", self.capture))

    def test_desktop_over_budget_is_rejected_before_gio(self):
        self.filename.write_bytes(b"x" * (MAX_DESKTOP_BYTES + 1))
        self.assertIn("budget", self.failure("observation_incomplete", self.capture))
        self.assertEqual(self.loads, [])

    def test_gio_parse_failure_preserves_directory_record(self):
        self.Gio.DesktopAppInfo.new_from_filename = lambda path: None
        item = self.capture()
        self.assertFalse(item.available)
        self.assertIsNotNone(item.desktop)
        self.assertEqual(item.identifier, "example.desktop")
        self.failure("mode_unsupported", item.validate)

    def test_cancellation_remains_cancellation_in_capture_and_validation(self):
        item = self.capture()

        def stop():
            raise Failure("Cancelled.", "cancelled")

        ctx = NS(check=stop)
        self.failure("cancelled", lambda: self.capture(ctx=ctx))
        self.failure("cancelled", lambda: item.validate(ctx))


if __name__ == "__main__":
    unittest.main()
