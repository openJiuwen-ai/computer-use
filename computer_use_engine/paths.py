# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path
from time import sleep

LINUX_RUNTIME = Path("os_adapter/linux/.build/runtime")
_WINDOWS_BUILD = Path("os_adapter/windows/.build")
_WINDOWS_DRIVER = "computer-use-driver.exe"
_WINDOWS_CAPTURE = "computer-use-capture.exe"
_WINDOWS_FILES = (_WINDOWS_DRIVER, _WINDOWS_CAPTURE, "Interop.NativeUIA.dll")
# The bundled .NET Framework executables exit before Main at 253 UTF-16 units.
_WINDOWS_FRAMEWORK_PATH_MAX = 252


def linux_python():
    command = os.environ.get("COMPUTER_USE_PYTHON")
    if command:
        return shutil.which(command) or command
    system_python = shutil.which("/usr/bin/python3")
    if system_python is not None:
        probe = subprocess.run(
            [
                system_python, "-I", "-S", "-c",
                "import sys; sys.exit(sys.implementation.name != 'cpython' or sys.version_info < (3, 11))",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            return system_python
    return sys.executable


def checkout_root():
    root = Path(__file__).resolve().parents[1]
    return root if (root / "pyproject.toml").is_file() and (root / "os_adapter").is_dir() else None


def _publish_runtime(temporary, target, *, windows):
    # Windows can deny a directory rename while a reader holds any staged file.
    delays = (0.02, 0.04, 0.08, 0.16, 0.32) if windows else ()
    for delay in (*delays, None):
        try:
            os.rename(temporary, target)
        except OSError as error:
            if target.is_dir():
                return
            if delay is None or getattr(error, "winerror", None) not in (5, 32):
                raise
        else:
            return
        sleep(delay)


def _installed_runtime_target(assets):
    manifest = (assets / "manifest.json").read_bytes()
    key = hashlib.sha256(manifest).hexdigest()[:20]
    return Path.home() / ".computer-use/runtime" / key


def _windows_executables_fit(directory):
    for name in (_WINDOWS_DRIVER, _WINDOWS_CAPTURE):
        path = directory / name
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            path = path.absolute()
        if len(str(path).encode("utf-16-le", "surrogatepass")) // 2 > _WINDOWS_FRAMEWORK_PATH_MAX:
            return False
    return True


def _short_windows_bundled_driver(assets):
    """Use only the installed, co-located, manifest-verified release bundle."""
    from computer_use.protocol.session import NATIVE_PROTOCOL_VERSION

    try:
        root = assets.resolve(strict=True)
        directory = (root / _WINDOWS_BUILD).resolve(strict=True)
        if root.parent != assets.parent.resolve() or not directory.is_relative_to(root):
            return None
        if not _windows_executables_fit(directory):
            return None
        manifest_path = root / "backend.json"
        if manifest_path.resolve(strict=True) != manifest_path:
            return None
        manifest = json.loads(manifest_path.read_bytes())
        if not isinstance(manifest, dict) or (
            manifest.get("format"), manifest.get("target"), manifest.get("protocol")
        ) != (1, "windows-x86_64", NATIVE_PROTOCOL_VERSION):
            return None
        files = manifest.get("files")
        expected = {(_WINDOWS_BUILD / name).as_posix(): directory / name for name in _WINDOWS_FILES}
        if not isinstance(files, dict) or files.keys() != expected.keys():
            return None
        for name, path in expected.items():
            if path.resolve(strict=True) != path:
                return None
            with path.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != files[name]["sha256"]:
                    return None
        return directory / _WINDOWS_DRIVER
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        return None


@lru_cache(maxsize=1)
def repository_root():
    root = checkout_root()
    if root is not None:
        return root
    # Wheels carry build inputs and optionally a matching prebuilt backend.
    # Materialize them into a versioned user-owned runtime directory.
    assets = Path(__file__).parent / "_resources"
    target = _installed_runtime_target(assets)
    if target.is_dir():
        return target
    directory = target.parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = Path(tempfile.mkdtemp(prefix=".prepare-", dir=directory))
    windows = sys.platform == "win32"
    primary_error = None
    try:
        shutil.copytree(assets, temporary, dirs_exist_ok=True)
        backend = temporary / "backend.json"
        if backend.is_file():
            # Wheel installers need not preserve archive executable bits.
            metadata = json.loads(backend.read_text(encoding="utf-8"))
            files = metadata["files"] if "format" in metadata else metadata.get("files", {})
            for name, details in files.items():
                (temporary / name).chmod(details["mode"])
        _publish_runtime(temporary, target, windows=windows)
        return target
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if temporary.exists():
            try:
                shutil.rmtree(temporary)
            except OSError as cleanup_error:
                if not windows or primary_error is None:
                    raise
                primary_error.add_note(f"Could not remove staged runtime {temporary}: {cleanup_error}")


def native_driver(platform=None, root=None):
    platform = platform or sys.platform
    if root is None and platform == sys.platform == "win32" and checkout_root() is None:
        assets = Path(__file__).parent / "_resources"
        if not _windows_executables_fit(_installed_runtime_target(assets) / _WINDOWS_BUILD):
            bundled = _short_windows_bundled_driver(assets)
            if bundled is not None:
                return {"command": str(bundled), "args": []}
    root = Path(root) if root is not None else repository_root()
    if platform == "darwin":
        return dict(
            command=str(root / "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver"),
            args=[],
        )
    if platform == "linux":
        if (root / LINUX_RUNTIME / "__main__.py").is_file():
            # CPython adds the entry directory itself to the import path, including with -I.
            return {"command": linux_python(), "args": ["-I", str(root / LINUX_RUNTIME)]}
        return dict(command=str(root / "os_adapter/linux/.venv/bin/python"), args=["-m", "computer_use_linux"])
    if platform == "win32":
        return dict(command=str(root / _WINDOWS_BUILD / _WINDOWS_DRIVER), args=[])
    raise RuntimeError(f"platform {platform} is not implemented")
