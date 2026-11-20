# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import platform
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from computer_use.interaction.read import accessibility
from computer_use.interaction.read.accessibility import _native
from computer_use_native_tools import backend_artifacts, read_engine_build, sdk_native

ROOT = Path(__file__).resolve().parents[2]
MAC_DRIVER = backend_artifacts.MAC_DRIVER
WINDOWS_DIR = "os_adapter/windows/.build"
WINDOWS_PAYLOADS = ("computer-use-driver.exe", "computer-use-capture.exe", "Interop.NativeUIA.dll")
LINUX_DRIVER = "os_adapter/linux/.build/driver-0.0.1-py3-none-any.whl"


def test_sdk_requests_a_rebuild_for_the_previous_native_read_contract(monkeypatch):
    monkeypatch.setattr(accessibility, "_ax_read", SimpleNamespace(NATIVE_READ_CONTRACT=0), raising=False)
    with pytest.raises(RuntimeError, match="Rust accessibility read engine is outdated. Rebuild the native SDK"):
        _native.require_read_engine.__wrapped__()


@pytest.mark.parametrize(
    ("system", "machine", "tag", "key", "abi", "target", "wheel", "suffix", "library"),
    [
        (
            "darwin",
            "arm64",
            "cp311",
            "SOABI",
            "cpython-311-darwin",
            "aarch64-apple-darwin",
            "macosx_14_0_arm64",
            ".cpython-311-darwin.so",
            "lib_ax_read.dylib",
        ),
        (
            "darwin",
            "x86_64",
            "cp313",
            "SOABI",
            "cpython-313-darwin",
            "x86_64-apple-darwin",
            "macosx_14_0_x86_64",
            ".cpython-313-darwin.so",
            "lib_ax_read.dylib",
        ),
        (
            "win32",
            "AMD64",
            "cp312",
            "EXT_SUFFIX",
            ".cp312-win_amd64.pyd",
            "x86_64-pc-windows-msvc",
            "win_amd64",
            ".cp312-win_amd64.pyd",
            "_ax_read.dll",
        ),
        (
            "linux",
            "x86_64",
            "cp311",
            "SOABI",
            "cpython-311-x86_64-linux-gnu",
            "x86_64-unknown-linux-gnu",
            "linux_x86_64",
            ".cpython-311-x86_64-linux-gnu.so",
            "lib_ax_read.so",
        ),
        (
            "linux",
            "aarch64",
            "cp313",
            "SOABI",
            "cpython-313-aarch64-linux-gnu",
            "aarch64-unknown-linux-gnu",
            "linux_aarch64",
            ".cpython-313-aarch64-linux-gnu.so",
            "lib_ax_read.so",
        ),
    ],
)
def test_native_sdk_build_identity(monkeypatch, system, machine, tag, key, abi, target, wheel, suffix, library):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    monkeypatch.setattr(sysconfig, "get_config_var", {key: abi}.get)
    build = read_engine_build.host_build()
    assert build.target == target
    assert build.wheel_tag == (tag, tag, wheel)
    assert build.filename == f"_ax_read{suffix}"
    assert build.library == library


@pytest.mark.parametrize(
    ("system", "abi"),
    [("linux", "cpython-312-x86_64-linux-musl"), ("win32", ".cp313t-win_amd64.pyd"), ("win32", "_d.pyd")],
)
def test_native_sdk_rejects_nonrelease_or_unsupported_abi(monkeypatch, system, abi):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(sysconfig, "get_config_var", lambda _key: abi)
    assert read_engine_build.host_build() is None


