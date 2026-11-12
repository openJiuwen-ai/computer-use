# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import hashlib
import json
import runpy
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from zipfile import ZipFile

import pytest
import setuptools
from computer_use import paths
from computer_use.errors import ComputerUseError
from computer_use.native.connection import Connection
from computer_use_native_tools import native

MAC_DRIVER = "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver"
MAC_TAG = "macosx_14_0_arm64"
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
    cached = Path(paths.native_driver()["command"])
    assert cached.is_relative_to(home)
    assert cached.is_file()
    assert native.build_native() == cached


def test_linux_platform_wheel_prepares_venv_outside_site_packages(installed, monkeypatch):
    resources, home = installed
    monkeypatch.setattr(sys, "platform", "linux")
    (resources / "backend.json").write_text(json.dumps({"target": "linux-x86_64"}), encoding="utf-8")
    wheel = resources / LINUX_WHEEL
    wheel.parent.mkdir(parents=True)
    wheel.touch()
    (resources / LINUX_LOCK).write_text("python-xlib==0.33\n", encoding="utf-8")
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
    assert ("-m", "pip", "install", "--no-deps", str(root / LINUX_WHEEL)) in commands


def test_linux_platform_wheel_reports_missing_backend(installed, monkeypatch):
    resources, _ = installed
    monkeypatch.setattr(sys, "platform", "linux")
    (resources / "backend.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="Bundled Linux backend wheel is missing"):
        native.build_native()


def test_linux_source_build_wheel_launches_installed_driver(installed, monkeypatch, tmp_path):
    resources, home = installed
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("COMPUTER_USE_PYTHON", sys.executable)
    runtime = resources / paths.LINUX_RUNTIME
    driver_package = tmp_path / "os_adapter/linux/src/computer_use_linux"
    driver_package.mkdir(parents=True)
    (driver_package / "__init__.py").touch()
    (driver_package / "__main__.py").write_text("def main():\n    return 7\n", encoding="utf-8")

    run = native.run

    def run_during_packaging(command, args, cwd, capture=False):
        if capture:
            return run(command, args, cwd, capture=True)
        assert args[:3] == ["-m", "pip", "install"]
        runtime.mkdir(parents=True)

    monkeypatch.setattr(native, "run", run_during_packaging)
    native.stage_linux_runtime(tmp_path, runtime)

    def unexpected_setup(*args, **kwargs):
        pytest.fail("The installed driver attempted runtime setup.")

    monkeypatch.setattr(native, "run", unexpected_setup)
    driver = paths.native_driver()
    assert Path(driver["args"][-1]).is_relative_to(home)
    assert native.build_native() == Path(driver["command"])
    assert subprocess.run([driver["command"], *driver["args"]], check=False).returncode == 7


@pytest.mark.parametrize("returncode", [0, 1])
def test_linux_python_prefers_a_supported_system_interpreter(monkeypatch, returncode):
    monkeypatch.delenv("COMPUTER_USE_PYTHON", raising=False)
    system_python = "/usr/bin/python3"
    monkeypatch.setattr(paths.shutil, "which", lambda command: system_python)

    def run(args, *, stdout, stderr, check):
        assert args[:4] == [system_python, "-I", "-S", "-c"]
        return subprocess.CompletedProcess(args, returncode)

    monkeypatch.setattr(paths.subprocess, "run", run)
    assert paths.linux_python() == (system_python if returncode == 0 else sys.executable)


def test_linux_wheel_reports_invalid_python_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(native, "run", lambda *args: "unexpected interpreter output")
    with pytest.raises(RuntimeError, match="Could not read the Linux desktop Python version and ABI"):
        native.stage_linux_runtime(tmp_path, tmp_path / "runtime")


def test_source_wheel_keeps_a_writable_build_tree(installed):
    resources, home = installed
    source = resources / "os_adapter/macos/Package.swift"
    source.parent.mkdir(parents=True)
    source.write_text("// build input", encoding="utf-8")
    root = paths.repository_root()
    assert root.is_relative_to(home)
    assert (root / source.relative_to(resources)).read_bytes() == source.read_bytes()


@pytest.fixture
def source_builder(monkeypatch):
    monkeypatch.delenv("COMPUTER_USE_BACKEND_ARCHIVE", raising=False)
    monkeypatch.delenv("COMPUTER_USE_BACKEND_DIR", raising=False)
    monkeypatch.setattr(setuptools, "setup", lambda **_: None)
    return runpy.run_path(str(ROOT / "setup.py"))


def stage_mac_backend(stage):
    executable = stage / MAC_DRIVER
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"driver")
    (stage / "backend.json").write_text(json.dumps({
        "format": 1,
        "target": "macos-arm64",
        "protocol": native.NATIVE_PROTOCOL_VERSION,
        "files": {MAC_DRIVER: {"sha256": hashlib.sha256(b"driver").hexdigest(), "mode": 0o755}},
    }), encoding="utf-8")


