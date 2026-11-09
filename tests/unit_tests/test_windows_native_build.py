# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import subprocess
from pathlib import Path

import pytest
from computer_use_native_tools import native, pointer

CREATE_PROCESS_LIMIT = 32767


@pytest.mark.parametrize("compiler_fails", [False, True])
def test_windows_driver_compiles_deep_sources_with_a_temporary_response_file(tmp_path, monkeypatch, compiler_fails):
    root = tmp_path / "stage with spaces 中文"
    backend = root / "os_adapter/windows"
    sources_dir = backend / "Sources"
    nested = sources_dir / ("x" * max(1, 189 - len(str(sources_dir))))
    nested.mkdir(parents=True)
    sources = [nested / f"源码 {index:03}.cs" for index in reversed(range(190))]
    for source in sources:
        source.touch()

    windir = tmp_path / "Windows with spaces"
    framework = windir / "Microsoft.NET/Framework64/v4.0.30319"
    framework.mkdir(parents=True)
    compiler = framework / "csc.exe"
    for name in (
        "csc.exe",
        "System.dll",
        "System.Core.dll",
        "System.Web.Extensions.dll",
        "System.Drawing.dll",
        "System.Runtime.WindowsRuntime.dll",
        "System.Runtime.dll",
    ):
        (framework / name).touch()
    metadata = windir / "System32/WinMetadata"
    metadata.mkdir(parents=True)
    for name in ("Foundation", "Graphics", "Storage", "Media", "Globalization"):
        (metadata / f"Windows.{name}.winmd").touch()

    monkeypatch.setattr(native.sys, "platform", "win32")
    monkeypatch.setenv("WINDIR", str(windir))
    monkeypatch.setattr(pointer, "generate_pointer_assets", lambda *, root: None)
    monkeypatch.setattr(native, "report", lambda _: None)
    output = backend / ".build/computer-use-driver.exe"
    response_files = []
    direct_commands = []

    def command_length(command, args):
        text = subprocess.list2cmdline([str(command), *map(str, args)])
        return len(text.encode("utf-16-le")) // 2 + 1

    def run(command, args, cwd):
        assert cwd == backend
        if command_length(command, args) > CREATE_PROCESS_LIMIT:
            raise OSError(206, "CreateProcess command line is too long")
        if command == compiler and len(args) == 1 and args[0].startswith("@"):
            path = Path(args[0][1:])
            response_files.append(path)
            assert path.read_bytes().startswith(b"\xef\xbb\xbf")
            lines = path.read_text(encoding="utf-8-sig").splitlines()
            assert all(line.startswith('"') and line.endswith('"') for line in lines)
            flags = [line[1:-1] for line in lines]
            assert flags[:6] == [
                "/nologo",
                "/target:exe",
                "/platform:x64",
                "/optimize+",
                "/warnaserror+",
                f"/out:{output}",
            ]
            assert f"/reference:{output.with_name('Interop.NativeUIA.dll')}" in flags
            assert flags[-len(sources) :] == list(map(str, sorted(sources)))
            assert command_length(command, flags) > CREATE_PROCESS_LIMIT
            if compiler_fails:
                raise RuntimeError("csc.exe failed")
        else:
            direct_commands.append((command, args))

    monkeypatch.setattr(native, "run", run)
    if compiler_fails:
        with pytest.raises(RuntimeError, match="csc.exe failed"):
            native.build_windows(root)
    else:
        assert native.build_windows(root) == output

    assert len(response_files) == 1 and not response_files[0].parent.exists()
    assert direct_commands[0][0] == compiler and f"/out:{output.with_name('import-uia.exe')}" in direct_commands[0][1]
    assert direct_commands[1] == (output.with_name("import-uia.exe"), [output.parent])
    if not compiler_fails:
        assert (
            direct_commands[2][0] == compiler
            and f"/out:{output.with_name('computer-use-capture.exe')}" in direct_commands[2][1]
        )
