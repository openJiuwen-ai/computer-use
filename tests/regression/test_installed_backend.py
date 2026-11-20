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
from computer_use.interaction.read.accessibility import _native as read_engine
from computer_use_native_tools import native

MAC_DRIVER = "os_adapter/macos/.build/Jiuwen CUA.app/Contents/MacOS/computer-use-driver"
WINDOWS_DRIVER = "os_adapter/windows/.build/computer-use-driver.exe"
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


@pytest.mark.parametrize(("system", "driver"), [("darwin", MAC_DRIVER), ("win32", WINDOWS_DRIVER)])
def test_platform_wheel_uses_cached_driver_without_compiling(installed, monkeypatch, system, driver):
    resources, home = installed
    monkeypatch.setattr(sys, "platform", system)
    (resources / "backend.json").write_text("{}", encoding="utf-8")
    executable = resources / driver
    executable.parent.mkdir(parents=True)
    executable.touch()

    def unexpected_compile(*args, **kwargs):
        pytest.fail("An installed platform wheel called the compiler.")

    monkeypatch.setattr(native, "run", unexpected_compile)
    monkeypatch.setattr(read_engine, "require_read_engine", unexpected_compile)
    cached = Path(paths.native_driver()["command"])
    assert cached.is_relative_to(home)
    assert cached.is_file()
    assert native.build_native() == cached


def test_windows_platform_wheel_build_reuses_verified_bundle(installed, monkeypatch):
    resources, home = installed
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    executable = resources / WINDOWS_DRIVER
    executable.parent.mkdir(parents=True)
    files = {}
    for name in (executable.name, "computer-use-capture.exe", "Interop.NativeUIA.dll"):
        path = executable.with_name(name)
        path.write_bytes(name.encode())
        files[path.relative_to(resources).as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": 0o755,
        }
    manifest = {"format": 1, "target": "windows-x86_64", "protocol": native.NATIVE_PROTOCOL_VERSION, "files": files}
    (resources / "backend.json").write_text(json.dumps(manifest), encoding="utf-8")

    def unexpected_build(*_args, **_kwargs):
        pytest.fail("Building a bundled driver must not materialize the cache or load the Rust read engine.")

    monkeypatch.setattr(native, "run", unexpected_build)
    monkeypatch.setattr(read_engine, "require_read_engine", unexpected_build)
    assert native.build_native() == executable.resolve()
    assert not (home / ".computer-use/runtime").exists()


def test_windows_source_build_finds_checkout_before_loading_read_engine(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    build_steps = []

    def unexpected_read_engine():
        pytest.fail("The source driver builder must not load the Rust read engine.")

    monkeypatch.setattr(read_engine, "require_read_engine", unexpected_read_engine)
    monkeypatch.setattr(native, "build_windows", build_steps.append)
    native.build_native()
    assert build_steps == [ROOT]


@pytest.mark.parametrize(
    ("system", "relative", "args", "version"),
    [
        ("win32", WINDOWS_DRIVER, [], []),
        ("linux", "os_adapter/linux/.venv/bin/python", ["-m", "computer_use_linux"], ["--protocol-version", "6"]),
    ],
)
def test_checkout_permissions_reports_missing_driver_before_loading_read_engine(
    monkeypatch,
    system,
    relative,
    args,
    version,
):
    def unexpected_read_engine():
        pytest.fail("The permission check must not load the Rust read engine.")

    def missing_driver(command, **_options):
        assert command == [str(ROOT / relative), *args, "--check-permissions", *version]
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(read_engine, "require_read_engine", unexpected_read_engine)
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(native.subprocess, "run", missing_driver)
    with pytest.raises(RuntimeError, match="本机后端尚未构建"):
        native.read_permissions(root=ROOT)


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
        distribution.validate_backend(ROOT, linux_backend(**overrides), "linux-x86_64", destination=destination)
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
def test_inspect_staged_backend_rejects_payload_corruption(
    distribution,
    linux_backend,
    tmp_path,
    corruption,
    message,
):
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
        ("cp311-cp311-any", "wrong platform tag"),
        ("cp311-cp311-linux_x86_64", "unexpected backend files"),
    ],
)
def test_assembly_rejects_invalid_built_wheel(distribution, linux_backend, tmp_path, monkeypatch, tag, message):
    from computer_use_native_tools import sdk_native

    staging = tmp_path / "staging"
    backend = linux_backend()
    engine = sdk_native.BUILD["build_for"]("linux-x86_64", "cp311")
    extension = tmp_path / engine.filename
    extension.write_bytes(b"read engine fixture")
    notice = sdk_native.BUILD["notice_path"](extension)
    notice.parent.mkdir()
    notice.write_bytes(b"generated third-party license fixture")
    engine_archive = backend.with_name(sdk_native.artifact_name(ROOT, "linux-x86_64", "cp311") + ".zip")
    sdk_native.write_archive(ROOT, engine, extension, engine_archive)

    def build(*_args, **kwargs):
        assert "COMPUTER_USE_BACKEND_ARCHIVE" not in kwargs["env"]
        assert kwargs["env"]["COMPUTER_USE_BACKEND_DIR"] == str(staging / "payload")
        wheel = staging / "openjiuwen_computeruse-0.0.1-cp311-cp311-linux_x86_64.whl"
        with ZipFile(wheel, "w") as archive:
            archive.writestr("openjiuwen_computeruse-0.0.1.dist-info/WHEEL", f"Tag: {tag}\n")
            archive.writestr("openjiuwen_computeruse-0.0.1.dist-info/METADATA", "Version: 0.0.1\n")

    monkeypatch.setenv("COMPUTER_USE_BACKEND_ARCHIVE", "inherited-backend.zip")
    monkeypatch.setattr(distribution.subprocess, "run", build)
    with pytest.raises(ValueError, match=message):
        distribution._assemble_platform(ROOT, backend, staging, "linux-x86_64")
