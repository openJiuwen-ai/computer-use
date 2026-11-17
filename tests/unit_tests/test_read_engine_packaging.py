# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import runpy
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest
from computer_use_native_tools import backend_artifacts, read_engine_build

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def capture_wheel(monkeypatch):
    setuptools = pytest.importorskip("setuptools")
    for name in ("BACKEND_ARCHIVE", "BACKEND_DIR", "SDK_NATIVE_ARCHIVE"):
        monkeypatch.delenv(f"COMPUTER_USE_{name}", raising=False)

    def load():
        arguments = {}
        monkeypatch.setattr(setuptools, "setup", lambda **kwargs: arguments.update(kwargs))
        runpy.run_path(str(ROOT / "setup.py"))
        distribution = setuptools.Distribution(arguments)
        wheel = distribution.get_command_obj("bdist_wheel")
        wheel.ensure_finalized()
        return distribution, wheel

    return load


def driver_archive(directory, target, member):
    data = b"driver fixture"
    members = [member]
    if target == "windows-x86_64":
        members.extend((
            "os_adapter/windows/.build/computer-use-capture.exe",
            "os_adapter/windows/.build/Interop.NativeUIA.dll",
        ))
    manifest = {
        "format": backend_artifacts.FORMAT,
        "target": target,
        "protocol": backend_artifacts.protocol_version(ROOT),
        "fingerprint": backend_artifacts.fingerprint(ROOT, target),
        "files": {name: {"sha256": backend_artifacts.sha256(data), "mode": 0o755} for name in members},
    }
    archive = directory / "driver.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("backend.json", backend_artifacts.canonical(manifest))
        for name in members:
            output.writestr(name, data)
    return archive


def test_source_rust_build_failure_keeps_wheel_installable(capture_wheel, monkeypatch, tmp_path):
    distribution, wheel = capture_wheel()
    command = distribution.get_command_obj("build_ext")
    command.ensure_finalized()
    command.build_lib = str(tmp_path / "build")
    extension = distribution.ext_modules[0]
    output = Path(command.get_ext_fullpath(extension.name))
    notice = read_engine_build.notice_path(output)

    def fail(_root, destination):
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"partial extension")
        notice.parent.mkdir()
        notice.write_bytes(b"partial notice")
        raise subprocess.CalledProcessError(1, ["cargo", "build"])

    monkeypatch.setitem(command.build_extension.__globals__["READ_ENGINE"], "build_read_engine", fail)
    command.build_extension(extension)

    assert not output.exists()
    assert not notice.exists()
    assert command.get_outputs() == []
    assert wheel.get_tag() == ("py3", "none", wheel.native_platform)
    assert (output.parent / "_ax_read_status.json").read_text(encoding="utf-8") == '{"reason":"native_build_failed"}'


@pytest.mark.parametrize(("target", "member", "platform_tag"), [
    ("macos-x86_64", "os_adapter/macos/.build/ComputerUseDriver.app/Contents/MacOS/computer-use-driver", "macosx_14_0_x86_64"),
    ("linux-x86_64", "os_adapter/linux/.build/driver-0.0.1-py3-none-any.whl", "linux_x86_64"),
    ("windows-x86_64", "os_adapter/windows/.build/computer-use-driver.exe", "win_amd64"),
])
def test_wheel_uses_archived_read_engine_abi(capture_wheel, monkeypatch, tmp_path, target, member, platform_tag):
    sdk_native = runpy.run_path(str(ROOT / "scripts/sdk_native.py"))
    build = read_engine_build.build_for(target, "cp312")
    extension = tmp_path / build.filename
    extension.write_bytes(b"read engine fixture")
    notice = read_engine_build.notice_path(extension)
    notice.parent.mkdir()
    notice.write_bytes(b"generated third-party license fixture")
    archive = tmp_path / "engine.zip"
    sdk_native["write_archive"](ROOT, build, extension, archive)
    driver = driver_archive(tmp_path, target, member)
    monkeypatch.setenv("COMPUTER_USE_BACKEND_ARCHIVE", str(driver))
    monkeypatch.setenv("COMPUTER_USE_SDK_NATIVE_ARCHIVE", str(archive))

    distribution, wheel = capture_wheel()
    assert not wheel.root_is_pure
    assert wheel.get_tag() == ("cp312", "cp312", platform_tag)
    command = distribution.get_command_obj("build_ext")
    command.ensure_finalized()
    command.build_lib = str(tmp_path / "build")
    command.build_extension(distribution.ext_modules[0])
    installed = Path(command.get_ext_fullpath(distribution.ext_modules[0].name))
    assert installed.name == build.filename
    assert installed.read_bytes() == extension.read_bytes()
    assert read_engine_build.notice_path(installed).read_bytes() == notice.read_bytes()
