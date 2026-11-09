# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Portable backend archives shared by the native builders and wheel assembly."""

import ast
import hashlib
import json
import platform
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath

FORMAT = 1
TARGETS = {
    "macos-arm64": ("darwin", "arm64", "macosx_14_0_arm64"),
    "macos-x86_64": ("darwin", "x86_64", "macosx_14_0_x86_64"),
    "windows-x86_64": ("win32", "x86_64", "win_amd64"),
    "linux-x86_64": ("linux", "x86_64", "linux_x86_64"),
    "linux-aarch64": ("linux", "arm64", "linux_aarch64"),
}
GENERATED = {"AgentPointerAssets.swift", "AgentPointerAssets.cs"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def protocol_version(root):
    tree = ast.parse((Path(root) / "computer_use_engine/protocol/session.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "NATIVE_PROTOCOL_VERSION" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("Native protocol version is missing.")


def host_target():
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64" if machine in ("amd64", "x86_64") else machine
    for target, (system, architecture, _) in TARGETS.items():
        if system == sys.platform and arch == architecture:
            return target
    raise ValueError(f"Unsupported native build host: {sys.platform}/{machine}")


def fingerprint(root, target):
    root = Path(root)
    system = {"darwin": "macos", "win32": "windows", "linux": "linux"}[TARGETS[target][0]]
    files = [root / name for name in ("scripts/native.py", "scripts/backend_artifacts.py", "scripts/distribution.py")]
    directories = [root / f"os_adapter/{system}"]
    if system != "linux":
        files.extend((
            root / "scripts/pointer.py",
            root / "computer_use_engine/json.py",
            root / "computer_use_engine/interaction/read/presentation/formatting.py",
        ))
        directories.append(root / "os_adapter/assets/agent-pointer")
    for directory in directories:
        for path in directory.rglob("*"):
            if not path.is_file() or path.name in GENERATED:
                continue
            if not any(
                part.startswith(".") or part in ("__pycache__", "build", "dist") or part.endswith(".egg-info")
                for part in path.relative_to(directory).parts
            ):
                files.append(path)
    inputs = {
        path.relative_to(root).as_posix(): sha256(path.read_bytes().replace(b"\r\n", b"\n"))
        for path in sorted(set(files))
    }
    return sha256(canonical({"format": FORMAT, "target": target, "protocol": protocol_version(root), "inputs": inputs}))


def artifact_name(root, target):
    return f"backend-{target}-{fingerprint(root, target)}"


def payload_allowed(name, target):
    if target.startswith("macos-"):
        return name.startswith("os_adapter/macos/.build/ComputerUseDriver.app/")
    if target.startswith("windows-"):
        return name in {
            "os_adapter/windows/.build/computer-use-driver.exe",
            "os_adapter/windows/.build/computer-use-capture.exe",
            "os_adapter/windows/.build/Interop.NativeUIA.dll",
        }
    return PurePosixPath(name).parent.as_posix() == "os_adapter/linux/.build" and name.endswith(".whl")


def write_archive(root, target, paths, output, *, source_commit):
    root, output = Path(root), Path(output)
    files = {}
    for path in sorted(paths):
        name = path.relative_to(root).as_posix()
        if path.is_symlink() or not payload_allowed(name, target):
            raise ValueError(f"Unsupported backend payload: {name}")
        files[name] = {"sha256": sha256(path.read_bytes()), "mode": 0o755 if path.stat().st_mode & 0o111 else 0o644}
    manifest = {
        "format": FORMAT,
        "target": target,
        "fingerprint": fingerprint(root, target),
        "protocol": protocol_version(root),
        "source_commit": source_commit,
        "builder": {"system": platform.platform(), "python": platform.python_version()},
        "files": files,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backend.json", canonical(manifest))
        for name in files:
            archive.write(root / name, name)
    # Apply the same validation used by SDK assembly before accepting build output.
    inspect_archive(output, root=root, target=target)
    return manifest


def inspect_archive(path, *, root=None, target=None):
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("backend.json"))
        actual_target = manifest["target"]
        if manifest["format"] != FORMAT or actual_target not in TARGETS:
            raise ValueError("Unsupported backend artifact format or target.")
        if target is not None and actual_target != target:
            raise ValueError(f"Backend target mismatch: expected {target}, got {actual_target}.")
        if root is not None and (
            manifest["fingerprint"] != fingerprint(root, actual_target)
            or manifest["protocol"] != protocol_version(root)
        ):
            raise ValueError("Backend artifact does not match this checkout's native sources/protocol; rebuild it.")
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != {"backend.json", *manifest["files"]}:
            raise ValueError("Backend artifact file list is inconsistent.")
        for name, details in manifest["files"].items():
            path = PurePosixPath(name)
            info = archive.getinfo(name)
            if path.is_absolute() or ".." in path.parts or "\\" in name:
                raise ValueError(f"Invalid backend artifact file: {name}")
            if not payload_allowed(name, actual_target) or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"Invalid backend artifact file: {name}")
            if details["mode"] not in (0o644, 0o755) or sha256(archive.read(name)) != details["sha256"]:
                raise ValueError(f"Invalid backend artifact file: {name}")
        required = {
            "darwin": "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver",
            "win32": "os_adapter/windows/.build/computer-use-driver.exe",
        }.get(TARGETS[actual_target][0])
        if not manifest["files"] or (required and required not in manifest["files"]):
            raise ValueError("Backend artifact has no driver.")
        if actual_target.startswith("windows-") and len(manifest["files"]) != 3:
            raise ValueError("Windows backend requires driver, capture helper and UIA interop DLL.")
        if required and actual_target.startswith("macos-") and manifest["files"][required]["mode"] != 0o755:
            raise ValueError("macOS driver must be executable.")
        return manifest


def unpack_archive(path, destination, *, root, target=None):
    manifest = inspect_archive(path, root=root, target=target)
    destination = Path(destination)
    with zipfile.ZipFile(path) as archive:
        for name, details in manifest["files"].items():
            output = destination / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(archive.read(name))
            output.chmod(details["mode"])
    (destination / "backend.json").write_bytes(canonical(manifest))
    return manifest
