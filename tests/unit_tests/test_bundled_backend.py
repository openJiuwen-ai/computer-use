# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import hashlib
import json
import platform
import sys
from importlib import import_module
from pathlib import Path
from zipfile import ZipFile

import pytest
from computer_use import paths
from computer_use.protocol.session import NATIVE_PROTOCOL_VERSION
from computer_use_native_tools import native

MAC_DRIVER = "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver"
WINDOWS_DRIVER = "os_adapter/windows/.build/computer-use-driver.exe"
WINDOWS_FILES = {
    WINDOWS_DRIVER: b"driver",
    "os_adapter/windows/.build/computer-use-capture.exe": b"capture",
    "os_adapter/windows/.build/Interop.NativeUIA.dll": b"interop",
}
LINUX_WHEEL = "os_adapter/linux/.build/openjiuwen_computeruse_linux-0.0.1-py3-none-any.whl"
LINUX_LOCK = "os_adapter/linux/requirements.lock"
ROOT = Path(__file__).resolve().parents[2]
COMMIT = "a" * 40


@pytest.fixture
def installed(tmp_path, monkeypatch):
    resources = tmp_path / "site-packages/computer_use/_resources"
    resources.mkdir(parents=True)
    (resources / "manifest.json").write_text("{}", encoding="utf-8")
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(paths, "__file__", str(resources.parent / "paths.py"))
    paths.repository_root.cache_clear()
    yield resources, home
    paths.repository_root.cache_clear()