def test_native_sdk_build_does_not_copy_another_abi_output(monkeypatch, tmp_path):
    first, second = (read_engine_build.build_for("macos-arm64", tag) for tag in ("cp311", "cp312"))
    crate = tmp_path / "computer_use_engine/interaction/read/accessibility"
    crate.mkdir(parents=True)
    (crate / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.85.1"\n', encoding="utf-8")
    builds = iter((first, second))
    monkeypatch.setattr(read_engine_build, "host_build", lambda: next(builds))
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "rustc 1.85.1 (fixture)")
    compiling = iter((first, second))
    first_output, second_output = (tmp_path / build.filename for build in (first, second))

    def compile_abi(command, **_options):
        build = next(compiling)
        target_dir = Path(command[command.index("--target-dir") + 1])
        library = target_dir / build.target / "release" / build.library
        library.parent.mkdir(parents=True, exist_ok=True)
        library.write_text(build.soabi, encoding="utf-8")
        if build == first:
            # The second interpreter finishes before the first copies Cargo's output.
            read_engine_build.build_read_engine(tmp_path, second_output)

    monkeypatch.setattr(subprocess, "run", compile_abi)
    monkeypatch.setattr(read_engine_build, "_read_engine_notice", lambda *_args: "license fixture")
    read_engine_build.build_read_engine(tmp_path, first_output)
    assert first_output.read_text(encoding="utf-8") == first.soabi
    assert second_output.read_text(encoding="utf-8") == second.soabi


def frozen_driver_archive(output, target, *, protocol=None):
    if target.startswith("macos-"):
        payloads = {MAC_DRIVER: b"old macOS executable", backend_artifacts.MAC_INFO: b"bundle metadata"}
    elif target.startswith("windows-"):
        payloads = {f"{WINDOWS_DIR}/{name}": name.encode() for name in WINDOWS_PAYLOADS}
    else:
        payloads = {LINUX_DRIVER: b"old Linux driver wheel"}
    manifest = {
        "format": backend_artifacts.FORMAT,
        "target": target,
        "fingerprint": backend_artifacts.fingerprint(ROOT, target),
        "source_commit": "a" * 40,
        "protocol": backend_artifacts.protocol_version(ROOT) if protocol is None else protocol,
        "files": {
            name: {"sha256": backend_artifacts.sha256(data), "mode": 0o755 if name == MAC_DRIVER else 0o644}
            for name, data in payloads.items()
        },
    }
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("backend.json", backend_artifacts.canonical(manifest))
        for name, data in payloads.items():
            archive.writestr(name, data)
    return manifest, payloads


def native_sdk_archive(directory, target, tag):
    build = read_engine_build.build_for(target, tag)
    extension = directory / build.filename
    extension.write_bytes(b"target-specific SDK extension")
    notice = read_engine_build.notice_path(extension)
    notice.parent.mkdir(exist_ok=True)
    notice.write_text("read engine license fixture", encoding="utf-8")
    archive = directory / "engine.zip"
    manifest = sdk_native.write_archive(ROOT, build, extension, archive)
    return archive, manifest, extension.read_bytes()


