# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import platform
import runpy
import subprocess
import sys
import sysconfig
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from computer_use_native_tools import backend_artifacts, native, read_engine_build

ROOT = Path(__file__).resolve().parents[2]
MAC_PLATFORM = "macosx_14_0_arm64"
NATIVE_TAG = ("cp311", "cp311", MAC_PLATFORM)


@pytest.fixture
def capture_setup(monkeypatch):
    setuptools = pytest.importorskip("setuptools")
    for name in (
        "COMPUTER_USE_BACKEND_ARCHIVE",
        "COMPUTER_USE_BACKEND_DIR",
        "COMPUTER_USE_SDK_NATIVE_ARCHIVE",
    ):
        monkeypatch.delenv(name, raising=False)

    def load():
        arguments = {}
        monkeypatch.setattr(setuptools, "setup", lambda **kwargs: arguments.update(kwargs))
        runpy.run_path(str(ROOT / "setup.py"))
        distribution = setuptools.Distribution(arguments)
        return distribution, arguments["cmdclass"]["bdist_wheel"](distribution)

    return load


@pytest.fixture
def mac_cpython_311(monkeypatch, capture_setup):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: "arm64")
    config = sysconfig.get_config_var
    monkeypatch.setattr(
        sysconfig, "get_config_var", lambda key: "cpython-311-darwin" if key == "SOABI" else config(key)
    )
    return capture_setup


@pytest.mark.parametrize(
    ("system", "key", "abi", "tag"),
    [
        ("linux", "SOABI", "cpython-311-x86_64-linux-gnu", "linux_x86_64"),
        ("win32", "EXT_SUFFIX", ".cp311-win_amd64.pyd", "win_amd64"),
    ],
)
def test_source_distribution_uses_the_read_engine_abi(capture_setup, monkeypatch, system, key, abi, tag):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    config = sysconfig.get_config_var
    monkeypatch.setattr(sysconfig, "get_config_var", lambda name: abi if name == key else config(name))
    distribution, wheel = capture_setup()
    wheel.ensure_finalized()
    assert distribution.has_ext_modules()
    assert not wheel.root_is_pure
    assert wheel.plat_name == tag
    monkeypatch.setattr(type(wheel).__base__, "get_tag", lambda self: ("cp311", "cp311", tag))
    assert wheel.get_tag() == ("cp311", "cp311", tag)


@pytest.mark.parametrize(
    ("system", "machine", "abi"),
    [
        ("linux", "arm64", "cpython-311-darwin"),
        ("darwin", "powerpc", "cpython-311-darwin"),
        ("darwin", "x86_64", "cpython-316-darwin"),
        ("darwin", "arm64", "cpython-311d-darwin"),
        ("darwin", "arm64", "cpython-313t-darwin"),
    ],
)
def test_source_build_records_unsupported_hosts(capture_setup, monkeypatch, tmp_path, system, machine, abi):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setattr(sysconfig, "get_config_var", lambda key: abi)
    monkeypatch.setattr(platform, "mac_ver", lambda: ("14.0", ("", "", ""), ""))
    distribution, wheel = capture_setup()
    command = distribution.get_command_obj("build_ext")
    command.ensure_finalized()
    command.build_lib = str(tmp_path)
    extension = distribution.ext_modules[0]
    command.build_extension(extension)
    wheel.ensure_finalized()

    output = Path(command.get_ext_fullpath(extension.name))
    assert not output.exists()
    assert json.loads(output.with_name("_ax_read_status.json").read_bytes()) == {"reason": "unsupported_platform"}
    assert wheel.get_tag()[:2] == ("py3", "none")


def test_accelerated_wheel_checks_exact_abi_tag(mac_cpython_311, monkeypatch):
    distribution, wheel = mac_cpython_311()
    wheel.ensure_finalized()
    assert distribution.has_ext_modules()
    assert [extension.name for extension in distribution.ext_modules] == [
        "computer_use.interaction.read.accessibility._ax_read",
    ]
    assert not wheel.root_is_pure
    assert wheel.plat_name == MAC_PLATFORM
    base_wheel = type(wheel).__base__
    monkeypatch.setattr(base_wheel, "get_tag", lambda self: NATIVE_TAG)
    assert wheel.get_tag() == NATIVE_TAG
    monkeypatch.setattr(base_wheel, "get_tag", lambda self: ("cp311", "abi3", MAC_PLATFORM))
    with pytest.raises(ValueError, match="Wheel has tag"):
        wheel.get_tag()


@pytest.mark.parametrize("machine", ["arm64", "x86_64"])
@pytest.mark.parametrize("minor", [311, 312, 313, 314, 315])
def test_source_wheel_uses_its_interpreter_abi_and_architecture(capture_setup, monkeypatch, machine, minor):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(platform, "machine", lambda: machine)
    config = sysconfig.get_config_var
    monkeypatch.setattr(
        sysconfig, "get_config_var", lambda key: f"cpython-{minor}-darwin" if key == "SOABI" else config(key)
    )
    distribution, wheel = capture_setup()
    wheel.ensure_finalized()
    assert not wheel.root_is_pure
    assert wheel.plat_name == f"macosx_14_0_{machine}"
    tag = (f"cp{minor}", f"cp{minor}", wheel.plat_name)
    monkeypatch.setattr(type(wheel).__base__, "get_tag", lambda self: tag)
    assert wheel.get_tag() == tag
    command = distribution.get_command_obj("build_ext")
    assert command.get_ext_filename("_ax_read") == f"_ax_read.cpython-{minor}-darwin.so"


