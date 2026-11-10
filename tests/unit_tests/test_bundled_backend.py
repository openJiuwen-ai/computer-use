# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from zipfile import ZipFile

import pytest
from computer_use import paths
from computer_use_native_tools import native

MAC_DRIVER = "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver"
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
        ("py3-none-any", "wrong platform tag"),
        ("py3-none-linux_x86_64", "unexpected backend files"),
    ],
)
def test_assembly_rejects_invalid_built_wheel(distribution, linux_backend, tmp_path, monkeypatch, tag, message):
    staging = tmp_path / "staging"

    def build(*_args, **_kwargs):
        wheel = staging / "openjiuwen_computeruse-0.0.1-py3-none-linux_x86_64.whl"
        with ZipFile(wheel, "w") as archive:
            archive.writestr("openjiuwen_computeruse-0.0.1.dist-info/WHEEL", f"Tag: {tag}\n")
            archive.writestr("openjiuwen_computeruse-0.0.1.dist-info/METADATA", "Version: 0.0.1\n")

    monkeypatch.setattr(distribution.subprocess, "run", build)
    with pytest.raises(ValueError, match=message):
        distribution._assemble_platform(ROOT, linux_backend(), staging, "linux-x86_64")
