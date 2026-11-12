# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build one distribution from explicitly separated SDK and tool sources."""

import hashlib
import json
import logging
import os
import platform
import runpy
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from setuptools import Extension, find_packages, setup
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py

try:
    from setuptools.command.bdist_wheel import bdist_wheel
except ModuleNotFoundError:
    from wheel.bdist_wheel import bdist_wheel

ROOT = Path(__file__).resolve().parent
_LOG = logging.getLogger(__name__)
BACKENDS = runpy.run_path(str(ROOT / "scripts/backend_artifacts.py"))
READ_ENGINE = runpy.run_path(str(ROOT / "scripts/read_engine_build.py"))
READ_ENGINE_MODULE = "computer_use.interaction.read.accessibility._ax_read"
BACKEND_ARCHIVE = os.environ.get("COMPUTER_USE_BACKEND_ARCHIVE")
BACKEND_DIR = os.environ.get("COMPUTER_USE_BACKEND_DIR")
if BACKEND_ARCHIVE and BACKEND_DIR:
    raise ValueError("Select one backend archive or one prepared backend directory.")
BACKEND_TARGET = None
if BACKEND_DIR:
    CLOUD_BACKENDS = runpy.run_path(str(ROOT / "scripts/cloud_build/backend_artifact.py"))
    PREPARED_BACKEND = CLOUD_BACKENDS["inspect_directory"](BACKEND_DIR)
    if PREPARED_BACKEND["protocol"] != BACKENDS["protocol_version"](ROOT):
        raise ValueError("Prepared backend protocol does not match this checkout.")
    BACKEND_TARGET = PREPARED_BACKEND["target"]
elif BACKEND_ARCHIVE:
    ARCHIVED_BACKEND = BACKENDS["inspect_archive"](BACKEND_ARCHIVE, root=ROOT)
    BACKEND_TARGET = ARCHIVED_BACKEND["target"]

ENGINE_ARCHIVE = os.environ.get("COMPUTER_USE_SDK_NATIVE_ARCHIVE")
if ENGINE_ARCHIVE:
    SDK_NATIVE = runpy.run_path(str(ROOT / "scripts/sdk_native.py"))
    ENGINE_MANIFEST = SDK_NATIVE["inspect_archive"](ROOT, ENGINE_ARCHIVE)
    if ENGINE_MANIFEST["target"] != BACKEND_TARGET:
        raise ValueError("The Rust read engine artifact requires a matching prepared driver.")
    ENGINE_TAG, ENGINE_FILENAME = tuple(ENGINE_MANIFEST["wheel_tag"]), ENGINE_MANIFEST["filename"]
else:
    ENGINE_BUILD = READ_ENGINE["host_build"]()
    if ENGINE_BUILD is None:
        raise RuntimeError(READ_ENGINE["HOST_REQUIREMENT"])
    if BACKEND_TARGET is not None and ENGINE_BUILD.backend_target != BACKEND_TARGET:
        raise RuntimeError(READ_ENGINE["HOST_REQUIREMENT"] + " For cross-host wheel assembly, supply its SDK archive.")
    ENGINE_TAG, ENGINE_FILENAME = ENGINE_BUILD.wheel_tag, ENGINE_BUILD.filename

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


