# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build-time contract for backend artifacts exchanged by the build jobs."""

import hashlib
import json
import platform
import re
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

WHEEL_PLATFORMS = {
    "macos-arm64": "macosx_14_0_arm64",
    "windows-x86_64": "win_amd64",
    "linux-x86_64": "linux_x86_64",
}
TARGETS = tuple(WHEEL_PLATFORMS)
PLATFORMS = dict(zip((("darwin", "arm64"), ("win32", "x86_64"), ("linux", "x86_64")), TARGETS))
FORMAT = 1

MAC_BUNDLE = "os_adapter/macos/.build/ComputerUseDriver.app"
MAC_DRIVER = f"{MAC_BUNDLE}/Contents/MacOS/computer-use-driver"
WINDOWS_FILES = {
    f"os_adapter/windows/.build/{name}"
    for name in ("computer-use-driver.exe", "computer-use-capture.exe", "Interop.NativeUIA.dll")
}
LINUX_LOCK = "os_adapter/linux/requirements.lock"
LINUX_WHEEL = re.compile(r"os_adapter/linux/\.build/openjiuwen_computeruse_linux-[\w.+!-]+-py3-none-any\.whl")


def host_target(system=None):
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "aarch64": "arm64"}.get(machine, machine)
    system = system or sys.platform
    try:
        return PLATFORMS[system, machine]
    except KeyError:
        raise RuntimeError(f"Cannot build a backend for {system}/{machine}. Supported: {', '.join(TARGETS)}.") from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _payload_files(manifest, target):
    if manifest["format"] != FORMAT or manifest["target"] != target or target not in TARGETS:
        raise ValueError(f"Invalid backend format or target: {target}")
    files = manifest["files"]
    names = set(files)
    if target == "macos-arm64":
        valid = {MAC_DRIVER, f"{MAC_BUNDLE}/Contents/Info.plist"} <= names and all(
            name.startswith(f"{MAC_BUNDLE}/Contents/") for name in names
        )
        valid = valid and files[MAC_DRIVER]["mode"] == 0o755
    elif target == "windows-x86_64":
        valid = names == WINDOWS_FILES
    else:
        valid = (
            LINUX_LOCK in names
            and len(names) == 2
            and all(name == LINUX_LOCK or LINUX_WHEEL.fullmatch(name) for name in names)
        )
    if not valid:
        raise ValueError(f"Unexpected backend files for {target}")
    folded = set()
    for name, entry in files.items():
        relative = PurePosixPath(name)
        if relative.as_posix() != name or relative.is_absolute() or any(char in name for char in '\\:<>"|?*'):
            raise ValueError(f"Invalid backend path: {name}")
        if any(
            part == ".." or part.endswith((" ", ".")) or PureWindowsPath(part).is_reserved()
            for part in relative.parts
        ) or name.casefold() in folded:
            raise ValueError(f"Invalid backend path: {name}")
        folded.add(name.casefold())
        if entry["mode"] not in (0o644, 0o755):
            raise ValueError(f"Invalid backend file mode: {name}")
    return files


def _check_file(name, entry, data):
    if digest(data) != entry["sha256"]:
        raise ValueError(f"Backend checksum mismatch: {name}")


def inspect_archive(path, target, *, destination=None, expected_inputs=None):
    """Validate the archive before optionally unpacking it into a private staging directory."""
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("backend.json"))
        files = _payload_files(manifest, target)
        if expected_inputs is not None and (manifest["fingerprint"], manifest["protocol"]) != expected_inputs:
            raise ValueError(f"{target} backend does not match this checkout's native inputs/protocol.")
        if set(archive.namelist()) != {"backend.json", *files} or len(archive.infolist()) != len(files) + 1:
            raise ValueError(f"Unexpected backend files for {target}")
        for name, entry in files.items():
            if stat.S_ISLNK(archive.getinfo(name).external_attr >> 16):
                raise ValueError(f"Invalid backend file type: {name}")
            _check_file(name, entry, archive.read(name))
        if destination is not None:
            destination = Path(destination)
            for name, entry in files.items():
                output = destination / name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(archive.read(name))
                output.chmod(entry["mode"])
            (destination / "backend.json").write_bytes(canonical(manifest))
    return manifest


def inspect_directory(path):
    """Validate a staged wheel payload, including its runtime cache manifest."""
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Backend payload must be a real directory.")
    root = root.resolve()
    actual = set()
    for item in root.rglob("*"):
        if item.is_symlink() or item.resolve() != item:
            raise ValueError(f"Backend payload contains a link: {item.relative_to(root)}")
        if item.is_file():
            actual.add(item.relative_to(root).as_posix())
        elif not item.is_dir():
            raise ValueError(f"Invalid backend payload entry: {item.relative_to(root)}")
    if not {"backend.json", "manifest.json"} <= actual:
        raise ValueError("Backend payload is missing its manifests.")
    metadata = (root / "backend.json").read_bytes()
    manifest = json.loads(metadata)
    files = _payload_files(manifest, manifest["target"])
    if actual != {"backend.json", "manifest.json", *files}:
        raise ValueError(f"Unexpected backend files for {manifest['target']}")
    cache = {name: entry["sha256"] for name, entry in files.items()}
    cache["backend.json"] = digest(metadata)
    if json.loads((root / "manifest.json").read_bytes()) != cache:
        raise ValueError("Backend cache manifest does not match the payload.")
    for name, entry in files.items():
        _check_file(name, entry, (root / name).read_bytes())
    return manifest
