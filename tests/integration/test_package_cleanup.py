"""A reused build tree must not retain retired Python modules or resources."""

import runpy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repository import ROOT

try:
    from setuptools import Distribution
except ImportError:
    Distribution = None


@unittest.skipIf(Distribution is None, "setuptools build dependency is not installed; run with uv --with setuptools")
class PackageCleanupTests(unittest.TestCase):
    @staticmethod
    def command(root, destination):
        with patch("setuptools.setup"):
            cls = runpy.run_path(str(ROOT / "setup.py"))["BuildWithResources"]
        cls.run.__globals__.update(ROOT=root, DIRECTORIES=(), FILES=())
        distribution = Distribution(dict(packages=["computer_use"], package_dir={"computer_use": str(root / "computer_use_engine")}))
        distribution.script_name = str(root / "setup.py")
        command = cls(distribution)
        command.ensure_finalized()
        command.build_lib = str(destination)
        return command

    def test_rebuilding_prunes_deleted_modules_and_old_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "computer_use_engine"
            source.mkdir(parents=True)
            (source / "__init__.py").write_text("current = True\n")
            output = root / "build/lib"
            old = output / "computer_use/execution"
            old.mkdir(parents=True)
            (old / "retired.py").write_text("old = True\n")
            resource = output / "computer_use/_resources/retired"
            resource.mkdir(parents=True)
            (resource / "obsolete.py").write_text("old = True\n")
            self.command(root, output).run()
            self.assertFalse(old.exists())
            self.assertFalse(resource.exists())
            self.assertEqual((output / "computer_use/__init__.py").read_text(), "current = True\n")
            self.assertEqual((output / "computer_use/_resources/manifest.json").read_text(), "{}")

    def test_build_never_prunes_the_source_package(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "computer_use_engine"
            source.mkdir(parents=True)
            marker = source / "__init__.py"
            marker.write_text("keep = True\n")
            with self.assertRaisesRegex(RuntimeError, "source package"):
                self.command(root, source).run()
            self.assertTrue(marker.exists())