def write_installed_backend(resources, target, files):
    members = {}
    for name, data in files.items():
        path = resources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        members[name] = {"mode": 0o755 if name == MAC_DRIVER else 0o644, "sha256": hashlib.sha256(data).hexdigest()}
    (resources / "backend.json").write_text(
        json.dumps({"format": 1, "target": target, "protocol": NATIVE_PROTOCOL_VERSION, "files": members}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("system", "machine", "target", "driver"),
    [("darwin", "arm64", "macos-arm64", MAC_DRIVER), ("win32", "x86_64", "windows-x86_64", WINDOWS_DRIVER)],
)
def test_platform_wheel_uses_cached_driver_without_compiling(installed, monkeypatch, system, machine, target, driver):
    resources, home = installed
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    mac_info = MAC_DRIVER.removesuffix("MacOS/computer-use-driver") + "Info.plist"
    files = {MAC_DRIVER: b"driver", mac_info: b"bundle metadata"} if system == "darwin" else WINDOWS_FILES
    write_installed_backend(resources, target, files)
    monkeypatch.setattr(paths, "_windows_executables_fit", lambda directory: True)

    def unexpected_compile(*args, **kwargs):
        pytest.fail("An installed platform wheel called the compiler.")

    monkeypatch.setattr(native, "run", unexpected_compile)
    cached = Path(paths.native_driver()["command"])
    assert cached.is_relative_to(home)
    assert cached == paths.repository_root() / driver
    assert cached.is_file()
    assert native.build_native() == cached


def test_windows_platform_wheel_build_reuses_verified_short_bundle(installed, monkeypatch):
    resources, home = installed
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    write_installed_backend(resources, "windows-x86_64", WINDOWS_FILES)
    monkeypatch.setattr(paths, "_windows_executables_fit", lambda directory: directory.is_relative_to(resources.resolve()))

    assert native.build_native() == (resources / WINDOWS_DRIVER).resolve()
    assert not (home / ".computer-use/runtime").exists()


def test_linux_platform_wheel_prepares_venv_outside_site_packages(installed, monkeypatch):
    resources, home = installed
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    write_installed_backend(resources, "linux-x86_64", {LINUX_WHEEL: b"backend", LINUX_LOCK: b"python-xlib==0.33\n"})
    commands = []

    def run(command, args, cwd):
        commands.append(tuple(map(str, args)))
        if "venv" in args:
            (Path(args[-1]) / "bin").mkdir(parents=True)

    monkeypatch.setattr(native, "run", run)
    executable = native.build_native()
    root = paths.repository_root()
    assert root.is_relative_to(home)
    assert executable == root / "os_adapter/linux/.venv/bin/python"
    assert paths.native_driver()["command"] == str(executable)
    assert not list(resources.rglob(".venv"))
    wheel = str((root / LINUX_WHEEL).resolve())
    assert f"sys.path.insert(0, {wheel!r})" in commands[0][-1]
    assert ("-m", "pip", "install", "--only-binary=:all:", "-r", "requirements.lock") in commands
    assert ("-m", "pip", "install", "--no-deps", wheel) in commands


def test_linux_platform_wheel_reports_missing_backend(installed, monkeypatch):
    resources, _ = installed
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    write_installed_backend(resources, "linux-x86_64", {})

    with pytest.raises(RuntimeError, match="Bundled Linux backend wheel is missing"):
        native.build_native()


def test_source_wheel_keeps_a_writable_build_tree(installed):
    resources, home = installed
    source = resources / "os_adapter/macos/Package.swift"
    source.parent.mkdir(parents=True)
    source.write_text("// build input", encoding="utf-8")
    root = paths.repository_root()
    assert root.is_relative_to(home)
    assert (root / source.relative_to(resources)).read_bytes() == source.read_bytes()


@pytest.fixture
def distribution(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts/cloud_build"))
    return import_module("distribution")


@pytest.fixture
def linux_backend(tmp_path, distribution):
    def create(*, payload=b"backend", **overrides):
        files = {LINUX_WHEEL: b"backend", LINUX_LOCK: b"python-xlib==0.33\n"}
        manifest = {
            "format": 1,
            "target": "linux-x86_64",
            "source_commit": COMMIT,
            "fingerprint": distribution.fingerprint(ROOT, "linux-x86_64"),
            "protocol": distribution.protocol_version(ROOT),
            "files": {name: {"mode": 0o644, "sha256": distribution.digest(data)} for name, data in files.items()},
            **overrides,
        }
        path = tmp_path / "linux-x86_64.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("backend.json", json.dumps(manifest))
            archive.writestr(LINUX_LOCK, files[LINUX_LOCK])
            archive.writestr(LINUX_WHEEL, payload)
        return path

    return create


def test_reuses_backend_from_another_commit_with_matching_native_inputs(distribution, linux_backend):
    manifest = distribution.validate_backend(ROOT, linux_backend(), "linux-x86_64")
    assert manifest["source_commit"] == COMMIT


def test_sets_version_in_staged_pyproject(distribution, tmp_path):
    project = tmp_path / "pyproject.toml"
    project.write_text((ROOT / project.name).read_text(encoding="utf-8"), encoding="utf-8")
    distribution.set_package_version(project, "0.0.1.dev741")
    assert 'version = "0.0.1.dev741"' in project.read_text(encoding="utf-8")
    assert 'version = "0.0.1"' in (ROOT / project.name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"fingerprint": "wrong"}, "does not match this checkout"),
        ({"protocol": 0}, "does not match this checkout"),
        ({"payload": b"corrupted"}, "Backend checksum mismatch"),
    ],
)
def test_rejects_mismatched_backend(distribution, linux_backend, tmp_path, overrides, message):
    destination = tmp_path / "payload"
    destination.mkdir()
    with pytest.raises(ValueError, match=message):
        distribution.validate_backend(
            ROOT, linux_backend(**overrides), "linux-x86_64", destination=destination
        )
    assert not list(destination.iterdir())


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ("extra", "Unexpected backend files"),
        ("cache", "cache manifest does not match"),
        ("data", "Backend checksum mismatch"),
        ("link", "contains a link"),
    ],
)
def test_inspect_staged_backend_rejects_payload_corruption(distribution, linux_backend, tmp_path, corruption, message):
    artifacts = import_module("backend_artifact")
    payload = tmp_path / "payload"
    payload.mkdir()
    manifest = distribution.validate_backend(ROOT, linux_backend(), "linux-x86_64", destination=payload)
    cache = {name: entry["sha256"] for name, entry in manifest["files"].items()}
    cache["backend.json"] = artifacts.digest((payload / "backend.json").read_bytes())
    (payload / "manifest.json").write_bytes(artifacts.canonical(cache))
    assert artifacts.inspect_directory(payload) == manifest

    if corruption == "extra":
        (payload / "unexpected.py").write_text("", encoding="utf-8")
    elif corruption == "cache":
        (payload / "manifest.json").write_text("{}", encoding="utf-8")
    elif corruption == "data":
        (payload / LINUX_WHEEL).write_bytes(b"corrupted")
    else:
        outside = tmp_path / "outside.whl"
        (payload / LINUX_WHEEL).replace(outside)
        try:
            (payload / LINUX_WHEEL).symlink_to(outside)
        except OSError as error:
            pytest.skip(f"Creating a symlink is unavailable: {error}")
    with pytest.raises(ValueError, match=message):
        artifacts.inspect_directory(payload)


@pytest.mark.parametrize(
    ("tag", "message"),
    [
        ("py3-none-any", "wrong platform tag"),
        ("py3-none-linux_x86_64", "unexpected backend files"),
    ],
)
def test_assembly_rejects_invalid_built_wheel(distribution, linux_backend, tmp_path, monkeypatch, tag, message):
    staging = tmp_path / "staging"

    def build(*_args, **kwargs):
        assert "COMPUTER_USE_BACKEND_ARCHIVE" not in kwargs["env"]
        assert kwargs["env"]["COMPUTER_USE_BACKEND_DIR"] == str(staging / "payload")
        wheel = staging / "openjiuwen_computeruse-0.0.1-py3-none-linux_x86_64.whl"
        with ZipFile(wheel, "w") as archive:
            archive.writestr("openjiuwen_computeruse-0.0.1.dist-info/WHEEL", f"Tag: {tag}\n")
            archive.writestr("openjiuwen_computeruse-0.0.1.dist-info/METADATA", "Version: 0.0.1\n")

    monkeypatch.setenv("COMPUTER_USE_BACKEND_ARCHIVE", "inherited-backend.zip")
    monkeypatch.setattr(distribution.subprocess, "run", build)
    with pytest.raises(ValueError, match=message):
        distribution._assemble_platform(ROOT, linux_backend(), staging, "linux-x86_64")
