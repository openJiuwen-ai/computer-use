# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build one distribution from explicitly separated SDK and tool sources."""

import hashlib
import json
import os
import platform
import runpy
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py

try:
    from setuptools.command.bdist_wheel import bdist_wheel
except ModuleNotFoundError:
    from wheel.bdist_wheel import bdist_wheel

ROOT = Path(__file__).resolve().parent
BACKENDS = runpy.run_path(str(ROOT / "scripts/backend_artifacts.py"))
BACKEND_ARCHIVE = os.environ.get("COMPUTER_USE_BACKEND_ARCHIVE")
BACKEND_DIR = os.environ.get("COMPUTER_USE_BACKEND_DIR")
if BACKEND_ARCHIVE and BACKEND_DIR:
    raise ValueError("Select one backend archive or one prepared backend directory.")
if BACKEND_DIR:
    CLOUD_BACKENDS = runpy.run_path(str(ROOT / "scripts/cloud_build/backend_artifact.py"))
    PREPARED_BACKEND = CLOUD_BACKENDS["inspect_directory"](BACKEND_DIR)
    if PREPARED_BACKEND["protocol"] != BACKENDS["protocol_version"](ROOT):
        raise ValueError("Prepared backend protocol does not match this checkout.")

# Public imports retain stable, unambiguous package names while the checkout
# groups code by responsibility. Never scan the repository or native sources as
# one Python namespace: the Linux driver is an independent installable package.
PACKAGE_ROOTS = {
    "computer_use": "computer_use_engine",
    "computer_use_agent": "computer_use_agent",
    "computer_use_mcp": "interface_adapter/mcp",
    "computer_use_native_tools": "scripts",
}


def discover_packages():
    packages = []
    for package, directory in PACKAGE_ROOTS.items():
        packages.append(package)

        def _iter_child(_source_items: Iterator[Any], package_name: Any) -> Iterator[Any]:
            for child in _source_items:
                yield f"{package_name}.{child}"

        packages.extend(
            _iter_child(
                iter(
                    find_packages(
                        where=str(ROOT / directory),
                        exclude=("backends", "backends.*", "_resources", "_resources.*"),
                    )
                ),
                package,
            )
        )
    return sorted(packages)


DIRECTORIES = (
    "os_adapter/macos/Sources",
    "os_adapter/linux/src",
    "os_adapter/windows/Sources",
    "os_adapter/windows/Build",
    "os_adapter/windows/Workers",
    "os_adapter/assets/agent-pointer",
)
FILES = (
    "os_adapter/macos/Package.swift",
    "os_adapter/linux/pyproject.toml",
    "os_adapter/linux/requirements.lock",
)

RESOURCE_SUFFIXES = {".swift", ".cs", ".py", ".svg", ".json", ".md", ".html", ".css", ".js", ".mjs"}


def native_platform():
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in {"arm64", "x86_64"}:
        if tuple(map(int, platform.mac_ver()[0].split(".")[:2])) >= (14, 0):
            return f"macosx_14_0_{machine}"
    if sys.platform == "win32" and machine in {"amd64", "x86_64"}:
        return "win_amd64"
    if sys.platform == "linux" and machine in {"x86_64", "aarch64"}:
        return f"linux_{machine}"
    return None


class BuildWithResources(build_py):
    native_platform = None

    def run(self):
        # The build tree belongs to this distribution. Clear it as a whole so
        # retired top-level hosts cannot leak into a wheel after a relocation.
        output_root = Path(self.build_lib)
        resolved = output_root.resolve()
        names = {name.split(".", 1)[0] for name in self.packages or ()}
        sources = [Path(self.get_package_dir(name)).resolve() for name in names]
        if output_root.is_symlink() or any(
            resolved.is_relative_to(source) or source.is_relative_to(resolved) for source in sources
        ):
            raise RuntimeError("Build output must not overwrite a source package or a symlink.")
        if output_root.exists():
            shutil.rmtree(output_root)
        super().run()
        output = resolved / "computer_use" / "_resources"
        output.mkdir(parents=True, exist_ok=True)
        if BACKEND_DIR:
            shutil.copytree(BACKEND_DIR, output, dirs_exist_ok=True)
            return
        resources = [ROOT / name for name in FILES]
        for directory in DIRECTORIES:
            base = ROOT / directory

            def _iter_path(_source_items: Iterator[Any], resource_root: Any) -> Iterator[Any]:
                for path in _source_items:
                    if (
                        path.is_file()
                        and not any(
                            part.startswith(".") or part == "__pycache__"
                            for part in path.relative_to(resource_root).parts
                        )
                        and path.suffix in RESOURCE_SUFFIXES
                    ):
                        yield path

            resources.extend(_iter_path(iter(base.rglob("*")), base))
        manifest = {}
        for resource in sorted(set(resources)):
            relative = resource.relative_to(ROOT)
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resource, target)
            manifest[relative.as_posix()] = hashlib.sha256(resource.read_bytes()).hexdigest()
        if BACKEND_ARCHIVE:
            backend = BACKENDS["unpack_archive"](BACKEND_ARCHIVE, output, root=ROOT)
            for name in ["backend.json", *backend["files"]]:
                manifest[name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
        elif self.native_platform:
            subprocess.run(
                [
                    sys.executable, "-m", "computer_use_native_tools.native",
                    str(ROOT), str(output), self.native_platform,
                ],
                cwd=resolved,
                check=True,
            )
            backend = json.loads((output / "backend.json").read_bytes())
            for name in ["backend.json", *backend["files"]]:
                manifest[name] = hashlib.sha256((output / name).read_bytes()).hexdigest()
        (output / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )


class BackendWheel(bdist_wheel):
    def finalize_options(self):
        self.native_platform = None if BACKEND_ARCHIVE or BACKEND_DIR else native_platform()
        if self.native_platform is not None:
            self.plat_name = self.native_platform
        super().finalize_options()

    def run(self):
        self.distribution.get_command_obj("build_py").native_platform = self.native_platform
        super().run()

    def get_tag(self):
        if BACKEND_DIR:
            return "py3", "none", CLOUD_BACKENDS["WHEEL_PLATFORMS"][PREPARED_BACKEND["target"]]
        if BACKEND_ARCHIVE:
            backend = BACKENDS["inspect_archive"](BACKEND_ARCHIVE, root=ROOT)
            return "py3", "none", BACKENDS["TARGETS"][backend["target"]][2]
        return super().get_tag()


setup(
    packages=discover_packages(),
    package_dir=PACKAGE_ROOTS,
    cmdclass={"build_py": BuildWithResources, "bdist_wheel": BackendWheel},
)
