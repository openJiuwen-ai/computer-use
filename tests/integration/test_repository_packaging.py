"""Repository relocation must preserve SDK imports and installed host resources."""

import hashlib
import json
import runpy
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from repository import ROOT

from computer_use import paths

try:
    from setuptools import Distribution
except ImportError:
    Distribution = None


class ResourceLayoutTests(unittest.TestCase):
    def tearDown(self):
        paths.repositoryRoot.cache_clear()

    def test_checkout_is_detected_from_the_engine_directory(self):
        with patch.object(paths, "__file__", str(ROOT / "computer_use_engine/paths.py")):
            self.assertEqual(paths.checkoutRoot(), ROOT)
        self.assertTrue((ROOT / "os_adapter/macos/Package.swift").is_file())

    def test_native_driver_paths_follow_the_shared_repository_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertEqual(
                paths.nativeDriver("darwin", root)["command"],
                str(root / "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver"),
            )
            self.assertEqual(
                paths.nativeDriver("linux", root),
                dict(command=str(root / "os_adapter/linux/.venv/bin/python"), args=["-m", "computer_use_linux"]),
            )
            self.assertEqual(
                paths.nativeDriver("win32", root)["command"],
                str(root / "os_adapter/windows/.build/computer-use-driver.exe"),
            )

    def test_wheel_resources_materialize_the_same_layout_without_writing_site_packages(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            installed = root / "site-packages/computer_use"
            source = installed / "_resources/os_adapter/linux/pyproject.toml"
            source.parent.mkdir(parents=True)
            source.write_text("fixture native source\n", encoding="utf-8")
            manifest = json.dumps(
                {"os_adapter/linux/pyproject.toml": hashlib.sha256(source.read_bytes()).hexdigest()}
            )
            (installed / "_resources/manifest.json").write_text(manifest, encoding="utf-8")
            with (
                patch.object(paths, "__file__", str(installed / "paths.py")),
                patch.object(Path, "home", return_value=root / "home"),
            ):
                paths.repositoryRoot.cache_clear()
                self.assertIsNone(paths.checkoutRoot())
                materialized = paths.repositoryRoot()
                self.assertTrue(materialized.is_relative_to(root / "home/.computer-use/runtime"))
                self.assertEqual((materialized / "os_adapter/linux/pyproject.toml").read_bytes(), source.read_bytes())
                self.assertEqual(paths.repositoryRoot(), materialized)
                self.assertFalse((installed / "src").exists())

    def test_console_names_stay_stable_while_host_packages_are_separate(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            project["project"]["scripts"],
            {
                "computer-use-mcp": "computer_use_mcp.server:cli",
                "computer-use-build": "computer_use_native_tools.native:buildCli",
                "computer-use-permissions": "computer_use_native_tools.native:permissionsCli",
                "computer-use-linux-session": "computer_use_native_tools.native:linuxLauncherCli",
            },
        )


@unittest.skipIf(Distribution is None, "setuptools build dependency is not installed; run with uv --with setuptools")
class PackageMappingTests(unittest.TestCase):
    @staticmethod
    def configuration():
        with patch("setuptools.setup") as setup:
            namespace = runpy.run_path(str(ROOT / "setup.py"))
        return namespace, setup.call_args.kwargs

    def test_explicit_package_roots_do_not_discover_native_or_legacy_packages(self):
        _, configuration = self.configuration()
        self.assertEqual(configuration["package_dir"]["computer_use"], "computer_use_engine")
        self.assertEqual(configuration["package_dir"]["computer_use_mcp"], "interface_adapter/mcp")
        self.assertEqual(configuration["package_dir"]["computer_use_native_tools"], "scripts")
        packages = configuration["packages"]
        self.assertIn("computer_use.interaction.read", packages)
        self.assertEqual(set(configuration["package_dir"]), {"computer_use", "computer_use_mcp", "computer_use_native_tools"})
        forbidden = (
            "computer_use.backends",
            "computer_use_linux",
            "computer_use.mcp",
            "computer_use.react_demo",
            "computer_use.scripts",
            "computer_use.library",
            "computer_use.experimental",
        )
        self.assertFalse(
            [name for name in packages if any(name == old or name.startswith(old + ".") for old in forbidden)]
        )
        for package, source in configuration["package_dir"].items():
            self.assertTrue((ROOT / source / "__init__.py").is_file(), package)

    def test_reused_build_prunes_stale_modules_in_external_hosts_as_well_as_sdk(self):
        namespace, _ = self.configuration()
        command_type = namespace["BuildWithResources"]
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("sdk", "mcp"):
                source = root / name
                source.mkdir()
                (source / "__init__.py").write_text("current = True\n")
            distribution = Distribution(
                dict(
                    packages=["computer_use", "computer_use_mcp"],
                    package_dir={"computer_use": str(root / "sdk"), "computer_use_mcp": str(root / "mcp")},
                )
            )
            distribution.script_name = str(root / "setup.py")
            command = command_type(distribution)
            command.ensure_finalized()
            output = root / "build/lib"
            command.build_lib = str(output)
            for name in ("computer_use", "computer_use_mcp"):
                stale = output / name / "retired.py"
                stale.parent.mkdir(parents=True)
                stale.write_text("obsolete = True\n")
            retired = output / "retired_host"
            retired.mkdir()
            (retired / "__init__.py").write_text("obsolete = True\n")
            with patch.dict(command_type.run.__globals__, ROOT=root, DIRECTORIES=(), FILES=()):
                command.run()
            self.assertFalse(retired.exists())
            for name in ("computer_use", "computer_use_mcp"):
                self.assertFalse((output / name / "retired.py").exists())
                self.assertEqual((output / name / "__init__.py").read_text(), "current = True\n")


if __name__ == "__main__":
    unittest.main()
