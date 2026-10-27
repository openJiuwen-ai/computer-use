"""Command resolution preserves environment and working-directory semantics."""

import errno
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_use._process import resolve_executable


class ExecutableResolutionTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX executable permissions and relative PATH entries")
    def test_resolved_path_is_absolute_even_for_relative_search_entries(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "bin" / "probe"
            binary.parent.mkdir()
            binary.write_text("fixture")
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            with patch("computer_use._process.os.name", "posix"):
                result = resolve_executable("probe", env={"PATH": "bin"}, cwd=folder)
            self.assertEqual(result, str(binary))

    @unittest.skipIf(os.name == "nt", "POSIX executable permissions and relative PATH entries")
    def test_empty_path_entry_and_relative_program_use_the_child_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            binary = Path(folder) / "probe"
            binary.write_text("fixture")
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
            with patch("computer_use._process.os.name", "posix"):
                self.assertEqual(resolve_executable("probe", env={"PATH": ""}, cwd=folder), str(binary))
                self.assertEqual(resolve_executable("./probe", env={"PATH": ""}, cwd=folder), str(binary))

    def test_missing_program_keeps_file_not_found_details(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError) as caught:
                resolve_executable("computer-use-missing-program", env={"PATH": folder})
        self.assertEqual(caught.exception.errno, errno.ENOENT)
        self.assertEqual(caught.exception.filename, "computer-use-missing-program")

    def test_windows_resolution_uses_parent_environment_and_directory(self):
        with patch("computer_use._process.os.name", "nt"), patch.dict(os.environ, PATH="parent-bin"):
            with patch("computer_use._process.shutil.which", return_value="/resolved/program.exe") as lookup:
                self.assertEqual(
                    resolve_executable("program", env={"PATH": "child-bin"}, cwd="child"),
                    os.path.abspath("/resolved/program.exe"),
                )
        self.assertEqual(lookup.call_args.kwargs["path"], os.path.join(os.getcwd(), "parent-bin"))