def publish_source_backend(staging: Path, output: Path, platform_tag: str) -> dict[str, str]:
    """Validate the staged files and publish the native build directory."""
    metadata = staging / "backend.json"
    contents = metadata.read_bytes()
    backend = json.loads(contents)
    target, files = backend["target"], backend["files"]
    if (
        backend["format"] != BACKENDS["FORMAT"]
        or backend["protocol"] != BACKENDS["protocol_version"](ROOT)
        or BACKENDS["TARGETS"][target][2] != platform_tag
    ):
        raise ValueError("Invalid source backend manifest.")
    if not isinstance(files, dict) or not files:
        raise ValueError("Invalid source backend manifest.")

    build_path = Path("os_adapter", target.split("-", 1)[0], ".build")
    payload = staging / build_path
    manifest = {}
    for path in payload.rglob("*"):
        if path.is_symlink():
            raise ValueError("Source backend payload contains a symlink.")
        if path.is_file():
            manifest[path.relative_to(staging).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    if manifest != {name: details["sha256"] for name, details in files.items()}:
        raise ValueError("Source backend payload does not match its manifest.")
    if any(details["mode"] not in (0o644, 0o755) for details in files.values()):
        raise ValueError("Invalid source backend file mode.")

    manifest[metadata.name] = hashlib.sha256(contents).hexdigest()
    destination = output / build_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    metadata.replace(output / metadata.name)
    # Publish the driver in one move, after every other fallible step.
    payload.replace(destination)
    return manifest


def install_source_backend(build_lib: Path, output: Path, platform_tag: str | None) -> dict[str, str]:
    """Add the locally built driver or record why only the SDK is installed."""
    reason = "unsupported_platform"
    if platform_tag:
        try:
            with TemporaryDirectory(
                prefix="native-", dir=build_lib.parent, ignore_cleanup_errors=True,
            ) as temporary:
                staging = Path(temporary)
                subprocess.run(
                    [
                        sys.executable, "-m", "computer_use_native_tools.native",
                        str(ROOT), str(staging), platform_tag,
                    ],
                    cwd=build_lib,
                    check=True,
                )
                return publish_source_backend(staging, output, platform_tag)
        except (subprocess.CalledProcessError, OSError, KeyError, TypeError, ValueError):
            (output / "backend.json").unlink(missing_ok=True)
            _LOG.debug("Could not assemble the source-built Computer Use driver.", exc_info=True)
            reason = "native_build_failed"
    status_file = "backend-status.json"
    status = json.dumps({"reason": reason}).encode("utf-8")
    (output / status_file).write_bytes(status)
    _LOG.warning("Computer Use driver unavailable (%s). Installing the Python SDK.", reason)
    return {status_file: hashlib.sha256(status).hexdigest()}


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
        else:
            manifest.update(install_source_backend(resolved, output, self.native_platform))
        (output / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
        )


class BuildReadEngine(build_ext):
    def get_ext_filename(self, ext_name):
        filename = Path(super().get_ext_filename(ext_name))
        return str(filename.with_name(ENGINE_FILENAME))

    def build_extension(self, extension):
        output = Path(self.get_ext_fullpath(extension.name))
        if ENGINE_ARCHIVE:
            data, notice = SDK_NATIVE["read_payload"](ENGINE_ARCHIVE, ENGINE_MANIFEST)
            destination = READ_ENGINE["notice_path"](output)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(notice)
            output.write_bytes(data)
        else:
            READ_ENGINE["build_read_engine"](ROOT, output)


class BackendWheel(bdist_wheel):
    def finalize_options(self):
        self.native_platform = None if BACKEND_ARCHIVE or BACKEND_DIR else native_platform()
        platform_tag = ENGINE_TAG[2]
        if self.plat_name not in (None, platform_tag):
            raise ValueError(f"The read engine requires wheel platform {platform_tag}.")
        self.plat_name = platform_tag
        super().finalize_options()

    def run(self):
        self.distribution.get_command_obj("build_py").native_platform = self.native_platform
        super().run()

    def get_tag(self):
        if ENGINE_ARCHIVE:
            return ENGINE_TAG
        tag = super().get_tag()
        if tag != ENGINE_TAG:
            raise ValueError(f"Wheel has tag {tag}, expected {ENGINE_TAG} for its Rust read engine.")
        return tag


setup(
    packages=discover_packages(),
    package_dir=PACKAGE_ROOTS,
    ext_modules=[Extension(READ_ENGINE_MODULE, sources=[])],
    cmdclass={
        "build_py": BuildWithResources,
        "bdist_wheel": BackendWheel,
        "build_ext": BuildReadEngine,
    },
)