def test_accelerated_wheel_rejects_conflicting_platform_tag(mac_cpython_311):
    _, wheel = mac_cpython_311()
    wheel.plat_name = "macosx_14_0_universal2"
    with pytest.raises(ValueError, match="requires wheel platform"):
        wheel.ensure_finalized()


def test_accelerated_build_requires_the_pinned_rust_compiler(mac_cpython_311, monkeypatch):
    mac_cpython_311()
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "rustc 1.86.0 (other build)")
    build = read_engine_build.host_build()
    with pytest.raises(RuntimeError, match="requires Rust 1.85.1"):
        read_engine_build.build_read_engine(ROOT, Path(build.filename))


@pytest.mark.parametrize(
    ("system", "build_method"),
    [("darwin", "build_mac_os"), ("win32", "build_windows"), ("linux", "build_linux")],
)
def test_computer_use_build_prepares_driver_independently(monkeypatch, tmp_path, system, build_method):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(read_engine_build, "host_build", lambda: None)
    driver = tmp_path / "driver"

    def build(root, **_options):
        assert root == tmp_path
        return driver

    monkeypatch.setattr(native, build_method, build)
    assert native.build_native(tmp_path) == driver


def test_build_read_engine_rejects_an_output_from_another_abi(monkeypatch, tmp_path):
    build = read_engine_build.build_for("macos-arm64", "cp312")
    other_abi = read_engine_build.build_for("macos-arm64", "cp311")
    monkeypatch.setattr(read_engine_build, "host_build", lambda: build)

    with pytest.raises(RuntimeError, match="target Python ABI"):
        read_engine_build.build_read_engine(tmp_path, tmp_path / other_abi.filename)


def test_runtime_admits_the_installed_read_engine(monkeypatch):
    from computer_use.interaction.read import accessibility
    from computer_use.interaction.read.accessibility import _native

    extension = SimpleNamespace(NATIVE_READ_CONTRACT=2)
    monkeypatch.setattr(accessibility, "_ax_read", extension, raising=False)
    assert _native.require_read_engine.__wrapped__() is extension


def _set_driver_payload(monkeypatch, tmp_path, target, files, *, prepared):
    manifest = {
        "format": backend_artifacts.FORMAT,
        "target": target,
        "fingerprint": backend_artifacts.fingerprint(ROOT, target),
        "protocol": backend_artifacts.protocol_version(ROOT),
        "files": {
            name: {
                "sha256": backend_artifacts.sha256(data),
                "mode": 0o755 if name == backend_artifacts.MAC_DRIVER else 0o644,
            }
            for name, data in files.items()
        },
    }
    if prepared:
        for name, data in files.items():
            path = tmp_path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        metadata = backend_artifacts.canonical(manifest)
        (tmp_path / "backend.json").write_bytes(metadata)
        digests = {name: entry["sha256"] for name, entry in manifest["files"].items()}
        digests["backend.json"] = backend_artifacts.sha256(metadata)
        (tmp_path / "manifest.json").write_text(json.dumps(digests), encoding="utf-8")
        monkeypatch.setenv("COMPUTER_USE_BACKEND_DIR", str(tmp_path))
        return

    archive = tmp_path / "backend.zip"
    with ZipFile(archive, "w") as zip_file:
        zip_file.writestr("backend.json", backend_artifacts.canonical(manifest))
        for name, data in files.items():
            zip_file.writestr(name, data)
    monkeypatch.setenv("COMPUTER_USE_BACKEND_ARCHIVE", str(archive))


@pytest.mark.parametrize("prepared", [False, True])
def test_cross_host_wheel_requires_a_matching_read_engine_archive(mac_cpython_311, monkeypatch, tmp_path, prepared):
    files = {"os_adapter/linux/.build/openjiuwen_computeruse_linux-0.0.1-py3-none-any.whl": b"driver"}
    if prepared:
        files["os_adapter/linux/requirements.lock"] = b"runtime dependencies"
    _set_driver_payload(monkeypatch, tmp_path, "linux-x86_64", files, prepared=prepared)

    with pytest.raises(RuntimeError, match="For cross-host wheel assembly, supply its SDK archive"):
        mac_cpython_311()


def test_same_host_wheel_builds_read_engine_for_prepared_driver(mac_cpython_311, monkeypatch, tmp_path):
    files = {backend_artifacts.MAC_DRIVER: b"driver", backend_artifacts.MAC_INFO: b"bundle metadata"}
    _set_driver_payload(monkeypatch, tmp_path, "macos-arm64", files, prepared=True)
    distribution, wheel = mac_cpython_311()
    command = distribution.get_command_obj("build_ext")
    command.ensure_finalized()
    command.build_lib = str(tmp_path / "wheel")
    extension = distribution.ext_modules[0]
    output = Path(command.get_ext_fullpath(extension.name))

    def build(root, destination):
        assert root == ROOT and destination == output
        assert destination.name == "_ax_read.cpython-311-darwin.so"
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"local read engine")

    monkeypatch.setitem(command.build_extension.__globals__["READ_ENGINE"], "build_read_engine", build)
    command.build_extension(extension)
    assert output.read_bytes() == b"local read engine"
    wheel.ensure_finalized()
    assert wheel.plat_name == MAC_PLATFORM