@pytest.mark.parametrize(
    ("target", "tag"),
    [
        ("macos-arm64", "cp312"),
        ("macos-x86_64", "cp311"),
        ("windows-x86_64", "cp313"),
        ("linux-x86_64", "cp312"),
        ("linux-aarch64", "cp311"),
    ],
)
def test_sdk_wheel_preserves_explicit_driver_and_uses_separate_sdk_extension(tmp_path, monkeypatch, target, tag):
    pytest.importorskip("build.__main__")
    pytest.importorskip("wheel")
    driver = tmp_path / "driver.zip"
    driver_manifest, payloads = frozen_driver_archive(driver, target)
    engine, engine_manifest, extension = native_sdk_archive(tmp_path, target, tag)
    driver_digest = sdk_native.file_digest(driver)
    engine_digest = sdk_native.file_digest(engine)
    output = tmp_path / "wheels"
    run = subprocess.run

    def build_without_isolation(command, **kwargs):
        return run([*command[:-1], "--no-isolation", command[-1]], **kwargs)

    monkeypatch.setattr(subprocess, "run", build_without_isolation)
    sdk_native.assemble_wheel(ROOT, driver, engine, output)
    (wheel,) = output.glob("*.whl")
    wheel_tag = "-".join(engine_manifest["wheel_tag"])
    assert wheel.name.endswith(f"-{wheel_tag}.whl")
    assert sdk_native.file_digest(driver) == driver_digest
    assert sdk_native.file_digest(engine) == engine_digest
    prefix = "computer_use/_resources/"
    with zipfile.ZipFile(wheel) as archive:
        member = f"computer_use/interaction/read/accessibility/{engine_manifest['filename']}"
        assert archive.read(member) == extension
        assert [name for name in archive.namelist() if Path(name).name.startswith("_ax_read.")] == [member]
        (metadata,) = [name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")]
        assert f"Tag: {wheel_tag}" in archive.read(metadata).decode()
        assert json.loads(archive.read(f"{prefix}backend.json")) == driver_manifest
        license_entry = "computer_use/interaction/read/accessibility/_ax_read_licenses/THIRD_PARTY_LICENSES.txt"
        assert archive.read(license_entry) == b"read engine license fixture"
        for name in driver_manifest["files"]:
            assert archive.read(f"{prefix}{name}") == payloads[name]
        cache_manifest = json.loads(archive.read(f"{prefix}manifest.json"))
        assert cache_manifest["backend.json"] == backend_artifacts.sha256(archive.read(f"{prefix}backend.json"))


def test_native_sdk_rejects_corrupt_extension_and_incompatible_driver(tmp_path):
    engine, manifest, _ = native_sdk_archive(tmp_path, "linux-x86_64", "cp311")
    driver = tmp_path / "driver.zip"
    frozen_driver_archive(driver, "linux-aarch64")
    with pytest.raises(ValueError, match="Backend target mismatch"):
        sdk_native.inspect_pair(ROOT, driver, engine)
    frozen_driver_archive(driver, "linux-x86_64", protocol=-1)
    with pytest.raises(ValueError, match="sources/protocol"):
        sdk_native.inspect_pair(ROOT, driver, engine)
    frozen_driver_archive(driver, "linux-x86_64")
    with zipfile.ZipFile(engine, "w") as archive:
        archive.writestr(sdk_native.MANIFEST, backend_artifacts.canonical(manifest))
        archive.writestr(manifest["filename"], b"bad extension")
        archive.writestr(sdk_native.NOTICE, b"read engine license fixture")
    with pytest.raises(ValueError, match="artifact checksum mismatch"):
        sdk_native.inspect_pair(ROOT, driver, engine)


@pytest.mark.parametrize("notice", [None, b"wrong license"], ids=["missing-license", "incorrect-license"])
def test_native_sdk_rejects_invalid_license_notice(tmp_path, notice):
    engine, manifest, extension = native_sdk_archive(tmp_path, "linux-x86_64", "cp311")
    with zipfile.ZipFile(engine, "w") as archive:
        archive.writestr(sdk_native.MANIFEST, backend_artifacts.canonical(manifest))
        archive.writestr(manifest["filename"], extension)
        if notice is not None:
            archive.writestr(sdk_native.NOTICE, notice)
    reason = "file list is inconsistent" if notice is None else "artifact checksum mismatch"
    with pytest.raises(ValueError, match=reason):
        sdk_native.inspect_archive(ROOT, engine)


def test_native_sdk_build_does_not_publish_an_unloadable_library(tmp_path, monkeypatch):
    build = read_engine_build.host_build()
    if build is None:
        pytest.skip("Native SDK builder is unavailable on this interpreter")

    def build_unloadable_library(_root, output):
        output.write_bytes(b"not an executable library")
        return output

    monkeypatch.setitem(sdk_native.BUILD, "build_read_engine", build_unloadable_library)
    with pytest.raises(subprocess.CalledProcessError):
        sdk_native.build_archive(ROOT, tmp_path)
    assert not list(tmp_path.iterdir())


def test_native_sdk_build_rejects_prerelease_python(tmp_path, monkeypatch):
    interpreter = SimpleNamespace(version_info=SimpleNamespace(releaselevel="candidate"))
    monkeypatch.setattr(sdk_native, "sys", interpreter)
    with pytest.raises(RuntimeError, match="final CPython release"):
        sdk_native.build_archive(ROOT, tmp_path)
    assert not list(tmp_path.iterdir())