@pytest.mark.parametrize("failure", ["compile", "launch", "missing", "malformed", "metadata", "payload", "move"])
def test_failed_source_build_discards_partial_driver_and_records_reason(
    installed, monkeypatch, tmp_path, source_builder, failure,
):
    resources, home = installed

    def build(command, *, cwd, check):
        assert cwd == tmp_path and check
        stage = Path(command[-2])
        if failure == "launch":
            raise FileNotFoundError("Could not launch the builder.")
        if failure in ("compile", "payload", "move"):
            stage_mac_backend(stage)
        if failure == "compile":
            raise subprocess.CalledProcessError(1, command)
        if failure in ("malformed", "metadata"):
            (stage / "backend.json").write_text("{" if failure == "malformed" else "{}", encoding="utf-8")
        if failure == "payload":
            (stage / MAC_DRIVER).write_bytes(b"corrupt")

    replace = Path.replace

    def move(path, target):
        if failure == "move" and path.name in (".build", Path(MAC_DRIVER).name):
            raise PermissionError("Could not move the driver.")
        return replace(path, target)

    monkeypatch.setattr(subprocess, "run", build)
    monkeypatch.setattr(Path, "replace", move)
    files = source_builder["install_source_backend"](tmp_path, resources, MAC_TAG)

    assert set(files) == {"backend-status.json"}
    assert not (resources / "backend.json").exists()
    assert not (resources / MAC_DRIVER).exists()
    assert paths.installed_backend_unavailable_reason() == "native_build_failed"
    assert not home.exists()


@pytest.mark.parametrize("platform_tag", [MAC_TAG, None])
def test_source_build_includes_backend_or_status_in_manifest(tmp_path, monkeypatch, source_builder, platform_tag):
    def build(command, **_):
        assert platform_tag == MAC_TAG
        stage_mac_backend(Path(command[-2]))

    monkeypatch.setattr(subprocess, "run", build)
    command = source_builder["BuildWithResources"](setuptools.Distribution({"packages": []}))
    command.build_lib = str(tmp_path / "build")
    command.native_platform = platform_tag
    command.ensure_finalized()
    command.run()
    resources = Path(command.build_lib) / "computer_use/_resources"
    manifest = json.loads((resources / "manifest.json").read_bytes())
    files = ("backend.json", MAC_DRIVER) if platform_tag else ("backend-status.json",)
    for name in ("os_adapter/macos/Package.swift", *files):
        assert manifest[name] == hashlib.sha256((resources / name).read_bytes()).hexdigest()
    if platform_tag is None:
        assert json.loads((resources / files[0]).read_bytes()) == {"reason": "unsupported_platform"}


@pytest.mark.parametrize(("reason", "detail", "code"), [
    ("native_build_failed", "could not be built during installation", "execution_failed"),
    ("unsupported_platform", "is unavailable on this operating system or architecture", "unsupported_operation"),
])
def test_unavailable_source_wheel_explains_startup_failure(installed, monkeypatch, reason, detail, code):
    resources, _ = installed
    monkeypatch.setattr(sys, "platform", "darwin")
    (resources / "backend-status.json").write_text(json.dumps({"reason": reason}), encoding="utf-8")

    with pytest.raises(ComputerUseError, match=detail) as failure:
        asyncio.run(Connection(lambda: None)._connect())

    assert failure.value.code == code


@pytest.mark.parametrize(("system", "files"), [
    ("darwin", (MAC_DRIVER,)),
    ("win32", (WINDOWS_DRIVER, "os_adapter/windows/.build/computer-use-capture.exe",
               "os_adapter/windows/.build/Interop.NativeUIA.dll")),
    ("linux", (str(paths.LINUX_RUNTIME / "__main__.py"),)),
    ("linux", (LINUX_WHEEL,)),
])
def test_installed_backend_check_requires_driver_resources(installed, monkeypatch, system, files):
    resources, home = installed
    monkeypatch.setattr(sys, "platform", system)
    (resources / "backend.json").write_text("{}", encoding="utf-8")
    assert paths.installed_backend_unavailable_reason() == "backend_missing"
    for name in files:
        path = resources / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    assert paths.installed_backend_unavailable_reason() is None
    assert not home.exists()


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
def test_rejects_mismatched_backend(distribution, linux_backend, overrides, message):
    with pytest.raises(ValueError, match=message):
        distribution.validate_backend(ROOT, linux_backend(**overrides), "linux-x86_64")


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
    engine_archive = backend.with_name(sdk_native.artifact_name(ROOT, "linux-x86_64", "cp311") + ".zip")
    sdk_native.write_archive(ROOT, engine, extension, engine_archive)

    def build(*_args, **_kwargs):
        wheel = staging / "openjiuwen_computeruse-0.0.1-cp311-cp311-linux_x86_64.whl"
        with ZipFile(wheel, "w") as archive:
            archive.writestr("openjiuwen_computeruse-0.0.1.dist-info/WHEEL", f"Tag: {tag}\n")
            archive.writestr("openjiuwen_computeruse-0.0.1.dist-info/METADATA", "Version: 0.0.1\n")

    monkeypatch.setattr(distribution.subprocess, "run", build)
    with pytest.raises(ValueError, match=message):
        distribution._assemble_platform(ROOT, backend, staging, "linux-x86_64")
