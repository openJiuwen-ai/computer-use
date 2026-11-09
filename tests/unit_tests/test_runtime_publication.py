# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import ctypes
import hashlib
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from computer_use import paths
from computer_use.protocol.session import NATIVE_PROTOCOL_VERSION


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    package = tmp_path / "installed/computer_use"
    resources = package / "_resources"
    resources.mkdir(parents=True)
    (resources / "manifest.json").write_bytes(b"manifest")
    (resources / "driver.exe").write_bytes(b"driver")
    monkeypatch.setattr(paths, "__file__", str(package / "paths.py"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    paths.repository_root.cache_clear()
    yield tmp_path / "home/.computer-use/runtime"
    paths.repository_root.cache_clear()


@pytest.mark.parametrize(
    ("driver_units", "valid_capture", "use_bundle"),
    [(251, True, False), (252, True, True), (252, False, False)],
    ids=["short-default", "capture-too-long", "unverified-bundle"],
)
def test_windows_installed_driver_uses_verified_bundle_when_capture_path_is_too_long(
    tmp_path, runtime, monkeypatch, driver_units, valid_capture, use_bundle
):
    resources = tmp_path / "installed/computer_use/_resources"
    build = Path("os_adapter/windows/.build")
    bundle = resources / build
    bundle.mkdir(parents=True)
    driver = "computer-use-driver.exe"
    capture = "computer-use-capture.exe"
    files = {}
    for name in (driver, capture, "Interop.NativeUIA.dll"):
        data = name.encode()
        (bundle / name).write_bytes(data)
        files[(build / name).as_posix()] = {"sha256": hashlib.sha256(data).hexdigest(), "mode": 0o755}
    (resources / "backend.json").write_text(
        json.dumps({"format": 1, "target": "windows-x86_64", "protocol": NATIVE_PROTOCOL_VERSION, "files": files}),
        encoding="utf-8",
    )
    if not valid_capture:
        (bundle / capture).write_bytes(b"changed capture")

    key = hashlib.sha256((resources / "manifest.json").read_bytes()).hexdigest()[:20]
    suffix = Path(".computer-use/runtime") / key / build
    tmp_path = tmp_path.resolve()
    prefix_units = len(str(tmp_path / suffix / driver).encode("utf-16-le")) // 2
    home = tmp_path / ("h" * (driver_units - prefix_units - 1))
    materialized = home / suffix
    assert len(str(materialized / driver).encode("utf-16-le")) // 2 == driver_units
    assert len(str(materialized / capture).encode("utf-16-le")) // 2 == driver_units + 1
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(paths, "sys", SimpleNamespace(platform="win32"))

    result = paths.native_driver()

    expected = bundle.resolve() if use_bundle else materialized
    assert result == {"command": str(expected / driver), "args": []}
    assert materialized.exists() is not use_bundle


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS directory publication with an open child")
def test_publishes_same_stage_after_open_driver_is_released(runtime, monkeypatch):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_file = kernel.CreateFileW
    open_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    open_file.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    native_rename = os.rename
    attempts = []
    failures = []
    held = None

    def rename(stage, target):
        nonlocal held
        attempts.append((stage, target))
        if len(attempts) == 1:
            held = open_file(str(stage / "driver.exe"), 0x80000000, 7, None, 3, 0, None)
            assert held not in (None, ctypes.c_void_p(-1).value)
        try:
            native_rename(stage, target)
        except OSError as error:
            failures.append(error.winerror)
            raise

    def release(delay):
        nonlocal held
        assert delay > 0 and failures == [5]
        assert not attempts[0][1].exists()
        assert kernel.CloseHandle(held)
        held = None

    monkeypatch.setattr(paths.os, "rename", rename)
    monkeypatch.setattr(paths, "sleep", release, raising=False)
    try:
        target = paths.repository_root()
    finally:
        if held is not None:
            assert kernel.CloseHandle(held)
    assert attempts == [(attempts[0][0], target)] * 2
    assert failures == [5]
    assert (target / "driver.exe").read_bytes() == b"driver"
    assert list(runtime.iterdir()) == [target]


def publication_error(code):
    error = PermissionError(f"publication error {code}")
    error.winerror = code
    return error


@pytest.mark.parametrize(
    ("platform", "code", "retry"),
    [("win32", 5, True), ("win32", 32, True), ("win32", 3, False), ("darwin", 5, False), ("linux", 5, False)],
)
def test_publication_retries_are_bounded_and_platform_local(runtime, monkeypatch, platform, code, retry):
    error = publication_error(code)
    attempts = []
    delays = []

    def rename(stage, target):
        attempts.append((stage, target))
        raise error

    monkeypatch.setattr(paths, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(paths.os, "rename", rename)
    monkeypatch.setattr(paths, "sleep", delays.append, raising=False)
    with pytest.raises(PermissionError) as caught:
        paths.repository_root()
    assert caught.value is error
    assert bool(delays) is retry
    assert len(attempts) == len(delays) + 1 <= 8
    assert len(set(attempts)) == 1
    assert sum(delays) < 1
    assert list(runtime.iterdir()) == []


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_concurrent_publication_keeps_the_existing_winner(runtime, monkeypatch, platform):
    def rename(stage, target):
        target.mkdir()
        (target / "driver.exe").write_bytes(b"winner")
        raise FileExistsError("another process published first")

    monkeypatch.setattr(paths, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(paths.os, "rename", rename)
    target = paths.repository_root()
    assert (target / "driver.exe").read_bytes() == b"winner"
    assert list(runtime.iterdir()) == [target]


def test_windows_cleanup_failure_preserves_publication_error(runtime, monkeypatch):
    error = publication_error(5)

    def rename(stage, target):
        raise error

    def cleanup(stage):
        raise PermissionError("still held during cleanup")

    with monkeypatch.context() as patch:
        patch.setattr(paths, "sys", SimpleNamespace(platform="win32"))
        patch.setattr(paths, "sleep", lambda delay: None, raising=False)
        patch.setattr(paths.os, "rename", rename)
        patch.setattr(paths.shutil, "rmtree", cleanup)
        with pytest.raises(PermissionError) as caught:
            paths.repository_root()
    assert caught.value is error
    assert any("still held during cleanup" in note for note in caught.value.__notes__)
    assert len(list(runtime.glob(".prepare-*"))) == 1


def test_windows_winner_cleanup_failure_is_independent_of_callers_exception(runtime, monkeypatch):
    def rename(stage, target):
        target.mkdir()
        raise FileExistsError("another process published first")

    def cleanup(stage):
        raise PermissionError("still held during cleanup")

    with monkeypatch.context() as patch:
        patch.setattr(paths, "sys", SimpleNamespace(platform="win32"))
        patch.setattr(paths.os, "rename", rename)
        patch.setattr(paths.shutil, "rmtree", cleanup)
        try:
            raise RuntimeError("unrelated caller failure")
        except RuntimeError as unrelated:
            with pytest.raises(PermissionError, match="still held"):
                paths.repository_root()
            assert not getattr(unrelated, "__notes__", [])
